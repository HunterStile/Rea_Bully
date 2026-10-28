"""Source-bound DAT door objects, without applying locks or creating runtime actors.

Reads original PERIMETER/TRIGGER data and verified straight-line DATLoad calls.
Joins existing skinned assets only; retains native pose instructions and all raw
flags. A catalog entry does not establish that the door is currently active.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import ctypes
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import struct

from catalog_door_links import name_hash
from catalog_population import ROOT, fields, read_trigger_blocks, source, text_lines, ue_position
from catalog_world_specials import verified_script_source
from export_nif import FormatError
from export_zone import digest, read_ide_definitions


def below_normal():
    if hasattr(ctypes, "windll"):
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.SetPriorityClass.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        if not kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000):
            raise OSError("Could not set BelowNormal process priority")


def verify_source(reference):
    path = Path(reference["path"])
    if digest(path) != reference["sha256"]:
        raise FormatError(f"Changed source: {path}")
    return path


def verify_archive_reference(reference, entries):
    path = verify_source(reference)
    entry = entries.get(path.resolve())
    if not entry or not entry["verified"] or entry["extracted_sha256"] != reference["sha256"]:
        raise FormatError(f"Asset is not bound to the original archive extraction: {path}")
    return entry["index"]


def datload_calls(listing):
    """Four adjacent instructions, using full constants, never truncated comments."""
    lines = listing.read_text(encoding="utf-8").splitlines()
    constants, function, result = {}, "", []
    for index, line in enumerate(lines):
        if line.startswith(".function"):
            constants, function = {}, line.split(None, 1)[1]
        match = re.match(r'^\.constant\s+(k\d+)\s+(.+)$', line)
        if match:
            try:
                constants[match[1]] = json.loads(match[2])
            except ValueError:
                continue
        match = re.match(r'^getglobal\s+r(\d+)\s+(k\d+)\b', line)
        if not match or constants.get(match[2]) != "DATLoad" or index + 3 >= len(lines):
            continue
        register = int(match[1])
        argument = re.match(rf'^loadk\s+r{register+1}\s+(k\d+)\b', lines[index+1])
        slot = re.match(rf'^loadk\s+r{register+2}\s+(k\d+)\b', lines[index+2])
        call = re.match(rf'^call\s+r{register}\s+3\s+1\b', lines[index+3])
        if not (argument and slot and call):
            continue
        name, value = constants.get(argument[1]), constants.get(slot[1])
        if not isinstance(name, str) or not name.lower().endswith(".dat") or type(value) not in (int, float):
            continue
        if Path(name).name != name or not math.isfinite(value) or int(value) != value:
            raise FormatError("Unrecognized literal DATLoad arguments")
        result.append({"dat_name": name, "slot_raw": int(value), "function": function,
            "first_instruction_line": index+1, "last_instruction_line": index+4,
            "control_flow": "Straight-line call only; enclosing branch conditions and live activation are not evaluated"})
    return result


def verified_loaders(root):
    directory = root / "local/disassembled"
    manifest_path = directory / "manifest.json"
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))["files"]
    by_name = defaultdict(list)
    for entry in entries:
        by_name[Path(entry["listing"]).name.casefold()].append(entry)
    result = []
    # This is a declared scope, not a guess that every script with a door name is global.
    for script in ("main", "SchoolHallways", "Bdorm"):
        listing = directory / (script + ".luasm")
        provenance = verified_script_source(listing, root / "local/scripts" / (script + ".lur"),
            manifest_path, by_name[listing.name.casefold()])
        calls = datload_calls(listing)
        result.append({"script": script, "classification": "main_script_call" if script == "main" else "area_script_call",
            "source": provenance, "calls": calls})
    return result


def dat_groups(path):
    """Check explicit perimeter/trigger grouping and declared counts independently."""
    data = path.read_bytes()
    lines = text_lines(path)
    # Original byte spans include original line endings; padding is excluded.
    logical = data.split(b"\0", 1)[0]
    raw_lines = logical.splitlines(keepends=True)
    offsets = [0]
    for raw in raw_lines:
        offsets.append(offsets[-1] + len(raw))
    blocks = read_trigger_blocks(path)
    occupied, groups, current = set(), [], None
    for block in blocks:
        depth, end_line = 0, None
        for i in range(block["line"], len(lines)):
            text = lines[i].split("#", 1)[0].strip()
            if text == "BEGIN":
                depth += 1
            elif text == "END":
                depth -= 1
                if depth == 0:
                    end_line = i+1
                    break
        if end_line is None:
            raise FormatError("DAT block has no matching END")
        start, end = offsets[block["line"]-1], offsets[end_line]
        block = {**block, "end_line": end_line, "byte_offset": start, "byte_length": end-start,
            "raw_sha256": hashlib.sha256(data[start:end]).hexdigest()}
        occupied.update(range(block["line"], end_line+1))
        if block["type"] == "PERIMETER":
            current = {"perimeter": block, "triggers": []}
            groups.append(current)
        elif block["type"] == "TRIGGER":
            if current is None:
                raise FormatError("TRIGGER before PERIMETER")
            current["triggers"].append(block)
    declarations = []
    header = {}
    first_block_line = min((b["line"] for b in blocks), default=len(lines)+1)
    for number, line in enumerate(lines, 1):
        if number in occupied:
            continue
        if number < first_block_line:
            match = re.fullmatch(r'\s*(NTRIGGERS|NPERIMETERS)\s+(\d+)\s*', line.split("#", 1)[0])
            if match:
                if match[1] in header:
                    raise FormatError("Duplicate DAT header count")
                header[match[1]] = int(match[2])
                continue
        match = re.fullmatch(r'\s*NTRIGGERS\s+(\d+)\s*', line.split("#", 1)[0])
        if match:
            declarations.append((number, int(match[1])))
    for i, group in enumerate(groups):
        perimeter = group["perimeter"]
        following = groups[i+1]["perimeter"]["line"] if i+1 < len(groups) else len(lines)+1
        count = [(line, value) for line, value in declarations if perimeter["end_line"] < line < following]
        if len(count) != 1 or count[0][1] != len(group["triggers"]):
            raise FormatError(f"NTRIGGERS/group mismatch at {path}:{perimeter['line']}")
        if group["triggers"] and count[0][0] >= group["triggers"][0]["line"]:
            raise FormatError("NTRIGGERS occurs after first trigger")
        group["declared_trigger_count"] = count[0][1]
        group["count_line"] = count[0][0]
    if (len(declarations) != len(groups) or header.get("NPERIMETERS") != len(groups)
            or header.get("NTRIGGERS") != sum(len(g["triggers"]) for g in groups)):
        raise FormatError(f"DAT header/group count mismatch: {path}")
    return groups


def checked_fields(block, required):
    values = fields(block)
    counts = Counter(field["key"] for field in block["fields"])
    issues = [f"Expected exactly one {key}" for key in required if counts[key] != 1]
    return values, issues


def pose(trigger):
    values, issues = checked_fields(trigger, ("POSITION", "YAWPITCHROLL", "YAW", "AREACODE",
        "TRIGGERNAME", "ZHEIGHT", "ISMISSIONSPECIFIC", "ISLOCKED"))
    for key in ("POSITION", "YAWPITCHROLL"):
        value = values.get(key)
        if not isinstance(value, list) or len(value) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in value):
            issues.append(f"Invalid finite three-vector {key}")
    for key in ("YAW", "ZHEIGHT"):
        value = values.get(key)
        if type(value) not in (int, float) or not math.isfinite(value):
            issues.append(f"Invalid scalar {key}")
    if type(values.get("AREACODE")) is not int or not 0 <= values["AREACODE"] <= 255:
        issues.append("AREACODE is not an original byte")
    for key in ("ISMISSIONSPECIFIC", "ISLOCKED"):
        if type(values.get(key)) is not bool:
            issues.append(f"Invalid boolean {key}")
    if not isinstance(values.get("TRIGGERNAME"), str) or not values["TRIGGERNAME"] or not values["TRIGGERNAME"].isascii():
        issues.append("Invalid original ASCII TRIGGERNAME")
    result = {"position_source_game_m": values.get("POSITION"), "yaw_pitch_roll_degrees_raw": values.get("YAWPITCHROLL"),
        "yaw_degrees_raw": values.get("YAW"), "scale_from_factory": [1, 1, 1],
        "position_source": "TRIGGER POSITION via 0x4effb0; not POINT arrival or perimeter vertices",
        "final_engine_rotation_status": "pending_matrix_convention_validation_no_UE_rotation_emitted"}
    if not issues:
        result["position_unreal_cm"] = ue_position(values["POSITION"])
        result["native_rotation_recipe"] = [
            {"axis": [0, 0, 1], "angle_degrees_expression": "YAWPITCHROLL.x - stored_YAW_radians * 180 / native_pi", "mode": 0},
            {"axis": [0, 1, 0], "angle_degrees_raw": values["YAWPITCHROLL"][1], "mode": 2},
            {"axis": [1, 0, 0], "angle_degrees_raw": values["YAWPITCHROLL"][2], "mode": 2}]
    return result, issues


def definition_index(objects):
    result, files = defaultdict(list), {}
    for entries in read_ide_definitions(objects).values():
        for entry in entries:
            path = Path(entry["source"])
            if path not in files:
                files[path] = (path.read_text(encoding="utf-8-sig").splitlines(), digest(path))
            raw = files[path][0][entry["line"]-1]
            tokens = [v.strip() for v in raw.split("#", 1)[0].split(",")]
            record = {**entry, "source_sha256": files[path][1], "raw_line": raw, "raw_columns": tokens}
            if entry["section"] == "panm" and len(tokens) == 8:
                record.update(animation_group=tokens[3], ped_group=tokens[4])
            result[name_hash(entry["name"])].append(record)
    return result


def select_definition(name, definitions):
    candidates = definitions.get(name_hash(name), [])
    signatures = {(d["model_id"], d["name"].casefold(), d["texture_dictionary"].casefold(), d["section"],
                   tuple(v.casefold() for v in d["raw_columns"][3:])) for d in candidates}
    if len(signatures) != 1 or not candidates or candidates[0]["name"].casefold() != name.casefold():
        return None, candidates
    selected = dict(candidates[0])
    selected["equivalent_sources"] = [{"path": d["source"], "sha256": d["source_sha256"], "line": d["line"]} for d in candidates]
    return selected, []


def asset_record(model, definition, archive_entries):
    """Recheck existing conversion and all original animation extents, no export."""
    if model["model_id"] != definition["model_id"] or model["name"].casefold() != definition["name"].casefold():
        raise FormatError("Converted model does not match selected original IDE")
    old = model["original_ide"]
    if (old["sha256"] != definition["source_sha256"] or old["line"] != definition["line"]
            or old["raw"] != definition["raw_line"] or old["animation_group"] != definition.get("animation_group")):
        raise FormatError("Converted model IDE provenance differs")
    for reference in (model["source_nif"], model["skin_report"], model["source_group"],
                      {"path": model["output"], "sha256": model["output_sha256"]}):
        verify_source(reference)
    texture = model["texture_resolution"]
    selected = [c for c in texture["candidates"] if c["index"] == texture["selected_index"]]
    if len(selected) != 1 or selected[0]["sha256"] != texture["selected_sha256"]:
        raise FormatError("Converted model texture provenance is ambiguous")
    verify_source(selected[0])
    nif_index = verify_archive_reference(model["source_nif"], archive_entries)
    texture_index = verify_archive_reference(selected[0], archive_entries)
    group = json.loads(gzip.decompress(Path(model["source_group"]["path"]).read_bytes()))
    if group["group"]["group_name"].casefold() != definition["animation_group"].casefold():
        raise FormatError("Converted curves have a different original animation group")
    agr = group["group"]["source"]
    agr_index = verify_archive_reference(agr, archive_entries)
    agr_bytes = verify_source(agr).read_bytes()
    extent = group["group"]["expected_agr_bytes"]
    if (len(agr_bytes) != agr["bytes"] or any(agr_bytes[extent:])
            or len(agr_bytes)-extent != agr["padding_bytes"]
            or hashlib.sha256(agr_bytes[:extent]).hexdigest() != agr["payload_sha256"]):
        raise FormatError("Original AGR extent/padding changed")
    hxd = group["dictionary"]["source"]
    hxd_bytes = verify_source(hxd).read_bytes()
    hxd_range = hxd_bytes[hxd["embedded_offset"]:hxd["embedded_offset"]+hxd["embedded_size"]]
    if len(hxd_range) != hxd["embedded_size"] or hashlib.sha256(hxd_range).hexdigest() != hxd["embedded_sha256"]:
        raise FormatError("Original embedded HXD changed")
    clips = []
    for clip in group["clips"]:
        start, size = clip["agr_offset"], clip["agr_size"]
        if start < 0 or start+size > extent or hashlib.sha256(agr_bytes[start:start+size]).hexdigest() != clip["agr_sha256"]:
            raise FormatError("Clip is not bound to its original AGR bytes")
        converted = [c for c in model["clips"] if c["name"] == clip["name"]]
        if len(converted) != 1 or converted[0]["duration"] != clip["duration"]:
            raise FormatError("Clip conversion name/duration differs")
        clips.append({**{k: clip[k] for k in ("name", "ordinal", "duration", "agr_offset", "agr_size", "agr_sha256")},
            "glb_name": converted[0]["glb_name"]})
    return {"model_key": model["model_key"], "definition": definition, "source_nif": model["source_nif"],
        "converted_glb": {"path": model["output"], "sha256": model["output_sha256"]},
        "source_texture": {"path": selected[0]["path"], "sha256": selected[0]["sha256"]},
        "original_archive_indices": {"nif": nif_index, "texture": texture_index, "agr": agr_index},
        "source_group": model["source_group"], "source_agr": agr, "source_hxd": hxd,
        "skin_report": model["skin_report"], "clips": clips,
        "runtime_binding_status": "existing_original_geometry_and_clips_verified_runtime_not_integrated",
        "collision_status": "not_reconstructed_from_render_mesh", "interaction_status": "not_implemented"}


def native_evidence(root):
    addresses = ["6d55a0", "51c390", "4effb0", "4edb20", "436f50", "438850", "415eb0", "415e30",
        "415e00", "406830", "768820", "412a70", "4ef400", "4e9c10", "6d6570", "416150", "416f80"]
    functions = []
    for address in addresses:
        path = root / "local/native/decompiled/full-20261020/functions" / ("0x"+address+".c")
        text = path.read_text(encoding="utf-8")
        functions.append({**source(path), "address": "0x"+address, "rea_evidence_id": re.search(r'Evidence: (ev_\w+)', text)[1]})
    executable = root / "local/native/Bully.steam-original.exe.unpacked.exe"
    data = executable.read_bytes()
    pe = struct.unpack_from("<I", data, 0x3c)[0]
    sections = struct.unpack_from("<H", data, pe+6)[0]
    optional_size = struct.unpack_from("<H", data, pe+20)[0]
    image_base = struct.unpack_from("<I", data, pe+24+28)[0]
    start_va, end_va = 0x6d6bb0, 0x6d6eaa
    matches = []
    for i in range(sections):
        off = pe+24+optional_size+i*40
        _, rva, size, raw = struct.unpack_from("<4I", data, off+8)
        if rva <= start_va-image_base and end_va-image_base <= rva+size:
            start = raw+start_va-image_base-rva
            matches.append((start, data[start:start+end_va-start_va]))
    if len(matches) != 1:
        raise FormatError("Trigger parser native byte extent is ambiguous")
    start, raw = matches[0]
    return {"functions": functions, "trigger_parser": {"source": source(executable), "start_va": hex(start_va),
        "end_va_exclusive": hex(end_va), "file_offset": start, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        "inspection": "Bounded dumpbin disassembly; function absent from retained Ghidra function inventory",
        "field_stores": {"POSITION": "+0x04/+0x08/+0x0c", "YAWPITCHROLL": "+0x10/+0x14/+0x18",
            "YAW_radians": "+0x38 at VA 0x6d6ccd", "AREACODE": "+0x3c at VA 0x6d6c3c",
            "ISMISSIONSPECIFIC": "+0x3f bit 0 at VA 0x6d6d00", "ISLOCKED": "+0x3f bit 1 at VA 0x6d6d26"},
        "yaw_normalization": "Original single +/-360 correction followed by pi/180; raw values retained"}}


def run(root, game, output):
    loaders = verified_loaders(root)
    calls = defaultdict(list)
    for loader in loaders:
        for call in loader["calls"]:
            calls[call["dat_name"].casefold()].append({"script": loader["script"], "classification": loader["classification"], **call})
    directory = root / "local/extracted/DAT/Trigger"
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    world = root / "local/extracted/Stream/World"
    world_manifest_path = world / "manifest.json"
    world_manifest = json.loads(world_manifest_path.read_text(encoding="utf-8"))
    archive_entries = {(world / e["path"]).resolve(): e for e in world_manifest["entries"]}
    definitions = definition_index(game / "Objects")
    conversion_path = root / "evidence/skinned-props-export-v04-rgb.json"
    converted = json.loads(conversion_path.read_text(encoding="utf-8"))
    models_by_id = {m["model_id"]: m for m in converted["models"] if m["status"] == "exported"}
    link_path = root / "evidence/world-door-links.json"
    old_links = json.loads(link_path.read_text(encoding="utf-8"))["links"]
    files, objects, assets, exclusions = [], [], {}, Counter()
    for entry in manifest["entries"]:
        if entry["name"].casefold() not in calls:
            continue
        path = directory / entry["path"]
        if directory.resolve() not in path.resolve().parents or not entry["verified"] or digest(path) != entry["extracted_sha256"]:
            raise FormatError("DAT does not match verified archive extraction")
        groups = dat_groups(path)
        door_count = 0
        for group in groups:
            perimeter = group["perimeter"]
            pf, per_issues = checked_fields(perimeter, ("PLACEMENT", "ISDOOR", "HEIGHT", "DEPTH", "NPERIMETERPOINTS"))
            if pf.get("ISDOOR") is not True:
                exclusions["ISDOOR_not_TRUE_placement_triggers" if pf.get("PLACEMENT") else "no_model_placement_triggers"] += len(group["triggers"])
                continue
            point_x = [f["value"] for f in perimeter["fields"] if f["key"] == "PERIMETERPOINTX"]
            point_y = [f["value"] for f in perimeter["fields"] if f["key"] == "PERIMETERPOINTY"]
            if len(point_x) != len(point_y) or len(point_x) != pf.get("NPERIMETERPOINTS"):
                per_issues.append("Perimeter vertex count mismatch")
            order = [f["key"] for f in perimeter["fields"] if f["key"] in {"PERIMETERPOINTX", "PERIMETERPOINTY"}]
            if order != [key for _ in point_x for key in ("PERIMETERPOINTX", "PERIMETERPOINTY")]:
                per_issues.append("Perimeter XY fields are not alternating source pairs")
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in point_x+point_y+[pf.get("HEIGHT"), pf.get("DEPTH")]):
                per_issues.append("Invalid finite perimeter geometry")
            name = pf.get("PLACEMENT")
            definition, candidates = select_definition(name, definitions) if isinstance(name, str) and name else (None, [])
            key = None
            if definition and definition["model_id"] in models_by_id:
                model = models_by_id[definition["model_id"]]
                key = model["model_key"]
                if key not in assets:
                    assets[key] = asset_record(model, definition, archive_entries)
            for trigger in group["triggers"]:
                tf = fields(trigger)
                transform, issues = pose(trigger)
                issues = per_issues + issues
                if not name:
                    issues.append("ISDOOR TRUE has empty PLACEMENT; native model -1 cannot create an object")
                elif not definition:
                    issues.append("Original model lookup missing or ambiguous")
                if definition and not key:
                    issues.append("Original model is not present in the existing skinned conversion report")
                links = []
                for link in old_links:
                    source_trigger = link["source_trigger"]
                    if (Path(source_trigger["source"]["path"]).resolve() == path.resolve()
                            and source_trigger["line"] == trigger["line"]):
                        if (source_trigger["source"]["sha256"] != entry["extracted_sha256"]
                                or source_trigger["name"] != tf.get("TRIGGERNAME")
                                or source_trigger["source_position"] != tf.get("POSITION")):
                            raise FormatError("Existing transition link differs from original trigger")
                        links.append({"id": link["id"], "name": link["name"], "kind": link["kind"],
                            "destination_coordinate_proven": link["destination_coordinate_proven"]})
                objects.append({"id": f"{path.name}:{trigger['line']}", "dat": path.name,
                    "source": {**source(path), "archive_index": entry["index"]},
                    "perimeter": perimeter, "trigger": trigger, "declared_trigger_count": group["declared_trigger_count"],
                    "name": tf.get("TRIGGERNAME"), "name_hash": hex(name_hash(tf["TRIGGERNAME"])) if isinstance(tf.get("TRIGGERNAME"), str) else None,
                    "area_code_raw": tf.get("AREACODE"), "placement_name": name, "model_key": key,
                    "definition": definition, "ambiguous_definition_candidates": candidates,
                    "pose": transform, "source_isdoor_raw": pf.get("ISDOOR"),
                    "is_mission_specific_raw": tf.get("ISMISSIONSPECIFIC"), "source_islocked_raw": tf.get("ISLOCKED"),
                    "effective_lock_state": "unknown_saved_overrides_and_script_state_not_evaluated",
                    "activation": "not_evaluated_area_mission_bit_model_period_flags_and_loaded_pools_required",
                    "load_calls": calls[path.name.casefold()], "existing_transition_links": links,
                    "classification": "source_bound_model_and_pose_recipe" if not issues else "ambiguous_or_pending",
                    "issues": issues, "runtime_integrated": False})
                door_count += 1
        files.append({**source(path), "archive_index": entry["index"], "perimeters": len(groups),
            "trigger_count": sum(len(g["triggers"]) for g in groups), "door_candidates": door_count,
            "group_counts_verified": True, "load_calls": calls[path.name.casefold()]})
    result = {"schema": "rea-bully-dat-door-objects-v1", "classification": "static_source_bound_manifest_not_runtime",
        "source_manifest": source(manifest_path), "conversion_report": source(conversion_path), "transition_report": source(link_path),
        "world_archive_manifest": source(world_manifest_path),
        "loaders": loaders, "dat_files": files, "native_evidence": native_evidence(root),
        "objects": objects, "models": list(assets.values()),
        "summary": {"verified_dat_files": len(files), "door_candidates": len(objects), "models_verified": len(assets),
            "classifications": dict(Counter(o["classification"] for o in objects)),
            "by_dat": dict(Counter(o["dat"] for o in objects)), "by_model": dict(Counter(o["placement_name"] for o in objects)),
            "with_existing_transition_link": sum(bool(o["existing_transition_links"]) for o in objects),
            "mission_specific_raw_true": sum(o["is_mission_specific_raw"] is True for o in objects), "excluded_triggers": dict(exclusions)},
        "limitations": ["Catalog only: no Unreal actor, lock, interaction, collision or scheduling has been applied.",
            "ISDOOR TRUE is retained literally, including windows/gates; it is not inferred from a name.",
            "Native 0x436f50 requires nonmission trigger, matching area and model period flags; main/area DATLoad calls do not prove current activation.",
            "Pose position is proven from TRIGGER. Native rotation call recipe is retained; final engine matrix/quaternion conversion is pending.",
            "Doors sharing a position are not automatically paired or synchronized; transition references are exact original trigger records.",
            "ISLOCKED is a source flag, not effective state. Original save overrides and current script calls must still be reconstructed.",
            "PARA is referenced by a parametric CAT track and must not be treated as a short ambient loop.",
            "CAT branches, player synchronization, collision changes, camera execution and arrival handshake are not implemented."]}
    result["door_action_catalog"] = door_action_catalog(root, result["models"])
    result["door_action_catalog"]["original_archive_index"] = verify_archive_reference(result["door_action_catalog"]["source"], archive_entries)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return result


def door_action_catalog(root, models):
    # Import-safe parser supplied by the parallel prop-actions investigation.
    from catalog_prop_actions import Cat
    path = root / "local/extracted/Stream/World/06543/Door.cat"
    cat = Cat(path, known_names=("PropAnimation", "PropAnimationParametric"))
    wanted = [(1896, "Closed", "IDLE", 88, "PropAnimation", 17460),
        (5591, "Executes/DefaultOpening", "OPEN", 88, "PropAnimation", 12504),
        (5612, "Executes/DefaultClosing", "CLOSE", 88, "PropAnimation", 18775),
        (2029, "NotUseable/OpenParametric", "PARA", 96, "PropAnimationParametric", 17728)]
    nodes = {node["offset"]: node for node in cat.flat()}
    links = []
    for offset, label, clip, attribute, kind, expected_string in wanted:
        node = nodes[offset]
        candidates = []
        for track_offset in node.get("track_offsets", []):
            track = cat.track(track_offset)
            attrs = track["attrs"]
            if (attrs.get("0", {}).get("uint", 0) & 0x7fffffff) != name_hash(kind):
                continue
            record = attrs.get(str(attribute), {})
            if record.get("string") == clip:
                if record["file_offset"] != expected_string:
                    raise FormatError("Door CAT clip offset changed")
                candidates.append({"track_data_offset": track_offset, "track_file_offset": cat.data+track_offset,
                    "track_type_hash": hex(name_hash(kind)), "track_type_hash_candidate": kind,
                    "clip_attribute": attribute, "clip_string_file_offset": expected_string,
                    "clip_leaf": clip, "native_track_execution": "not_implemented"})
        if len(candidates) != 1:
            raise FormatError("Expected exact Door CAT clip reference missing/ambiguous")
        links.append({"node_offset": offset, "node_label_candidate": "/DOOR/"+label,
            "node_name_hash": node.get("name_hash"), **candidates[0]})
    return {"source": source(path), "header": list(cat.h), "links": links,
        "model_clip_joins": [{"model_key": model["model_key"], "clip_leaf": link["clip_leaf"],
            "matches": [clip for clip in model["clips"] if clip["name"].replace("\\", "/").split("/")[-1] == link["clip_leaf"]]}
            for model in models for link in links],
        "join_semantics": "Same clip leaf in original IDE-selected group. Does not prove all CAT conditions permit this model or branch.",
        "execution_status": "static_CAT_string_relocations_and_original_AGR_clips_only"}


if __name__ == "__main__":
    below_normal()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--game", type=Path, default=Path("C:/Program Files (x86)/Steam/steamapps/common/Bully Scholarship Edition"))
    parser.add_argument("--output", type=Path, default=ROOT / "evidence/door-objects-v05.json")
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.game, args.output)["summary"], indent=2))

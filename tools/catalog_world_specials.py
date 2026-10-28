"""Read every IPB section and conditional placement using retained native evidence.

Independent parser; no target execution, original game changes or GLB mutation.
Record boundaries follow retained REA readers, never scanning payloads for tags.
Unknown state remains unknown; names are corroboration, not activation policy.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import struct

from export_nif import FormatError, Reader
from export_world import unreal_transform, gltf_matrix, write_json
from export_zone import archive_index, choose_definition, digest, read_ide_definitions, ResolutionError
from catalog_door_links import name_hash
from catalog_world import model_key


KINDS = {0: "false_unless_override", 1: "not_completed_and_not_active", 2: "active_specific_mission",
    3: "active_or_completed", 4: "completed", 5: "explicit_special_group_enabled", 6: "not_completed", 7: "not_active_specific_mission"}
FIXED_RECORDS = {"inst": 120, "spec": 128, "proj": 108, "occl": 28}
NATIVE_READERS = {"section_dispatch": "0x435290", "header": "0x4358d0",
    "inst": "0x433560", "spec": "0x4335c0", "proj": "0x433630", "occl": "0x4336a0",
    "prop": "0x434850", "rail": "0x4350a0", "perm": "0x433840", "trig": "0x433a20",
    "pont": "0x433ed0", "pthx": "0x4346c0", "pois": "0x434f40",
    "condition_predicate": "0x6ae790", "enable_group_max_four": "0x6ae730", "disable_group": "0x6ae760"}


def count(reader):
    value = reader.read("I")
    if value > 100_000:
        raise FormatError(f"Excessive IPB count {value} at {reader.pos-4}")
    return value


def text8(reader):
    return reader.take(reader.read("B")).decode("ascii")


def skip_record(reader, tag):
    """Only boundary/name parsing for non-placement sections, not gameplay semantics."""
    if tag in FIXED_RECORDS:
        reader.take(FIXED_RECORDS[tag])
        return {}
    name = text8(reader)
    if tag == "prop":
        return {"name": name}
    if tag == "rail":
        opaque = reader.read("I")
        points = count(reader)
        reader.take(points * 12)
        return {"name": name, "opaque_u32": opaque, "point_count": points}
    if tag == "perm":
        opaque = list(reader.read("3I"))
        entries = count(reader)
        reader.take(entries * 4)
        return {"name": name, "opaque_u32_fields": opaque, "entry_count": entries}
    if tag == "trig":
        reader.take(19 * 4)
        return {"name": name}
    if tag == "pont":
        reader.take(8 * 4)
        strings = [text8(reader) for _ in range(3)]
        reader.take(6 * 4)
        strings.extend(text8(reader) for _ in range(2))
        return {"name": name, "other_strings": strings}
    if tag == "pthx":
        reader.take(8)
        entries = count(reader)
        reader.take(entries * 36)
        return {"name": name, "entry_count": entries}
    if tag == "pois":
        opaque = reader.read("I")
        entries = count(reader)
        records = []
        for _ in range(entries):
            strings = [text8(reader) for _ in range(4)]
            reader.take(21 * 4)
            records.append({"strings": strings})
        return {"name": name, "opaque_u32": opaque, "entry_count": entries, "entries": records}
    raise FormatError(f"Unknown section {tag!r}")


def placement(raw, tag, section_index, index, offset):
    values = struct.unpack("<i64sf3f3f4f" + ("4I" if tag == "spec" else "2I"), raw)
    model_id, raw_name, opaque_float, *rest = values
    position, scale, quaternion = list(rest[:3]), list(rest[3:6]), list(rest[6:10])
    if not all(math.isfinite(v) for v in (opaque_float, *position, *scale, *quaternion)):
        raise FormatError(f"Nonfinite {tag} placement at {offset}")
    if abs(sum(v*v for v in quaternion) - 1) > .01:
        raise FormatError(f"Nonunit {tag} quaternion at {offset}")
    record = {"section_index": section_index, "record_index": index, "record_offset": offset,
        "record_bytes": len(raw), "record_sha256": hashlib.sha256(raw).hexdigest(), "section": tag,
        "model_id": model_id, "name": raw_name.split(b"\0")[0].decode("ascii"),
        "opaque_float_68": opaque_float, "position": position, "scale": scale, "quaternion_xyzw": quaternion,
        "opaque_u32_112": values[13], "instance_name_hash_116": values[14]}
    record["unreal_transform"] = unreal_transform(record)
    record["gltf_matrix_column_major"] = gltf_matrix(record)
    if tag == "spec":
        record.update(condition_hash_120=values[15], condition_kind_124=values[16],
            condition_semantics=KINDS.get(values[16], "unrecognized_native_kind"),
            eligibility="explicit_group_selectable" if values[16] == 5 else "mission_state_dependent")
    return record


def parse_ipb(data):
    if data[:4] != b"Ipl$" or len(data) < 23:
        raise FormatError("Not an admitted original IPB")
    reader = Reader(data)
    reader.take(19)
    section_count = count(reader)
    sections = []
    for section_index in range(section_count):
        offset = reader.pos
        tag = reader.take(4).decode("ascii")
        if tag not in {*FIXED_RECORDS, "prop", "rail", "perm", "trig", "pont", "pthx", "pois"}:
            raise FormatError(f"Unknown section tag {tag!r} at {offset}")
        records = count(reader)
        section = {"section_index": section_index, "tag": tag, "offset": offset, "record_count": records, "records": []}
        for index in range(records):
            start = reader.pos
            try:
                if tag in {"inst", "spec"}:
                    record = placement(reader.take(FIXED_RECORDS[tag]), tag, section_index, index, start)
                else:
                    record = skip_record(reader, tag)
                    record.update(record_index=index, record_offset=start, record_bytes=reader.pos-start)
            except (FormatError, UnicodeError, struct.error) as exc:
                raise FormatError(f"{tag} section {section_index}, record {index} at {start}: {exc}") from exc
            section["records"].append(record)
        section["end_offset"] = reader.pos
        sections.append(section)
    parsed_end = reader.pos
    if any(reader.take(len(data)-reader.pos)):
        raise FormatError(f"Nonzero trailing data after declared sections at {parsed_end}")
    return {"declared_section_count": section_count, "parsed_end_offset": parsed_end,
        "zero_sector_padding_bytes": len(data)-parsed_end, "sections": sections}


def evaluate_condition(kind, group_hash, *, active_mission_hash=None, completed_mission_hashes=None,
                       enabled_group_hashes=(), override_hash=None, override_kind=None):
    """Static transcription of native predicate; unknown progress returns None."""
    completed = None if completed_mission_hashes is None else group_hash in completed_mission_hashes
    active = group_hash == active_mission_hash
    result = {0: False, 2: active, 5: group_hash in enabled_group_hashes, 7: not active}.get(kind, False)
    if kind == 1:
        result = False if active or completed else None if completed is None else True
    elif kind == 3:
        result = True if active or completed else None if completed is None else False
    elif kind == 4:
        result = completed
    elif kind == 6:
        result = None if completed is None else not completed
    if override_hash not in (None, -1, 0xffffffff) and override_hash == group_hash:
        if override_kind == 0:
            return False
        if override_kind == kind:
            return True
    return result


def verified_script_source(path, original, manifest_path, entries):
    """Bind listing and logical LUR bytes to the existing roundtrip manifest.

    Archive allocations include zero sector padding; Unluac's assembled output
    ends at the logical chunk. Never strip arbitrary zeros from a Lua chunk.
    The retained manifest records its exact logical boundary and all hashes.
    """
    assembled = path.with_suffix(".lur")
    if not original.is_file() or not assembled.is_file():
        raise FormatError("Original or reassembled LUR is missing")
    original_bytes, assembled_bytes = original.read_bytes(), assembled.read_bytes()
    original_sha, assembled_sha, listing_sha = digest(original), digest(assembled), digest(path)
    matches = [entry for entry in entries
        if entry.get("verified") is True and entry.get("input_sha256") == original_sha
        and Path(entry["input"]).name.casefold() == original.name.casefold()
        and entry.get("listing_sha256") == listing_sha
        and entry.get("reassembled_sha256") == assembled_sha]
    if len(matches) != 1:
        raise FormatError("Listing/original/reassembly has no unique matching verified roundtrip record")
    entry = matches[0]
    logical = entry["logical_bytes"]
    if (logical != len(assembled_bytes) or entry["allocated_bytes"] != len(original_bytes)
            or entry["padding_bytes"] != len(original_bytes)-logical
            or original_bytes[:logical] != assembled_bytes
            or hashlib.sha256(original_bytes[:logical]).hexdigest() != entry["original_logical_sha256"]
            or not entry["zero_padding"] or any(original_bytes[logical:])):
        raise FormatError("Logical bytecode/padding differs from verified roundtrip boundaries")
    return {"listing": str(path.resolve()), "listing_sha256": listing_sha,
        "original_lur": str(original.resolve()), "original_lur_sha256": original_sha,
        "assembled_lur": str(assembled.resolve()), "assembled_lur_sha256": assembled_sha,
        "assembled_roundtrip_equal": True, "roundtrip_scope": "logical chunk; archive zero padding separately verified",
        "logical_bytes": logical, "zero_padding_bytes": len(original_bytes)-logical,
        "roundtrip_manifest": str(manifest_path.resolve()), "roundtrip_manifest_sha256": digest(manifest_path)}


def script_evidence(root, hashes):
    """Hash candidates and direct four-opcode calls from roundtripped bytecode.

    Decompiled Lua is deliberately unused. Each listing's assembled LUR must
    equal the original logical bytes, with zero archive padding independently
    verified, before any string/call can contribute evidence.
    """
    names = defaultdict(dict)
    operations, sources, rejected = [], [], []
    pairs = [(root / "local/disassembled", root / "local/scripts"),
             (root / "local/world-disassembled", root / "local/extracted/Stream/World")]
    for directory, originals in pairs:
        manifest_path = directory / "manifest.json"
        entries_by_listing = defaultdict(list)
        if manifest_path.is_file():
            for entry in json.loads(manifest_path.read_text(encoding="utf-8"))["files"]:
                entries_by_listing[Path(entry["listing"]).name.casefold()].append(entry)
        for path in sorted(directory.rglob("*.luasm")):
            original = originals / path.relative_to(directory).with_suffix(".lur")
            try:
                source = verified_script_source(path, original, manifest_path, entries_by_listing[path.name.casefold()])
            except FormatError as exc:
                rejected.append({"listing": str(path.resolve()), "reason": str(exc)})
                continue
            sources.append(source)
            constants, function = {}, ""
            lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
            for number, line in enumerate(lines, 1):
                if line.startswith(".function"):
                    constants, function = {}, line.split(None, 1)[1]
                match = re.match(r'^\.constant\s+(k\d+)\s+(".*")$', line)
                if match:
                    try:
                        value = json.loads(match[2])
                        hashed = name_hash(value)
                    except (ValueError, UnicodeError):
                        continue
                    constants[match[1]] = value
                    if hashed in hashes:
                        entry = names[hashed].setdefault(value.upper(), {"name": value, "case_equivalent_names": [], "sources": []})
                        if value not in entry["case_equivalent_names"]:
                            entry["case_equivalent_names"].append(value)
                        entry["sources"].append({"listing": source["listing"], "line": number, "function": function})
                global_read = re.match(r'^getglobal\s+r(\d+)\s+(k\d+)', line)
                if not global_read or constants.get(global_read[2]) != "AreaLoadSpecialEntities" or number+2 >= len(lines):
                    continue
                register = int(global_read[1])
                arg = re.match(rf'^loadk\s+r{register+1}\s+(k\d+)\b', lines[number])
                enabled = re.match(rf'^loadbool\s+r{register+2}\s+([01])\s+0\b', lines[number+1])
                call = re.match(rf'^call\s+r{register}\s+3\s+1\b', lines[number+2])
                if arg and enabled and call and isinstance(constants.get(arg[1]), str):
                    value = constants[arg[1]]
                    operations.append({"api": "AreaLoadSpecialEntities", "name": value, "name_hash": name_hash(value),
                        "enabled": enabled[1] == "1", "function": function, "listing": source["listing"],
                        "instruction_lines": [number, number+3], "original_lur_sha256": source["original_lur_sha256"],
                        "control_flow": "Direct straight-line instruction sequence; surrounding branch conditions not evaluated"})
            # An original script's exact file stem can also identify a mission
            # hash; it remains a candidate, never proof of activation.
            value = original.stem
            hashed = name_hash(value)
            if hashed in hashes:
                entry = names[hashed].setdefault(value.upper(), {"name": value, "case_equivalent_names": [], "sources": []})
                if value not in entry["case_equivalent_names"]:
                    entry["case_equivalent_names"].append(value)
                entry["sources"].append({"original_lur": source["original_lur"], "provenance": "exact original script filename stem"})
    return {"name_candidates": {key: list(value.values()) for key, value in names.items()},
        "direct_group_operations": operations, "verified_listing_sources": sources, "rejected_listings": rejected}


def native_evidence(root):
    """Link the parser's claims to retained REA outputs, not current execution."""
    result = []
    for role, address in NATIVE_READERS.items():
        path = root / "local/native/decompiled/full-20261020/functions" / (address + ".c")
        text = path.read_text(encoding="utf-8")
        identity = re.search(r"Input SHA-256: ([0-9a-f]{64})", text)
        evidence = re.search(r"Evidence: (ev_[0-9a-f]+)", text)
        result.append({"role": role, "address": address, "retained_pseudocode": str(path.resolve()),
            "retained_pseudocode_sha256": digest(path), "analysis_copy_sha256": identity[1] if identity else None,
            "rea_evidence_id": evidence[1] if evidence else None})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", type=Path, default=Path("local/extracted/Stream/World"))
    parser.add_argument("--game", type=Path, default=Path("C:/Program Files (x86)/Steam/steamapps/common/Bully Scholarship Edition"))
    parser.add_argument("--report", type=Path, default=Path("evidence/world-specials.json"))
    args = parser.parse_args()
    by_name, manifest = archive_index(args.world)
    definitions = read_ide_definitions(args.game / "Objects")
    root = Path(__file__).resolve().parents[1]
    final_world_path = root / "evidence/world-export.json"
    final_world = json.loads(final_world_path.read_text(encoding="utf-8")) if final_world_path.exists() else None
    known_models = {m["model_key"]: m for m in final_world["models"]} if final_world else {}
    zones, errors = [], []
    for filename, candidates in sorted(by_name.items()):
        if not filename.endswith(".ipb"):
            continue
        if len({c["manifest_sha256"] for c in candidates}) != 1:
            raise FormatError(f"Differing original IPB candidates for {filename}")
        candidate = candidates[0]
        path = Path(candidate["path"])
        if digest(path) != candidate["manifest_sha256"]:
            raise FormatError("Original IPB differs from verified archive extraction")
        zone = {"name": path.stem, "source_ipb": str(path.resolve()), "source_ipb_sha256": candidate["manifest_sha256"],
            "source_archive_index": candidate["index"]}
        try:
            zone.update(parse_ipb(path.read_bytes()), status="parsed")
            for section in zone["sections"]:
                if section["tag"] not in {"inst", "spec"}:
                    continue
                for record in section["records"]:
                    try:
                        record["definition"] = choose_definition(record, definitions)
                        record["definition_status"] = "resolved"
                        record["model_key"] = model_key(record["model_id"], record["definition"]["name"])
                        known = known_models.get(record["model_key"])
                        record["existing_static_conversion_status"] = known["status"] if known else "not_in_first_inst_catalog"
                        if known and known["status"] == "exported":
                            record["existing_static_model_glb"] = known["output"]
                            record["existing_static_model_glb_sha256"] = known["output_sha256"]
                    except ResolutionError as exc:
                        record.update(definition_status="ambiguous", definition_candidates=exc.candidates)
        except (FormatError, UnicodeError, struct.error, ValueError) as exc:
            zone.update(status="unresolved_layout", error=str(exc))
            errors.append({"zone": path.stem, "error": str(exc)})
        zones.append(zone)
    sections = [section for zone in zones for section in zone.get("sections", [])]
    specials = [p for s in sections if s["tag"] == "spec" for p in s["records"]]
    extra_inst = [p for s in sections if s["tag"] == "inst" and s["section_index"] != 0 for p in s["records"]]
    targets = {p["condition_hash_120"] for p in specials} | {p["instance_name_hash_116"] for p in specials + extra_inst}
    scripts = script_evidence(root, targets)
    groups = defaultdict(list)
    for record in specials:
        hashed, kind = record["condition_hash_120"], record["condition_kind_124"]
        record["condition_name_candidates"] = scripts["name_candidates"].get(hashed, [])
        record["instance_name_candidates"] = scripts["name_candidates"].get(record["instance_name_hash_116"], [])
        record["no_active_mission_unknown_progress_empty_groups"] = evaluate_condition(kind, hashed)
        groups[(kind, hashed)].append(record)
    condition_groups = [{"condition_kind": kind, "condition_hash": hashed,
        "condition_semantics": KINDS.get(kind, "unknown"), "placement_count": len(records),
        "name_candidates": scripts["name_candidates"].get(hashed, []),
        "script_group_operations": [o for o in scripts["direct_group_operations"] if o["name_hash"] == hashed]}
        for (kind, hashed), records in sorted(groups.items())]
    result = {"schema_version": 2, "classification": "local_binary_layout_correlated_with_retained_REA_static_native_evidence",
        "native_evidence": native_evidence(root),
        "source_world_sha256": manifest["source"]["image"]["sha256"],
        "summary": {"zone_count": len(zones), "zones_fully_parsed": len(zones)-len(errors),
            "sections_by_tag": dict(Counter(s["tag"] for s in sections)),
            "records_by_tag": dict(Counter({tag: sum(s["record_count"] for s in sections if s["tag"] == tag) for tag in sorted({s["tag"] for s in sections})})),
            "spec_records": len(specials), "spec_condition_kinds": dict(Counter(p["condition_kind_124"] for p in specials)),
            "supplementary_inst_records": len(extra_inst),
            "spec_hashes_resolved_to_original_strings": sum(bool(g["name_candidates"]) for g in condition_groups),
            "spec_condition_groups": len(condition_groups),
            "spec_default_predicate_results": dict(Counter(str(p["no_active_mission_unknown_progress_empty_groups"]) for p in specials)),
            "verified_script_listings": len(scripts["verified_listing_sources"]),
            "rejected_script_listings": len(scripts["rejected_listings"])},
        "existing_static_catalog_sha256_read_only": digest(final_world_path) if final_world else None,
        "condition_groups": condition_groups, "script_evidence": scripts,
        "native_condition_kinds": KINDS, "errors": errors,
        "limitations": ["No target execution or original runtime observation",
            "inst/spec placement decoding and record boundaries are verified; other sections have boundary/name parsing only",
            "Mission completion and active special groups are not inferred from a filename or saved progress",
            "Native hashes can collide; name resolutions must retain every candidate",
            "No existing world catalog, GLB or UE assets were changed by this inventory"], "zones": zones}
    write_json(args.report, result)
    print(json.dumps({"summary": result["summary"], "errors": errors}, indent=2))


if __name__ == "__main__":
    main()

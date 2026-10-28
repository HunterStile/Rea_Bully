"""Export original static instances in a local Bully IPB, with explicit coverage.

Input: indexed World extraction + original Objects/*.ide text definitions.
Output: embedded-texture GLBs under local/ and a metadata-only coverage report.
Ambiguous differing archive candidates are reported rather than picked by order.
No native gameplay, animation, collision, time-of-day or LOD simulation is added.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import struct

from export_nif import FormatError, Nif, export, load_textures, read_instances, texture_basename


class ResolutionError(FormatError):
    def __init__(self, message, candidates=None):
        super().__init__(message)
        self.candidates = candidates or []


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_ide_definitions(objects):
    """Read id/name/texture links only from documented static/animated sections."""
    definitions = defaultdict(list)
    for path in sorted(objects.rglob("*")):
        if not path.is_file() or path.suffix.lower() != ".ide":
            continue
        section = ""
        for line_number, raw in enumerate(path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1):
            text = raw.split("#", 1)[0].strip()
            if not text:
                continue
            if "," not in text:
                section = text.lower()
                continue
            if section not in {"objs", "tobj", "panm"}:
                continue
            cells = [c.strip() for c in text.split(",")]
            try:
                model_id = int(cells[0], 0)
            except (ValueError, IndexError):
                continue
            if len(cells) < 3:
                continue
            definitions[model_id].append({"model_id": model_id, "name": cells[1],
                "texture_dictionary": cells[2], "section": section,
                "source": str(path), "line": line_number})
    return definitions


def choose_definition(instance, definitions):
    candidates = definitions.get(instance["model_id"], [])
    name = instance["name"].lower()
    candidates = [d for d in candidates if not name or d["name"].lower() == name]
    groups = defaultdict(list)
    for candidate in candidates:
        groups[(candidate["name"].lower(), candidate["texture_dictionary"].lower(), candidate["section"])].append(candidate)
    if len(groups) != 1:
        raise ResolutionError("IDE definition missing or ambiguous for placement id/name", candidates)
    equivalent = next(iter(groups.values()))
    chosen = dict(equivalent[0])
    chosen["equivalent_definition_sources"] = [{"source": d["source"], "line": d["line"]} for d in equivalent]
    return chosen


def invisible_reason(name):
    low = name.lower()
    if low.startswith(("nog_", "nogo_", "walkable_", "walkt")) or "_nd" in low:
        return "utility/no-draw volume excluded from visible static preview"
    if "proxy" in low or "proxies" in low:
        return "distance-LOD proxy excluded from near-field static preview"
    return None


def archive_index(world):
    manifest = json.loads((world / "manifest.json").read_text(encoding="utf-8"))
    by_name = defaultdict(list)
    for entry in manifest["entries"]:
        path = (world / entry["path"]).resolve()
        if world.resolve() not in path.parents:
            raise ResolutionError("Archive manifest path escapes extracted World")
        by_name[entry["name"].lower()].append({"index": entry["index"], "path": str(path),
            "manifest_sha256": entry["extracted_sha256"]})
    return by_name, manifest


def required_base_names(nif):
    result = set()
    for i, kind in enumerate(nif.types):
        if kind == "NiTexturingProperty":
            prop = nif.block(i)
            if prop["slots"] and prop["slots"][0]:
                source = nif.block(prop["slots"][0]["source"])
                result.add(texture_basename(source["filename"]))
    return result


def dictionary_texture_names(path):
    nif = Nif(path)
    return {texture_basename(nif.block(i)["filename"]) for i, kind in enumerate(nif.types)
            if kind == "NiSourceTexture"}


def find_supplemental_textures(missing, by_name):
    """Use only the exact referenced name and verified original pixels.

    This reconstructs a dependency, not the original engine's load-order rules.
    Differing pixels for the same name are ambiguous and stop that model.
    """
    found = defaultdict(list)
    needles = {name: name.encode("ascii") for name in missing}
    for filename, entries in by_name.items():
        if not filename.endswith(".nft"):
            continue
        for entry in entries:
            path = Path(entry["path"])
            raw = path.read_bytes()
            lower = raw.lower()
            possible = {name for name, needle in needles.items() if needle in lower}
            if not possible:
                continue
            actual_hash = hashlib.sha256(raw).hexdigest()
            if actual_hash != entry["manifest_sha256"]:
                raise ResolutionError("Supplemental dictionary differs from verified extraction", [entry])
            names = dictionary_texture_names(path)
            if not possible & names:
                continue
            decoded = load_textures([path])
            for name in possible & names:
                if name not in decoded:
                    continue
                found[name].append({**entry, "sha256": actual_hash,
                    "decoded_png_sha256": hashlib.sha256(decoded[name]["png"]).hexdigest()})
    chosen_paths, resolutions = [], []
    for name in sorted(missing):
        candidates = found[name]
        if not candidates or len({c["decoded_png_sha256"] for c in candidates}) != 1:
            raise ResolutionError(f"Exact supplemental texture absent or has differing original pixels: {name}", candidates)
        chosen = candidates[0]
        path = Path(chosen["path"])
        if path not in chosen_paths:
            chosen_paths.append(path)
        resolutions.append({"texture_name": name,
            "selection_reason": "exact referenced name, all matching original decoded pixels identical",
            "selected_index": chosen["index"], "selected_sha256": chosen["sha256"], "candidates": candidates})
    return chosen_paths, resolutions


def resolve_asset(filename, by_name, *, model_name=None, required_textures=None):
    candidates = []
    for entry in by_name.get(filename.lower(), []):
        candidate = dict(entry)
        path = Path(entry["path"])
        try:
            candidate["sha256"] = digest(path)
            if candidate["sha256"] != candidate["manifest_sha256"]:
                raise FormatError("Extracted file no longer matches verified manifest SHA-256")
            nif = Nif(path)
            if model_name is not None:
                names = [nif.block(i).get("name", "") for i, kind in enumerate(nif.types)
                         if kind in {"NiNode", "NiTriShape", "NiTriStrips"}]
                candidate["model_node_exact_match"] = model_name.lower() in {n.lower() for n in names}
            if required_textures is not None:
                names = {texture_basename(nif.block(i)["filename"]) for i, kind in enumerate(nif.types)
                         if kind == "NiSourceTexture"}
                candidate["missing_required_names"] = sorted(required_textures - names)
            candidate["valid_container"] = True
        except (FormatError, OSError, struct.error, KeyError) as exc:
            candidate["rejected"] = str(exc)
        candidates.append(candidate)
    valid = [c for c in candidates if c.get("valid_container")]
    if not valid:
        raise ResolutionError("No valid matching archive candidate", candidates)
    # Sector payload equality is verified from actual files, not name/order.
    hashes = {c["sha256"] for c in valid}
    reason = "single valid candidate"
    if len(hashes) == 1:
        chosen = valid[0]
        if len(valid) > 1:
            reason = "all valid candidates have identical verified bytes"
    else:
        if model_name is not None:
            matches = [c for c in valid if c.get("model_node_exact_match")]
            reason = "unique candidate with exact internal model-node name"
        else:
            matches = [c for c in valid if not c.get("missing_required_names")]
            reason = "unique candidate containing every referenced base texture name"
        if len(matches) != 1:
            raise ResolutionError("Differing archive payloads remain ambiguous after metadata validation", candidates)
        chosen = matches[0]
    return Path(chosen["path"]), {"selection_reason": reason, "selected_index": chosen["index"],
        "selected_sha256": chosen["sha256"], "candidates": candidates}


def export_zone(world, objects, ipb, output, report_path):
    output.mkdir(parents=True, exist_ok=True)
    if any(output.glob("*.glb")):
        raise FormatError("Output already contains GLBs; use a fresh output directory to avoid stale coverage")
    by_name, manifest = archive_index(world)
    definitions = read_ide_definitions(objects)
    instances = read_instances(ipb)
    grouped = defaultdict(list)
    for index, instance in enumerate(instances):
        grouped[(instance["model_id"], instance["name"].lower())].append((index, instance))
    report = {"schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_ipb": str(ipb.resolve()), "source_ipb_sha256": digest(ipb),
        "source_world_sha256": manifest["source"]["image"]["sha256"], "original_instance_count": len(instances),
        "output_directory": str(output.resolve()), "models": [], "summary": {},
        "input_transform_audit": {
            "all_finite": all(math.isfinite(v) for i in instances for v in (*i["position"], *i["scale"], *i["quaternion_xyzw"])),
            "negative_scale_determinant_count": sum(math.prod(i["scale"]) < 0 for i in instances),
            "nonuniform_scale_count": sum(max(i["scale"]) - min(i["scale"]) > 1e-5 for i in instances),
            "quaternion_norm_squared_error_max": max(abs(sum(v*v for v in i["quaternion_xyzw"]) - 1) for i in instances)},
        "limitations": ["Static inst section only; no collision, navigation, Lua, missions, skin or AGR animation",
            "Time-of-day object visibility and original distance LOD selection are not simulated",
            "Utility volumes and explicitly named proxy LOD geometry are excluded with report entries",
            "Original special shaders, animated controllers and extra texture slots are not evaluated",
            "No replacement geometry; unresolved or unsupported items are skipped explicitly"]}
    cache = {}
    for sequence, ((model_id, name), group) in enumerate(grouped.items(), 1):
        record = {"model_id": model_id, "name": group[0][1]["name"], "instance_indices": [i for i, _ in group],
            "instance_count": len(group), "status": "pending"}
        reason = invisible_reason(name)
        if reason:
            record.update(status="excluded", reason=reason)
        else:
            try:
                definition = choose_definition(group[0][1], definitions)
                record["definition"] = definition
                if definition["section"] == "panm":
                    raise FormatError("Animated panm model is unsupported in static export")
                model, model_resolution = resolve_asset(definition["name"] + ".nif", by_name, model_name=definition["name"])
                record["model_resolution"] = model_resolution
                model_data = Nif(model)
                if "NiSkinInstance" in model_data.types:
                    raise FormatError("Skinned model is unsupported in static export")
                required = required_base_names(model_data)
                texture_paths = []
                if required:
                    texture, texture_resolution = resolve_asset(definition["texture_dictionary"] + ".nft", by_name, required_textures=required)
                    record["texture_resolution"] = texture_resolution
                    texture_paths.append(texture)
                    missing = required - dictionary_texture_names(texture)
                    if missing:
                        extras, resolutions = find_supplemental_textures(missing, by_name)
                        texture_paths.extend(extras)
                        record["supplemental_texture_resolution"] = resolutions
                safe = re.sub(r"[^A-Za-z0-9_.-]", "_", definition["name"])
                target = output / f"{model_id:05d}_{safe}.glb"
                result = export(model, texture_paths, target, True, ipb,
                    instances=[i for _, i in group], texture_cache=cache, loose_textures=False, require_textures=True)
                record.update(status="exported", output=str(target.resolve()), export=result)
            except (FormatError, OSError, struct.error, KeyError, ValueError) as exc:
                record.update(status="ambiguous" if isinstance(exc, ResolutionError) else "unsupported", reason=str(exc))
                if isinstance(exc, ResolutionError):
                    record["resolution_candidates"] = exc.candidates
        report["models"].append(record)
        if sequence % 20 == 0 or sequence == len(grouped):
            print(json.dumps({"processed_models": sequence, "total_models": len(grouped),
                "statuses": dict(Counter(r["status"] for r in report["models"]))}), flush=True)
    statuses = Counter(r["status"] for r in report["models"])
    instance_statuses = Counter()
    for record in report["models"]:
        instance_statuses[record["status"]] += record["instance_count"]
    exports = [r["export"] for r in report["models"] if r["status"] == "exported"]
    report["summary"] = {"unique_model_groups": len(grouped), "models_by_status": dict(statuses),
        "instances_by_status": dict(instance_statuses), "glb_count": len(exports),
        "shapes": sum(r["shapes"] for r in exports), "vertices": sum(r["vertices"] for r in exports),
        "triangles": sum(r["triangles"] for r in exports), "texture_references": sum(r["texture_count"] for r in exports),
        "mirrored_shapes_corrected": sum(r["mirrored_shapes_corrected"] for r in exports),
        "bounds_min_gltf": [min(r["bounds_min_gltf"][i] for r in exports) for i in range(3)] if exports else None,
        "bounds_max_gltf": [max(r["bounds_max_gltf"][i] for r in exports) for i in range(3)] if exports else None}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", type=Path, required=True)
    parser.add_argument("--objects", type=Path, required=True)
    parser.add_argument("--ipb", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    workspace = Path(__file__).resolve().parents[1]
    if workspace / "local" not in args.output.resolve().parents:
        parser.error("Derived GLBs must be under workspace/local")
    if workspace not in args.report.resolve().parents:
        parser.error("Coverage report must be within this workspace")
    try:
        export_zone(args.world, args.objects, args.ipb, args.output, args.report)
    except (FormatError, OSError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()

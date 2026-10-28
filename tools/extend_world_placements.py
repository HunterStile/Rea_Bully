"""Join supplementary unconditional IPB instances to an immutable world catalog.

Only models with an existing verified static GLB are added to placements.
Every other source instance is retained as pending, and no spec is activated.
The output is a new inline catalog; existing zone manifests/assets stay intact.
"""
from __future__ import annotations
import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import json
from pathlib import Path

from catalog_world import model_key
from catalog_world_specials import parse_ipb
from export_nif import FormatError
from export_world import gltf_matrix, unreal_transform, world_bounds
from export_zone import digest


def extend_catalog(baseline, specials, baseline_sha256):
    if specials.get("existing_static_catalog_sha256_read_only") != baseline_sha256:
        raise FormatError("Specials catalog is not bound to this exact baseline")
    if specials["source_world_sha256"] != baseline["source_world_sha256"]:
        raise FormatError("Catalogs belong to different World archives")
    if specials.get("errors") or any(z.get("status") != "parsed" for z in specials["zones"]):
        raise FormatError("Complete IPB parsing is required before extending placements")
    if baseline.get("supplementary_extension"):
        raise FormatError("Input must be the original first-inst baseline, not an already extended catalog")
    result = copy.deepcopy(baseline)
    result["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    result["complete"] = False
    models = {m["model_key"]: m for m in result["models"]}
    zones = {z["name"]: z for z in result["zones"]}
    if len(zones) != len(result["zones"]) or set(zones) != {z["name"] for z in specials["zones"]}:
        raise FormatError("The exact same unique source zones are required")
    checked_models, added, pending = set(), [], []
    total_inst = total_spec = 0
    for source in specials["zones"]:
        zone = zones[source["name"]]
        path = Path(source["source_ipb"])
        if source["source_ipb_sha256"] != zone["source_ipb_sha256"] or digest(path) != source["source_ipb_sha256"]:
            raise FormatError("Source IPB hash does not match the immutable baseline: " + source["name"])
        decoded = parse_ipb(path.read_bytes())
        if len(decoded["sections"]) != len(source["sections"]):
            raise FormatError("Specials section inventory differs from original IPB")
        first = decoded["sections"][0]
        if first["tag"] != "inst" or len(first["records"]) != len(zone["placements"]):
            raise FormatError("Baseline must contain exactly the original first inst section")
        for raw, original in zip(first["records"], zone["placements"]):
            if any(raw[k] != original[k] for k in ("model_id", "name", "position", "scale", "quaternion_xyzw")):
                raise FormatError("Baseline placement differs from original first inst section")
        zone["supplementary_pending"] = []
        zone["source_placement_count"] = sum(s["record_count"] for s in decoded["sections"] if s["tag"] == "inst")
        for actual, declared in zip(decoded["sections"], source["sections"]):
            if any(actual[k] != declared[k] for k in ("tag", "section_index", "offset", "end_offset", "record_count")):
                raise FormatError("Declared section boundaries differ from original IPB")
            total_spec += actual["record_count"] if actual["tag"] == "spec" else 0
            if actual["tag"] != "inst":
                continue
            total_inst += actual["record_count"]
            if actual["section_index"] == 0:
                continue
            if len(actual["records"]) != len(declared["records"]):
                raise FormatError("Supplementary records missing from source catalog")
            for raw, reported in zip(actual["records"], declared["records"]):
                if any(raw[k] != reported.get(k) for k in raw):
                    raise FormatError("Supplementary placement differs from its original bytes")
                key = model_key(raw["model_id"], raw["name"])
                reference = {"zone": zone["name"], "source_ipb": str(path.resolve()),
                    "source_ipb_sha256": source["source_ipb_sha256"], "source_archive_index": source["source_archive_index"],
                    **{k: raw[k] for k in ("section_index", "record_index", "record_offset", "record_bytes", "record_sha256")}}
                model = models.get(key)
                if not model or model["status"] != "exported":
                    unresolved = {"source": reference, "model_key_candidate": key,
                        "reason": "not_in_baseline_model_catalog" if not model else model["status"],
                        "original_record": copy.deepcopy(reported)}
                    zone["supplementary_pending"].append(unresolved)
                    pending.append(unresolved)
                    continue
                if model["model_id"] != raw["model_id"] or model["name"].casefold() != raw["name"].casefold():
                    raise FormatError("Model identity differs from original placement")
                if key not in checked_models:
                    if digest(Path(model["output"])) != model["output_sha256"]:
                        raise FormatError("Existing GLB no longer matches baseline: " + key)
                    checked_models.add(key)
                instance = {**raw, "model_key": key, "instance_index": len(zone["placements"]),
                    "source_ipb_record": reference,
                    "instance_index_semantics": "Dense catalog index; source section and record indices retained separately",
                    "status": "exported", "model_glb": model["output"],
                    "unreal_transform": unreal_transform(raw), "gltf_matrix_column_major": gltf_matrix(raw),
                    "world_bounds_unreal_cm": world_bounds(raw, model["bounds_entity_unreal_cm"])}
                zone["placements"].append(instance)
                model["placement_count"] += 1
                if zone["name"] not in model["zones"]:
                    model["zones"].append(zone["name"])
                added.append({"source": reference, "model_key": key, "instance_index": instance["instance_index"]})
        zone["placement_count"] = len(zone["placements"])
        zone["placements_by_status"] = dict(Counter(p["status"] for p in zone["placements"]))
        bounds = [p["world_bounds_unreal_cm"] for p in zone["placements"] if p.get("world_bounds_unreal_cm")]
        zone["world_bounds_unreal_cm"] = {"min": [min(b["min"][axis] for b in bounds) for axis in range(3)],
            "max": [max(b["max"][axis] for b in bounds) for axis in range(3)]} if bounds else None
        zone.pop("zone_manifest", None)
        zone.pop("zone_manifest_sha256", None)
    result["supplementary_extension"] = {"baseline_sha256": baseline_sha256,
        "source_inst_records": total_inst, "added_static_placements": added, "pending_placements": pending,
        "summary": {"supplementary_inst_records": len(added)+len(pending), "added_static_placements": len(added),
            "existing_static_glbs_verified": len(checked_models), "pending_placements": len(pending),
            "pending_by_model_key": dict(Counter(p["model_key_candidate"] for p in pending)),
            "spec_records_excluded": total_spec},
        "policy": "All spec records remain excluded; no season, mission progress or special group is inferred"}
    statuses = Counter()
    for model in result["models"]:
        statuses[model["status"]] += model["placement_count"]
    result["summary"].update(placement_count=sum(statuses.values()), placements_by_status=dict(statuses),
        original_inst_records=total_inst, supplementary_pending=len(pending))
    result["limitations"] = [*baseline.get("limitations", []),
        "Supplementary source instances without an existing static conversion remain explicit in supplementary_extension.pending_placements",
        "No spec placement is activated; mission/season/group state is not inferred",
        "This catalog is inline; baseline zone manifests and every GLB are unchanged"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=Path("evidence/world-export.json"))
    parser.add_argument("--specials", type=Path, default=Path("local/world-specials-v04-verified.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve() in (args.baseline.resolve(), args.specials.resolve()):
        parser.error("A new output path is required; source catalogs are immutable")
    baseline_sha, specials_sha = digest(args.baseline), digest(args.specials)
    result = extend_catalog(json.loads(args.baseline.read_text(encoding="utf-8")),
                            json.loads(args.specials.read_text(encoding="utf-8")), baseline_sha)
    result["supplementary_extension"].update(baseline_catalog=str(args.baseline.resolve()),
        specials_catalog=str(args.specials.resolve()), specials_catalog_sha256=specials_sha)
    if digest(args.baseline) != baseline_sha or digest(args.specials) != specials_sha:
        raise FormatError("Input changed during source join")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    print(json.dumps(result["supplementary_extension"]["summary"], indent=2))


if __name__ == "__main__":
    main()

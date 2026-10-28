"""Provenance-preserving exact world-placement deduplication plan.

No fuzzy transforms are merged. q and -q are equivalent quaternion rotations.
All original source placements remain represented in the source references.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import json
from pathlib import Path

from export_zone import digest


def canonical_quaternion(values):
    sign = next((1 if value > 0 else -1 for value in (values[3], *values[:3]) if value), 1)
    return tuple(sign * value for value in values)


def placement_key(instance):
    return (instance["model_key"], *instance["position"], *instance["scale"], *canonical_quaternion(instance["quaternion_xyzw"]))


def owner_order(zone, instance):
    primary = zone["classification"] == "outdoor" and zone["name"].lower() != "ftest"
    return (0 if primary else 1 if zone["name"].lower().startswith("zone_") else 2,
        zone["source_archive_index"], instance["instance_index"])


def plan(inventory, source=None):
    groups = defaultdict(list)
    for zone in inventory["zones"]:
        for instance in zone["placements"]:
            groups[placement_key(instance)].append((zone, instance))
    duplicates = []
    for candidates in groups.values():
        if len(candidates) == 1:
            continue
        ordered = sorted(candidates, key=lambda pair: owner_order(*pair))
        refs = [{"zone": zone["name"], "source_ipb_sha256": zone["source_ipb_sha256"],
            "source_archive_index": zone["source_archive_index"], "instance_index": instance["instance_index"]}
            for zone, instance in ordered]
        duplicates.append({"model_key": ordered[0][1]["model_key"], "owner": refs[0], "equivalent_source_placements": refs})
    return {"schema_version": 1, "source_catalog": str(source.resolve()) if source else None,
        "source_catalog_sha256": digest(source) if source else None,
        "equivalence": "Exact model_key, position XYZ, scale XYZ, canonical quaternion XYZW; q/-q equivalent, no numeric tolerance",
        "owner_rule": "MainMap Outdoor base zone, then zone_*, then other; tie by source archive index and original placement index",
        "summary": {"original_placements": sum(z["placement_count"] for z in inventory["zones"]),
            "unique_exact_placements": len(groups), "duplicate_groups": len(duplicates),
            "duplicate_extra_placements": sum(len(group["equivalent_source_placements"])-1 for group in duplicates),
            "cross_zone_duplicate_groups": sum(len({ref["zone"] for ref in group["equivalent_source_placements"]}) > 1 for group in duplicates)},
        "duplicate_groups": duplicates}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=Path("evidence/world-coverage.json"))
    parser.add_argument("--report", type=Path, default=Path("evidence/world-placement-dedup.json"))
    args = parser.parse_args()
    result = plan(json.loads(args.catalog.read_text(encoding="utf-8")), args.catalog)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()

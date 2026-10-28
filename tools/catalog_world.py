"""Inventory every verified original World IPB without importing or changing Steam.

Area labels come from ColAreas.cfg and are joined to areabbox.dat by exact label.
Unmapped zones remain unclassified; an i/t filename prefix is not area evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path

from export_nif import FormatError, read_instances
from export_zone import archive_index, choose_definition, digest, invisible_reason, read_ide_definitions, ResolutionError


def area_sources(game):
    bounds_path = game / "Config/dat/areabbox.dat"
    groups_path = game / "Coll/ColAreas.cfg"
    bounds = {}
    for number, raw in enumerate(bounds_path.read_text().splitlines(), 1):
        text = raw.split("#", 1)[0].strip()
        if not text or "," not in text:
            continue
        name, rest = text.split(",", 1)
        cells = rest.split()
        if len(cells) == 7:
            bounds[name.lower()] = {"name": name, "bounds_source_game_m": [float(x) for x in cells[:6]],
                "radar_type": cells[6], "line": number, "source": str(bounds_path.resolve())}
    groups, label = defaultdict(list), None
    for number, raw in enumerate(groups_path.read_text().splitlines(), 1):
        text = raw.split("#", 1)[0].strip()
        if not text:
            continue
        if not raw[0].isspace():
            label = text
        elif label:
            groups[text.lower()].append({"area": label, "source": str(groups_path.resolve()), "line": number,
                "area_definition": bounds.get(label.lower())})
    return groups, [{"path": str(p.resolve()), "sha256": digest(p)} for p in (bounds_path, groups_path, game / "Config/world.dat")]


def model_key(model_id, name):
    return f"{model_id:05d}_{name.lower()}"


def catalog(world, game, report_path):
    by_name, manifest = archive_index(world)
    definitions = read_ide_definitions(game / "Objects")
    areas, config_sources = area_sources(game)
    zones, models = [], {}
    for filename, entries in sorted(by_name.items()):
        if not filename.endswith(".ipb"):
            continue
        # Never collapse differing same-name files into an arbitrary selection.
        if len({entry["manifest_sha256"] for entry in entries}) != 1:
            raise FormatError(f"Ambiguous differing IPB payloads: {filename}")
        entry = entries[0]
        path = Path(entry["path"])
        if digest(path) != entry["manifest_sha256"]:
            raise FormatError(f"IPB differs from extraction manifest: {path}")
        instances = read_instances(path)
        mappings = areas.get(path.stem.lower(), [])
        outdoor = any((m.get("area_definition") or {}).get("radar_type") == "Outdoor" for m in mappings)
        known = [m["area_definition"] for m in mappings if m.get("area_definition")]
        category = "outdoor" if outdoor else "separate_area" if known else "unclassified"
        zone = {"name": path.stem, "source_ipb": str(path.resolve()), "source_ipb_sha256": entry["manifest_sha256"],
            "source_archive_index": entry["index"], "equivalent_archive_indices": [e["index"] for e in entries],
            "classification": category, "classification_sources": mappings,
            "placement_count": len(instances), "placements": [],
            "placement_origin_bounds_game_m": {
                "min": [min(i["position"][axis] for i in instances) for axis in range(3)] if instances else None,
                "max": [max(i["position"][axis] for i in instances) for axis in range(3)] if instances else None}}
        for index, instance in enumerate(instances):
            key = model_key(instance["model_id"], instance["name"])
            zone["placements"].append({"instance_index": index, "model_key": key, **instance})
            if key not in models:
                record = {"model_key": key, "model_id": instance["model_id"], "name": instance["name"],
                    "placement_count": 0, "zones": [], "status": "defined"}
                try:
                    record["definition"] = choose_definition(instance, definitions)
                except ResolutionError as exc:
                    record.update(status="ambiguous_definition", reason=str(exc), candidates=exc.candidates)
                reason = invisible_reason(instance["name"])
                if reason:
                    record.update(status="excluded", reason=reason)
                models[key] = record
            models[key]["placement_count"] += 1
            if path.stem not in models[key]["zones"]:
                models[key]["zones"].append(path.stem)
        zone["unique_model_count"] = len({p["model_key"] for p in zone["placements"]})
        zones.append(zone)
    report = {"schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_world_sha256": manifest["source"]["image"]["sha256"], "config_sources": config_sources,
        "ide_sources": [{"path": str(p.resolve()), "sha256": digest(p)} for p in sorted((game / "Objects").rglob("*.ide"))],
        "coordinate_convention": {"source": "game XYZ metres, Z up", "gltf": "(X, Z, -Y) metres",
            "unreal": "(100*X, -100*Y, 100*Z) centimetres after glTF import",
            "placement_rotation": "conjugate original IPB quaternion, then conjugate by axis conversion",
            "model_export": "entity-space geometry strips root and immediate child transforms as the verified school pipeline"},
        "summary": {"zone_count": len(zones), "placement_count": sum(z["placement_count"] for z in zones),
            "unique_model_count": len(models), "zone_classifications": dict(Counter(z["classification"] for z in zones)),
            "model_definition_statuses": dict(Counter(m["status"] for m in models.values())),
            "definition_sections": dict(Counter(m.get("definition", {}).get("section", "unresolved") for m in models.values()))},
        "limitations": ["Only IPB inst sections are inventoried; this is not an original streaming schedule",
            "Placement origin bounds do not include mesh extents", "A ColAreas group identifies collision-area membership, not mission availability",
            "Unclassified zones are retained; interior/exterior and mission use are not guessed from names"],
        "zones": zones, "models": list(models.values())}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", type=Path, default=Path("local/extracted/Stream/World"))
    parser.add_argument("--game", type=Path, default=Path("C:/Program Files (x86)/Steam/steamapps/common/Bully Scholarship Edition"))
    parser.add_argument("--report", type=Path, default=Path("evidence/world-coverage.json"))
    args = parser.parse_args()
    result = catalog(args.world, args.game, args.report)
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()

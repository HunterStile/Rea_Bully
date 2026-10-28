"""Recover static item geometry for source-bound supplementary IPB placements.

Reads only original item IDE rows and indexed NIF/NFT sources. Reuses existing
strict geometry/texture codecs; no collection, spinning, glow or save behavior.
Writes new GLBs and a new catalog without modifying the world baseline.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from catalog_world import model_key
from catalog_world_specials import parse_ipb
from export_nif import FormatError, Nif, export
from export_world import Resolver, gltf_matrix, summary, unreal_transform, world_bounds
from export_zone import ResolutionError, digest, required_base_names
from validate_world_export import validate_glb


def read_item_definitions(path):
    """Read the three explicit fields of item sections, retaining exact lines."""
    section, definitions = "", defaultdict(list)
    source_sha = digest(path)
    for line_number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        text = raw.split("#", 1)[0].strip()
        if not text:
            continue
        cells = text.replace(",", " ").split()
        if len(cells) == 1:
            section = cells[0].lower()
            continue
        if section != "item":
            continue
        if len(cells) != 3 or not cells[0].isdigit() or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", c) for c in cells[1:]):
            raise FormatError(f"Unrecognized item row at {path}:{line_number}")
        definition = {"model_id": int(cells[0]), "name": cells[1], "texture_dictionary": cells[2],
            "section": "item", "source": str(path.resolve()), "source_sha256": source_sha,
            "line": line_number, "raw_line": raw}
        definitions[definition["model_id"]].append(definition)
    return definitions


def select_item_definition(record, definitions):
    matches = [d for d in definitions.get(record["model_id"], []) if d["name"].casefold() == record["name"].casefold()]
    signatures = {(d["name"].casefold(), d["texture_dictionary"].casefold()) for d in matches}
    if len(signatures) != 1:
        raise ResolutionError("No unique original item definition for source id/name", matches)
    chosen = dict(matches[0])
    chosen["equivalent_definition_sources"] = [{"source": d["source"], "line": d["line"], "source_sha256": d["source_sha256"]} for d in matches]
    return chosen


def verified_pending_groups(catalog, definitions):
    """Re-read exact raw records before converting any pending item."""
    groups, sources = {}, {}
    seen = set()
    existing_keys = {m["model_key"] for m in catalog["models"]}
    for zone in catalog["zones"]:
        for pending in zone.get("supplementary_pending", []):
            reference, reported = pending["source"], pending["original_record"]
            if reference["zone"] != zone["name"] or reference["source_ipb_sha256"] != zone["source_ipb_sha256"]:
                raise FormatError("Item placement does not belong to this source zone")
            path = Path(reference["source_ipb"])
            if path not in sources:
                sources[path] = {"sha256": digest(path), "parsed": parse_ipb(path.read_bytes())}
            if sources[path]["sha256"] != reference["source_ipb_sha256"]:
                raise FormatError("Item IPB source hash changed")
            sections = sources[path]["parsed"]["sections"]
            section_index, record_index = reference["section_index"], reference["record_index"]
            if not 0 < section_index < len(sections):
                raise FormatError("Item must reference a supplementary inst section")
            section = sections[section_index]
            if section["tag"] != "inst" or not 0 <= record_index < len(section["records"]):
                raise FormatError("Item does not reference an unconditional inst record")
            actual = section["records"][record_index]
            if any(actual[k] != reported.get(k) for k in actual):
                raise FormatError("Item placement report differs from original record")
            for key in ("record_offset", "record_bytes", "record_sha256"):
                if actual[key] != reference[key]:
                    raise FormatError("Item record provenance differs from original bytes")
            identity = (zone["name"], section_index, record_index)
            if identity in seen:
                raise FormatError("Duplicate pending source item reference")
            seen.add(identity)
            key = model_key(actual["model_id"], actual["name"])
            if key != pending["model_key_candidate"] or key in existing_keys:
                raise FormatError("Pending model identity is wrong or already exists in the catalog")
            definition = select_item_definition(actual, definitions)
            if key not in groups:
                groups[key] = {"model_key": key, "definition": definition, "placements": []}
            elif groups[key]["definition"]["texture_dictionary"].casefold() != definition["texture_dictionary"].casefold():
                raise FormatError("Different item definitions share one model key")
            groups[key]["placements"].append({"source": copy.deepcopy(reference), "record": actual})
    expected = catalog.get("supplementary_extension", {}).get("pending_placements", [])
    if len(expected) != len(seen):
        raise FormatError("Per-zone and overall pending source inventories disagree")
    return groups


def export_item(group, resolver, output, invalid_color_policy="reject"):
    definition, key = group["definition"], group["model_key"]
    nif_path, model_resolution = resolver.asset(definition["name"] + ".nif", model_name=definition["name"])
    nif = Nif(nif_path)
    if "NiSkinInstance" in nif.types or any("Controller" in kind for kind in nif.types):
        raise FormatError("Item is skinned or controlled; it requires a separate animated conversion")
    nodes = []
    for index, kind in enumerate(nif.types):
        if kind in {"NiNode", "NiTriShape", "NiTriStrips"}:
            node = nif.block(index)
            nodes.append({"block": index, "type": kind, **{k: node[k] for k in ("name", "translation", "rotation", "scale")}})
    if definition["name"].casefold() not in {n["name"].casefold() for n in nodes}:
        raise FormatError("Original NIF has no exact matching model-node name")
    required = required_base_names(nif)
    textures, texture_resolution = [], None
    if required:
        texture, texture_resolution = resolver.asset(definition["texture_dictionary"] + ".nft", required_textures=required)
        if required - resolver.texture_names(texture):
            raise FormatError("Declared item dictionary lacks original base texture names")
        textures.append(texture)
    target = output / (key + ".glb")
    if target.exists() or target.with_suffix(".report.json").exists():
        raise FormatError("Refusing to replace an existing converted item")
    result = export(nif_path, textures, target, True, texture_cache=resolver.texture_cache,
        loose_textures=False, require_textures=True, invalid_geometry_policy="reject", invalid_color_policy=invalid_color_policy)
    validation = validate_glb(target, result)
    low, high = result["bounds_min_gltf"], result["bounds_max_gltf"]
    return {"model_key": key, "model_id": definition["model_id"], "name": definition["name"],
        "definition": definition, "status": "exported", "placement_count": len(group["placements"]),
        "zones": sorted({p["source"]["zone"] for p in group["placements"]}),
        "output": str(target.resolve()), "output_relative": target.name, "output_sha256": digest(target),
        "model_resolution": model_resolution, "texture_resolution": texture_resolution, "export": result,
        "bounds_entity_unreal_cm": {"min": [100*low[i] for i in (0, 2, 1)], "max": [100*high[i] for i in (0, 2, 1)]},
        "validation": validation, "animation_pending": False, "shading_pending": bool(result["vertex_color_omissions"]),
        "custom_shader_maps_pending": any("custom shader maps" in w for w in result["warnings"]),
        "source_node_transforms": nodes,
        "geometry_frame": "Existing world entity-space convention: remove NIF scene/model-root transforms, preserve deeper hierarchy; original node transforms recorded",
        "runtime_presentation": "Static original item geometry only; collection, saved visibility, rotation, bobbing and glow not implemented",
        "collision_semantics": "Original item collision/trigger behavior has not been reconstructed"}


def join_items(catalog, groups, models):
    result = copy.deepcopy(catalog)
    if result.get("collectable_extension"):
        raise FormatError("Collectable overlay already applied")
    by_key = {m["model_key"]: m for m in models}
    if set(by_key) != set(groups):
        raise FormatError("Every source item group needs exactly one verified static model")
    for model in models:
        if model["status"] != "exported" or digest(Path(model["output"])) != model["output_sha256"]:
            raise FormatError("Item GLB is missing, incomplete or changed")
    added = []
    for zone in result["zones"]:
        for pending in zone.get("supplementary_pending", []):
            key = pending["model_key_candidate"]
            reference = pending["source"]
            candidates = [p for p in groups[key]["placements"] if p["source"] == reference]
            if len(candidates) != 1:
                raise FormatError("No unique verified raw item placement for pending source")
            raw, model = candidates[0]["record"], by_key[key]
            instance = {**raw, "model_key": key, "instance_index": len(zone["placements"]),
                "source_ipb_record": copy.deepcopy(reference), "status": "exported", "model_glb": model["output"],
                "instance_index_semantics": "Dense catalog index; source section and record indices retained separately",
                "unreal_transform": unreal_transform(raw), "gltf_matrix_column_major": gltf_matrix(raw),
                "world_bounds_unreal_cm": world_bounds(raw, model["bounds_entity_unreal_cm"]),
                "source_definition_section": "item", "collection_behavior_implemented": False}
            zone["placements"].append(instance)
            added.append({"source": copy.deepcopy(reference), "model_key": key, "instance_index": instance["instance_index"]})
        zone["supplementary_pending"] = []
        zone["placement_count"] = len(zone["placements"])
        zone["placements_by_status"] = dict(Counter(p["status"] for p in zone["placements"]))
        bounds = [p["world_bounds_unreal_cm"] for p in zone["placements"] if p.get("world_bounds_unreal_cm")]
        zone["world_bounds_unreal_cm"] = {"min": [min(b["min"][axis] for b in bounds) for axis in range(3)],
            "max": [max(b["max"][axis] for b in bounds) for axis in range(3)]} if bounds else None
    result["models"].extend(copy.deepcopy(models))
    extension = result["supplementary_extension"]
    extension["pending_placements"] = []
    extension["added_static_placements"].extend(added)
    extension["summary"].update(added_static_placements=len(extension["added_static_placements"]), pending_placements=0,
        pending_by_model_key={}, resolved_item_placements=len(added), new_item_glbs=len(models))
    result["collectable_extension"] = {"new_models": len(models), "added_item_placements": len(added),
        "collection_behavior_implemented": False, "all_spec_records_still_excluded": True,
        "source_placements": added}
    result["total_model_count"] = len(result["models"])
    result["summary"] = summary(result)
    result["summary"].update(original_inst_records=sum(z["source_placement_count"] for z in result["zones"]), supplementary_pending=0)
    result["complete"] = not any(m["status"] == "pending" for m in result["models"])
    result["limitations"].append("Item geometry is static. Collection, saved visibility, rotation/bobbing, glow and original pickup collision are not reconstructed.")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=Path("local/world-export-with-supplementary-v04.json"))
    parser.add_argument("--world", type=Path, default=Path("local/extracted/Stream/World"))
    parser.add_argument("--ide", type=Path, default=Path("C:/Program Files (x86)/Steam/steamapps/common/Bully Scholarship Edition/Objects/default.ide"))
    parser.add_argument("--output", type=Path, default=Path("local/converted/collectables-v04"))
    parser.add_argument("--report", type=Path, default=Path("evidence/collectables-export-v04.json"))
    parser.add_argument("--output-catalog", type=Path, default=Path("local/world-export-with-collectables-v04.json"))
    parser.add_argument("--invalid-color-policy", choices=["reject", "base_material_only"], default="reject",
        help="Explicitly omit unsupported RGB data with provenance instead of inventing normalization")
    args = parser.parse_args()
    local = Path(__file__).resolve().parents[1] / "local"
    if local not in args.output.resolve().parents or local not in args.output_catalog.resolve().parents:
        parser.error("Derived geometry/catalog must remain under workspace/local")
    if args.output.exists() or args.report.exists() or args.output_catalog.exists():
        parser.error("New output directory and report/catalog paths required")
    catalog_sha, ide_sha = digest(args.catalog), digest(args.ide)
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    groups = verified_pending_groups(catalog, read_item_definitions(args.ide))
    if not groups:
        parser.error("No pending original item records to convert")
    resolver = Resolver(args.world)
    if resolver.manifest["source"]["image"]["sha256"] != catalog["source_world_sha256"]:
        raise FormatError("Source archive does not match placement catalog")
    args.output.mkdir(parents=True)
    models = []
    for key in sorted(groups):
        model = export_item(groups[key], resolver, args.output, args.invalid_color_policy)
        models.append(model)
        print(json.dumps({"model_key": key, "status": "exported_and_verified", **model["validation"]}), flush=True)
    result = join_items(catalog, groups, models)
    result["collectable_extension"].update(source_catalog=str(args.catalog.resolve()), source_catalog_sha256=catalog_sha,
        report=str(args.report.resolve()))
    result["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    if digest(args.catalog) != catalog_sha or digest(args.ide) != ide_sha:
        raise FormatError("Source catalog or item definitions changed during conversion")
    with args.output_catalog.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False); stream.write("\n")
    report = {"schema_version": 1, "generated_at_utc": result["generated_at_utc"], "passed": True,
        "classification": "original_item_static_geometry_and_placement_recovery_not_collection_runtime",
        "source_catalog": str(args.catalog.resolve()), "source_catalog_sha256": catalog_sha,
        "source_ide": str(args.ide.resolve()), "source_ide_sha256": ide_sha,
        "invalid_color_policy": args.invalid_color_policy,
        "source_world_sha256": catalog["source_world_sha256"], "models": models,
        "summary": {"converted_models": len(models), "source_item_placements": sum(m["placement_count"] for m in models),
            "static_glbs_validated": len(models), "new_catalog_placements": result["summary"]["placement_count"],
            "remaining_supplementary_pending": 0, "collection_behavior_implemented": False},
        "output_catalog": str(args.output_catalog.resolve()), "output_catalog_sha256": digest(args.output_catalog),
        "limitations": ["Static item models and raw source placement transforms only; not pickup gameplay or original animated presentation",
            "Entity-space pivot convention matches the existing world converter; this step does not independently observe the original item runtime pivot",
            "All existing catalogs, models and Steam resources remain unchanged; all spec placements remain excluded"]}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False); stream.write("\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()

"""Export all original IPB zones as shared entity-space GLBs and placements.

No replacement geometry, invented transforms, or archive-order disambiguation.
The coverage JSON is also a checkpoint: a completed asset is reused only when
its original model/texture hashes and generated GLB hash still match.
"""
from __future__ import annotations

import argparse
from collections import Counter, OrderedDict, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct

from catalog_world import catalog
from export_nif import FormatError, Nif, export, load_textures, placement_matrix, texture_basename
from export_zone import ResolutionError, archive_index, dictionary_texture_names, digest, required_base_names, resolve_asset


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def unreal_transform(instance):
    """Original metres/Z-up to the independently verified UE glTF import basis."""
    x, y, z, w = instance["quaternion_xyzw"]
    return {"translation_cm": [instance["position"][0] * 100, -instance["position"][1] * 100, instance["position"][2] * 100],
        "rotation_quaternion_xyzw": [x, -y, z, w], "scale_xyz": list(instance["scale"])}


def gltf_matrix(instance):
    # C maps (x,y,z) -> (x,z,-y). Conjugation maps model-space glTF to world glTF.
    matrix = placement_matrix(instance)
    indices, signs = (0, 2, 1, 3), (1, 1, -1, 1)
    result = [signs[row] * signs[col] * matrix[indices[row]*4 + indices[col]] for row in range(4) for col in range(4)]
    return [result[row*4+col] for col in range(4) for row in range(4)]


def world_bounds(instance, entity_bounds):
    """Conservative transformed entity AABB, in UE cm, including negative scale."""
    from itertools import product
    matrix = placement_matrix(instance)
    corners = []
    for values in product(*zip(entity_bounds["min"], entity_bounds["max"])):
        game = (values[0] / 100, -values[1] / 100, values[2] / 100)
        result = [sum(matrix[row*4+col] * game[col] for col in range(3)) + matrix[row*4+3] for row in range(3)]
        corners.append((100*result[0], -100*result[1], 100*result[2]))
    return {"min": [min(v[axis] for v in corners) for axis in range(3)],
        "max": [max(v[axis] for v in corners) for axis in range(3)]}


class Resolver:
    def __init__(self, world):
        self.by_name, self.manifest = archive_index(world)
        self.asset_cache, self.names_cache = {}, {}
        self.texture_cache = OrderedDict()
        self.texture_locations = None

    def asset(self, filename, **kwargs):
        key = (filename.lower(), kwargs.get("model_name"), tuple(sorted(kwargs.get("required_textures") or [])))
        if key not in self.asset_cache:
            try:
                self.asset_cache[key] = resolve_asset(filename, self.by_name, **kwargs)
            except ResolutionError as exc:
                required = kwargs.get("required_textures")
                if not required:
                    raise
                candidates = [dict(c) for c in exc.candidates if c.get("valid_container") and not c.get("missing_required_names")]
                if len(candidates) < 2:
                    raise
                for candidate in candidates:
                    decoded = self.decoded([Path(candidate["path"])])
                    candidate["referenced_decoded_png_sha256"] = {name: hashlib.sha256(decoded[name]["png"]).hexdigest() for name in sorted(required) if name in decoded}
                signatures = {tuple(c["referenced_decoded_png_sha256"].items()) for c in candidates}
                if len(signatures) != 1 or any(len(c["referenced_decoded_png_sha256"]) != len(required) for c in candidates):
                    raise ResolutionError("Differing archive dictionaries have differing referenced original decoded pixels", candidates)
                chosen = candidates[0]
                self.asset_cache[key] = Path(chosen["path"]), {
                    "selection_reason": "dictionary bytes differ, but all referenced original decoded base-texture pixels are identical",
                    "selected_index": chosen["index"], "selected_sha256": chosen["sha256"], "candidates": candidates}
        return self.asset_cache[key]

    def texture_names(self, path):
        key = str(path)
        if key not in self.names_cache:
            self.names_cache[key] = dictionary_texture_names(path)
        return self.names_cache[key]

    def supplemental(self, missing):
        if self.texture_locations is None:
            self.texture_locations = defaultdict(list)
            rejected = 0
            for filename, entries in self.by_name.items():
                if not filename.endswith(".nft"):
                    continue
                for entry in entries:
                    try:
                        for name in self.texture_names(Path(entry["path"])):
                            self.texture_locations[name].append(entry)
                    except (FormatError, OSError, struct.error, KeyError, ValueError):
                        rejected += 1
            print(json.dumps({"texture_name_index": len(self.texture_locations), "rejected_dictionaries": rejected}), flush=True)
        paths, resolutions = [], []
        for name in sorted(missing):
            candidates = []
            # Equal archive bytes require only one decode; still retain every source.
            decoded_hashes = {}
            for entry in self.texture_locations.get(name, []):
                path = Path(entry["path"])
                source_hash = digest(path)
                if source_hash != entry["manifest_sha256"]:
                    raise ResolutionError("Supplemental NFT differs from extraction manifest", [entry])
                if source_hash not in decoded_hashes:
                    textures = self.decoded([path])
                    if name not in textures:
                        continue
                    decoded_hashes[source_hash] = hashlib.sha256(textures[name]["png"]).hexdigest()
                candidates.append({**entry, "sha256": source_hash, "decoded_png_sha256": decoded_hashes[source_hash]})
            if not candidates or len({c["decoded_png_sha256"] for c in candidates}) != 1:
                raise ResolutionError(f"Exact supplemental texture absent or has differing original pixels: {name}", candidates)
            chosen = candidates[0]
            path = Path(chosen["path"])
            if path not in paths:
                paths.append(path)
            resolutions.append({"texture_name": name, "selection_reason": "exact referenced name; all original decoded pixels identical",
                "selected_index": chosen["index"], "selected_sha256": chosen["sha256"], "candidates": candidates})
        return paths, resolutions

    def decoded(self, paths):
        key = tuple(str(p.resolve()) for p in paths)
        if key not in self.texture_cache:
            self.texture_cache[key] = load_textures(paths)
        self.texture_cache.move_to_end(key)
        while len(self.texture_cache) > 6:
            self.texture_cache.popitem(last=False)
        return self.texture_cache[key]


def current_checkpoint(record, invalid_geometry_policy=None, invalid_color_policy=None):
    if record.get("status") != "exported":
        return False
    try:
        result = record["export"]
        if invalid_geometry_policy == "reject" and result.get("geometry_omissions"):
            return False
        if invalid_color_policy == "reject" and result.get("vertex_color_omissions"):
            return False
        hashes_valid = (digest(Path(record["output"])) == record["output_sha256"]
            and digest(Path(result["source"])) == result["source_sha256"]
            and all(digest(Path(source["path"])) == source["sha256"] for source in result["texture_sources"]))
        if hashes_valid and "source_uv_selection" not in result:
            # Early exports assumed UV0. Reuse is safe only if every original
            # base map actually names UV0; otherwise the corrected codec reruns.
            nif = Nif(Path(result["source"]))
            for index, kind in enumerate(nif.types):
                if kind == "NiTexturingProperty":
                    prop = nif.block(index)
                    if prop["slots"] and prop["slots"][0] and prop["slots"][0]["flags"] & 255:
                        return False
        return hashes_valid
    except (OSError, KeyError, FormatError, struct.error):
        return False


def summary(report):
    exported = [m for m in report["models"] if m["status"] == "exported"]
    statuses = Counter(m["status"] for m in report["models"])
    placements = Counter()
    for model in report["models"]:
        placements[model["status"]] += model["placement_count"]
    return {"zone_count": len(report["zones"]), "placement_count": sum(placements.values()),
        "unique_model_count": len(report["models"]), "models_by_status": dict(statuses),
        "placements_by_status": dict(placements), "glb_count": len(exported),
        "entity_shapes": sum(m["export"]["shapes"] for m in exported),
        "entity_vertices": sum(m["export"]["vertices"] for m in exported),
        "entity_triangles": sum(m["export"]["triangles"] for m in exported),
        "visible_model_animation_pending": sum(m.get("animation_pending", False) for m in exported),
        "partial_geometry_models": sum(bool(m["export"].get("geometry_omissions")) for m in exported),
        "omitted_invalid_source_triangles": sum(len(g["omitted_source_triangle_indices"]) for m in exported for g in m["export"].get("geometry_omissions", [])),
        "vertex_lighting_pending_models": sum(bool(m["export"].get("vertex_color_omissions")) for m in exported),
        "output_bytes": sum(Path(m["output"]).stat().st_size for m in exported)}


def classify_empty_original(record):
    """An original node-only NIF has no visible mesh for this converter to omit."""
    if record.get("reason") != "No supported original mesh geometry found":
        return
    resolution = record["model_resolution"]
    chosen = next(c for c in resolution["candidates"] if c["index"] == resolution["selected_index"])
    path = Path(chosen["path"])
    if digest(path) != resolution["selected_sha256"]:
        raise FormatError("Node-only original differs from verified source")
    nif = Nif(path)
    metadata_only = {"NiNode", "NiZBufferProperty", "NiVertexColorProperty", "NiStringExtraData"}
    if set(nif.types) <= metadata_only:
        record.update(status="empty_original", reason="Verified original NIF contains nodes/properties only and no mesh blocks",
            original_blocks_by_type=dict(Counter(nif.types)))


def finalize_zones(report, output):
    for record in report["models"]:
        classify_empty_original(record)
    by_key = {m["model_key"]: m for m in report["models"]}
    for zone in report["zones"]:
        zone_bounds = []
        for instance in zone["placements"]:
            model = by_key[instance["model_key"]]
            instance["status"] = model["status"]
            instance["unreal_transform"] = unreal_transform(instance)
            instance["gltf_matrix_column_major"] = gltf_matrix(instance)
            if model["status"] == "exported":
                instance["model_glb"] = model["output"]
                instance["world_bounds_unreal_cm"] = world_bounds(instance, model["bounds_entity_unreal_cm"])
                zone_bounds.append(instance["world_bounds_unreal_cm"])
        zone["placements_by_status"] = dict(Counter(p["status"] for p in zone["placements"]))
        zone["world_bounds_unreal_cm"] = {"min": [min(b["min"][axis] for b in zone_bounds) for axis in range(3)],
            "max": [max(b["max"][axis] for b in zone_bounds) for axis in range(3)]} if zone_bounds else None
        zone["bounds_method"] = "Conservative transformed entity AABBs for successfully exported placements only"
        zone_path = output / "zones" / (zone["name"] + ".json")
        zone.pop("zone_manifest", None)
        zone.pop("zone_manifest_sha256", None)
        write_json(zone_path, zone)
        zone["zone_manifest"] = str(zone_path.resolve())
        zone["zone_manifest_sha256"] = digest(zone_path)
    report["summary"] = summary(report)


def export_world(world, game, output, report_path, catalog_path, resume=False, limit=None, invalid_geometry_policy="reject", invalid_color_policy="reject"):
    inventory = catalog(world, game, catalog_path)
    report = {"schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "complete": False, "total_model_count": len(inventory["models"]),
        "invalid_geometry_policy": invalid_geometry_policy,
        "invalid_color_policy": invalid_color_policy,
        "source_world_sha256": inventory["source_world_sha256"], "source_catalog": str(catalog_path.resolve()),
        "source_catalog_sha256": digest(catalog_path), "coordinate_convention": inventory["coordinate_convention"],
        "config_sources": inventory["config_sources"], "output_directory": str(output.resolve()),
        "limitations": ["Geometry and base textures only; original collision, NPCs, missions and streaming schedule not recreated here",
            "panm models without skin can be displayed in their original initial pose; their animation is pending",
            "NiSkinInstance geometry requires the separate character/animated-object pipeline",
            "Unresolved dependencies are explicit; differing same-name texture pixels remain ambiguous",
            "Original UV controllers, secondary texture slots, vertex alpha and special shaders are not evaluated"],
        "zones": inventory["zones"], "models": []}
    for zone in report["zones"]:
        for instance in zone["placements"]:
            instance["unreal_transform"] = unreal_transform(instance)
            instance["gltf_matrix_column_major"] = gltf_matrix(instance)
    prior = {}
    if report_path.exists() and resume:
        previous = json.loads(report_path.read_text(encoding="utf-8"))
        if previous["source_world_sha256"] != report["source_world_sha256"]:
            raise FormatError("Resume report belongs to another World archive")
        prior = {m["model_key"]: m for m in previous["models"]}
    elif any(output.glob("*.glb")):
        raise FormatError("Output contains GLBs: use --resume to verify and reuse prior outputs")
    output.mkdir(parents=True, exist_ok=True)
    resolver = Resolver(world)
    attempted = 0
    mainmap_names = {z["name"] for z in inventory["zones"] if z["classification"] == "outdoor" and z["name"].lower() != "ftest"}
    def priority(model):
        return (0 if any(z in mainmap_names for z in model["zones"]) else 1 if any(z.lower().startswith("zone_") for z in model["zones"]) else 2,
            model["model_key"])
    ordered = sorted(inventory["models"], key=priority)
    for number, original in enumerate(ordered, 1):
        record = dict(original)
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", record["model_key"])
        target = output / f"{safe}.glb"
        if record["status"] == "defined":
            record["status"] = "pending"
        if record["status"] == "pending" and current_checkpoint(prior.get(record["model_key"], {}), invalid_geometry_policy, invalid_color_policy):
            record = dict(prior[record["model_key"]])
            if Path(record["output"]).resolve() != target.resolve():
                shutil.copyfile(record["output"], target)
                record["export"] = dict(record["export"], output=str(target))
                record["output"] = str(target.resolve())
                write_json(target.with_suffix(".report.json"), record["export"])
        elif record["status"] == "pending" and (limit is None or attempted < limit):
            attempted += 1
            try:
                definition = record["definition"]
                model, model_resolution = resolver.asset(definition["name"] + ".nif", model_name=definition["name"])
                record["model_resolution"] = model_resolution
                nif = Nif(model)
                record["animation_pending"] = definition["section"] == "panm" or any("Controller" in kind for kind in nif.types)
                if "NiSkinInstance" in nif.types:
                    raise FormatError("NiSkinInstance requires separate skinned/animated-object pipeline")
                required = required_base_names(nif)
                textures = []
                if required:
                    texture, resolution = resolver.asset(definition["texture_dictionary"] + ".nft", required_textures=required)
                    record["texture_resolution"] = resolution
                    textures.append(texture)
                    missing = required - resolver.texture_names(texture)
                    if missing:
                        extras, resolutions = resolver.supplemental(missing)
                        textures.extend(extras)
                        record["supplemental_texture_resolution"] = resolutions
                # At most six dictionary groups retained; reuse codecs unchanged.
                resolver.decoded(textures)
                result = export(model, textures, target, True, texture_cache=resolver.texture_cache,
                    loose_textures=False, require_textures=True, invalid_geometry_policy=invalid_geometry_policy,
                    invalid_color_policy=invalid_color_policy)
                record.update(status="exported", output=str(target.resolve()), output_sha256=digest(target), export=result)
            except (FormatError, OSError, struct.error, KeyError, ValueError) as exc:
                record.update(status="ambiguous" if isinstance(exc, ResolutionError) else "unsupported", reason=str(exc))
                if isinstance(exc, ResolutionError):
                    record["resolution_candidates"] = exc.candidates
        if record["status"] == "exported":
            record["shading_pending"] = bool(record["export"].get("vertex_color_omissions"))
            record["custom_shader_maps_pending"] = any("custom shader maps" in warning for warning in record["export"]["warnings"])
            record["output_relative"] = str(Path(record["output"]).relative_to(output.resolve())).replace("\\", "/")
            low, high = record["export"]["bounds_min_gltf"], record["export"]["bounds_max_gltf"]
            record["bounds_entity_unreal_cm"] = {"min": [100*low[i] for i in (0, 2, 1)], "max": [100*high[i] for i in (0, 2, 1)]}
        report["models"].append(record)
        if number % 100 == 0 or number == len(inventory["models"]):
            report["summary"] = summary(report)
            write_json(report_path, report)
            print(json.dumps({"processed_models": number, **report["summary"]}), flush=True)
    finalize_zones(report, output)
    report["complete"] = not any(m["status"] == "pending" for m in report["models"])
    write_json(report_path, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", type=Path, default=Path("local/extracted/Stream/World"))
    parser.add_argument("--game", type=Path, default=Path("C:/Program Files (x86)/Steam/steamapps/common/Bully Scholarship Edition"))
    parser.add_argument("--output", type=Path, default=Path("local/converted/world_models"))
    parser.add_argument("--report", type=Path, default=Path("evidence/world-export.json"))
    parser.add_argument("--catalog-report", type=Path, default=Path("evidence/world-coverage.json"))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit", type=int, help="Optional bounded investigation; unprocessed models remain pending")
    parser.add_argument("--invalid-geometry-policy", choices=["reject", "omit_invalid_faces"], default="reject")
    parser.add_argument("--invalid-color-policy", choices=["reject", "base_material_only"], default="reject")
    parser.add_argument("--finalize-only", action="store_true", help="Rebuild zone metadata and classify verified node-only originals; no GLB mutation")
    args = parser.parse_args()
    local = Path(__file__).resolve().parents[1] / "local"
    if local not in args.output.resolve().parents:
        parser.error("Derived game assets must be under workspace/local")
    if args.finalize_only:
        result = json.loads(args.report.read_text(encoding="utf-8"))
        if not result.get("complete"):
            parser.error("Finalize-only requires a completed full conversion report")
        finalize_zones(result, Path(result["output_directory"]))
        write_json(args.report, result)
        print(json.dumps(result["summary"], indent=2))
        return
    result = export_world(args.world, args.game, args.output, args.report, args.catalog_report, args.resume, args.limit,
        args.invalid_geometry_policy, args.invalid_color_policy)
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()

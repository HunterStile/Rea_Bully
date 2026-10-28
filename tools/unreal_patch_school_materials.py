"""Restore explicit NIF ONE/ONE blend states on the imported school meshes.

Import in Unreal Python and call apply(world) after collision preparation.
No source geometry is imported or replaced. Original materials are retained.
"""
import hashlib
import json
import os
from pathlib import Path
import unreal

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "evidence/school-material-catalog.json"
MATERIAL_ROOT = "/Game/RecoveredMaterials/SchoolAdditive"
lib = unreal.MaterialEditingLibrary


def _expression(material, cls):
    node = lib.create_material_expression(material, cls)
    if node is None:
        raise RuntimeError(f"Could not create {cls}")
    return node


def _connect(source, output, target, input_name):
    if not lib.connect_material_expressions(source, output, target, input_name):
        raise RuntimeError(f"Failed material connection: {output} -> {input_name}")


def _constant(material, value):
    result = _expression(material, unreal.MaterialExpressionConstant)
    result.set_editor_property("r", float(value))
    return result


def _material(record):
    key = f"M_{record['model_id']:05d}_{record['shape_block']}_Additive"
    path = MATERIAL_ROOT + "/" + key
    signature = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
    material = unreal.EditorAssetLibrary.load_asset(path) if unreal.EditorAssetLibrary.does_asset_exist(path) else None
    created = material is None
    if material is None:
        material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            key, MATERIAL_ROOT, unreal.Material, unreal.MaterialFactoryNew())
        if material is None:
            raise RuntimeError(f"Could not create material {path}")
        material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_ADDITIVE)
        material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
        material.set_editor_property("two_sided", record["double_sided"])
        texture = unreal.EditorAssetLibrary.load_asset(record["unreal_original_texture"])
        if not isinstance(texture, unreal.Texture2D):
            raise RuntimeError(f"Original base texture missing: {record['unreal_original_texture']}")
        sample = _expression(material, unreal.MaterialExpressionTextureSample)
        sample.set_editor_property("texture", texture)
        color = _expression(material, unreal.MaterialExpressionConstant3Vector)
        color.set_editor_property("constant", unreal.LinearColor(*record["diffuse_rgb"], 1.0))
        diffuse = _expression(material, unreal.MaterialExpressionMultiply)
        _connect(sample, "RGB", diffuse, "A")
        _connect(color, "", diffuse, "B")
        emission = diffuse
        if record["has_vertex_rgb"]:
            vertex = _expression(material, unreal.MaterialExpressionVertexColor)
            emission = _expression(material, unreal.MaterialExpressionMultiply)
            _connect(diffuse, "", emission, "A")
            # VertexColor's first pin is unnamed and masks RGB in UE 5.8.
            _connect(vertex, "", emission, "B")
        if not lib.connect_material_property(emission, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR):
            raise RuntimeError("Failed emissive connection")
        opacity = _constant(material, 1)
        if record["alpha_test"]:
            if record["test_function"] != 4:
                raise RuntimeError("Unsupported additive alpha-test comparison")
            alpha = _expression(material, unreal.MaterialExpressionMultiply)
            _connect(sample, "A", alpha, "A")
            _connect(_constant(material, record["material_alpha"]), "", alpha, "B")
            test = _expression(material, unreal.MaterialExpressionIf)
            test.set_editor_property("equals_threshold", 0.0)
            _connect(alpha, "", test, "A")
            _connect(_constant(material, record["threshold_byte"] / 255.0), "", test, "B")
            _connect(opacity, "", test, "A > B")
            zero = _constant(material, 0)
            _connect(zero, "", test, "A == B")
            _connect(zero, "", test, "A < B")
            opacity = test
        if not lib.connect_material_property(opacity, "", unreal.MaterialProperty.MP_OPACITY):
            raise RuntimeError("Failed additive opacity connection")
        lib.layout_material_expressions(material)
        lib.recompile_material(material)
        unreal.EditorAssetLibrary.set_metadata_tag(material, "REAAdditiveSource", signature)
        if not unreal.EditorAssetLibrary.save_loaded_asset(material, only_if_is_dirty=False):
            raise RuntimeError(f"Failed saving material {path}")
    else:
        if unreal.EditorAssetLibrary.get_metadata_tag(material, "REAAdditiveSource") != signature:
            raise RuntimeError(f"Existing recovered material has different provenance: {path}")
    if (material.get_editor_property("blend_mode") != unreal.BlendMode.BLEND_ADDITIVE
            or material.get_editor_property("shading_model") != unreal.MaterialShadingModel.MSM_UNLIT):
        raise RuntimeError(f"Additive material state verification failed: {path}")
    return material, created


def apply(world):
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    selected = [r for r in catalog["records"] if r["mode"] == "additive_one_one"]
    if len(selected) != 25:
        raise RuntimeError("Expected 25 source-verified additive material records")
    materials = {}
    created_count = 0
    for record in selected:
        material, created = _material(record)
        created_count += int(created)
        materials[record["unreal_original_material"]] = material
    patched_paths = {m.get_path_name() for m in materials.values()}
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    mesh_records = []
    seen = set()
    matched = set()
    visual_only_actors = []
    for actor in actors.get_all_level_actors():
        if not isinstance(actor, unreal.StaticMeshActor) or actor.get_world() != world:
            continue
        component = actor.static_mesh_component
        mesh = component.static_mesh
        if mesh is None:
            continue
        mesh_path = mesh.get_path_name()
        if mesh_path not in seen:
            changes = []
            for index, slot in enumerate(mesh.get_editor_property("static_materials")):
                original = slot.material_interface
                path = original.get_path_name() if original else ""
                if path in materials:
                    replacement = materials[path]
                    mesh.set_material(index, replacement)
                    changes.append({"slot": index, "original": path, "replacement": replacement.get_path_name()})
                    matched.add(path)
                elif path in patched_paths:
                    matched.update(p for p, material in materials.items() if material.get_path_name() == path)
            if changes and not unreal.EditorAssetLibrary.save_loaded_asset(mesh, only_if_is_dirty=False):
                raise RuntimeError(f"Failed saving patched mesh {mesh_path}")
            if changes:
                mesh_records.append({"mesh": mesh_path, "slots": changes})
            seen.add(mesh_path)
        slots = list(mesh.get_editor_property("static_materials"))
        if slots and all(s.material_interface and s.material_interface.get_path_name() in patched_paths for s in slots):
            component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
            component.set_cast_shadow(False)
            # UE's primary StaticMeshComponent.h exposes bDisallowNanite as
            # EditAnywhere/BlueprintReadWrite. Additive shading needs raster
            # fallback; this component override does not rebuild mesh geometry.
            component.set_editor_property("disallow_nanite", True)
            if component.get_collision_enabled() != unreal.CollisionEnabled.NO_COLLISION:
                raise RuntimeError(f"Additive visual geometry still collides: {actor.get_actor_label()}")
            if not component.get_editor_property("disallow_nanite"):
                raise RuntimeError(f"Additive visual geometry still permits Nanite: {actor.get_actor_label()}")
            visual_only_actors.append({"actor": actor.get_actor_label(), "mesh": mesh_path, "collision": "NoCollision", "cast_shadow": False, "disallow_nanite": True})
    missing = sorted(set(materials) - matched)
    if missing:
        raise RuntimeError("No exact imported mesh slot matched original material: " + ", ".join(missing))
    return {"status": "applied_and_verified", "catalog": str(CATALOG),
            "catalog_sha256": hashlib.sha256(CATALOG.read_bytes()).hexdigest(),
            "source_materials": len(selected), "created_materials": created_count,
            "matched_source_materials": len(matched), "mesh_changes": mesh_records,
            "visual_only_actors": visual_only_actors,
            "material_mapping": {p: m.get_path_name() for p, m in materials.items()},
            "limitations": ["Original shader lighting, fog, sorting, special texture slots and vertex alpha semantics remain unresolved.",
                            "Unlit texture × diffuse × vertex RGB is a conservative color mapping, not original rendering fidelity.",
                            "The SRC_COLOR/SRC_COLOR material remains explicitly unsupported and unchanged.",
                            "NoCollision is an exploration correction for explicit additive visual meshes, not recovered original collision."]}


if __name__ == "__main__":
    level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if not level.load_level("/Game/Maps/SchoolExploration"):
        raise RuntimeError("Exploration map missing")
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    report = apply(world)
    if not level.save_current_level():
        raise RuntimeError("Failed saving patched exploration map")
    report["map"] = "/Game/Maps/SchoolExploration"
    report["engine"] = unreal.SystemLibrary.get_engine_version()
    Path(os.environ["BULLY_UE_REPORT"]).write_text(json.dumps(report, indent=2), encoding="utf-8")

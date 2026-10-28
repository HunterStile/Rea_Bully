"""Run inside UnrealEditor-Cmd: import a converted original mesh and save a preview map.

The map is an asset inspection scene. This script does not implement Bully gameplay.
Requires BULLY_GLB and BULLY_UE_REPORT in the child process environment.
"""
import hashlib
import json
import os
from pathlib import Path

import unreal


source = Path(os.environ["BULLY_GLB"]).resolve(strict=True)
report_path = Path(os.environ["BULLY_UE_REPORT"]).resolve()
if report_path.exists():
    raise RuntimeError(f"Choose a new report path: {report_path}")
sources = sorted(source.glob("*.glb")) if source.is_dir() else [source]
if not sources:
    raise RuntimeError("No GLB sources")
asset_folder = os.environ.get("BULLY_ASSET_FOLDER", "/Game/Original/Campus")
tasks = []
for item in sources:
    task = unreal.AssetImportTask()
    task.filename = str(item)
    task.destination_path = asset_folder + "/" + item.stem.replace("-", "_")
    task.automated = True
    task.replace_existing = False
    task.save = True
    tasks.append(task)
unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks(tasks)
paths = [str(value) for value in unreal.EditorAssetLibrary.list_assets(
    asset_folder, recursive=True, include_folder=False)]
meshes = []
for path in paths:
    asset = unreal.EditorAssetLibrary.load_asset(path)
    if isinstance(asset, unreal.StaticMesh):
        meshes.append(asset)
if not meshes:
    raise RuntimeError("Import did not produce a StaticMesh; no preview scene will be claimed")

level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
map_path = os.environ.get("BULLY_MAP", "/Game/Maps/CampusPreview")
if not level.new_level(map_path):
    raise RuntimeError("Could not create preview level")
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
for mesh in meshes:
    actor = actors.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(0, 0, 0))
    actor.static_mesh_component.set_static_mesh(mesh)
    actor.set_actor_label(mesh.get_name())
sun = actors.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(0, 0, 3000),
                                    unreal.Rotator(-45, -30, 0))
sun.light_component.set_editor_property("intensity", 35000.0)
sun.light_component.set_editor_property("mobility", unreal.ComponentMobility.MOVABLE)
sun.light_component.set_editor_property("atmosphere_sun_light", True)
sky = actors.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 0))
sky.light_component.set_editor_property("mobility", unreal.ComponentMobility.MOVABLE)
sky.light_component.set_editor_property("source_type", unreal.SkyLightSourceType.SLS_CAPTURED_SCENE)
sky.light_component.set_editor_property("real_time_capture", True)
actors.spawn_actor_from_class(unreal.SkyAtmosphere, unreal.Vector(0, 0, 0))
if not level.save_current_level():
    raise RuntimeError("Saving preview level failed")
unreal.EditorAssetLibrary.save_directory(asset_folder, only_if_is_dirty=False, recursive=True)
report = {
    "sources": [{"path": str(item), "sha256": hashlib.sha256(item.read_bytes()).hexdigest()}
                for item in sources],
    "engine": unreal.SystemLibrary.get_engine_version(), "map": map_path,
    "mesh_assets": [mesh.get_path_name() for mesh in meshes],
    "imported_asset_paths": paths,
    "status": "imported_and_saved",
    "limitations": ["Original asset inspection scene, not playable Bully.",
                    "No character controller, missions, AI, audio, or original game services implemented.",
                    "No visual fidelity or improved rendering claim follows from headless import."],
}
report_path.parent.mkdir(parents=True, exist_ok=True)
report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
unreal.log("BULLY_IMPORT_REPORT " + str(report_path))

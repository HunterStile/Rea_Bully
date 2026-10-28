"""Frame the existing original-asset preview in Unreal, without reimporting assets."""
import json
import math
import os
from pathlib import Path
import unreal

level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
map_path = os.environ.get("BULLY_MAP", "/Game/Maps/CampusPreview")
if not level.load_level(map_path):
    raise RuntimeError("Campus preview map missing")
subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
meshes = [actor for actor in subsystem.get_all_level_actors() if isinstance(actor, unreal.StaticMeshActor)]
if not meshes:
    raise RuntimeError("No original mesh actors")
minimum = [float("inf")] * 3
maximum = [float("-inf")] * 3
for actor in meshes:
    origin, extent = actor.get_actor_bounds(False)
    for axis, key in enumerate(("x", "y", "z")):
        minimum[axis] = min(minimum[axis], getattr(origin, key) - getattr(extent, key))
        maximum[axis] = max(maximum[axis], getattr(origin, key) + getattr(extent, key))
center = unreal.Vector(*[(low + high) * 0.5 for low, high in zip(minimum, maximum)])
radius = math.sqrt(sum((high - low) ** 2 for low, high in zip(minimum, maximum))) * 0.44
position = center + unreal.Vector(radius, -radius, radius * 0.65)
target = unreal.Vector(center.x, center.y, minimum[2] + 1000.0)
rotation = unreal.MathLibrary.find_look_at_rotation(position, target)
camera = next((actor for actor in subsystem.get_all_level_actors()
               if isinstance(actor, unreal.CameraActor) and actor.get_actor_label() == "REA_OverviewCamera"), None)
if camera is None:
    camera = subsystem.spawn_actor_from_class(unreal.CameraActor, position, rotation)
else:
    camera.set_actor_location(position, False, False)
    camera.set_actor_rotation(rotation, False)
camera.set_actor_label("REA_OverviewCamera")
component = camera.get_component_by_class(unreal.CameraComponent)
settings = component.get_editor_property("post_process_settings")
for property_name, value in {
    "override_auto_exposure_method": True,
    "auto_exposure_method": unreal.AutoExposureMethod.AEM_MANUAL,
    "override_auto_exposure_apply_physical_camera_exposure": True,
    "auto_exposure_apply_physical_camera_exposure": True,
    "override_camera_iso": True,
    "camera_iso": 100.0,
    "override_camera_shutter_speed": True,
    "camera_shutter_speed": 125.0,
    "override_depth_of_field_fstop": True,
    "depth_of_field_fstop": 4.0,
}.items():
    settings.set_editor_property(property_name, value)
component.set_editor_property("post_process_settings", settings)
component.set_editor_property("post_process_blend_weight", 1.0)
start = next((actor for actor in subsystem.get_all_level_actors()
              if isinstance(actor, unreal.PlayerStart) and actor.get_actor_label() == "REA_PreviewStart"), None)
if start is None:
    start = subsystem.spawn_actor_from_class(unreal.PlayerStart, position, rotation)
else:
    start.set_actor_location(position, False, False)
    start.set_actor_rotation(rotation, False)
start.set_actor_label("REA_PreviewStart")
unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(position, rotation)
if not level.save_current_level():
    raise RuntimeError("Could not save framed map")
report = {"status": "framed_and_saved", "map": map_path, "actors": len(meshes),
          "bounds_cm": {"minimum": minimum, "maximum": maximum},
          "camera_cm": [position.x, position.y, position.z],
          "camera_target_cm": [target.x, target.y, target.z],
          "camera_exposure": {"mode": "manual_physical", "iso": 100, "shutter_reciprocal": 125, "fstop": 4},
          "limitations": ["Framing verified through engine APIs; this is not a rendered screenshot or gameplay test."]}
Path(os.environ["BULLY_UE_REPORT"]).write_text(json.dumps(report, indent=2), encoding="utf-8")

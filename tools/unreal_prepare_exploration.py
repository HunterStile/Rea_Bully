"""Create a separate exploration map and validate a clear spawn on recovered geometry."""
import json
import math
import os
import sys
from pathlib import Path
import unreal

root = Path(__file__).resolve().parents[1]
report_path = Path(os.environ["BULLY_UE_REPORT"])
source_map = "/Game/Maps/SchoolZonePreview"
target_map = "/Game/Maps/SchoolExploration"
level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
if not level.load_level(source_map):
    raise RuntimeError("Original school preview map missing")
# Static mesh assets can still be compiling after load_level returns. Waiting
# here also completes their post-compilation collision registration. Otherwise
# all floor traces may report no hit on an otherwise valid editor world.
unreal.AutomationLibrary.finish_loading_before_screenshot()
world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
mesh_actors = [actor for actor in actors.get_all_level_actors() if isinstance(actor, unreal.StaticMeshActor)]
if len(mesh_actors) != 704:
    raise RuntimeError(f"Unexpected source geometry count: {len(mesh_actors)}")
collision_records = []
seen = set()
for actor in mesh_actors:
    component = actor.static_mesh_component
    mesh = component.static_mesh
    if mesh.get_path_name() not in seen:
        body = mesh.get_editor_property("body_setup")
        if body is None:
            raise RuntimeError(f"Mesh has no physics body: {mesh.get_path_name()}")
        before = str(body.get_editor_property("collision_trace_flag"))
        if (body.get_editor_property("collision_trace_flag") != unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE
                or not body.get_editor_property("double_sided_geometry")):
            body.set_editor_property("collision_trace_flag", unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE)
            body.set_editor_property("double_sided_geometry", True)
            if not unreal.EditorAssetLibrary.save_loaded_asset(mesh, only_if_is_dirty=False):
                raise RuntimeError(f"Failed saving mesh collision: {mesh.get_path_name()}")
        collision_records.append({"mesh": mesh.get_path_name(), "before": before,
                                  "after": "CTF_USE_COMPLEX_AS_SIMPLE", "double_sided": True})
        seen.add(mesh.get_path_name())
    component.set_collision_profile_name("BlockAll")
    # These low-polygon imported materials do not all declare Nanite usage.
    # Use the conventional mesh path in this exploration map so they retain
    # their real materials instead of falling back to Unreal's default one.
    component.set_editor_property("disallow_nanite", True)
    component.set_static_mesh(None)
    component.set_static_mesh(mesh)

# Restore source-declared additive effects before choosing a walkable route.
# Visual effect quads must not introduce invisible collision barriers.
sys.path.insert(0, str(root / "tools"))
from unreal_patch_school_materials import apply as apply_original_blending
material_patch = apply_original_blending(world)
unreal.AutomationLibrary.finish_loading_before_screenshot()


def hit_info(raw):
    if raw is None or raw is False:
        return None
    if isinstance(raw, unreal.HitResult):
        hit = raw
    elif isinstance(raw, tuple):
        hit = next((part for part in raw if isinstance(part, unreal.HitResult)), None)
        if hit is None:
            raise RuntimeError(f"Unexpected trace response: {raw!r}")
    else:
        raise RuntimeError(f"Unexpected trace type: {type(raw)}")
    # FHitResult's HasNativeBreak exposes GameplayStatics::BreakHitResult as
    # its Python struct conversion, not as a GameplayStatics class method.
    info = hit.to_tuple()
    return info if info[0] else None


def floor_at(x, y, height=2000.0):
    raw = unreal.SystemLibrary.line_trace_single(world, unreal.Vector(x, y, height), unreal.Vector(x, y, -1800),
        unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, True, [], unreal.DrawDebugTrace.NONE, True)
    return hit_info(raw)


def capsule_clear(position, end):
    raw = unreal.SystemLibrary.capsule_trace_single(world, position, end, 35.0, 88.0,
        unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, False, [], unreal.DrawDebugTrace.NONE, True)
    return hit_info(raw) is None


# Prefer the school courtyard area; require real terrain, level footing and a
# clear corridor for the first walking check. No synthetic floor is introduced.
candidates = [(25000, 7300), (24000, 7300), (23000, 7300), (23000, 9000), (22000, 11000)]
candidates += [(27000 + dx, 10700 + dy) for dx, dy in
              [(0, 0), (-500, 0), (500, 0), (0, -500), (0, 500), (-1000, 0), (0, -1000), (1000, 0)]]
candidates += [(x, y) for x in range(18000, 31000, 1000) for y in range(4000, 16000, 1000)]
selected = None
for x, y in candidates:
    floor = floor_at(x, y)
    if floor is None:
        continue
    point, normal, floor_actor = floor[5], floor[7], floor[9]
    if not isinstance(floor_actor, unreal.StaticMeshActor) or normal.z < 0.97 or not 300 < point.z < 1300:
        continue
    position = unreal.Vector(point.x, point.y, point.z + 92)
    if not capsule_clear(position, position + unreal.Vector(0, 0, 1)):
        continue
    for yaw in (180, 90, -90, 0):
        forward = unreal.Vector(math.cos(math.radians(yaw)), math.sin(math.radians(yaw)), 0)
        end = position + forward * 1100
        if not capsule_clear(position, end):
            continue
        samples = [floor_at(position.x + forward.x * distance, position.y + forward.y * distance,
                            point.z + 100) for distance in (300, 650, 1000)]
        if any(sample is None or abs(sample[5].z - point.z) > 20 or sample[7].z < 0.97 for sample in samples):
            continue
        selected = {"position": position, "yaw": yaw, "floor_actor": floor_actor.get_actor_label(),
                    "floor_cm": [point.x, point.y, point.z], "clear_corridor_cm": 1100}
        break
    if selected:
        break
if selected is None:
    raise RuntimeError("No walkable spawn with sufficient real collision and clear ground was found")

for actor in list(actors.get_all_level_actors()):
    if isinstance(actor, unreal.PlayerStart):
        actors.destroy_actor(actor)
start = actors.spawn_actor_from_class(unreal.PlayerStart, selected["position"], unreal.Rotator(0, selected["yaw"], 0))
start.set_actor_label("REA_ExplorationStart")
settings = world.get_world_settings()
mode = unreal.load_class(None, "/Script/ReaBullyRuntime.ReaBullyGameMode")
if mode is None:
    raise RuntimeError("Compiled exploration GameMode missing")
settings.set_editor_property("default_game_mode", mode)
settings.set_editor_property("kill_z", -5000.0)

volume = actors.spawn_actor_from_class(unreal.PostProcessVolume, unreal.Vector(0, 0, 0))
volume.set_actor_label("REA_ExplorationExposure")
volume.set_editor_property("unbound", True)
exposure = volume.get_editor_property("settings")
for name, value in {
    "override_auto_exposure_method": True, "auto_exposure_method": unreal.AutoExposureMethod.AEM_MANUAL,
    "override_auto_exposure_apply_physical_camera_exposure": True, "auto_exposure_apply_physical_camera_exposure": True,
    "override_camera_iso": True, "camera_iso": 100.0,
    "override_camera_shutter_speed": True, "camera_shutter_speed": 125.0,
    "override_depth_of_field_fstop": True, "depth_of_field_fstop": 5.6,
}.items():
    exposure.set_editor_property(name, value)
volume.set_editor_property("settings", exposure)

unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(
    selected["position"] + unreal.Vector(0, 0, 64), unreal.Rotator(0, selected["yaw"], 0))
if not unreal.EditorLoadingAndSavingUtils.save_map(world, target_map):
    raise RuntimeError("Failed saving exploration map")
report = {"status": "prepared_and_saved", "map": target_map, "source_map": source_map,
    "mesh_actors": len(mesh_actors), "collision_meshes": collision_records,
    "component_render_path": "conventional_static_mesh_for_all_704_actors",
    "material_patch": material_patch,
    "spawn_cm": [selected["position"].x, selected["position"].y, selected["position"].z],
    "spawn_yaw_degrees": selected["yaw"], "floor_actor": selected["floor_actor"],
    "floor_cm": selected["floor_cm"], "clear_corridor_cm": selected["clear_corridor_cm"],
    "engine": unreal.SystemLibrary.get_engine_version(),
    "limitations": ["Collision is generated from visible triangles, not recovered Bully collision data.",
                    "Scene preparation supports the exploration controller; original gameplay remains incomplete.",
                    "Scene preparation is not evidence that a play session passed."]}
report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

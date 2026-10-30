"""UE editor queries only: compare rays/capsules at the failed integrated start.

Root coordinates execution. BULLY_UE_REPORT must name a new file. Optionally
REA_COLLISION_DIAGNOSTIC_MAP loads one zone; otherwise use the current editor world.
No assets/maps are saved, no actors spawned and no gameplay/audio started.
"""
import json
import os
from pathlib import Path


def encode(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (tuple, list)):
        return [encode(x) for x in value]
    if all(hasattr(value, key) for key in ('x', 'y', 'z')):
        return [value.x, value.y, value.z]
    if hasattr(value, 'get_path_name'):
        return value.get_path_name()
    if hasattr(value, 'get_editor_property'):
        fields = {}
        for name in ('blocking_hit', 'start_penetrating', 'time', 'distance', 'location',
                     'impact_point', 'normal', 'impact_normal', 'penetration_depth', 'item',
                     'face_index', 'component'):
            try:
                fields[name] = encode(value.get_editor_property(name))
            except Exception:
                pass
        if fields:
            return fields
    return str(value)


def main():
    import unreal
    report = Path(os.environ['BULLY_UE_REPORT'])
    if report.exists():
        raise ValueError('New diagnostic report required')
    asset_path = os.environ.get('REA_COLLISION_DIAGNOSTIC_ASSET')
    if asset_path:
        mesh = unreal.load_asset(asset_path)
        if not isinstance(mesh, unreal.StaticMesh):
            raise RuntimeError('A saved collision StaticMesh is required')
        geometry = json.loads(unreal.ReaBullyWorldPropTools.read_collision_triangle_geometry(mesh))
        with report.open('x', encoding='utf-8') as stream:
            json.dump({'status': 'editor_query_diagnostic', 'asset_geometry': geometry,
                'render_triangles': mesh.get_num_triangles(0),
                'source_glb_sha256': unreal.EditorAssetLibrary.get_metadata_tag(mesh, 'REAOriginalCollisionGLBSHA256'),
                'asset_or_world_saved': False, 'gameplay_executed': False}, stream, indent=2)
        return
    level = os.environ.get('REA_COLLISION_DIAGNOSTIC_MAP')
    if level and not unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).load_level(level):
        raise RuntimeError('Could not load requested diagnostic zone')
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    if world is None:
        raise RuntimeError('Editor world required')
    point = json.loads(os.environ.get('REA_COLLISION_DIAGNOSTIC_POINT', '[17973.3002,11888.5002,763.364]'))
    original_components = []
    for actor in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors():
        for component in actor.get_components_by_class(unreal.HierarchicalInstancedStaticMeshComponent):
            if not component.component_has_tag('OriginalCOL3'):
                continue
            mesh = component.get_editor_property('static_mesh')
            setup = mesh.get_editor_property('body_setup') if mesh else None
            body = {}
            if setup:
                for name in ('collision_trace_flag', 'double_sided_geometry', 'never_needs_cooked_collision_data'):
                    try:
                        body[name] = encode(setup.get_editor_property(name))
                    except Exception as exc:
                        body[name] = {'unavailable': str(exc)}
            original_components.append({'component': component.get_path_name(), 'mesh': encode(mesh),
                'instances': component.get_instance_count(), 'body': body,
                'native_mesh_inspection': json.loads(unreal.ReaBullyWorldPropTools.inspect_collision_mesh(mesh)),
                'collision_enabled': str(component.get_collision_enabled()),
                'collision_profile': str(component.get_collision_profile_name())})
    calls = []
    for label, start_z, end_z in [('spawn_down', point[2]+150, point[2]-300),
                                 ('source_floor_contact', 717.5084544141355, 707.5084544141355)]:
        start, end = unreal.Vector(point[0], point[1], start_z), unreal.Vector(point[0], point[1], end_z)
        for complex_query in (False, True):
            for shape in ('line', 'capsule'):
                for channel in ('visibility', 'pawn_profile'):
                    kwargs = dict(world_context_object=world, start=start, end=end,
                        trace_complex=complex_query, actors_to_ignore=[], draw_debug_type=unreal.DrawDebugTrace.NONE,
                        ignore_self=False)
                    if shape == 'capsule':
                        kwargs.update(radius=34., half_height=88.)
                    name = shape+'_trace_single'
                    if channel == 'pawn_profile':
                        name += '_by_profile'
                        kwargs['profile_name'] = 'Pawn'
                    else:
                        kwargs['trace_channel'] = unreal.TraceTypeQuery.TRACE_TYPE_QUERY1
                    row = {'query': label, 'shape': shape, 'channel': channel,
                           'complex': complex_query, 'start': encode(start), 'end': encode(end)}
                    try:
                        result = getattr(unreal.SystemLibrary, name)(**kwargs)
                        row.update(result=encode(result), result_type=type(result).__name__)
                    except Exception as exc:
                        row['error'] = str(exc)
                    calls.append(row)
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open('x', encoding='utf-8') as stream:
        json.dump({'status': 'editor_query_diagnostic', 'world': world.get_path_name(),
            'engine': unreal.SystemLibrary.get_engine_version(), 'point': point,
            'original_components': original_components, 'queries': calls,
            'asset_or_world_saved': False, 'gameplay_executed': False,
            'physics_scene_initialization_verified': False,
            'interpretation': 'Editor query misses alone do not establish a collision failure; compare game-world telemetry.'}, stream, indent=2)
    unreal.log('REA_WORLD_COLLISION_DIAGNOSTIC '+str(report))


if __name__ == '__main__':
    main()

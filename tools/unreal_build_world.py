"""Build original placement zones as reusable HISM levels, then an exploration persistent map."""
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import sys
import unreal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from world_integration_contracts import require_ambient_npcs
plan_path = Path(os.environ['REA_WORLD_PLAN'])
plan = json.loads(plan_path.read_text(encoding='utf-8'))
prop_plan_path = os.environ.get('REA_WORLD_PROPS_PLAN')
prop_plan = json.loads(Path(prop_plan_path).read_text(encoding='utf-8')) if prop_plan_path else None
prop_zones = {zone['name']: zone for zone in prop_plan['zones']} if prop_plan else {}


def read_optional(name):
    path = os.environ.get(name)
    return json.loads(Path(path).read_text(encoding='utf-8')) if path else None


def verify_reference(reference):
    path = ROOT/reference['path']
    if hashlib.sha256(path.read_bytes()).hexdigest() != reference['sha256']:
        raise RuntimeError('Integration evidence changed: ' + str(path))


ambient_plan = read_optional('REA_WORLD_AMBIENT_PLAN')
door_plan = read_optional('REA_WORLD_DOORS_PLAN')
collision_plan = read_optional('REA_WORLD_COLLISION_PLAN')
audio_report = read_optional('REA_WORLD_AUDIO_REPORT')
if ambient_plan:
    if not ambient_plan.get('ready_for_world_builder'):
        raise RuntimeError('Ambient runtime plan has not passed source checks')
    for reference in ambient_plan['sources'].values():
        verify_reference(reference)
for extra in (door_plan, collision_plan):
    if extra:
        for reference in extra.get('source_catalogs', []):
            verify_reference(reference)
        for reference in extra.get('source_reports', {}).values():
            verify_reference(reference)
door_zones = {zone['name']: zone for zone in door_plan['zones']} if door_plan else {}
collision_zones = {zone['name']: zone for zone in collision_plan['zones']} if collision_plan else {}
npc_records = read_optional('REA_WORLD_NPCS')
npcs = {npc['ped_id']: npc for npc in npc_records['npcs']} if npc_records else {}
if npc_records and (npc_records.get('status') != 'npc_assets_verified' or len(npcs) != len(npc_records['npcs'])):
    raise RuntimeError('Verified independent NPC import report required')
if ambient_plan:
    npcs = require_ambient_npcs(ambient_plan, npc_records)
    for placement in ambient_plan['placements']:
        npc = npcs[placement['ped_id']]
        asset = unreal.load_asset(npc['data'])
        if not asset or unreal.EditorAssetLibrary.get_metadata_tag(asset, 'REAOriginalNPCSHA256') != npc['source_sha256']:
            raise RuntimeError('Ambient NPC data must verify before any map changes')
report = Path(os.environ['BULLY_UE_REPORT'])
if report.exists():
    raise RuntimeError('New report required')
requested = set(filter(None, os.environ.get('REA_WORLD_ZONES', '').split(',')))
unknown_zones = requested - {zone['name'] for zone in plan['zones']}
if unknown_zones:
    raise RuntimeError('Requested zones missing from verified plan: ' + ', '.join(sorted(unknown_zones)))
level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
geometry_class = unreal.load_class(None, '/Script/ReaBullyRuntime.ReaBullyZoneGeometry')
manifest = []
records = []
for zone in plan['zones']:
    if requested and zone['name'] not in requested:
        continue
    if not zone['placements'] or not zone['bounds']:
        continue
    path = '/Game/World/Zones/' + zone['name']
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        if not level.load_level(path):
            raise RuntimeError('Cannot reload generated zone')
        for actor in actors.get_all_level_actors():
            if isinstance(actor, (unreal.ReaBullyZoneGeometry, unreal.ReaBullyAmbientPed,
                                  unreal.ReaBullyWorldProp, unreal.ReaBullyWorldDoor)):
                actors.destroy_actor(actor)
    elif not level.new_level(path):
        raise RuntimeError('Cannot create zone ' + path)
    geometry = actors.spawn_actor_from_class(geometry_class, unreal.Vector())
    geometry.set_actor_label('Original_' + zone['name'])
    native_collisions = collision_zones.get(zone['name'], {}).get('placements', [])
    replacement_keys = {p['model_key'] for p in native_collisions if p['replace_visual_collision']}
    replacement_meshes = {mesh['path'] for placement in zone['placements'] if placement['model_key'] in replacement_keys
                          for mesh in placement['meshes']}
    grouped = defaultdict(list)
    for placement in zone['placements']:
        source = placement['transform']
        scale = source['scale_xyz']
        transform = unreal.Transform(location=unreal.Vector(*source['translation_cm']),
            rotation=unreal.Quat(*source['rotation_quaternion_xyzw']).rotator(), scale=unreal.Vector(*scale))
        reverse = scale[0]*scale[1]*scale[2] < 0
        for mesh in placement['meshes']:
            grouped[(mesh['path'], mesh['visual_only'], reverse)].append(transform)
    counts = []
    for (mesh_path, visual, reverse), transforms in sorted(grouped.items()):
        mesh = unreal.load_asset(mesh_path)
        if not isinstance(mesh, unreal.StaticMesh):
            raise RuntimeError('Verified imported mesh missing: ' + mesh_path)
        count = geometry.add_original_instances(mesh, transforms, visual, reverse)
        if count != len(transforms):
            raise RuntimeError('Original instance creation failed')
        counts.append({'mesh': mesh_path, 'instances': count, 'visual_only': visual, 'reverse_culling': reverse})
    # Collision replacement must not disable shadows on the rendered mesh.
    for component in geometry.get_components_by_class(unreal.HierarchicalInstancedStaticMeshComponent):
        mesh = component.get_editor_property('static_mesh')
        if mesh and mesh.get_path_name() in replacement_meshes:
            component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
    collision_counts = []
    for placement in native_collisions:
        mesh = unreal.load_asset(placement['mesh'])
        if not isinstance(mesh, unreal.StaticMesh) or not placement['replace_visual_collision']:
            raise RuntimeError('Verified complete original collision mesh required')
        if unreal.EditorAssetLibrary.get_metadata_tag(mesh, 'REAOriginalCollisionGLBSHA256') != placement['source_glb_sha256']:
            raise RuntimeError('Original collision provenance changed: ' + placement['model_key'])
        source = placement['transform']
        transform = unreal.Transform(location=unreal.Vector(*source['translation_cm']),
            rotation=unreal.Quat(*source['rotation_quaternion_xyzw']).rotator(), scale=unreal.Vector(*source['scale_xyz']))
        if geometry.add_original_collision_instances(mesh, [transform]) != 1:
            raise RuntimeError('Could not create original collision instance')
        collision_counts.append({'model_key': placement['model_key'], 'source': placement['source'], 'mesh': placement['mesh']})
    unreal.AutomationLibrary.finish_loading_before_screenshot()
    placed_props = []
    for prop in prop_zones.get(zone['name'], {}).get('placements', []):
        data = unreal.load_asset(prop['data'])
        if not data or data.get_editor_property('source_sha256') != prop['source_sha256']:
            raise RuntimeError('Original prop data provenance changed: ' + prop['model_key'])
        source = prop['transform']
        transform = unreal.Transform(location=unreal.Vector(*source['translation_cm']),
            rotation=unreal.Quat(*source['rotation_quaternion_xyzw']).rotator(), scale=unreal.Vector(*source['scale_xyz']))
        actor = actors.spawn_actor_from_class(unreal.ReaBullyWorldProp, unreal.Vector())
        actor.set_actor_transform(transform, False, True)
        actor.set_editor_property('source_reference', json.dumps(prop['source'], sort_keys=True))
        actor.set_actor_label('OriginalProp_' + prop['model_key'])
        if not actor.configure_recovered_prop(data):
            raise RuntimeError('Original prop could not be configured: ' + prop['model_key'])
        placed_props.append({'model_key': prop['model_key'], 'source': prop['source'],
                             'playback': prop['playback'], 'collision': prop['collision']})
    placed_npcs = []
    if ambient_plan:
        for placement in ambient_plan['placements']:
            if placement['zone'] != zone['name']:
                continue
            npc = npcs[placement['ped_id']]
            data = unreal.load_asset(npc['data'])
            if not data or unreal.EditorAssetLibrary.get_metadata_tag(data, 'REAOriginalNPCSHA256') != npc['source_sha256']:
                raise RuntimeError('Original NPC data provenance changed')
            route = []
            evidence = placement['source_evidence']
            source_points = evidence.get('route', {}).get('points', evidence.get('original_points', []))
            waits = {tuple(point['ue_position_cm']): point.get('WAITTIME', 0) for point in source_points}
            for point in placement['points']:
                entry = unreal.ReaBullyAmbientPoint()
                entry.set_editor_property('position', unreal.Vector(*point))
                entry.set_editor_property('wait_seconds', waits.get(tuple(point), 0))
                route.append(entry)
            location = unreal.Vector(*placement['position_cm']) + unreal.Vector(0, 0, npc['half_height'] + 2)
            ped = actors.spawn_actor_from_class(unreal.ReaBullyAmbientPed, location)
            ped.set_editor_property('data', data)
            ped.set_editor_property('route', route)
            ped.set_editor_property('source_reference', placement['source_reference'])
            ped.set_actor_label('OriginalPed_' + str(npc['ped_id']))
            placed_npcs.append({'ped_id': npc['ped_id'], 'source': placement['source_reference'],
                                'selection_policy': placement['selection_policy'], 'route_points': len(route)})
    elif zone['name'] == 'tschool' and npc_records:
        source = json.loads((ROOT/'evidence/school-ambient-source.json').read_text(encoding='utf-8'))
        for npc in npc_records['npcs']:
            data = unreal.load_asset(npc['data'])
            if not data:
                raise RuntimeError('Verified NPC data asset missing')
            route = []
            if npc['ped_id'] == 50:
                patrol = next(path for path in source['patrol_paths'] if path['name'] == 'SGD_Prefect1')
                points = patrol['points']
                reference = patrol['source']['path'] + ':' + str(patrol['line'])
            elif npc['ped_id'] == 8:
                poi = next(p for p in source['poi_points'] if p['ped_type'] == 'NERD' and
                    8 in p['candidate_ped_ids_same_hierarchy_only'])
                points = [poi]
                reference = poi['source']['path'] + ':' + str(poi['line'])
            else:
                raise RuntimeError('Original ambient role not configured for this ped')
            for point in points:
                entry = unreal.ReaBullyAmbientPoint()
                entry.set_editor_property('position', unreal.Vector(*point['ue_position_cm']))
                entry.set_editor_property('wait_seconds', point.get('WAITTIME', 0))
                route.append(entry)
            location = unreal.Vector(*points[0]['ue_position_cm']) + unreal.Vector(0, 0, npc['half_height'] + 2)
            ped = actors.spawn_actor_from_class(unreal.ReaBullyAmbientPed, location)
            ped.set_editor_property('data', data)
            ped.set_editor_property('route', route if len(route) > 1 else [])
            ped.set_editor_property('source_reference', reference)
            ped.set_actor_label('OriginalPed_' + str(npc['ped_id']))
            placed_npcs.append({'ped_id': npc['ped_id'], 'source': reference})
    placed_doors = []
    for placement in door_zones.get(zone['name'], {}).get('placements', []):
        data = unreal.load_asset(placement['data'])
        if not data or data.get_editor_property('source_sha256') != placement['source_sha256']:
            raise RuntimeError('Original door provenance changed')
        source = placement['transform']
        transform = unreal.Transform(location=unreal.Vector(*source['translation_cm']),
            rotation=unreal.Quat(*source['rotation_quaternion_xyzw']).rotator(), scale=unreal.Vector(*source['scale_xyz']))
        door = actors.spawn_actor_from_class(unreal.ReaBullyWorldDoor, unreal.Vector())
        door.set_actor_transform(transform, False, True)
        for key, value in {'source_name': placement['name'], 'source_area_code': placement['area_code'],
                'source_reference': json.dumps(placement['source'], sort_keys=True),
                'source_locked_raw': placement['source_locked_raw'], 'exploration_unlocked': placement['exploration_unlocked']}.items():
            door.set_editor_property(key, value)
        transition = placement['transition']
        enabled = bool(transition and transition['area_in_world_plan'])
        door.set_editor_property('has_travel_target', enabled)
        if enabled:
            door.set_editor_property('travel_destination_cm', unreal.Vector(*transition['position_cm']))
            door.set_editor_property('travel_area', transition['area'])
            door.set_editor_property('travel_yaw_degrees', transition['yaw_degrees'])
        if not door.configure_recovered_door(data):
            raise RuntimeError('Door could not bind its original clips')
        door.set_actor_label('OriginalDoor_' + placement['id'])
        placed_doors.append({'id': placement['id'], 'model_key': placement['model_key'], 'travel': enabled,
                             'source': placement['source'], 'shading_pending': placement['shading_pending']})
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    if not unreal.EditorLoadingAndSavingUtils.save_map(world, path):
        raise RuntimeError('Failed saving original zone')
    item = unreal.ReaBullyWorldZone()
    for key, value in {'name': zone['name'], 'display_name': zone['name'], 'level': world,
        'minimum': unreal.Vector(*zone['bounds']['min']), 'maximum': unreal.Vector(*zone['bounds']['max']),
        'exterior': zone['runtime_enabled'], 'always_loaded': zone['always_loaded'], 'area': zone['area']}.items():
        item.set_editor_property(key, value)
    manifest.append(item)
    prop_sources = {(prop['model_key'], prop['source']['instance_index']) for prop in placed_props}
    remaining_pending = [pending for pending in zone['pending']
        if (pending['model_key'], pending['source'].get('instance_index')) not in prop_sources]
    records.append({'zone': zone['name'], 'map': path, 'source_placements': zone['source_count'],
        'visible_placements': len(zone['placements']), 'pending': remaining_pending, 'duplicates': zone['duplicates'],
        'components': counts, 'props': placed_props, 'npcs': placed_npcs, 'doors': placed_doors,
        'original_collisions': collision_counts, 'runtime_enabled': zone['runtime_enabled']})
    unreal.log(f'REA_WORLD_ZONE_SAVED {zone["name"]} {len(counts)} components')

if not manifest:
    raise RuntimeError('No verified original zones available')
data_path = '/Game/World/DA_World'
if unreal.EditorAssetLibrary.does_asset_exist(data_path):
    data = unreal.load_asset(data_path)
else:
    factory = unreal.DataAssetFactory()
    factory.set_editor_property('data_asset_class', unreal.ReaBullyWorldData)
    data = unreal.AssetToolsHelpers.get_asset_tools().create_asset('DA_World', '/Game/World', unreal.ReaBullyWorldData, factory)
data.set_editor_property('zones', manifest)
start = None
if ambient_plan:
    first = next(p for p in ambient_plan['placements'] if p['ped_id'] == 9)
    start = unreal.Vector(*first['position_cm']) + unreal.Vector(250, 0, 120)
    data.set_editor_property('start', start)
unreal.EditorAssetLibrary.set_metadata_tag(data, 'REAWorldPlanSHA256', hashlib.sha256(plan_path.read_bytes()).hexdigest())
unreal.EditorAssetLibrary.save_loaded_asset(data, only_if_is_dirty=False)

# Preserve the verified lighting/exposure and game mode, replacing its old
# single-zone mesh actors with the world director's streamable original levels.
if not level.load_level('/Game/Maps/SchoolExploration'):
    raise RuntimeError('Verified lighting and player setup missing')
unreal.AutomationLibrary.finish_loading_before_screenshot()
for actor in actors.get_all_level_actors():
    if isinstance(actor, unreal.StaticMeshActor):
        actors.destroy_actor(actor)
    elif start is not None and isinstance(actor, unreal.PlayerStart):
        actor.set_actor_location(start, False, True)
        actor.set_actor_rotation(unreal.Rotator(pitch=0, yaw=210, roll=0), False)
director = actors.spawn_actor_from_class(unreal.ReaBullyWorldDirector, unreal.Vector())
director.set_editor_property('data', data)
director.set_actor_label('REA_OriginalWorld')
if audio_report:
    if audio_report.get('status') != 'audio_assets_imported_pcm_verified_runtime_pending' or audio_report.get('waves') != 207:
        raise RuntimeError('Verified imported audio report required')
    audio_data = unreal.load_asset(audio_report['data'])
    if not audio_data or len(audio_data.get_editor_property('waves')) != 207:
        raise RuntimeError('Verified recovered audio data missing')
    audio = actors.spawn_actor_from_class(unreal.ReaBullyWorldAudio, unreal.Vector())
    audio.set_editor_property('data', audio_data)
    audio.set_actor_label('REA_RecoveredWorldAudio')
world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
if not unreal.EditorLoadingAndSavingUtils.save_map(world, '/Game/Maps/WorldExploration'):
    raise RuntimeError('Failed saving world exploration map')
report.write_text(json.dumps({'status': 'zones_saved', 'plan_sha256': hashlib.sha256(plan_path.read_bytes()).hexdigest(),
    'prop_plan_sha256': hashlib.sha256(Path(prop_plan_path).read_bytes()).hexdigest() if prop_plan_path else None,
    'map': '/Game/Maps/WorldExploration', 'zones': records,
    'integration': {'npcs': sum(len(r['npcs']) for r in records), 'doors': sum(len(r['doors']) for r in records),
        'original_collision_instances': sum(len(r['original_collisions']) for r in records),
        'audio_waves': 207 if audio_report else 0, 'props': sum(len(r['props']) for r in records),
        'start_policy': 'Exploration start 2.5m from original Cornelius hangout' if start else 'previous_verified_start'},
    'limitations': plan['limitations'], 'engine': unreal.SystemLibrary.get_engine_version()}, indent=2), encoding='utf-8')

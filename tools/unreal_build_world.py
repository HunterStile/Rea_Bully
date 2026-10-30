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
from plan_prop_interactions import make_plan as make_interaction_plan, require_interaction_import
from plan_prop_gestures import make_plan as make_gesture_plan, require_gesture_import
from unreal_world_scene import save_scene
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
collision_helpers = read_optional('REA_WORLD_COLLISION_HELPERS')
if collision_helpers:
    from plan_collision_helpers import validate as validate_collision_helpers
    validate_collision_helpers(collision_helpers)
    if collision_helpers['source_reports']['static_catalog']['sha256'] != plan['catalog_sha256']:
        raise RuntimeError('Collision helpers and visible placements use different original catalogs')
helper_zones = {zone['name']: zone for zone in collision_helpers['zones']} if collision_helpers else {}
audio_report = read_optional('REA_WORLD_AUDIO_REPORT')
interaction_path = os.environ.get('REA_PROP_INTERACTION_PLAN')
interaction_plan = read_optional('REA_PROP_INTERACTION_PLAN')
interaction_import = read_optional('REA_PROP_INTERACTION_IMPORT')
gesture_path = os.environ.get('REA_PROP_GESTURE_PLAN')
gesture_plan = read_optional('REA_PROP_GESTURE_PLAN')
gesture_import = read_optional('REA_PROP_GESTURE_IMPORT')
gesture_assets, gesture_placements = {}, {}
if gesture_plan:
    if gesture_plan != make_gesture_plan():
        raise RuntimeError('Carnival gesture plan differs from verified original sources')
    gesture_digest = hashlib.sha256(Path(gesture_path).read_bytes()).hexdigest()
    gesture_records = require_gesture_import(gesture_plan, gesture_import, gesture_digest)
    for model in gesture_plan['models']:
        asset = unreal.load_asset(gesture_records[model['model_key']]['data'])
        if (not isinstance(asset, unreal.ReaBullyPropGestureData) or
                asset.get_editor_property('evidence_sha256') != gesture_digest or
                unreal.EditorAssetLibrary.get_metadata_tag(asset, 'REAGestureEvidenceSHA256') != gesture_digest or
                asset.get_editor_property('source_sha256') != model['source_sha256']):
            raise RuntimeError('Carnival gesture assets must verify before any map changes')
        for role in ('idle', 'gesture'):
            clip = asset.get_editor_property(role + '_clip')
            if not clip or clip.get_path_name() != model['clips'][role]:
                raise RuntimeError('Carnival gesture original clip binding changed')
        gesture_assets[model['model_key']] = asset
    for placement in gesture_plan['placements']:
        identity = (placement['zone'], placement['source']['instance_index'])
        matching = [prop for prop in prop_zones.get(placement['zone'], {}).get('placements', [])
                    if prop['source'] == placement['source'] and prop['model_key'] == placement['model_key']
                    and prop['transform'] == placement['transform']]
        if identity in gesture_placements or len(matching) != 1:
            raise RuntimeError('Carnival gesture must enrich exactly one existing original prop')
        gesture_placements[identity] = placement
elif gesture_import:
    raise RuntimeError('Gesture import report requires its verified source plan')
interaction_zones = defaultdict(list)
if interaction_plan:
    if interaction_plan != make_interaction_plan():
        raise RuntimeError('Prop interaction plan differs from original source proof')
    interaction_digest = hashlib.sha256(Path(interaction_path).read_bytes()).hexdigest()
    require_interaction_import(interaction_plan, interaction_import, interaction_digest)
    model = interaction_plan['model']
    interaction_asset = unreal.load_asset(interaction_import['data'])
    prop_asset = unreal.load_asset(model['prop_data'])
    if (not isinstance(interaction_asset, unreal.ReaBullyPropInteractionData) or
            not isinstance(prop_asset, unreal.ReaBullyWorldPropData) or
            unreal.EditorAssetLibrary.get_metadata_tag(interaction_asset, 'REAInteractionEvidenceSHA256') != interaction_digest or
            interaction_asset.get_editor_property('evidence_sha256') != interaction_digest or
            interaction_asset.get_editor_property('source_sha256') != model['source_sha256'] or
            prop_asset.get_editor_property('source_sha256') != model['source_sha256']):
        raise RuntimeError('Prop interaction assets must verify before any map changes')
    for name, action in [('closed_clip', 'IDLE'), ('open_clip', 'OPEN'), ('close_clip', 'CLOSE')]:
        clip = interaction_asset.get_editor_property(name)
        if not clip or clip.get_path_name() != model['clips'][action]:
            raise RuntimeError('Prop interaction clip binding changed')
    for placement in interaction_plan['placements']:
        interaction_zones[placement['zone']].append(placement)
elif interaction_import:
    raise RuntimeError('An interaction import report requires its verified source plan')
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
if collision_plan and collision_plan['source_reports']['world_plan']['sha256'] != hashlib.sha256(plan_path.read_bytes()).hexdigest():
    raise RuntimeError('Collision overlay belongs to a different world placement plan')
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
zone_batch = os.environ.get('REA_WORLD_ZONE_BATCH') == '1'
if zone_batch and not requested:
    raise RuntimeError('A bounded zone batch requires explicit source zone names')
input_env = {'plan': 'REA_WORLD_PLAN', 'npcs': 'REA_WORLD_NPCS', 'ambient': 'REA_WORLD_AMBIENT_PLAN',
    'props': 'REA_WORLD_PROPS_PLAN', 'doors': 'REA_WORLD_DOORS_PLAN', 'collisions': 'REA_WORLD_COLLISION_PLAN',
    'collision_helpers': 'REA_WORLD_COLLISION_HELPERS', 'audio_data': 'REA_WORLD_AUDIO_REPORT',
    'interactions': 'REA_PROP_INTERACTION_PLAN', 'interaction_import': 'REA_PROP_INTERACTION_IMPORT',
    'gestures': 'REA_PROP_GESTURE_PLAN', 'gesture_import': 'REA_PROP_GESTURE_IMPORT'}
build_inputs = {key: {'path': Path(os.environ[name]).resolve().relative_to(ROOT).as_posix(),
    'sha256': hashlib.sha256(Path(os.environ[name]).read_bytes()).hexdigest()}
    for key, name in input_env.items() if os.environ.get(name)}
unknown_zones = requested - {zone['name'] for zone in plan['zones']}
if unknown_zones:
    raise RuntimeError('Requested zones missing from verified plan: ' + ', '.join(sorted(unknown_zones)))
for name, placements in interaction_zones.items():
    if requested and name not in requested:
        continue
    matching = [zone for zone in plan['zones'] if zone['name'] == name]
    if len(matching) != 1 or not matching[0]['placements'] or not matching[0]['bounds']:
        raise RuntimeError('Interaction zone needs verified imported geometry: ' + name)
    for placement in placements:
        same_area = {zone['name'] for zone in plan['zones'] if zone['area'] == placement['area']}
        if any(prop['model_key'] == placement['model_key'] and prop['transform'] == placement['transform']
               for zone_name in same_area for prop in prop_zones.get(zone_name, {}).get('placements', [])):
            raise RuntimeError('DAT interaction duplicates an existing IPB prop at the exact transform')
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
    native_collisions = [*collision_zones.get(zone['name'], {}).get('placements', []),
                         *helper_zones.get(zone['name'], {}).get('placements', [])]
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
    collision_groups = defaultdict(list)
    collision_sources = {}
    for placement in native_collisions:
        if not placement['replace_visual_collision'] and not placement.get('collision_only_helper'):
            raise RuntimeError('Verified complete original collision body required')
        mesh_path = placement['mesh']
        previous = collision_sources.setdefault(mesh_path, placement['source_glb_sha256'])
        if previous != placement['source_glb_sha256']:
            raise RuntimeError('Collision instances disagree on source identity')
        source = placement['transform']
        transform = unreal.Transform(location=unreal.Vector(*source['translation_cm']),
            rotation=unreal.Quat(*source['rotation_quaternion_xyzw']).rotator(), scale=unreal.Vector(*source['scale_xyz']))
        collision_groups[mesh_path].append(transform)
        collision_counts.append({'model_key': placement['model_key'], 'source': placement['source'], 'mesh': placement['mesh'],
                                 'collision_only_helper': placement.get('collision_only_helper', False)})
    # Keep every source transform, while sharing one physics HISM component per
    # mesh instead of allocating a separate component for every placement.
    for mesh_path, transforms in sorted(collision_groups.items()):
        mesh = unreal.load_asset(mesh_path)
        if not isinstance(mesh, unreal.StaticMesh):
            raise RuntimeError('Verified original collision mesh missing: ' + mesh_path)
        if unreal.EditorAssetLibrary.get_metadata_tag(mesh, 'REAOriginalCollisionGLBSHA256') != collision_sources[mesh_path]:
            raise RuntimeError('Original collision provenance changed: ' + mesh_path)
        if geometry.add_original_collision_instances(mesh, transforms) != len(transforms):
            raise RuntimeError('Could not create all original collision instances')
    unreal.AutomationLibrary.finish_loading_before_screenshot()
    placed_props = []
    placed_gestures = []
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
        gesture = gesture_placements.get((zone['name'], prop['source']['instance_index']))
        if gesture:
            actor.set_editor_property('gesture_data', gesture_assets[prop['model_key']])
            actor.set_editor_property('interaction_source_id', gesture['source_id'])
            actor.set_editor_property('exploration_interaction_enabled', True)
            placed_gestures.append({'source_id': gesture['source_id'], 'model_key': gesture['model_key'],
                'source': gesture['source'], 'control_policy': gesture['control_policy']})
        if not actor.configure_recovered_prop(data):
            raise RuntimeError('Original prop could not be configured: ' + prop['model_key'])
        placed_props.append({'model_key': prop['model_key'], 'source': prop['source'],
                             'playback': prop['playback'], 'collision': prop['collision']})
    placed_interactions = []
    for placement in interaction_zones.get(zone['name'], []):
        source = placement['transform']
        transform = unreal.Transform(location=unreal.Vector(*source['translation_cm']),
            rotation=unreal.Quat(*source['rotation_quaternion_xyzw']).rotator(), scale=unreal.Vector(*source['scale_xyz']))
        actor = actors.spawn_actor_from_class(unreal.ReaBullyWorldProp, unreal.Vector())
        actor.set_actor_transform(transform, False, True)
        actor.set_editor_property('interaction_data', interaction_asset)
        actor.set_editor_property('interaction_source_id', placement['source_id'])
        actor.set_editor_property('source_reference', placement['source_id'])
        actor.set_editor_property('exploration_interaction_enabled', placement['exploration_interaction_enabled'])
        actor.set_actor_label('OriginalGarage_' + placement['source_name'])
        if not actor.configure_recovered_prop(prop_asset):
            raise RuntimeError('Original garage interaction could not be configured')
        placed_interactions.append({'source_id': placement['source_id'], 'model_key': placement['model_key'],
            'source': placement['source'], 'transform': placement['transform'],
            'control_policy': placement['control_policy'], 'collision_policy': placement['collision_policy']})
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
        'map_sha256': hashlib.sha256((ROOT/'unreal/Content/World/Zones'/(zone['name']+'.umap')).read_bytes()).hexdigest(),
        'visible_placements': len(zone['placements']), 'pending': remaining_pending, 'duplicates': zone['duplicates'],
        'components': counts, 'props': placed_props, 'prop_interactions': placed_interactions,
        'prop_gestures': placed_gestures,
        'npcs': placed_npcs, 'doors': placed_doors,
        'original_collisions': collision_counts, 'runtime_enabled': zone['runtime_enabled']})
    unreal.log(f'REA_WORLD_ZONE_SAVED {zone["name"]} {len(counts)} components')

if not manifest:
    raise RuntimeError('No verified original zones available')
plan_sha256 = hashlib.sha256(plan_path.read_bytes()).hexdigest()
start_policy = 'zone_batch_only' if zone_batch else save_scene(manifest, ambient_plan, audio_report, plan_sha256)
report.write_text(json.dumps({'status': 'zone_assets_saved' if zone_batch else 'zones_saved', 'plan_sha256': plan_sha256,
    'build_inputs': build_inputs, 'builder_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    'runtime_sha256': hashlib.sha256((ROOT/'unreal/Binaries/Win64/UnrealEditor-ReaBullyRuntime.dll').read_bytes()).hexdigest(),
    'prop_plan_sha256': hashlib.sha256(Path(prop_plan_path).read_bytes()).hexdigest() if prop_plan_path else None,
    'interaction_plan_sha256': hashlib.sha256(Path(interaction_path).read_bytes()).hexdigest() if interaction_path else None,
    'gesture_plan_sha256': hashlib.sha256(Path(gesture_path).read_bytes()).hexdigest() if gesture_path else None,
    'map': None if zone_batch else '/Game/Maps/WorldExploration', 'zones': records,
    'integration': {'npcs': sum(len(r['npcs']) for r in records), 'doors': sum(len(r['doors']) for r in records),
        'original_collision_instances': sum(len(r['original_collisions']) for r in records),
        'original_collision_helpers': sum(c['collision_only_helper'] for r in records for c in r['original_collisions']),
        'audio_waves': 207 if audio_report and not zone_batch else 0, 'props': sum(len(r['props']) for r in records),
        'prop_interactions': sum(len(r['prop_interactions']) for r in records),
        'prop_gestures': sum(len(r['prop_gestures']) for r in records),
        'start_policy': start_policy},
    'limitations': plan['limitations'], 'engine': unreal.SystemLibrary.get_engine_version()}, indent=2), encoding='utf-8')

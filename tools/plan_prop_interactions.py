"""Prepare explicit exploration actions from source-bound non-mission props.

This adds only the two DAT BikeGar objects called by the global main script.
F is an exploration control; native bike/trigger guards are retained as limits.
No mission, damage, hiding, dummy FXTESTG or carnival event is auto-scheduled.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from plan_world_doors import ROOT, action_clips, door_transform, read, reference, verify
from validate_door_closed_bounds import closed_bounds

CATALOG = ROOT/'evidence/prop-actions-static-v04-final-r2.json'
PROPS = ROOT/'evidence/skinned-props-export-v04-rgb.json'
IMPORTS = ROOT/'local/world-import/props/10121_bikegar.json'
SOURCE_VALIDATION = ROOT/'evidence/skinned-props-validation-v04-rgb-final.json'
BINDINGS = {
    'tbusines_doors.dat:23': ('BA_BMXGarage', 'tbusines', 0, 'MainMap'),
    'tBMX.dat:23': ('BMX_GarageDoor', 'tBMX', 62, 'BMXTrack'),
}
CLIPS = {'IDLE': 'BIKEGAR\\IDLE', 'OPEN': 'BIKEGAR\\OPEN', 'CLOSE': 'BIKEGAR\\CLOSE'}


def field(record, name):
    values = [x['value'] for x in record['fields'] if x['key'] == name]
    if len(values) != 1:
        raise ValueError('Missing/duplicate DAT field: ' + name)
    return values[0]


def checked_bindings(catalog):
    """Reject changes to the proof join instead of guessing from prop names."""
    rows = catalog['garage_dat_bindings']
    if len(rows) != 2 or {r['id'] for r in rows} != set(BINDINGS):
        raise ValueError('Garage trigger inventory differs from retained proof')
    for row in rows:
        name, zone, area, area_name = BINDINGS[row['id']]
        if (row['model_key'] != '10121_bikegar' or row['model_id'] != 10121 or
                row['binding'] != 'exact_DAT_PLACEMENT_to_IDE_model_and_verified_main_callback_target' or
                row['trigger_name'] != name or row['area_code_raw'] != area or
                row['source_isdoor_raw'] is not False or field(row['perimeter'], 'ISDOOR') is not False or
                field(row['perimeter'], 'PLACEMENT') != 'BikeGar' or
                field(row['trigger'], 'ISMISSIONSPECIFIC') is not False or
                field(row['trigger'], 'TRIGGERNAME') != name or
                field(row['trigger'], 'AREACODE') != area or
                field(row['trigger'], 'POSITION') != row['position_source_game_m'] or
                field(row['trigger'], 'YAWPITCHROLL') != row['yaw_pitch_roll_degrees_raw'] or
                field(row['trigger'], 'YAW') != row['yaw_degrees_raw']):
            raise ValueError('Garage DAT binding changed: ' + row['id'])
        callbacks = {c['function']: c for c in row['script_callbacks']}
        if (len(callbacks) != 2 or
                callbacks.get('main/f32', {}).get('node_path') != '/Global/Door/DoorFunctions/Closing/BIKEGAR' or
                callbacks.get('main/f33', {}).get('node_path') != '/Global/Door/DoorFunctions/Opening/BIKEGAR'):
            raise ValueError('Garage original callback join changed')
        yield row, zone, area_name


def verify_dat(row):
    data = verify(row['source']).read_bytes()
    verify(row['manifest'])
    # Recheck this exact archive extent, without pretending to hash all Trigger.img.
    with Path(row['archive_image']['path']).open('rb') as stream:
        stream.seek(row['archive_offset_bytes'])
        original = stream.read(row['archive_extent_bytes'])
    if data != original:
        raise ValueError('Garage DAT differs from its original archive extent')
    for record in (row['trigger'], row['perimeter']):
        start, size = record['byte_offset'], record['byte_length']
        if hashlib.sha256(data[start:start+size]).hexdigest() != record['raw_sha256']:
            raise ValueError('Garage DAT record changed')
    verify(row['original_ide'])


def make_plan(catalog_path=CATALOG, props_path=PROPS, import_path=IMPORTS):
    catalog, props, imported = read(catalog_path), read(props_path), read(import_path)
    model = next(m for m in props['models'] if m['model_key'] == '10121_bikegar')
    if (model.get('shared_dummy_group') or imported['status'] != 'prop_assets_verified' or
            imported['model_key'] != model['model_key'] or imported['source_sha256'] != model['output_sha256'] or
            imported.get('ambient_loop_original') is not None or action_clips(model) != CLIPS or
            set(imported['clips']) != set(CLIPS.values())):
        raise ValueError('Garage mesh/own-group import identity changed')
    verify({'path': model['output'], 'sha256': model['output_sha256']})
    verify(model['source_nif'])
    verify(model['source_group'])
    script = catalog['garage_global_script']['source']
    for path, digest in (('listing', 'listing_sha256'), ('original_lur', 'original_lur_sha256'),
                         ('assembled_lur', 'assembled_lur_sha256'), ('roundtrip_manifest', 'roundtrip_manifest_sha256')):
        verify({'path': script[path], 'sha256': script[digest]})
    if script['assembled_roundtrip_equal'] is not True:
        raise ValueError('Original global script roundtrip not verified')
    source_validation = read(SOURCE_VALIDATION)
    measured = closed_bounds(model)
    previous = next(m for m in source_validation['models'] if m['model_key'] == model['model_key'])
    idle = next(c for c in previous['clips'] if c['name'] == CLIPS['IDLE'])
    sample = next(s for s in idle['samples'] if s['ratio'] == 0)
    low, high = sample['original_cpu_bounds']
    expected = [[100*low[0], -100*high[1], 100*low[2]], [100*high[0], -100*low[1], 100*high[2]]]
    bounds = measured['closed_bounds_cm']
    error = max(abs(a-b) for ar, br in zip(expected, [bounds['min'], bounds['max']]) for a, b in zip(ar, br))
    if error > .002:
        raise ValueError('Garage idle bounds differ from independent original NIF/AGR skinning')
    placements = []
    for row, zone, area_name in checked_bindings(catalog):
        verify_dat(row)
        placements.append({'source_id': row['id'], 'source_name': row['trigger_name'],
            'source': row['source'], 'trigger_sha256': row['trigger']['raw_sha256'],
            'source_isdoor_raw': False, 'source_area_code': row['area_code_raw'],
            'source_locked_raw': field(row['trigger'], 'ISLOCKED'),
            'mission_specific': False, 'zone': zone, 'area': area_name,
            'model_key': model['model_key'], 'prop_data': imported['data'],
            'interaction_data': '/Game/World/Interactions/10121_bikegar/DA_GarageInteraction',
            'transform': door_transform(row), 'callbacks': row['script_callbacks'],
            'exploration_interaction_enabled': True,
            'control_policy': 'F toggles original one-shot OPEN/CLOSE; reconstructed on-foot exploration input',
            'collision_policy': 'disabled_original_object_collision_not_recovered',
            'travel_policy': 'none; this prop is ISDOOR FALSE, not an inferred door destination'})
    return {'schema': 'rea-bully-prop-interactions-v1', 'status': 'source_verified_import_and_runtime_pending',
        'source_catalogs': [reference(p) for p in (catalog_path, props_path, import_path, SOURCE_VALIDATION)],
        'model': {'model_key': model['model_key'], 'source_sha256': model['output_sha256'],
            'source': reference(ROOT/model['output']), 'mesh': imported['mesh'],
            'own_skeleton': imported['own_skeleton'], 'prop_data': imported['data'],
            'original_clips': CLIPS, 'clips': {k: imported['clips'][v] for k, v in CLIPS.items()},
            'closed_bounds_cm': bounds, 'original_bounds_max_error_cm': error},
        'placements': placements, 'summary': {'models': 1, 'placements': len(placements), 'new_autoplay_clips': 0},
        'native_guard_observations': ['Global main/f32 and main/f33 require gPlayer, a matching garage trigger, F_OnABike(), area 0 or 62 and an opening-state upvalue.',
            'The original global script tests vehicle ownership and excludes models 289 and 284; vehicle gameplay and original callback activation are not implemented here.'],
        'limitations': ['Manual F input is a reconstructed exploration feature, not original garage activation.',
            'No collision, bike storage/selection, automatic travel, native callback registration, save state or mission behavior is implemented.',
            'Only the two original DAT placements are eligible; four BikeGar IPB copies and SBikeGar remain unchanged.',
            'Closed skin bounds are an interaction distance proxy, not recovered original collision.',
            'Source proof and independent CPU bounds are not Unreal animation/runtime verification.']}


def require_interaction_import(plan, imported, plan_sha256):
    """Reject stale/missing asset bindings before a builder opens any map."""
    if not imported or imported.get('status') != 'prop_interaction_assets_verified_runtime_pending':
        raise ValueError('Prop interaction placement requires a verified import report')
    model = plan['model']
    if (imported.get('source_plan', {}).get('sha256') != plan_sha256 or
            imported.get('model_key') != model['model_key'] or
            imported.get('source_sha256') != model['source_sha256'] or
            imported.get('prop_data') != model['prop_data'] or
            imported.get('clips') != model['clips'] or
            imported.get('placements') != len(plan['placements'])):
        raise ValueError('Prop interaction import differs from the source plan')
    ids = [p['source_id'] for p in plan['placements']]
    if len(ids) != len(set(ids)) or set(ids) != set(BINDINGS):
        raise ValueError('Prop interaction placement identity is duplicated or changed')
    data_path = imported.get('data', '').split('.')[0]
    if not data_path or any(p['interaction_data'].split('.')[0] != data_path for p in plan['placements']):
        raise ValueError('Interaction data path differs from the verified import')
    return imported


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = make_plan()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result['summary']))


if __name__ == '__main__':
    main()

"""Bind six original carnival props to an explicit one-shot exploration greeting.

Own NIF/HXD/AGR clips and IPB placements are verified independently. F activation
is reconstructed; CAT time, speech, random and player-trigger rules are not run.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct

from audit_ambient_candidates import archive_source
from catalog_prop_actions import load_groups
from export_nif import read_instances
from export_world import unreal_transform
from plan_world_doors import ROOT, read, reference, verify
from plan_unreal_world import zone_area

PROPS = ROOT/'evidence/skinned-props-export-v04-rgb.json'
WORLD = ROOT/'local/world-export-with-collectables-v04.json'
ACTIONS = ROOT/'evidence/prop-actions-static-v04-final-r2.json'
VALIDATION = ROOT/'evidence/skinned-props-validation-v04-rgb-final.json'
CLIPS = {'idle': 'CARNI01\\CARNY_IDLE', 'gesture': 'CARNI01\\CARNY_CMERE'}
BINDINGS = {26: '10220_carni01', 27: '10222_carni02', 28: '10223_carni03',
            29: '10223_carni03', 30: '10223_carni03', 36: '10220_carni01'}


def idle_bounds(model, validation):
    measured = next(m for m in validation['models'] if m['model_key'] == model['model_key'])
    clip = next(c for c in measured['clips'] if c['name'] == CLIPS['idle'])
    sample = next(s for s in clip['samples'] if s['ratio'] == 0)
    if sample['gltf_source_slerp_difference_metres'] > .00002:
        raise ValueError('Original idle-pose validation exceeds interaction-proxy tolerance')
    low, high = sample['original_cpu_bounds']
    # Original source XYZ -> imported UE XYZ: reflect Y and convert metres to cm.
    minimum = [100*low[0], -100*high[1], 100*low[2]]
    maximum = [100*high[0], -100*low[1], 100*high[2]]
    if not all(math.isfinite(v) for v in minimum + maximum) or any(a >= b for a, b in zip(minimum, maximum)):
        raise ValueError('Invalid original idle bounds')
    return {'min': minimum, 'max': maximum, 'center': [(a+b)*.5 for a, b in zip(minimum, maximum)],
        'extent': [(b-a)*.5 for a, b in zip(minimum, maximum)],
        'method': 'Previous independent original NIF/AGR CPU skinning at CARNY_IDLE(0); proximity proxy only, no collision.'}


def source_placements(world, instances):
    zone = next(z for z in world['zones'] if z['name'] == 'zone_carn1')
    if zone_area(zone)[0] != 'MainMap':
        raise ValueError('Carnival streaming area changed')
    rows = [p for p in zone['placements'] if p['model_key'] in set(BINDINGS.values())]
    if len(rows) != len(BINDINGS) or {r['instance_index'] for r in rows} != set(BINDINGS):
        raise ValueError('Original carnival prop instance inventory changed')
    result = []
    for row in sorted(rows, key=lambda r: r['instance_index']):
        index = row['instance_index']
        original = instances[index]
        key = f'{original["model_id"]:05d}_{original["name"].lower()}'
        if row['model_key'] != BINDINGS[index] or key != BINDINGS[index] or row['unreal_transform'] != unreal_transform(original):
            raise ValueError('Carnival placement differs from original IPB bytes')
        result.append({'source_id': f'ipb:zone_carn1:{index}', 'zone': 'zone_carn1', 'area': 'MainMap',
            'model_key': key, 'source': {'zone': 'zone_carn1', 'instance_index': index,
                'source_ipb_sha256': zone['source_ipb_sha256']}, 'transform': row['unreal_transform'],
            'gesture_data': f'/Game/World/Gestures/{key}/DA_CarnivalGesture',
            'exploration_interaction_enabled': True,
            'control_policy': 'Manual F: original CARNY_CMERE once, then existing CARNY_IDLE loop; reconstructed exploration input.',
            'collision_policy': 'unchanged_disabled_original_object_collision_not_recovered',
            'travel_policy': 'none'})
    return result


def make_plan():
    props, world, actions, validation = map(read, (PROPS, WORLD, ACTIONS, VALIDATION))
    if validation.get('status') != 'passed' or validation.get('failures') or (ROOT/validation['source_report']).resolve() != PROPS.resolve():
        raise ValueError('Original prop pose validation must pass')
    models = [m for m in props['models'] if m['model_key'] in set(BINDINGS.values())]
    if len(models) != 3 or any(m.get('shared_dummy_group') for m in models):
        raise ValueError('Exactly three non-dummy original carnival skins required')
    group = load_groups({'models': models})[(130, 0)]
    group_clips = {c['name']: c for c in group['clips']}
    if not all(name in group_clips for name in CLIPS.values()):
        raise ValueError('Original own-group carnival clips absent')
    witness = next(r for r in actions['prop_animation_references'] if r['id'] == 'World:6535:522:476')
    cat_ref = next(c for c in actions['cat_sources'] if c['id'] == witness['cat_id'])
    cat_path = verify(cat_ref)
    cat_origin = archive_source(cat_path, ROOT/'local/extracted/Stream/World')
    exact = [m for m in witness['matches'] if m['kind'] == 'exact_hxd_full_name_hash' and m['attribute'] == 24]
    if (witness['track_domain'] != 'prop_animation' or len(exact) != 1 or exact[0]['clip_ids'] != ['130:0:1'] or
            struct.unpack_from('<I', cat_path.read_bytes(), exact[0]['file_offset'])[0] != group_clips[CLIPS['gesture']]['hxd_record']['name_hash']):
        raise ValueError('Carnival CAT gesture identity changed')
    zone = next(z for z in world['zones'] if z['name'] == 'zone_carn1')
    ipb_path = verify({'path': zone['source_ipb'], 'sha256': zone['source_ipb_sha256']})
    ipb_origin = archive_source(ipb_path, ROOT/'local/extracted/Stream/World')
    placements = source_placements(world, read_instances(ipb_path))
    records, references = [], [reference(path) for path in (PROPS, WORLD, ACTIONS, VALIDATION)]
    for model in sorted(models, key=lambda m: m['model_key']):
        for field in ('source_nif', 'source_group', 'original_ide'):
            verify(model[field])
        verify({'path': model['output'], 'sha256': model['output_sha256']})
        imported_path = ROOT/'local/world-import/props'/f'{model["model_key"]}.json'
        imported = read(imported_path)
        if (imported.get('status') != 'prop_assets_verified' or imported['model_key'] != model['model_key'] or
                imported['source_sha256'] != model['output_sha256'] or imported['ambient_loop_original'] != CLIPS['idle'] or
                not all(name in imported['clips'] for name in CLIPS.values())):
            raise ValueError('Carnival original clip import binding differs')
        references.append(reference(imported_path))
        records.append({'model_key': model['model_key'], 'source_sha256': model['output_sha256'],
            'source': reference(ROOT/model['output']), 'mesh': imported['mesh'], 'prop_data': imported['data'],
            'own_skeleton': imported['own_skeleton'], 'original_clips': CLIPS,
            'clips': {role: imported['clips'][name] for role, name in CLIPS.items()},
            'durations': {role: group_clips[name]['duration'] for role, name in CLIPS.items()},
            'gesture_data': f'/Game/World/Gestures/{model["model_key"]}/DA_CarnivalGesture',
            'idle_bounds_cm': idle_bounds(model, validation)})
    return {'schema': 'rea-bully-prop-gestures-v1', 'status': 'source_verified_import_and_runtime_pending',
        'source_catalogs': references, 'source_ipb': ipb_origin, 'cat_gesture_witness': {'source': cat_origin, 'reference': witness},
        'models': records, 'placements': placements,
        'summary': {'models': 3, 'placements': 6, 'manual_gesture_clips': 1, 'new_autoplay_clips': 0},
        'limitations': ['F is reconstructed exploration input; original CAT time, speech, trigger, random and default-tree binding are not executed.',
            'Only CARNY_CMERE is manually played once; CARNY_IDLE retains its existing reconstructed ambient-loop policy.',
            'No ride, Pinky, speech, minigame, mission or original object collision is enabled.',
            'The original idle-pose box is only a proximity proxy; no missing geometry or placement is invented.']}


def require_gesture_import(plan, imported, digest):
    if not imported or imported.get('status') != 'prop_gesture_assets_verified_runtime_pending':
        raise ValueError('Verified carnival gesture import required')
    if imported.get('source_plan', {}).get('sha256') != digest or imported.get('placements') != len(plan['placements']):
        raise ValueError('Gesture import belongs to another source plan')
    records = imported.get('models', [])
    if len(records) != len(plan['models']) or len({m.get('model_key') for m in records}) != len(records):
        raise ValueError('Duplicate or missing carnival gesture assets')
    by_key = {m['model_key']: m for m in records}
    for model in plan['models']:
        got = by_key.get(model['model_key'], {})
        if (got.get('source_sha256') != model['source_sha256'] or got.get('prop_data') != model['prop_data'] or
                got.get('clips') != model['clips'] or got.get('data', '').split('.')[0] != model['gesture_data']):
            raise ValueError('Carnival gesture assets differ from original clip binding')
    ids = [p['source_id'] for p in plan['placements']]
    if len(ids) != len(set(ids)) or set(ids) != {f'ipb:zone_carn1:{i}' for i in BINDINGS}:
        raise ValueError('Carnival gesture placement identity changed')
    return by_key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = make_plan()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result['summary']))


if __name__ == '__main__':
    main()

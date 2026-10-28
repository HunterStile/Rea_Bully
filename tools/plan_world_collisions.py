"""Overlay imported original COL3 bodies on exact existing visible placements.

Only complete triangle-only bodies replace visual collision. Mixed primitives,
missing imports and intentional visual-only placements preserve the fallback.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
import math
from pathlib import Path

from export_world_collisions import ROOT, DEFAULT_REPORT, checked, reference, require
from plan_unreal_world import placement_identity
from validate_audio_waves import lower_priority


def adapter_namespace(model, collisions, record):
    adapter = model.get('winding_adapter')
    if not adapter:
        require(not record.get('winding_adapter') and
                collisions.get('status') != 'engine_winding_adapter_exported_runtime_pending',
                'Undeclared or missing collision winding adapter')
        return '/Game/Original/WorldCollision/'
    require(collisions.get('status') == 'engine_winding_adapter_exported_runtime_pending'
            and collisions.get('engine_target') == 'ue_winding_adapter_v06'
            and model.get('status') == 'engine_winding_adapter_exported_runtime_pending'
            and adapter.get('policy') == 'ue58_chaos_swap_second_third_v06'
            and adapter.get('index_mapping') == [0, 2, 1]
            and adapter.get('source_export') == collisions['sources'].get('original_export')
            and record.get('winding_adapter') == adapter,
            'Collision winding adapter provenance differs')
    original = adapter['original_model']
    require(not original.get('winding_adapter') and
            adapter['source_glb'] == {'path': str(Path(original['output']).resolve()), 'sha256': original['output_sha256']},
            'Winding adapter original GLB identity differs')
    for field, value in original.items():
        if field not in ('output', 'output_sha256', 'status'):
            require(model.get(field) == value, 'Winding adapter changed original model metadata: '+field)
    return '/Game/Original/WorldCollisionUE/'


def verify_winding_experiment(collisions, imports, validation):
    if not any(m.get('winding_adapter') for m in collisions['models']):
        require(validation is None, 'Winding experiment supplied for an unadapted catalog')
        return
    require(validation is not None, 'Adapted collision plan requires the isolated winding validation report')
    floor_key = '05140_fgrd_sc1b11'
    floor = next((m for m in collisions['models'] if m['model_key'] == floor_key), None)
    record = imports.get(floor_key)
    require(floor is not None and record is not None, 'Winding experiment floor import is missing')
    require(record.get('status') == 'collision_assets_verified'
            and record.get('source_glb_sha256') == floor['output_sha256']
            and record.get('source_col') == floor['source_col']
            and record.get('source_collision') == floor['source_collision'],
            'Winding experiment floor import provenance differs')
    prefix = adapter_namespace(floor, collisions, record)
    distance = validation.get('walked_cm')
    require(validation.get('winding_experiment_floor_and_walk_passed') is True
            and validation.get('grounded_on_original_instances') is True
            and validation.get('keyboard_moves_player') is True
            and validation.get('winding_probe_instances') == 1
            and isinstance(distance, (int, float)) and math.isfinite(distance) and distance > 200
            and validation.get('winding_probe_mesh') == record['mesh']
            and record['mesh'].startswith(prefix+floor_key+'/'),
            'Winding experiment does not prove floor and walk for this imported adapter')


def plan(world, static_catalog, collisions, imports, winding_validation=None):
    require(world['catalog_sha256'] == collisions['sources']['world_catalog']['sha256'],
            'World plan and collision export reference different static catalogs')
    models = {m['model_key']: m for m in collisions['models']}
    require(len(models) == len(collisions['models']), 'Duplicate original collision model key')
    verify_winding_experiment(collisions, imports, winding_validation)
    pending_models = {m['model_key']: m for m in collisions['pending']}
    original_zones = {z['name']: z for z in static_catalog['zones']}
    zones = []
    for zone in world['zones']:
        original = original_zones[zone['name']]
        require(zone['source_ipb_sha256'] == original['source_ipb_sha256'], 'World placement source changed')
        row = {'name': zone['name'], 'source_ipb_sha256': original['source_ipb_sha256'], 'placements': [], 'pending': []}
        for placement in zone['placements']:
            key = placement['model_key']
            source = placement['source']
            candidates = [p for p in original['placements'] if p['model_key'] == key
                and p['instance_index'] == source['instance_index']
                and p.get('source_ipb_record') == source.get('source_ipb_record')]
            require(len(candidates) == 1 and source['zone'] == zone['name'], 'Ambiguous original collision placement')
            candidate = candidates[0]
            placement_identity(candidate)  # Finite source TRS, unit source quaternion; no transform normalization.
            require(candidate['unreal_transform'] == placement['transform'], 'World placement transform changed')
            base = {'source': {**source, 'source_ipb_sha256': original['source_ipb_sha256']}, 'model_key': key}
            model = models.get(key)
            if not model:
                row['pending'].append({**base, 'reason': pending_models.get(key, {}).get('reason', 'original_triangle_body_unavailable')})
                continue
            if not model['replace_visual_collision'] or any(model['missing_primitive_counts'].values()):
                row['pending'].append({**base, 'reason': 'mixed_primitives_keep_visual_fallback',
                                       'missing_primitive_counts': model['missing_primitive_counts']})
                continue
            if not placement['meshes'] or all(mesh.get('visual_only', False) for mesh in placement['meshes']):
                row['pending'].append({**base, 'reason': 'source_placement_intentionally_visual_only'})
                continue
            record = imports.get(key)
            if not record:
                row['pending'].append({**base, 'reason': 'original_collision_import_pending'})
                continue
            require(record['status'] == 'collision_assets_verified' and record['model_key'] == key
                and record['source_glb_sha256'] == model['output_sha256'] and record['source_col'] == model['source_col']
                and record['source_collision'] == model['source_collision'] and record['replace_visual_collision'] is True
                and record['body_policy'] == 'complex_as_simple_double_sided' and record['nanite'] is False
                and record['missing_primitive_counts'] == model['missing_primitive_counts']
                and record['triangles'] == model['triangles'], 'Collision import provenance or body policy differs: ' + key)
            prefix = adapter_namespace(model, collisions, record)
            require(record['mesh'].startswith(prefix + key + '/'), 'Collision asset outside own model folder')
            planned = {**base, 'transform': placement['transform'], 'mesh': record['mesh'],
                'source_glb_sha256': model['output_sha256'], 'source_col': model['source_col'],
                'source_model_sha256': model['source_collision']['sha256'],
                'replace_visual_collision': True, 'triangles': model['triangles'],
                'status': 'ready_for_original_collision_runtime_test'}
            if model.get('winding_adapter'):
                adapter = model['winding_adapter']
                planned['winding_adapter'] = {field: adapter[field] for field in ('policy', 'index_mapping', 'source_glb', 'source_export')}
                planned['status'] = 'ready_for_adapted_collision_full_scene_test'
            row['placements'].append(planned)
        zones.append(row)
    return {'schema': 'rea-bully-world-collision-plan-v1', 'zones': zones,
        'summary': {'placements': sum(len(z['placements']) for z in zones),
            'placed_model_keys': sorted({p['model_key'] for z in zones for p in z['placements']}),
            'pending': sum(len(z['pending']) for z in zones),
            'pending_reasons': dict(Counter(p['reason'] for z in zones for p in z['pending']))},
        'limitations': ['Only verified visible world placements are joined; absent graphics never gain invisible source blockers.',
            'Only triangle-only bodies replace visual collision; mixed bodies retain the existing fallback.',
            'Source COL surface/flag metadata is not mapped to UE material/friction/audio semantics.',
            'An isolated winding A/B pass validates only its floor and walk; all placements still require the full scene test.',
            'This plan does not execute physics; engine ground traversal remains to be tested.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--world-plan', type=Path, default=ROOT / 'local/world-integration-v06/world-plan.json')
    parser.add_argument('--collisions', type=Path, default=DEFAULT_REPORT)
    parser.add_argument('--imports', type=Path, default=ROOT / 'local/world-import/collisions')
    parser.add_argument('--winding-validation', type=Path, help='Successful isolated floor/walk A/B report, required for adapted catalogs')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    lower_priority()
    require(not args.output.exists(), 'New collision plan output required')
    collisions = json.loads(args.collisions.read_text())
    static = json.loads(checked(collisions['sources']['world_catalog']))
    world = json.loads(args.world_plan.read_text())
    imports = {p.stem: json.loads(p.read_text()) for p in args.imports.glob('*.json')}
    validation = json.loads(args.winding_validation.read_text()) if args.winding_validation else None
    if any(m.get('winding_adapter') for m in collisions['models']):
        from unreal_import_world_collisions import read_source
        for model in collisions['models']:
            read_source(model)  # Byte-exact original identity, explicit index swap, engine proof hashes.
    result = plan(world, static, collisions, imports, validation)
    result['source_reports'] = {'world_plan': reference(args.world_plan), 'collision_export': reference(args.collisions)}
    if args.winding_validation:
        result['source_reports']['winding_validation'] = reference(args.winding_validation)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result['summary']))


if __name__ == '__main__':
    main()

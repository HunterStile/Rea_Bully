"""Join original PANM placements to verified UE imports without enabling event clips.

This is a separate overlay: it never rewrites the historical static world
catalog. A missing import remains pending. The world builder can place each
entry's data asset with its exact ``transform`` and source reference.
"""
import argparse
import hashlib
import json
from pathlib import Path

from plan_unreal_world import placement_identity

ROOT = Path(__file__).resolve().parents[1]
AMBIENT_LOOPS = {
    '10071_tgkflag': 'TGKFLAG\\IDLE',
    '10220_carni01': 'CARNI01\\CARNY_IDLE',
    '10222_carni02': 'CARNI01\\CARNY_IDLE',
    '10223_carni03': 'CARNI01\\CARNY_IDLE',
}


def ambient_loop(model):
    """Only these four source models have an explicitly selected ambient loop.

    Exporter's geometric loop candidates do not authorize gameplay scheduling:
    PORTAPOO/DAMAGE01, the other carnival gestures and FXTESTG/IDLE stay inert.
    """
    selected = AMBIENT_LOOPS.get(model['model_key'])
    if selected and (model.get('shared_dummy_group') or
                     selected not in {clip['name'] for clip in model['clips']}):
        raise ValueError('Approved ambient loop missing from its original model')
    return selected


def plan(world, props, imports):
    models = {model['model_key']: model for model in props['models']}
    if len(models) != len(props['models']):
        raise ValueError('Duplicate skinned model identity')
    zones, seen = [], {}
    ordered = sorted(world['zones'], key=lambda zone: (
        zone['classification'] != 'outdoor', zone['source_archive_index']))
    source_count = 0
    for zone in ordered:
        row = {'name': zone['name'], 'source_ipb_sha256': zone['source_ipb_sha256'],
               'placements': [], 'pending': [], 'duplicates': []}
        exterior = zone['classification'] == 'outdoor' or zone['name'].startswith('zone_')
        for placement in zone['placements']:
            key = placement['model_key']
            if key not in models:
                continue
            source_count += 1
            model = models[key]
            source = {'zone': zone['name'], 'instance_index': placement['instance_index'],
                      'source_ipb_sha256': zone['source_ipb_sha256']}
            identity = ('MainMap' if exterior else zone['name'], *placement_identity(placement))
            if identity in seen:
                row['duplicates'].append({'source': source, 'owner': seen[identity], 'model_key': key})
                continue
            seen[identity] = source
            record = imports.get(key)
            loop = ambient_loop(model)
            if not record:
                row['pending'].append({'source': source, 'model_key': key, 'reason': 'unreal_prop_import_pending'})
                continue
            if (record.get('status') != 'prop_assets_verified' or
                    record.get('source_sha256') != model['output_sha256'] or
                    record.get('ambient_loop_original') != loop or
                    record.get('model_key') != key):
                raise ValueError('Prop import identity or playback policy changed: ' + key)
            row['placements'].append({'source': source, 'model_key': key,
                'transform': placement['unreal_transform'], 'data': record['data'],
                'mesh': record['mesh'], 'own_skeleton': record['own_skeleton'],
                'source_sha256': record['source_sha256'], 'ambient_loop_original': loop,
                'playback': 'original_ambient_loop' if loop else 'original_bind_pose',
                'collision': 'disabled_original_object_collision_not_recovered'})
        if row['placements'] or row['pending'] or row['duplicates']:
            zones.append(row)
    expected = sum(model['placement_count'] for model in models.values())
    if source_count != expected:
        raise ValueError(f'Prop placement coverage differs from source manifest: {source_count} != {expected}')
    return {'schema': 'rea-bully-world-prop-plan-v1', 'zones': zones,
            'summary': {'source_placements': source_count,
                'placed': sum(len(zone['placements']) for zone in zones),
                'pending': sum(len(zone['pending']) for zone in zones),
                'duplicates': sum(len(zone['duplicates']) for zone in zones)},
            'limitations': [
                'Only selected TGKFLAG/IDLE and CARNI01/CARNY_IDLE clips autoplay; their scheduling is reconstructed.',
                'Other models use their original bind pose; contextual clips are preserved but not scheduled.',
                'No original animated-object collision, interaction, destruction, door state or ACT scheduler is implemented.',
                'This overlay covers baseline IPB inst records, not DAT-created objects or conditional spec records.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=ROOT/'evidence/world-export.json')
    parser.add_argument('--props', type=Path, default=ROOT/'evidence/skinned-props-export-v04-rgb.json')
    parser.add_argument('--imports', type=Path, default=ROOT/'local/world-import/props')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    world = json.loads(args.catalog.read_text(encoding='utf-8'))
    props = json.loads(args.props.read_text(encoding='utf-8'))
    imports = {path.stem: json.loads(path.read_text(encoding='utf-8')) for path in args.imports.glob('*.json')}
    result = plan(world, props, imports)
    result['source_catalogs'] = [{
        'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()
    } for path in (args.catalog, args.props)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result['summary']))


if __name__ == '__main__':
    main()

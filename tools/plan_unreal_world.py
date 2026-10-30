"""Join exact original placements to verified imported mesh assets, without inventing missing parts."""
import argparse
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def zone_area(source):
    """Keep original area membership; label the supplemental streaming policy."""
    areas = {item['area'] for item in source.get('classification_sources', []) if item.get('area')}
    if len(areas) > 1:
        raise ValueError('Zone has more than one original area: ' + source['name'])
    if areas:
        return next(iter(areas)), 'original_ColAreas_membership'
    if source['name'].startswith('zone_'):
        return 'MainMap', 'reconstructed_supplemental_zone_streaming_membership'
    return 'Unresolved_' + source['name'], 'unresolved_original_area'


def placement_identity(placement):
    """Exact floats only; q and -q represent the same original rotation."""
    transform = placement['unreal_transform']
    quaternion = tuple(transform['rotation_quaternion_xyzw'])
    if next((v for v in reversed(quaternion) if v != 0), 1) < 0:
        quaternion = tuple(-v for v in quaternion)
    values = (*transform['translation_cm'], *quaternion, *transform['scale_xyz'])
    if not all(math.isfinite(v) for v in values):
        raise ValueError('Nonfinite original placement')
    if abs(sum(v*v for v in quaternion)-1) > .001:
        raise ValueError('Original rotation is not a unit quaternion')
    return (placement['model_key'], *values)


def plan(catalog, imports):
    models = {m['model_key']: m for m in catalog['models']}
    seen = {}
    zones = []
    # A single mainmap shares coordinates; separate original areas are not merged.
    ordered = sorted(catalog['zones'], key=lambda z: (z['classification'] != 'outdoor', z['source_archive_index']))
    for source in ordered:
        zone = {k: source[k] for k in ('name', 'classification', 'source_ipb_sha256')}
        zone.update(placements=[], pending=[], duplicates=[])
        # Later IPB inst sections include original collectables whose conversion
        # is still pending. Keep them visible in runtime coverage accounting.
        for pending in source.get('supplementary_pending', []):
            zone['pending'].append({'source': pending['source'],
                'model_key': pending['model_key_candidate'], 'reason': pending['reason'],
                'source_section': 'supplementary_inst'})
        zone['area'], zone['area_assignment'] = zone_area(source)
        bounds = source.get('world_bounds_unreal_cm')
        zone['bounds'] = bounds
        for placement in source['placements']:
            key = placement['model_key']
            identity = placement_identity(placement)
            # Outdoor does not mean MainMap: original ftest belongs to Island_3.
            # Conversely, multiple IPBs (iballtos/ishoot) can share one area.
            identity = (zone['area'], *identity)
            reference = {'zone': source['name'], 'instance_index': placement['instance_index']}
            if placement.get('source_ipb_record'):
                reference['source_ipb_record'] = placement['source_ipb_record']
            if identity in seen:
                zone['duplicates'].append({'source': reference, 'owner': seen[identity]})
                continue
            seen[identity] = reference
            original = models[key]
            record = imports.get(key)
            if not record or record['source_sha256'] != original.get('output_sha256'):
                zone['pending'].append({'source': reference, 'model_key': key,
                    'reason': original['status'] if original['status'] != 'exported' else 'unreal_import_pending'})
                continue
            if not record['meshes']:
                raise ValueError('An imported model must contain verified mesh assets')
            zone['placements'].append({'source': reference, 'model_key': key,
                'transform': placement['unreal_transform'],
                'meshes': [dict(mesh, visual_only=True) for mesh in record['meshes']]
                    if original.get('definition', {}).get('section') == 'item' else record['meshes']})
        zone['exterior'] = source['classification'] == 'outdoor' or source['name'].startswith('zone_')
        # This legacy field selects distance streaming in the builder, not
        # availability. Keep separate source areas even for outdoor/test IPBs.
        zone['runtime_enabled'] = zone['exterior']
        zone['always_loaded'] = source['name'] == 'tGlobal'
        zone['source_count'] = source['placement_count'] + len(source.get('supplementary_pending', []))
        zones.append(zone)
    return {'schema_version': 1, 'zones': zones,
        'limitations': ['Distance-based streaming is a reconstruction.',
                       'Default collision uses recovered visible triangles; separately verified COL overlays and original helpers are recorded by the world builder.',
                       'Missing models and original utility entries remain explicit.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=ROOT/'evidence/world-export.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding='utf-8'))
    imports = {p.stem: json.loads(p.read_text(encoding='utf-8')) for p in (ROOT/'local/world-import/models').glob('*.json')}
    result = plan(catalog, imports)
    result['catalog_sha256'] = hashlib.sha256(args.catalog.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({'zones': len(result['zones']),
        'placed': sum(len(z['placements']) for z in result['zones']),
        'pending': sum(len(z['pending']) for z in result['zones']), 'output': str(args.output)}))


if __name__ == '__main__':
    main()

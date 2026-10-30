"""Account for every recovered IPB and retain source-bound area entry candidates.

This is a source/coverage audit, never a claim that an area can already be walked.
Original exits and DT/SV destinations are candidates for subsequent floor probes.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from plan_unreal_world import zone_area

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def reference(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def verify(ref):
    if reference(ROOT/ref['path'])['sha256'] != ref['sha256']:
        raise ValueError('Source changed: ' + ref['path'])


def entry_candidates(area, transitions, zone_links):
    """Do not confuse exit metadata with a paired original teleport or a safe floor."""
    result = []
    for link in transitions['links']:
        if (link['destination_area_name'] != area or not link['destination_coordinate_proven']
                or link['source_trigger']['is_mission_specific_raw'] is not False):
            continue
        point = link['destination_position']
        result.append({'kind': 'original_transition_destination', 'link_id': link['id'],
            'name': link['name'], 'trigger_kind': link['kind'],
            'source_area': link['source_area_name'], 'position_cm': point['ue_position_cm'],
            'native_heading_radians': point['native_heading_radians'],
            'source': link['destination_point_list']['source'], 'line': point['line'],
            'both_files_global_main_loads': link['both_files_global_main_loads'],
            'runtime_ground_verified': False})
    for point in zone_links['exits']:
        if point['area_name'] == area:
            result.append({'kind': 'original_exit_location_metadata', 'exit_id': point['exit_id'],
                'position_cm': point['ue_position_cm'], 'source': zone_links['exits_source'],
                'line': point['line'], 'destination_comment_raw': point['destination_comment_raw'],
                'original_transition_pair_proven': False, 'runtime_ground_verified': False})
    return result


def audit(catalog, transitions, zone_links, collision_export, imports, world_text):
    models = {m['model_key']: m for m in catalog['models']}
    collisions = {m['model_key']: m for m in collision_export['models']}
    collision_pending = {m['model_key']: m['reason'] for m in collision_export['pending']}
    original_ipl = {}
    for line, raw in enumerate(world_text.splitlines(), 1):
        cells = raw.split('#', 1)[0].split()
        if len(cells) == 2 and cells[0] == 'IPL':
            original_ipl[Path(cells[1]).stem.casefold()] = {'line': line, 'original_path': cells[1]}
    zones, area_zones = [], defaultdict(list)
    for source in catalog['zones']:
        area, policy = zone_area(source)
        area_zones[area].append(source['name'])
        keys = sorted({p['model_key'] for p in source['placements']})
        states = Counter(models[k]['status'] for k in keys)
        placed_states = Counter(models[p['model_key']]['status'] for p in source['placements'])
        imported = [k for k in keys if k in imports and
                    imports[k].get('source_sha256') == models[k].get('output_sha256')]
        def collision_state(key):
            if key in collisions:
                return 'pure_triangles_exported' if collisions[key]['replace_visual_collision'] else 'mixed_primitives_pending'
            return collision_pending.get(key, 'not_covered_by_collision_export')
        zones.append({'name': source['name'], 'area': area, 'area_assignment': policy,
            'source_ipb': {'path': source['source_ipb'], 'sha256': source['source_ipb_sha256']},
            'classification': source['classification'], 'classification_sources': source['classification_sources'],
            'world_dat_ipl': original_ipl.get(source['name'].casefold()),
            'bounds_unreal_cm': source.get('world_bounds_unreal_cm'),
            'placements': len(source['placements']), 'distinct_models': len(keys),
            'distinct_model_export_statuses': dict(states), 'placement_export_statuses': dict(placed_states),
            'imported_distinct_models': len(imported),
            'exported_not_imported_models': [k for k in keys if models[k]['status'] == 'exported' and k not in imported],
            'pending_models': [{'model_key': k, 'status': models[k]['status'],
                'reason': models[k].get('reason'), 'section': models[k].get('definition', {}).get('section')}
                for k in keys if models[k]['status'] != 'exported'],
            'collision_distinct_model_statuses': dict(Counter(collision_state(k) for k in keys)),
            'collision_placement_statuses': dict(Counter(collision_state(p['model_key']) for p in source['placements'])),
            'runtime_zone_verified_by_this_audit': False})
    areas = []
    for area, names in sorted(area_zones.items()):
        candidates = entry_candidates(area, transitions, zone_links)
        bounds = next((b for b in zone_links['bounds'] if b['area_name'] == area), None)
        # A centroid is a diagnostic search seed only. It must not become a
        # spawn point until a subsequent actual geometry/physics probe passes.
        search = None
        if not candidates:
            boxes = [z['bounds_unreal_cm'] for z in zones if z['area'] == area and z['bounds_unreal_cm']]
            if boxes:
                lo = [min(b['min'][i] for b in boxes) for i in range(3)]
                hi = [max(b['max'][i] for b in boxes) for i in range(3)]
                search = {'kind': 'reconstructed_geometry_bounds_search_seed',
                    'position_cm': [(a+b)/2 for a, b in zip(lo, hi)],
                    'original_spawn_position': False, 'runtime_ground_verified': False,
                    'policy': 'Search source geometry for a walkable floor; do not teleport to this seed.'}
        areas.append({'name': area, 'zones': names, 'source_bounds': bounds,
            'entry_candidates': candidates, 'diagnostic_search_seed': search,
            'natural_nonmission_transition_available': any(c['kind'] == 'original_transition_destination'
                and c['both_files_global_main_loads'] for c in candidates),
            'runtime_ground_verified': False})
    return {'schema': 'rea-bully-world-scope-v1', 'status': 'source_coverage_audited_runtime_pending',
        'zones': zones, 'areas': areas,
        'summary': {'zones': len(zones), 'areas': len(areas),
            'zones_with_exported_geometry': sum(z['distinct_model_export_statuses'].get('exported', 0) > 0 for z in zones),
            'source_area_membership_zones': sum(z['area_assignment'] == 'original_ColAreas_membership' for z in zones),
            'reconstructed_mainmap_membership_zones': sum(z['area_assignment'].startswith('reconstructed') for z in zones),
            'placements': sum(z['placements'] for z in zones),
            'areas_with_source_entry_candidates': sum(bool(a['entry_candidates']) for a in areas),
            'areas_requiring_entry_recovery': [a['name'] for a in areas if not a['entry_candidates']],
            'multi_zone_areas': {a['name']: a['zones'] for a in areas if len(a['zones']) > 1}},
        'limitations': ['Import counts are a checkpoint snapshot, not a new Unreal validation.',
            'ExitLocs metadata is not a teleport graph or proof of an original spawn.',
            'Source destination coordinates are not evidence that the integrated physics floor is safe.',
            'Zone_* MainMap membership remains an explicit streaming reconstruction.',
            'Island_3/ftest and Test_Area/ttest remain separate original areas; their names do not prove they are inactive.',
            'Collision coverage describes exported source bodies; mixed primitives and runtime physics require further work.',
            'No mission trigger or schedule is activated by this audit.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=ROOT/'local/world-export-with-collectables-v04.json')
    parser.add_argument('--collisions', type=Path, default=ROOT/'evidence/world-collisions-export-v07.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    paths = [args.catalog, ROOT/'evidence/world-door-links.json', ROOT/'evidence/world-zone-links.json', args.collisions]
    catalog, transitions, zone_links, collisions = map(read, paths)
    refs = [*catalog['config_sources'], zone_links['exits_source'], zone_links['bounds_source']]
    refs += [{'path': z['source_ipb'], 'sha256': z['source_ipb_sha256']} for z in catalog['zones']]
    refs += [link[key]['source'] for link in transitions['links'] for key in ('source_trigger', 'destination_point_list')]
    unique = {r['path']: r for r in refs}
    for ref in unique.values():
        verify(ref)
    world_dat = next(r for r in catalog['config_sources'] if Path(r['path']).name == 'world.dat')
    checkpoints = list((ROOT/'local/world-import/models').glob('*.json'))
    imports = {p.stem: read(p) for p in checkpoints}
    result = audit(catalog, transitions, zone_links, collisions, imports, Path(world_dat['path']).read_text())
    result.update(created_at_utc=datetime.now(timezone.utc).isoformat(), sources=[reference(p) for p in paths],
        original_sources_reverified=list(unique.values()), import_checkpoint_snapshot=[reference(p) for p in checkpoints])
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result['summary']))


if __name__ == '__main__':
    main()

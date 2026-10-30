"""Choose better existing original source points after offline geometry diagnostics.

This derives a new diagnostic tour with identical area/zone coverage. It never
marks physics verified or synthesizes coordinates; failed candidates remain in
the evidence and the original plan is retained byte-for-byte.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import json
import math
from pathlib import Path
import re

from area_navigation_contracts import reference, verify_references
from catalog_door_links import split_points
from catalog_population import fields, poi_points, read_trigger_blocks, ue_position
from plan_world_doors import travel_yaw
from preflight_world_tour_geometry import ROOT, WorldGeometry, diagnose, read, require


def source_candidates(area_codes):
    directory = ROOT/'local/extracted/DAT/Trigger'
    manifest_path = directory/'manifest.json'
    manifest = read(manifest_path)
    manifest_ref = reference(manifest_path)
    by_path = {str((directory/row['path']).resolve()).casefold(): row for row in manifest['entries']}
    result = defaultdict(list)
    rejected = []
    for path in sorted(directory.glob('*.dat')):
        source_ref = None
        for block in read_trigger_blocks(path):
            value = fields(block)
            area = area_codes.get(value.get('AREACODE'))
            if area is None or block['type'] not in ('POINT', 'PATH', 'POI'):
                continue
            name = str(value.get(block['type']+'NAME', ''))
            if block['type'] == 'POI':
                name = str(next(f['value'] for f in block['fields'] if f['key'] == 'NAME'))
            if re.search(r'camera|campos|(?:^|_)cam(?:_|\d)|target|pickup|sound|sfx|projectile|ballspawn', name, re.I):
                continue
            if source_ref is None:
                source_ref = reference(path)
                record = by_path[str(path.resolve()).casefold()]
                require(record['verified'] and source_ref['sha256'] == record['extracted_sha256'], 'Original DAT extraction changed')
            if block['type'] == 'POINT':
                points = split_points(block)
                if len(points) != value['NPOINTPOINTS']:
                    rejected.append({'source': source_ref, 'record': name, 'reason': 'POINT count differs'})
                    continue
            elif block['type'] == 'PATH':
                points = [{'source_position': f['value'], 'ue_position_cm': ue_position(f['value']), 'line': f['line']}
                          for f in block['fields'] if f['key'] == 'PATHPOINT']
                if len(points) != value['NPATHPOINTS']:
                    rejected.append({'source': source_ref, 'record': name, 'reason': 'PATH count differs'})
                    continue
            else:
                points = [{'source_position': p['POIPOINT'], 'ue_position_cm': ue_position(p['POIPOINT']),
                           'line': p['line'], 'original_poi': p, 'original_point_index': i}
                          for i, p in enumerate(poi_points(block))
                          if p.get('TYPE') in ('Hang_Out', 'School_Grounds')]
            for index, point in enumerate(points):
                index = point.get('original_point_index', index)
                source = {'kind': 'original_DAT_'+block['type']+'_coordinate_for_diagnostic_travel',
                    'file': source_ref, 'manifest': manifest_ref, 'record_type': block['type'],
                    'record_name': name, 'point_index': index, 'line': point['line'],
                    'area_code': value['AREACODE'], 'source_position_game_m': point['source_position'],
                    'mission_specific_raw': value.get('POINTMISSIONSPEC', value.get('PATHMISSIONSPEC')),
                    'original_free_roam_arrival_proven': False}
                if point.get('original_poi'):
                    source['original_poi'] = point['original_poi']
                item = {'name': area+'/'+path.stem+'/'+name+'/'+str(index), 'area': area,
                        'position_cm': point['ue_position_cm'], 'source': source, 'runtime_ground_verified': False}
                if 'native_heading_radians' in point:
                    source.update({k: point[k] for k in ('source_yaw_pitch_roll_degrees', 'native_heading_radians')})
                    item.update(yaw_degrees=travel_yaw(point['native_heading_radians']),
                                facing_policy='original_POINT_heading_via_verified_0x416f80_parser')
                result[area].append(item)
    return result, rejected


def inside_zone(point, zone):
    bounds = zone.get('bounds_unreal_cm')
    return bool(bounds) and all(bounds['min'][i] <= point[i] <= bounds['max'][i] for i in range(3))


def candidate_rank(candidate, old):
    source = candidate['source']
    name = str(source.get('record_name', source.get('name', ''))).casefold()
    path = Path(source.get('file', source.get('source', {})).get('path', '')).stem.casefold()
    preferred = 0 if path == 'areatran' else 1 if any(word in name for word in ('player', 'jimmy', 'entry', 'spawn')) else 2
    return (preferred, source.get('record_type') == 'PATH',
            sum((a-b)**2 for a, b in zip(candidate['position_cm'], old['position_cm'])), candidate['name'])


def check_coverage(original, revised):
    require(len(revised) == len(original) and len({c['name'] for c in revised}) == len(revised), 'Tour checkpoint count/identity changed')
    require(Counter((c['area'], c['zone']) for c in original) == Counter((c['area'], c['zone']) for c in revised),
            'Tour area/zone coverage changed')


def derive(plan, scope, world, candidates):
    revised = copy.deepcopy(plan)
    zones = {z['name']: z for z in scope['zones']}
    areas = {a['name']: a for a in scope['areas']}
    diagnostics, changes = [], []
    for index, original in enumerate(plan['checkpoints']):
        current = copy.deepcopy(original)
        # These extra candidates are original POINT records, whose decoder is already proven by native 0x416f80.
        source = current['source']
        if source['kind'] == 'original_DAT_point_for_diagnostic_travel':
            matches = [c for c in candidates[current['area']] if
                       c['source']['file'] == source['file'] and
                       c['source']['record_name'].casefold() == source['point_name'].casefold() and
                       c['position_cm'] == current['position_cm']]
            require(len(matches) == 1 and 'yaw_degrees' in matches[0], 'Source POINT heading cannot be uniquely recovered')
            matched = matches[0]
            current['yaw_degrees'] = matched['yaw_degrees']
            current['facing_policy'] = matched['facing_policy']
            current['source']['source_yaw_pitch_roll_degrees'] = matched['source']['source_yaw_pitch_roll_degrees']
            current['source']['native_heading_radians'] = matched['source']['native_heading_radians']
        diagnostic = diagnose(world, current)
        attempted = []
        needs_entry = diagnostic['first_surfaces']['intended'] is None or diagnostic['spawn_capsule_overlaps']
        selected = None
        if needs_entry:
            options = []
            for entry in areas[current['area']]['entry_candidates']:
                candidate = {'name': current['area']+'/Scope/'+entry.get('name', 'Exit'+str(entry.get('exit_id'))),
                             'area': current['area'], 'position_cm': entry['position_cm'],
                             'source': entry, 'runtime_ground_verified': False}
                if 'native_heading_radians' in entry:
                    candidate.update(yaw_degrees=travel_yaw(entry['native_heading_radians']), facing_policy='original_transition_arrival_heading')
                options.append(candidate)
            options.extend(candidates[current['area']])
            eligible = [c for c in options if c['position_cm'] != current['position_cm'] and
                        inside_zone(c['position_cm'], zones[current['zone']]) and
                        (current['area'] != 'MainMap' or c['source'].get('record_type') in ('POI', 'PATH'))]
            seen = set()
            for candidate in sorted(eligible, key=lambda c: candidate_rank(c, current)):
                identity = tuple(candidate['position_cm']), candidate.get('yaw_degrees', 0)
                if identity in seen:
                    continue
                seen.add(identity)
                candidate = copy.deepcopy(candidate)
                candidate['zone'] = current['zone']
                result = diagnose(world, candidate)
                attempted.append(result)
                if not result['risks']:
                    selected = candidate
                    current = candidate
                    diagnostic = result
                    break
        revised['checkpoints'][index] = current
        if current != original:
            changes.append({'index': index, 'before': original, 'after': current,
                'reason': 'Original source coordinate with no diagnosed geometric risks' if selected else
                          'Original POINT heading preserved through the already verified native point decoder',
                'runtime_verified': False})
        diagnostics.append({'original_checkpoint_name': original['name'], 'selected_diagnostic': diagnostic,
                            'alternative_candidates_tried': attempted})
        print(json.dumps({'checkpoint': index+1, 'name': current['name'], 'changed': current != original,
                          'risks': diagnostic['risks']}), flush=True)
    check_coverage(plan['checkpoints'], revised['checkpoints'])
    revised['summary']['source_coordinate_kinds'] = dict(Counter(c['source']['kind'] for c in revised['checkpoints']))
    revised['summary']['offline_source_refinements'] = len(changes)
    revised['status'] = 'original_coordinate_candidates_offline_refined_runtime_pending'
    revised['limitations'].append('Offline triangle and capsule diagnostics guided selection; real engine traversal remains required.')
    return revised, changes, diagnostics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, default=ROOT/'evidence/world-tour-plan-v07-r3.json')
    parser.add_argument('--preflight', type=Path, default=ROOT/'evidence/world-tour-geometry-preflight-v07-r2.json')
    parser.add_argument('--scope', type=Path, default=ROOT/'evidence/world-scope-v07.json')
    parser.add_argument('--helpers', type=Path, default=ROOT/'evidence/world-collision-helpers-plan-v07.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--diagnostic', type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists() and not args.diagnostic.exists(), 'New output paths required')
    plan, scope, preflight, helpers = map(read, (args.plan, args.scope, args.preflight, args.helpers))
    require(preflight['sources']['tour'] == reference(args.plan), 'Preflight does not describe the selected source plan')
    verify_references(plan)
    sources = helpers['source_reports']
    world = WorldGeometry(read(sources['static_catalog']['path']), read(sources['collision_export']['path']), helpers)
    area_names = {a['name'] for a in scope['areas']}
    area_codes = {a['area_code']: a['area_name'] for a in read(ROOT/'evidence/world-zone-links.json')['area_enum']['entries']
                  if a['area_name'] in area_names}
    # Include every current POINT area, plus areas needing an alternative source position.
    candidates, rejected = source_candidates(area_codes)
    revised, changes, diagnostics = derive(plan, scope, world, candidates)
    report = {'schema': 'rea-bully-tour-source-refinement-v1', 'status': 'offline_candidate_selection_runtime_pending',
              'sources': {'base_plan': reference(args.plan), 'baseline_preflight': reference(args.preflight),
                          'scope': reference(args.scope), 'collision_helpers_scenario': reference(args.helpers),
                          'derivation_tool': reference(Path(__file__)),
                          'geometry_tool': reference(ROOT/'tools/preflight_world_tour_geometry.py'),
                          'point_heading_decoder': reference(ROOT/'tools/catalog_door_links.py')},
              'summary': {'checkpoints': len(revised['checkpoints']), 'areas': len({c['area'] for c in revised['checkpoints']}),
                          'changed_checkpoints': len(changes), 'source_coordinates_changed': sum(c['before']['position_cm'] != c['after']['position_cm'] for c in changes),
                          'with_floor_ray_hit': sum(d['selected_diagnostic']['first_surfaces']['intended'] is not None for d in diagnostics),
                          'without_reported_geometric_risk': sum(not d['selected_diagnostic']['risks'] for d in diagnostics)},
              'changes': changes, 'diagnostics': diagnostics, 'geometry_sources_rehashed': world.sources,
              'rejected_source_records_not_used': rejected,
              'limitations': ['The original body7816 helper is an explicit additive pending-runtime scenario.',
                  'Source POINT/PATH/POI coordinates are reused exactly; script/minigame points do not activate their gameplay.',
                  'No offline result proves Chaos collision, original-area transitions or complete world traversal.']}
    with args.diagnostic.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    revised['sources'].extend([reference(args.plan), reference(args.preflight), reference(args.helpers), reference(args.diagnostic)])
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(revised, stream, indent=2, allow_nan=False)
    print(json.dumps(report['summary']), flush=True)


if __name__ == '__main__':
    main()

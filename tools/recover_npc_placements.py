"""Find original nonmission POI alternatives for five geometric capsule conflicts.

Original XY/Z points, region quotas and named identities are retained. A new
selection is a reconstruction choice, supported by source triangles but never
declared validated in the engine. Unresolved identities keep explicit conflicts.
"""
import argparse
from collections import Counter
import copy
import json
import math
from pathlib import Path

import numpy as np
from catalog_population import fields, poi_points, read_trigger_blocks
from catalog_npc_locomotion import lower_priority
from compare_npc_grounding_policy import compare
from plan_town_population import select_point, inside_region, validate as validate_baseline
from preflight_world_tour_geometry import WorldGeometry, read, segment_distance
from prepare_npc_runtime_manifest import ROOT, reference, checked
from town_npc_manifest import load_runtime_manifest

TARGETS = (85, 100, 101, 21, 26)
BASELINE = ROOT/'evidence/ambient-runtime-plan-v07-r5.json'
COMPARISON = ROOT/'evidence/npc-grounding-policy-comparison-v07-r3.json'


def capsule_conflicts(candidate, other_records):
    center = np.asarray(candidate['new']['capsule_center_cm'])
    extent = candidate['capsule_half_height_cm']-33
    a, b = center-[0, 0, extent], center+[0, 0, extent]
    conflicts = []
    for other in other_records:
        if other['ped_id'] == candidate['ped_id'] or other['area'] != candidate['area'] or not other['new']['first_surface']:
            continue
        c = np.asarray(other['new']['capsule_center_cm'])
        h = other['capsule_half_height_cm']-33
        distance = segment_distance(a, b, c-[0, 0, h], c+[0, 0, h])
        if distance < 66.5:
            conflicts.append({'ped_id': other['ped_id'], 'capsule_axis_distance_cm': distance})
    return conflicts


def candidates(plan, population, regions, scope, old):
    ped = next(p for p in population['ped_definitions'] if p['id'] == old['ped_id'])
    outdoor = old['ped_id'] != 85
    if outdoor:
        original_group = old['source_evidence']['original_poi_group']
        filename = Path(original_group['source']['path']).name
        region = old['source_evidence']['population_region']
    else:
        filename = 'tschool_hangouts.dat'
        region = next(r for r in regions['regions'] if r['name'] == 'SchoolArea')
    zone = next(z for z in scope['zones'] if z['name'] == old['zone'])
    bounds = zone['bounds_unreal_cm']
    raw_cache, result, rejected = {}, [], []
    for group in population['poi_groups']:
        if Path(group['source']['path']).name != filename:
            continue
        path = checked(group['source'])
        if path not in raw_cache:
            raw_cache[path] = read_trigger_blocks(path)
        raw = [b for b in raw_cache[path] if b['type'] == 'POI' and b['line'] == group['line']]
        if (len(raw) != 1 or next(f['value'] for f in raw[0]['fields'] if f['key'] == 'NAME') != group['name'] or
                fields(raw[0])['AREACODE'] != group['area_code'] or poi_points(raw[0]) != group['points']):
            raise ValueError('Alternative POI differs from original DAT source')
        for index, point in enumerate(group['points']):
            if point['TYPE'] not in ({'Wall', 'Spectator'} if outdoor else {'Hang_Out'}):
                continue
            try:
                placement = select_point(ped, group, index, old['zone'], outdoor=outdoor)
                if point['MAX'] > 32:
                    raise ValueError('Abnormal original POI capacity excluded from bounded alternative selection')
                if group['area_code'] != old['area_code'] or not inside_region(point['POIPOINT'], region):
                    raise ValueError('Alternative lies outside the same original region')
                if not all(bounds['min'][i] <= placement['position_cm'][i] <= bounds['max'][i] for i in range(3)):
                    raise ValueError('Alternative lies outside the original source zone bounds')
            except ValueError as exc:
                rejected.append({'source': group['source'], 'group': group['name'], 'index': index, 'reason': str(exc)})
                continue
            if outdoor:
                placement['source_evidence']['population_region'] = copy.deepcopy(region)
                placement['source_evidence']['region_membership_policy'] = old['source_evidence']['region_membership_policy']
            result.append({'placement': placement,
                           'distance_from_previous_source_cm': math.dist(placement['position_cm'], old['position_cm']),
                           'same_explicit_faction': point['PEDTYPE'] == ped['ped_type']})
    return sorted(result, key=lambda c: (not c['same_explicit_faction'], c['distance_from_previous_source_cm'],
                                         c['placement']['source_reference'])), rejected


def make_plan(baseline, report, report_path):
    result = copy.deepcopy(baseline)
    changes = {row['ped_id']: row['selected'] for row in report['targets'] if row['selected'] is not None}
    unresolved = [row for row in report['targets'] if row['selected'] is None]
    for i, old in enumerate(result['placements']):
        if old['ped_id'] in changes:
            selected = changes[old['ped_id']]
            replacement = copy.deepcopy(selected['placement'])
            replacement['source_evidence']['alternative_selection'] = {
                'report': reference(report_path), 'previous_source_reference': old['source_reference'],
                'previous_original_position_cm': old['position_cm'],
                'selection_policy': report['selection_policy'], 'runtime_validated': False}
            result['placements'][i] = replacement
    if (len(result['placements']) != 31 or
            len({p['ped_id'] for p in result['placements']}) != 31 or
            Counter(p['zone'] for p in result['placements']) != Counter(p['zone'] for p in baseline['placements'])):
        raise ValueError('Alternative selection changed the distinct cast or source zones')
    result['schema'] = 'rea-bully-town-population-alternatives-v1'
    result['status'] = 'original_poi_alternatives_geometrically_screened_runtime_pending'
    result['sources'].update(baseline=reference(BASELINE), alternative_report=reference(report_path), generator=reference(__file__))
    result['geometric_alternative_selection'] = {'replaced_ped_ids': sorted(changes),
        'unresolved_conflicts': [{'ped_id': row['ped_id'], 'reason': 'No eligible original POI has both walkable new-ray support and clear individual capsule',
                                 'previous_source_preserved': True, 'candidate_count': len(row['candidates'])} for row in unresolved],
        'runtime_verified': False}
    result['limitations'] += ['Replacement POIs are original nonmission points in the same region and quotas; named assignments and ranking are reconstructed.',
        'Source-geometry slope and capsule clearance do not prove Chaos winding, real Visibility hits, streaming, behavior or path navigation.',
        'Unresolved identities remain at their previous original points with explicit conflict records; no collision or source coordinate is altered.']
    return result


def validate(plan):
    for source in plan['sources'].values():
        checked(source)
    baseline = read(checked(plan['sources']['baseline']))
    validate_baseline(baseline)
    report_path = checked(plan['sources']['alternative_report'])
    report = read(report_path)
    for source in report['sources'].values():
        checked(source)
    for source in report['geometry_sources_rehashed'].values():
        checked(source)
    if plan != make_plan(baseline, report, report_path):
        raise ValueError('Alternative population plan differs from its source-bound selection report')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.report.exists():
        raise FileExistsError('New report and plan paths required')
    lower_priority()
    baseline, previous = read(BASELINE), read(COMPARISON)
    validate_baseline(baseline)
    for ref in previous['sources'].values():
        checked(ref)
    checked(previous['runtime_source']); checked(previous['geometry_tool']); checked(previous['generator'])
    population = read(checked(baseline['sources']['population']))
    regions, scope = read(checked(baseline['sources']['regions'])), read(checked(baseline['sources']['scope']))
    manifest_path = checked(baseline['sources']['npc_manifest'])
    npcs = {n['ped_id']: n for n in load_runtime_manifest(manifest_path)['npcs']}
    world = WorldGeometry(read(checked(previous['sources']['catalog'])), read(checked(previous['sources']['collisions'])))
    records = {n['ped_id']: copy.deepcopy(n) for n in previous['npcs']}
    targets = []
    for ped_id in TARGETS:
        old = next(p for p in baseline['placements'] if p['ped_id'] == ped_id)
        choices, excluded = candidates(baseline, population, regions, scope, old)
        evaluated, selected = [], None
        for candidate in choices:
            diagnostic = compare(world, candidate['placement'], npcs[ped_id], records[ped_id]['area'])
            new = diagnostic['new']
            conflicts = capsule_conflicts(diagnostic, records.values()) if new['first_surface'] else []
            eligible = bool(new['first_surface'] and abs(new['first_surface']['geometric_normal_z']) >= .7 and
                            not new['capsule_overlaps'] and not conflicts)
            row = {**candidate, 'new_grounding_geometry': new, 'other_npc_capsule_conflicts': conflicts,
                   'eligible_geometry_candidate': eligible, 'runtime_verified': False}
            evaluated.append(row)
            if selected is None and eligible:
                selected = row
                records[ped_id] = diagnostic
        targets.append({'ped_id': ped_id, 'previous_placement': old, 'selected': selected,
                        'candidates': evaluated, 'excluded_by_source_constraints': excluded})
        print(json.dumps({'ped_id': ped_id, 'eligible_source_points': len(choices),
                          'geometrically_clear_candidates': sum(r['eligible_geometry_candidate'] for r in evaluated),
                          'selected': selected['placement']['source_reference'] if selected else None}), flush=True)
    report = {'schema': 'rea-bully-npc-original-poi-alternative-search-v1', 'status': 'offline_geometric_candidates_runtime_pending',
              'sources': {'baseline': reference(BASELINE), 'previous_comparison': reference(COMPARISON),
                          'population': baseline['sources']['population'], 'regions': baseline['sources']['regions'],
                          'scope': baseline['sources']['scope'], 'manifest': reference(manifest_path),
                          'generator': reference(__file__), 'comparison_tool': reference(ROOT/'tools/compare_npc_grounding_policy.py'),
                          'geometry_tool': reference(ROOT/'tools/preflight_world_tour_geometry.py'),
                          'runtime_source': reference(ROOT/'unreal/Source/ReaBullyRuntime/ReaBullyAmbient.cpp')},
              'geometry_sources_rehashed': world.sources, 'targets': targets,
              'selection_policy': 'Same original file/region/zone and afternoon eligibility; original explicit faction preferred over reconstructed DEFAULT; then nearest unchanged original point with new-ray geometric slope>=0.7 and no source/cast capsule overlap',
              'summary': {'targets': len(targets), 'selected': sum(t['selected'] is not None for t in targets),
                          'unresolved_ped_ids': [t['ped_id'] for t in targets if t['selected'] is None]},
              'limitations': ['No original point coordinates, collision or runtime code are changed.',
                             'Only POI types already supported as coordinate anchors in this preview are searched; other original activities remain unimplemented.',
                             'Single deterministic selection with distinct cast, not original population scheduling.',
                             'Double-sided static geometry only; actual engine face winding and animated obstacles require the runtime tour.']}
    with args.report.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
    result = make_plan(baseline, report, args.report)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(report['summary']), flush=True)


if __name__ == '__main__':
    main()

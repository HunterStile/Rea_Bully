"""Choose original regional Hang_Out anchors with close support and clear capsules.

This expands the earlier EventDAT-only search to MainMap_hangouts.dat. The
original points and their type/time/faction fields remain unchanged. Named
selection and the bounded 40 cm support policy are project reconstruction.
"""
import argparse
from collections import Counter
import copy
import json
import math
from pathlib import Path

from catalog_population import fields, poi_points, read_trigger_blocks
from catalog_npc_locomotion import lower_priority
from compare_npc_grounding_policy import compare
from plan_town_population import select_point, inside_region
from preflight_world_tour_geometry import WorldGeometry, read
from prepare_npc_runtime_manifest import ROOT, reference, checked
from recover_npc_placements import capsule_conflicts, validate as validate_baseline
from town_npc_manifest import load_runtime_manifest

BASELINE = ROOT/'evidence/ambient-runtime-plan-v07-r6.json'
CATALOG = ROOT/'local/world-export-v07-material-recovery.json'
COLLISIONS = ROOT/'evidence/world-collisions-export-v07-r2.json'
TARGETS = (100, 101, 21, 26)
MAX_PROJECTION_CM = 40.0
POLICY = ('Original MainMap Hang_Out points in the same original region and zone; '
          'original afternoon, gender, faction and capacity eligibility; prefer explicit '
          'faction, then nearest original point; require support within 40 cm of unchanged '
          'source feet, geometric slope >=0.7, and clear source/cast capsules')


def candidates(population, scope, old):
    ped = next(p for p in population['ped_definitions'] if p['id'] == old['ped_id'])
    region = old['source_evidence']['population_region']
    zone = next(z for z in scope['zones'] if z['name'] == old['zone'])
    bounds = zone['bounds_unreal_cm']
    cache, result, rejected = {}, [], []
    for group in population['poi_groups']:
        if Path(group['source']['path']).name.lower() != 'mainmap_hangouts.dat':
            continue
        path = checked(group['source'])
        if path not in cache:
            cache[path] = read_trigger_blocks(path)
        raw = [b for b in cache[path] if b['type'] == 'POI' and b['line'] == group['line']]
        if (len(raw) != 1 or next(f['value'] for f in raw[0]['fields'] if f['key'] == 'NAME') != group['name'] or
                fields(raw[0])['AREACODE'] != group['area_code'] or poi_points(raw[0]) != group['points']):
            raise ValueError('MainMap Hang_Out source differs from catalog')
        for index, point in enumerate(group['points']):
            # Scope the diagnostic to the same verified original population polygon.
            if (group['area_code'] != old['area_code'] or point['TYPE'] != 'Hang_Out' or
                    not inside_region(point['POIPOINT'], region)):
                continue
            try:
                placement = select_point(ped, group, index, old['zone'])
                if point['MAX'] > 32:
                    raise ValueError('Abnormal original POI capacity excluded from bounded selection')
                if not all(bounds['min'][i] <= placement['position_cm'][i] <= bounds['max'][i] for i in range(3)):
                    raise ValueError('Original point lies outside the same geometry zone')
            except ValueError as exc:
                rejected.append({'source': group['source'], 'group': group['name'], 'index': index, 'reason': str(exc)})
                continue
            placement['source_evidence']['population_region'] = copy.deepcopy(region)
            placement['source_evidence']['region_membership_policy'] = old['source_evidence']['region_membership_policy']
            result.append({'placement': placement,
                           'distance_from_previous_source_cm': math.dist(placement['position_cm'], old['position_cm']),
                           'same_explicit_faction': point['PEDTYPE'] == ped['ped_type']})
    return sorted(result, key=lambda c: (not c['same_explicit_faction'], c['distance_from_previous_source_cm'],
                                         c['placement']['source_reference'])), rejected


def eligible(new, conflicts):
    surface = new['first_surface']
    return bool(surface and abs(surface['source_feet_delta_cm']) <= MAX_PROJECTION_CM and
                abs(surface['geometric_normal_z']) >= .7 and not new['capsule_overlaps'] and not conflicts)


def make_plan(baseline, report, report_path):
    result = copy.deepcopy(baseline)
    changes = {row['ped_id']: row['selected'] for row in report['targets'] if row['selected'] is not None}
    for index, old in enumerate(result['placements']):
        if old['ped_id'] not in changes:
            continue
        replacement = copy.deepcopy(changes[old['ped_id']]['placement'])
        replacement['source_evidence']['hangout_refinement'] = {
            'report': reference(report_path), 'previous_source_reference': old['source_reference'],
            'previous_original_position_cm': old['position_cm'], 'selection_policy': POLICY,
            'runtime_validated': False, 'native_dat_activation_recovered': False}
        result['placements'][index] = replacement
    if (len(result['placements']) != 31 or
            {p['ped_id'] for p in result['placements']} != {p['ped_id'] for p in baseline['placements']} or
            Counter((p['zone'], p['ped_type']) for p in result['placements']) !=
            Counter((p['zone'], p['ped_type']) for p in baseline['placements'])):
        raise ValueError('Hangout refinement changed cast, zones or faction quotas')
    result['schema'] = 'rea-bully-town-population-hangout-refinement-v1'
    result['status'] = 'original_hangout_coordinates_geometrically_screened_runtime_pending'
    result['sources'].update(baseline=reference(BASELINE), hangout_report=reference(report_path), generator=reference(__file__))
    inherited = baseline['geometric_alternative_selection']
    result['previous_geometric_alternative_selection'] = copy.deepcopy(inherited)
    result['geometric_alternative_selection'] = {
        'replaced_ped_ids': sorted(set(inherited['replaced_ped_ids']) | set(changes)),
        'unresolved_conflicts': [copy.deepcopy(row) for row in inherited['unresolved_conflicts'] if row['ped_id'] not in changes],
        'runtime_verified': False}
    result['hangout_refinement'] = {'replaced_ped_ids': sorted(changes),
        'unresolved_target_ped_ids': [row['ped_id'] for row in report['targets'] if row['selected'] is None],
        'maximum_source_projection_cm': MAX_PROJECTION_CM, 'runtime_verified': False}
    result['limitations'] += [
        'MainMap_hangouts.dat provides original nonmission Hang_Out coordinates and eligibility; its native activation/loading has not been recovered.',
        'Hang_Out named assignments, ranking and 40 cm maximum projection are project choices; own family idle plays and original social AI is not recovered.',
        'All replaced points retain original XYZ, regions and daytime faction quotas; actual floor hits, winding, streaming and animation require an engine tour.']
    return result


def validate(plan):
    for source in plan['sources'].values():
        checked(source)
    baseline = read(checked(plan['sources']['baseline']))
    validate_baseline(baseline)
    report_path = checked(plan['sources']['hangout_report'])
    report = read(report_path)
    for source in report['sources'].values():
        checked(source)
    for source in report['geometry_sources_rehashed'].values():
        checked(source)
    population = read(checked(baseline['sources']['population']))
    scope = read(checked(baseline['sources']['scope']))
    if [row['ped_id'] for row in report['targets']] != list(TARGETS):
        raise ValueError('Hangout refinement target identities changed')
    for target in report['targets']:
        old = next(p for p in baseline['placements'] if p['ped_id'] == target['ped_id'])
        choices, excluded = candidates(population, scope, old)
        if target['previous_placement'] != old or target['excluded_by_source_constraints'] != excluded:
            raise ValueError('Hangout refinement changed its original source constraints')
        if len(target['candidates']) != len(choices):
            raise ValueError('Hangout refinement omitted original eligible source points')
        for candidate, choice in zip(target['candidates'], choices):
            if any(candidate[key] != value for key, value in choice.items()):
                raise ValueError('Hangout refinement modified an original source point')
            if candidate['eligible_geometry_candidate'] != eligible(candidate['new_grounding_geometry'], candidate['other_npc_capsule_conflicts']):
                raise ValueError('Hangout refinement eligibility differs from its geometric evidence')
        expected = next((c for c in target['candidates'] if c['eligible_geometry_candidate']), None)
        if target['selected'] != expected:
            raise ValueError('Hangout refinement selection differs from declared ranking')
    if plan != make_plan(baseline, report, report_path):
        raise ValueError('Hangout population plan differs from source-bound selection report')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.report.exists():
        raise FileExistsError('New report and population plan paths required')
    lower_priority()
    baseline = read(BASELINE)
    validate_baseline(baseline)
    population = read(checked(baseline['sources']['population']))
    scope = read(checked(baseline['sources']['scope']))
    areas = {z['name']: z['area'] for z in scope['zones']}
    manifest_path = checked(baseline['sources']['npc_manifest'])
    npcs = {n['ped_id']: n for n in load_runtime_manifest(manifest_path)['npcs']}
    world = WorldGeometry(read(CATALOG), read(COLLISIONS))
    records = {p['ped_id']: compare(world, p, npcs[p['ped_id']], areas[p['zone']]) for p in baseline['placements']}
    previous_records = copy.deepcopy(list(records.values()))
    targets = []
    for ped_id in TARGETS:
        old = next(p for p in baseline['placements'] if p['ped_id'] == ped_id)
        choices, excluded = candidates(population, scope, old)
        evaluated, selected, selected_record = [], None, None
        # Evaluate all choices against the same current cast; commit once ranking completes.
        for candidate in choices:
            diagnostic = compare(world, candidate['placement'], npcs[ped_id], areas[old['zone']])
            new = diagnostic['new']
            conflicts = capsule_conflicts(diagnostic, records.values()) if new['first_surface'] else []
            row = {**candidate, 'new_grounding_geometry': new, 'other_npc_capsule_conflicts': conflicts,
                   'eligible_geometry_candidate': eligible(new, conflicts), 'runtime_verified': False}
            evaluated.append(row)
            if selected is None and row['eligible_geometry_candidate']:
                selected, selected_record = row, diagnostic
        if selected_record is not None:
            records[ped_id] = selected_record
        targets.append({'ped_id': ped_id, 'previous_placement': old, 'selected': selected,
                        'candidates': evaluated, 'excluded_by_source_constraints': excluded})
        print(json.dumps({'ped_id': ped_id, 'eligible_source_points': len(choices),
                          'geometrically_clear_candidates': sum(r['eligible_geometry_candidate'] for r in evaluated),
                          'selected': selected['placement']['source_reference'] if selected else None,
                          'source_projection_cm': selected['new_grounding_geometry']['first_surface']['source_feet_delta_cm'] if selected else None}), flush=True)
    report = {'schema': 'rea-bully-npc-original-hangout-refinement-v1', 'status': 'offline_geometric_candidates_runtime_pending',
        'sources': {'baseline': reference(BASELINE), 'catalog': reference(CATALOG), 'collisions': reference(COLLISIONS),
                    'population': baseline['sources']['population'], 'regions': baseline['sources']['regions'],
                    'scope': baseline['sources']['scope'], 'manifest': reference(manifest_path),
                    'generator': reference(__file__), 'comparison_tool': reference(ROOT/'tools/compare_npc_grounding_policy.py'),
                    'geometry_tool': reference(ROOT/'tools/preflight_world_tour_geometry.py'),
                    'cast_capsule_tool': reference(ROOT/'tools/recover_npc_placements.py'),
                    'runtime_source': reference(ROOT/'unreal/Source/ReaBullyRuntime/ReaBullyAmbient.cpp')},
        'geometry_sources_rehashed': world.sources, 'baseline_r6_diagnostics': previous_records,
        'final_diagnostics': list(records.values()), 'targets': targets, 'selection_policy': POLICY,
        'maximum_source_projection_cm': MAX_PROJECTION_CM,
        'summary': {'targets': len(targets), 'selected': sum(t['selected'] is not None for t in targets),
                    'unresolved_ped_ids': [t['ped_id'] for t in targets if t['selected'] is None]},
        'limitations': ['Original static triangle geometry only; engine winding, collision channels and rendered behavior require runtime checks.',
                       'Hang_Out source fields and exact points are verified; original DAT loading and social AI activation are not proven.',
                       'Bounded close-support selection is reconstructed; no source coordinates or collision geometry are modified.',
                       'Helper 7816 belongs only to Midway and is explicitly excluded from these MainMap NPC checks.']}
    with args.report.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
    result = make_plan(baseline, report, args.report)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    validate(result)
    print(json.dumps(report['summary']), flush=True)


if __name__ == '__main__':
    main()

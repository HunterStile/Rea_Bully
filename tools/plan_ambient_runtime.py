"""Plan seven distinct campus NPCs from verified assets and original DAT points.

Named-ped selection and reuse of a route segment are explicit reconstruction
choices. Original time thresholds, route scheduling and AI are not inferred.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
import math
from pathlib import Path

from catalog_population import fields, poi_points, read_trigger_blocks, mapping
from prepare_npc_runtime_manifest import load_runtime_manifest, reference, checked

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT/'evidence/npc-import-manifest-v06.json'
SCHOOL = ROOT/'evidence/school-ambient-source.json'
REGIONS = ROOT/'evidence/ambient-population-regions.json'
ZONES = ('tschool', 'ischool', 'isc_dorm')
CAST = {'NERD': (9, 11), 'STUDENT': (72, 73), 'BULLY': (85, 99)}


def ue_position(position):
    if len(position) != 3 or not all(math.isfinite(v) for v in position):
        raise ValueError('Invalid original XYZ position')
    return [position[0]*100, -position[1]*100, position[2]*100]


def eligible_at_poi(ped, poi):
    # The hierarchy-derived candidate list is necessary, but never substitutes
    # for independently checking the actual original IDE type and gender.
    gender = poi['gender']
    return (ped['id'] in poi['candidate_ped_ids_same_hierarchy_only']
            and poi['ped_type'] == ped['ped_type']
            and (gender == 'Both' or gender == 'Female' and ped['female']
                 or gender == 'Male' and not ped['female'])
            and poi['area_code'] == 0 and poi['maximum_raw'] >= 1)


def check_position(record):
    expected = ue_position(record['source_position'])
    if any(abs(a-b) > 1e-6 for a, b in zip(record['ue_position_cm'], expected)):
        raise ValueError('Catalog position differs from original coordinate conversion')
    return expected


def verify_poi(poi, blocks):
    matches = [b for b in blocks if b['type'] == 'POI'
               and next((f['value'] for f in b['fields'] if f['key'] == 'NAME'), None) == poi['group_name']]
    if len(matches) != 1:
        raise ValueError('Original POI group is missing or ambiguous')
    point = [p for p in poi_points(matches[0]) if p['line'] == poi['line']]
    if len(point) != 1:
        raise ValueError('Original POI point is missing or ambiguous')
    original = point[0]
    expected = {'PEDTYPE': poi['ped_type'], 'GENDER': poi['gender'], 'MAX': poi['maximum_raw'],
                'POIPOINT': poi['source_position'], 'RADIUS': poi['radius_source_m'],
                'OVERRIDELIMIT': poi['override_limit_raw'], **poi['original_flags']}
    if any(original.get(key) != value for key, value in expected.items()):
        raise ValueError('POI source fields differ from catalog')
    check_position(poi)


def verify_route(route, blocks):
    found = [b for b in blocks if b['type'] == 'PATH' and fields(b).get('PATHNAME') == route['name']]
    if len(found) != 1:
        raise ValueError('Original path is missing or ambiguous')
    original = found[0]
    points = [f for f in original['fields'] if f['key'] == 'PATHPOINT']
    if (not route['point_count_valid'] or fields(original)['NPATHPOINTS'] != len(points)
            or fields(original)['AREACODE'] != route['area_code'] or original['line'] != route['line']
            or [p['value'] for p in points] != [p['source_position'] for p in route['points']]):
        raise ValueError('Original path point order/count differs from catalog')
    for point, raw in zip(route['points'], points):
        if point['line'] != raw['line']:
            raise ValueError('Original path point line differs')
        check_position(point)


def identity(ped, definition_source):
    return {'ped_id': ped['id'], 'model': ped['model'], 'ped_type': ped['ped_type'],
            'female': ped['female'], 'definition_source': definition_source, 'definition_line': ped['line']}


def hangout(ped, poi, definition_source):
    if not eligible_at_poi(ped, poi):
        raise ValueError('Named ped does not meet original POI type/gender eligibility')
    return {'ped_id': ped['id'], 'model': ped['model'], 'ped_type': ped['ped_type'],
            'zone': 'tschool', 'area_code': 0, 'behavior': 'hangout', 'points': [],
            'position_cm': check_position(poi),
            'source_reference': f"{Path(poi['source']['path']).name}:{poi['line']}:{poi['id']}",
            'source_evidence': {'poi': copy.deepcopy(poi), 'ped_identity': identity(ped, definition_source)},
            'selection_policy': 'Reconstructed named-ped assignment to one eligible original POI; exact point, no radius offset, no original scheduler claim'}


def validate_plan(plan):
    placements = plan['placements']
    ids = [p['ped_id'] for p in placements]
    if len(ids) != len(set(ids)):
        raise ValueError('Named ped duplicated across world zones')
    if set(ids) != {9, 11, 72, 73, 85, 99, 50} or 8 in ids:
        raise ValueError('Runtime cast differs from the selected six plus separate prefect')
    ambient = [p for p in placements if p['ped_id'] != 50]
    counts = Counter(p['ped_type'] for p in ambient)
    quotas = plan['population_context']['original_quotas']
    if quotas.get('NERD') != 2 or any(count > quotas.get(kind, 0) for kind, count in counts.items()) or len(ambient) > quotas['TOTAL']:
        raise ValueError('Original SchoolArea DAY_OUTOFCLASS population cap exceeded')
    for a in placements:
        if a['zone'] not in ZONES:
            raise ValueError('NPC assigned outside the three loaded zones')
        for b in placements:
            if a['ped_id'] < b['ped_id'] and a['zone'] == b['zone'] and math.dist(a['position_cm'], b['position_cm']) < 70:
                raise ValueError('NPC initial positions overlap the 33cm runtime capsules')
    occupancy = Counter(p['source_evidence']['poi']['id'] for p in placements if p['behavior'] == 'hangout')
    for p in placements:
        if p['behavior'] == 'hangout':
            poi = p['source_evidence']['poi']
            if occupancy[poi['id']] > poi['maximum_raw']:
                raise ValueError('Original POI maximum exceeded')
            if poi['radius_source_m'] == 0 and occupancy[poi['id']] > 1:
                raise ValueError('Zero-radius POI cannot place two peds at the same point')
    return True


def build():
    manifest = load_runtime_manifest(MANIFEST)
    if {n['ped_id'] for n in manifest['npcs']} != {8, 50, 9, 11, 72, 73, 85, 99}:
        raise ValueError('Runtime plan requires the eight-NPC promoted manifest')
    school = json.loads(SCHOOL.read_text())
    population = json.loads(checked(school['source_catalog']).read_text())
    checked(school['area_enum_source'])
    checked(population['ped_definitions_source'])
    checked(population['ped_population']['source'])
    for ref in school['patrol_activation_evidence']+school['poi_global_load_evidence']:
        checked(ref)
    regions = json.loads(REGIONS.read_text())
    region = next(r for r in regions['regions'] if r['name'] == 'SchoolArea')
    blocks_cache = {}

    def blocks(ref):
        path = checked(ref)
        if path not in blocks_cache:
            blocks_cache[path] = read_trigger_blocks(path)
        return blocks_cache[path]

    region_source = [b for b in blocks(region['source']) if b['type'] == 'TRIGGER' and b['line'] == region['line']]
    if len(region_source) != 1 or fields(region_source[0])['TRIGGERNAME'] != 'SchoolArea':
        raise ValueError('Original SchoolArea population trigger differs')
    quota = mapping(region_source[0]['fields'])['POPULATIONDATA']['POPULATION_DAY']
    if quota != region['population']['POPULATION_DAY']:
        raise ValueError('Original SchoolArea daytime quota differs')
    peds = {p['id']: p for p in school['ped_candidates']}
    for npc in manifest['npcs']:
        ped = peds[npc['ped_id']]
        if ped['model'] != npc['model'] or npc.get('ped_type', ped['ped_type']) != ped['ped_type']:
            raise ValueError('Imported NPC identity differs from original IDE candidate')
    pois = {p['id']: p for p in school['poi_points']}
    routes = {p['name']: p for p in school['patrol_paths']}
    assignments = [(9, 'tschool_nerds_0'), (11, 'tschool_nerds_1'), (72, 'tschool_students_1'),
                   (85, 'tschool_bullies_1'), (99, 'tschool_bullies_2')]
    placements = []
    for ped_id, poi_id in assignments:
        poi = pois[poi_id]
        verify_poi(poi, blocks(poi['source']))
        placements.append(hangout(peds[ped_id], poi, population['ped_definitions_source']))
    students = pois['tschool_students_1']
    if students['radius_source_m'] != 0 or students['original_flags']['USERADIUS'] is not False:
        raise ValueError('Revisit student placement: original radius policy changed')
    if not eligible_at_poi(peds[73], students):
        raise ValueError('Trevor is not eligible for the original STUDENT/Both POI')
    walk = routes['SGD_Prefect2']
    verify_route(walk, blocks(walk['source']))
    indices = [10, 11]
    selected = [walk['points'][i] for i in indices]
    distance = math.dist(selected[0]['source_position'][:2], students['source_position'][:2])
    if distance > 15:
        raise ValueError('Reconstructed student route is no longer near its eligible POI')
    placements.append({'ped_id': 73, 'model': peds[73]['model'], 'ped_type': 'STUDENT',
        'zone': 'tschool', 'area_code': 0, 'behavior': 'reconstructed_walk',
        'points': [check_position(p) for p in selected], 'position_cm': check_position(selected[0]),
        'source_reference': f"{Path(walk['source']['path']).name}:{walk['line']}:SGD_Prefect2[10:12]",
        'source_evidence': {'route_source': walk['source'], 'route_name': walk['name'],
            'route_line': walk['line'], 'original_point_indices': indices, 'original_points': copy.deepcopy(selected),
            'route_original_candidate_ids': walk['candidate_ped_ids_same_hierarchy_only'],
            'route_is_originally_a_prefect_route': True, 'student_route_assignment_is_reconstructed': True,
            'eligibility_poi': copy.deepcopy(students), 'ped_identity': identity(peds[73], population['ped_definitions_source']),
            'distance_from_eligible_poi_metres': distance},
        'selection_policy': 'Trevor type/gender eligibility comes from the STUDENT/Both POI; reuse two consecutive original prefect-route coordinates for project walking, not original student route eligibility or scheduling'})
    patrol = routes['SGD_Prefect1']
    verify_route(patrol, blocks(patrol['source']))
    if peds[50]['ped_type'] != 'PREFECT' or 50 not in patrol['candidate_ped_ids_same_hierarchy_only']:
        raise ValueError('Seth does not meet original prefect-path candidate filtering')
    placements.append({'ped_id': 50, 'model': peds[50]['model'], 'ped_type': 'PREFECT',
        'zone': 'tschool', 'area_code': 0, 'behavior': 'prefect_patrol',
        'points': [check_position(p) for p in patrol['points']], 'position_cm': check_position(patrol['points'][0]),
        'source_reference': f"{Path(patrol['source']['path']).name}:{patrol['line']}:SGD_Prefect1",
        'source_evidence': {'route': copy.deepcopy(patrol), 'activation_sources': school['patrol_activation_evidence'],
                            'ped_identity': identity(peds[50], population['ped_definitions_source'])},
        'selection_policy': 'Choose Seth once from original prefect candidates on SGD_Prefect1; separate scripted-patrol reconstruction, outside SchoolArea ambient quota; route loop/waits are not inferred'})
    result = {'schema': 'rea-bully-ambient-runtime-plan-v1', 'status': 'source_bound_reconstructed_spawn_plan',
        'ready_for_world_builder': True, 'runtime_spawn_validated': False, 'original_scheduler_recovered': False,
        'sources': {'npc_manifest': reference(MANIFEST), 'school_ambient': reference(SCHOOL),
                    'population_regions': reference(REGIONS), 'world_population': school['source_catalog'],
                    'generator': reference(__file__)},
        'loaded_zones': list(ZONES), 'placements': placements,
        'population_context': {'region': 'SchoolArea', 'source': region['source'], 'line': region['line'],
            'period_label': 'DAY_OUTOFCLASS', 'source_field': 'POPULATION_DAY', 'original_quotas': quota,
            'selected_ambient_counts': dict(Counter(p['ped_type'] for p in placements if p['ped_id'] != 50)),
            'selected_ambient_total': 6, 'unfilled_original_slots': quota['TOTAL']-6,
            'separate_prefect_patrol_count': 1, 'clock_thresholds_and_overlapping_region_precedence': 'not_recovered'},
        'imported_not_spawned': [{'ped_id': 8, 'model': peds[8]['model'],
            'reason': 'SchoolArea NERD quota2 is already filled by distinct Cornelius and Donald'}],
        'zone_counts': {zone: sum(p['zone'] == zone for p in placements) for zone in ZONES},
        'interior_policy': 'No duplicate named peds are created in ischool or isc_dorm; this compact source catalog supplies MainMap campus POIs/routes only.',
        'selection_policy': 'Fixed reconstructed campus cast: two nerds, two students, two bullies and one separate prefect. Prefer original tschool_bullies POIs with AFTERNOON flag; no faction inferred from group names.',
        'limitations': ['This is an explicit project spawn selection, not the original randomizer or clock scheduler.',
            'Original POI flags, MAX and override values remain evidence; only the stated population caps and initial placement policy are applied.',
            'Zero-radius student POI receives one stationary NPC. Trevor uses a declared reconstruction on original route coordinates.',
            'Original PATH WAITTIME, PATROLTYPE and AreaInitPatrolPath arguments are not interpreted as implemented AI semantics.',
            'Unreal terrain grounding, collision, walking and visual placement must be checked after world construction.']}
    validate_plan(result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'evidence/ambient-runtime-plan-v06.json')
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('New ambient runtime plan required')
    result = build()
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({'output': str(args.output), 'zone_counts': result['zone_counts'],
                      'ped_ids': [p['ped_id'] for p in result['placements']]}))

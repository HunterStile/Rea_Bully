"""Place a distinct daytime cast at original nonmission POI/path coordinates.

Outdoor Spectator/Wall points supply standing coordinates only. Their original
event actions are not executed: the preview uses the ped's own family idle.
"""
import argparse
from collections import Counter
import copy
import json
import math
from pathlib import Path

from catalog_population import fields, mapping, poi_points, read_trigger_blocks
from plan_ambient_runtime import ue_position
from plan_world_population import build as school_plan, choose_poi, activation
from prepare_npc_runtime_manifest import ROOT, checked, reference
from town_npc_manifest import load_runtime_manifest

BASELINE = ROOT/'evidence/ambient-runtime-plan-v07-r4.json'
POPULATION = ROOT/'evidence/world-population.json'
REGIONS = ROOT/'evidence/ambient-population-regions.json'
SCOPE = ROOT/'evidence/world-scope-v07.json'
OUTDOOR = (
    (76, 'eventsDowntown.dat', 'Spectator', 0, 'tbusines', 'DownTown'),
    (77, 'eventsDowntown.dat', 'Spectator', 1, 'tbusines', 'DownTown'),
    (100, 'eventsRichArea.dat', 'Spectator', 0, 'trich', 'RichArea'),
    (101, 'eventsRichArea.dat', 'Spectator', 5, 'trich', 'RichArea'),
    (21, 'eventsPoorArea.dat', 'Spectator', 0, 'tbusines', 'PoorArea'),
    (26, 'eventsPoorArea.dat', 'Spectator', 1, 'tbusines', 'PoorArea'),
    (42, 'eventsIndustrialArea.dat', 'Smoking', 3, 'tindust', 'IndustrialArea_DropOutEnclave'),
    (43, 'eventsIndustrialArea.dat', 'Smoking', 6, 'tindust', 'IndustrialArea_DropOutEnclave'))


def inside_region(position, region):
    if (region['source_yaw_pitch_roll'] != [0., 0., 0.] or region['source_yaw'] != 0 or
            not region['perimeter_original']['count_valid']):
        raise ValueError('Only verified unrotated original population perimeters are supported')
    x, y = [position[i]-region['source_position'][i] for i in range(2)]
    polygon = region['perimeter_original']['source_local_xy']
    inside = False
    for a, b in zip(polygon, polygon[1:]+polygon[:1]):
        if ((a[1] > y) != (b[1] > y) and
                x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]):
            inside = not inside
    return inside


def select_point(ped, group, index, zone, *, outdoor=False):
    point = group['points'][index]
    if point['TYPE'] not in ({'Spectator', 'Wall'} if outdoor else {'Hang_Out'}):
        raise ValueError('Unsupported original point activity for this preview')
    if not point['AFTERNOON'] or point['MAX'] < 1:
        raise ValueError('Original POI excludes the selected afternoon context')
    if point['GENDER'] not in ('Both', 'Female' if ped['female'] else 'Male'):
        raise ValueError('Original POI gender excludes the pedestrian')
    if point['PEDTYPE'] not in ('DEFAULT', ped['ped_type']):
        raise ValueError('Original explicit POI faction differs from pedestrian')
    return {'ped_id': ped['id'], 'model': ped['model'], 'ped_type': ped['ped_type'],
            'zone': zone, 'area_code': group['area_code'],
            'behavior': 'original_point_family_idle_preview' if outdoor else 'hangout',
            'points': [], 'position_cm': ue_position(point['POIPOINT']),
            'source_reference': f"{Path(group['source']['path']).name}:{point['line']}:{group['name']}[{index}]",
            'source_evidence': {'original_poi_group': copy.deepcopy(group), 'point_index': index,
                'original_ped': {k: ped[k] for k in ('id', 'model', 'female', 'ped_type', 'line')},
                'default_category_selection_reconstructed': point['PEDTYPE'] == 'DEFAULT',
                'original_event_action_activated': False},
            'selection_policy': ('Named assignment at an original nonmission POI; own family idle only, original Spectator/Wall action and scheduler unimplemented'
                                 if outdoor else 'Named selection meeting original POI type/gender/time; original scheduler not recovered')}


def build(manifest_path):
    manifest_path = Path(manifest_path)
    manifest = load_runtime_manifest(manifest_path)
    imported = {row['ped_id']: row for row in manifest['npcs']}
    baseline = json.loads(BASELINE.read_text())
    if baseline != school_plan(checked(baseline['sources']['npc_manifest'])):
        raise ValueError('Baseline population differs from its original source plan')
    population = json.loads(POPULATION.read_text())
    checked(population['ped_definitions_source'])
    peds = {row['id']: row for row in population['ped_definitions']}
    regions = json.loads(REGIONS.read_text())
    checked(regions['global_load_provenance'])
    scope = json.loads(SCOPE.read_text())
    by_zone = {row['name']: row for row in scope['zones']}
    cache = {}

    def blocks(ref):
        path = checked(ref)
        if path not in cache:
            cache[path] = read_trigger_blocks(path)
        return cache[path]

    def group(filename, name):
        matches = [g for g in population['poi_groups'] if Path(g['source']['path']).name == filename and g['name'] == name]
        if len(matches) != 1:
            raise ValueError('Original POI group is ambiguous')
        g = matches[0]
        raw = [b for b in blocks(g['source']) if b['type'] == 'POI' and b['line'] == g['line']]
        if (len(raw) != 1 or next(f['value'] for f in raw[0]['fields'] if f['key'] == 'NAME') != name or fields(raw[0])['AREACODE'] != g['area_code'] or
                poi_points(raw[0]) != g['points']):
            raise ValueError('Original POI source fields differ from catalog')
        return g

    def region(name):
        r = next(row for row in regions['regions'] if row['name'] == name)
        raw = [b for b in blocks(r['source']) if b['type'] == 'TRIGGER' and b['line'] == r['line']]
        if len(raw) != 1:
            raise ValueError('Original regional population trigger missing')
        f = mapping(raw[0]['fields'])
        if (f['TRIGGERNAME'] != name or f['AREACODE'] != r['area_code'] or
                f['POSITION'] != r['source_position'] or f['YAWPITCHROLL'] != r['source_yaw_pitch_roll'] or
                f['YAW'] != r['source_yaw'] or f['POPULATIONDATA'] != r['population']):
            raise ValueError('Original regional quota/transform changed')
        perimeter = next(b for b in blocks(r['source']) if b['line'] == r['perimeter_original']['line'])
        xs = [v['value'] for v in perimeter['fields'] if v['key'] == 'PERIMETERPOINTX']
        ys = [v['value'] for v in perimeter['fields'] if v['key'] == 'PERIMETERPOINTY']
        if [list(v) for v in zip(xs, ys)] != r['perimeter_original']['source_local_xy']:
            raise ValueError('Original regional polygon changed')
        return r

    placements = copy.deepcopy(baseline['placements'])
    placements.extend([
        choose_poi(peds[10], group('eventsSchoolHalls.dat', 'Student_Hangouts'), 1),
        choose_poi(peds[69], group('eventsSchoolHalls.dat', 'Student_Hangouts'), 9, permit_default=True),
        select_point(peds[147], group('tschool_hangouts.dat', 'tschool_jocks'), 7, 'tschool')])
    route = next(r for r in population['paths'] if r['name'] == '1F_Prefect02' and r['area_code'] == 2)
    raw = [b for b in blocks(route['source']) if b['type'] == 'PATH' and b['line'] == route['line']]
    if len(raw) != 1 or raw[0]['fields'] != route['fields'] or not route['point_count_valid']:
        raise ValueError('Original extra prefect route changed')
    source_points = []
    for field in route['fields']:
        if field['key'] == 'PATHPOINT':
            source_points.append({'source_position': field['value'], 'ue_position_cm': ue_position(field['value']), 'line': field['line']})
        elif field['key'] == 'WAITTIME':
            source_points[-1]['WAITTIME'] = field['value']
    script = (ROOT/'local/decompiled/SchoolHallways.lua').read_text()
    if '_1F_PREFECT02' not in script:
        raise ValueError('Hallway script does not reference the extra prefect route')
    placements.append({'ped_id': 49, 'model': peds[49]['model'], 'ped_type': 'PREFECT',
        'zone': 'ischool', 'area_code': 2, 'behavior': 'prefect_patrol',
        'points': [p['ue_position_cm'] for p in source_points], 'position_cm': source_points[0]['ue_position_cm'],
        'source_reference': f"PrefectsMainBuilding.dat:{route['line']}:1F_Prefect02",
        'source_evidence': {'route': {**copy.deepcopy(route), 'points': source_points},
                            'activation': activation('SchoolHallways', 'PrefectsMainBuilding.DAT')},
        'selection_policy': 'Distinct original prefect at original route; traversal/waits reconstructed, original response AI incomplete'})
    contexts = []
    for ped_id, filename, name, index, zone, region_name in OUTDOOR:
        g, r = group(filename, name), region(region_name)
        if g['area_code'] != 0 or r['area_code'] != 0:
            raise ValueError('Outdoor assignment changed original area')
        placement = select_point(peds[ped_id], g, index, zone, outdoor=True)
        if not inside_region(g['points'][index]['POIPOINT'], r):
            raise ValueError('Original POI lies outside selected regional polygon')
        zone_source = by_zone[zone]
        checked(zone_source['source_ipb'])
        bounds = zone_source['bounds_unreal_cm']
        if zone_source['area'] != 'MainMap' or not all(bounds['min'][i] <= placement['position_cm'][i] <= bounds['max'][i] for i in range(3)):
            raise ValueError('Original POI is outside the assigned source zone bounds')
        placement['source_evidence']['population_region'] = copy.deepcopy(r)
        placement['source_evidence']['region_membership_policy'] = 'Original unrotated local XY polygon translated by original origin; runtime overlap priority unresolved'
        placements.append(placement)
        if region_name not in {c['name'] for c in contexts}:
            contexts.append({'name': region_name, 'period': 'POPULATION_DAY', 'source': r['source'],
                             'original_quotas': r['population']['POPULATION_DAY']})
    ids = [p['ped_id'] for p in placements]
    if len(ids) != len(set(ids)) or set(ids) != set(imported):
        raise ValueError('Expanded cast lost or duplicated a measured named pedestrian')
    for p in placements:
        if p['model'] != imported[p['ped_id']]['model'] or p['ped_type'] != peds[p['ped_id']]['ped_type']:
            raise ValueError('Placement identity differs from the measured original skin')
        for q in placements:
            if p['ped_id'] < q['ped_id'] and p['area_code'] == q['area_code'] and math.dist(p['position_cm'], q['position_cm']) < 70:
                raise ValueError('Expanded pedestrian initial capsules overlap')
    for context in contexts:
        selected = [p for p in placements if p['source_evidence'].get('population_region', {}).get('name') == context['name']]
        counts = Counter(p['ped_type'] for p in selected)
        quota = context['original_quotas']
        if len(selected) > quota['TOTAL'] or any(n > quota.get(k, 0) for k, n in counts.items()):
            raise ValueError('Original regional daytime population quota exceeded')
    for context in baseline['area_population_contexts']:
        selected = [p for p in placements if p['area_code'] == context['area_code'] and p['behavior'] != 'prefect_patrol']
        counts, quota = Counter(p['ped_type'] for p in selected), context['original_quotas']
        if len(selected) > quota['TOTAL'] or any(n > quota.get(k, 0) for k, n in counts.items()):
            raise ValueError('Original interior daytime population quota exceeded')
    campus = [p for p in placements if p['zone'] == 'tschool' and p['behavior'] != 'prefect_patrol']
    campus_quota = region('SchoolArea')['population']['POPULATION_DAY']
    if any(n > campus_quota.get(k, 0) for k, n in Counter(p['ped_type'] for p in campus).items()) or len(campus) > campus_quota['TOTAL']:
        raise ValueError('Original campus daytime population quota exceeded')
    return {'schema': 'rea-bully-town-population-plan-v1', 'status': 'original_coordinates_reconstructed_daytime_preview',
            'ready_for_world_builder': True, 'runtime_spawn_validated': False, 'original_scheduler_recovered': False,
            'sources': {'baseline': reference(BASELINE), 'npc_manifest': reference(manifest_path),
                        'population': reference(POPULATION), 'regions': reference(REGIONS), 'scope': reference(SCOPE),
                        'generator': reference(__file__)},
            'placements': placements, 'regional_population_contexts': contexts,
            'area_population_contexts': baseline['area_population_contexts'],
            'zone_counts': dict(Counter(p['zone'] for p in placements)), 'imported_not_spawned': [],
            'limitations': ['All 31 named identities are unique; selection and fixed daytime scheduling are project policies.',
                'Spectator/Wall anchors provide exact coordinates only. Preview plays each own family idle; original spectator, smoking and social scenarios are not activated.',
                'Original regional polygon/quotas constrain assignments; native region overlap and clock transitions remain unresolved.',
                'CPU-verified skin/clip assets still require Unreal import, per-area floor/capsule and rendered behavior checks.',
                'Walking prefect paths use reconstructed traversal. No missions are started.']}


def validate(plan):
    for ref in plan['sources'].values():
        checked(ref)
    if plan != build(checked(plan['sources']['npc_manifest'])):
        raise ValueError('Town population plan differs from its verified original source construction')
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--npc-manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('New population plan required')
    result = build(args.npc_manifest)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({'placements': len(result['placements']), 'zones': result['zone_counts']}))

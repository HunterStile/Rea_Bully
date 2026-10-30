"""Extend the campus cast into original hall/library POIs and prefect routes.

All identities remain unique. DEFAULT POI category selection is explicitly a
project policy bounded by original regional quotas, not recovered native AI.
"""
import argparse
from collections import Counter
import copy
import json
import math
from pathlib import Path
import re

from catalog_population import read_trigger_blocks, fields, poi_points, read_population, POP_COLUMNS
from plan_ambient_runtime import ue_position
from prepare_npc_runtime_manifest import ROOT, checked, reference
from world_npc_manifest import load_runtime_manifest

AREAS = {2: ('School_Hallways', 'ischool', 'SchoolHallways'), 9: ('Library', 'isc_lib', 'Library')}
QUOTA_KEYS = ['TOTAL', 'PREFECT', 'NERD', 'JOCK', 'DROPOUT', 'GREASER', 'PREPPY',
              'STUDENT', 'COP', 'TEACHER', 'TOWNPERSON', 'SHOPKEEP', 'BULLY']


def activation(script, dat):
    """Retain a concrete DATLoad instruction witness from both recovered views."""
    lua = ROOT/f'local/decompiled/{script}.lua'
    asm = ROOT/f'local/disassembled/{script}.luasm'
    raw = ROOT/f'local/scripts/{script}.lur'
    text = lua.read_text()
    pattern = r'(L\d+_\d+) = DATLoad\s+(L\d+_\d+) = "'+re.escape(dat)+r'"\s+(L\d+_\d+) = (\d+)\s+\1\(\2, \3\)'
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        raise ValueError('No exact original DATLoad call for '+dat)
    assembly = asm.read_text()
    constant = re.search(r'(?m)^\.constant\s+(k\d+)\s+"'+re.escape(dat)+r'"\s*$', assembly, re.IGNORECASE)
    datload = re.search(r'(?m)^\.constant\s+(k\d+)\s+"DATLoad"\s*$', assembly)
    if not constant or not datload or not re.search(
            r'getglobal\s+r0\s+'+datload[1]+r'\b[^\n]*\nloadk\s+r1\s+'+constant[1]+r'\b[^\n]*\nloadk\s+r2[^\n]*\ncall\s+r0\s+3\s+1', assembly):
        raise ValueError('Original bytecode disassembly does not corroborate DATLoad')
    return {'lua': reference(lua), 'luasm': reference(asm), 'lur': reference(raw),
            'lua_line': text[:match.start()].count('\n')+1, 'call': match[0],
            'dat': dat, 'argument_raw': int(match[4])}


def choose_poi(ped, group, point_index, *, permit_default=False):
    point = group['points'][point_index]
    if point['TYPE'] != 'Hang_Out' or point['MAX'] < 1 or not point['AFTERNOON']:
        raise ValueError('POI is not an afternoon nonempty Hang_Out point')
    if point['GENDER'] not in ('Both', 'Female' if ped['female'] else 'Male'):
        raise ValueError('Original POI gender excludes this pedestrian')
    reconstructed = point['PEDTYPE'] == 'DEFAULT' and permit_default
    if point['PEDTYPE'] != ped['ped_type'] and not reconstructed:
        raise ValueError('Original explicit POI type differs from pedestrian')
    return {'ped_id': ped['id'], 'model': ped['model'], 'ped_type': ped['ped_type'],
            'zone': AREAS[group['area_code']][1], 'area_code': group['area_code'],
            'behavior': 'hangout', 'points': [], 'position_cm': ue_position(point['POIPOINT']),
            'source_reference': f"{Path(group['source']['path']).name}:{point['line']}:{group['name']}[{point_index}]",
            'source_evidence': {'poi_group': copy.deepcopy(group), 'point_index': point_index,
                'original_ped': {k: ped[k] for k in ('id', 'model', 'female', 'ped_type', 'line')},
                'default_poi_assignment_reconstructed': reconstructed},
            'selection_policy': ('Project choice for a DEFAULT point, constrained by original area/type/gender quotas; DEFAULT wildcard semantics not recovered'
                if reconstructed else 'Project named selection meeting original exact POI type/gender; original scheduler not recovered')}


def validate(plan):
    placements = plan['placements']
    ids = [p['ped_id'] for p in placements]
    if len(ids) != len(set(ids)):
        raise ValueError('A named pedestrian is duplicated across zones')
    for p in placements:
        if not all(math.isfinite(v) for v in p['position_cm']):
            raise ValueError('Nonfinite pedestrian location')
        proof = p['source_evidence']
        if 'poi_group' in proof:
            expected = choose_poi(proof['original_ped'], proof['poi_group'], proof['point_index'],
                                  permit_default=proof['default_poi_assignment_reconstructed'])
            if any(p[k] != expected[k] for k in ('position_cm', 'ped_id', 'model', 'ped_type', 'area_code', 'zone')):
                raise ValueError('Placement differs from original point/identity')
        for q in placements:
            if p['ped_id'] < q['ped_id'] and p['area_code'] == q['area_code']:
                # Different floors may share XY, but capsules must not overlap.
                if math.dist(p['position_cm'], q['position_cm']) < 70:
                    raise ValueError('Pedestrian initial capsules overlap')
    for area in plan['area_population_contexts']:
        selected = [p for p in placements if p['area_code'] == area['area_code'] and p['behavior'] != 'prefect_patrol']
        counts = Counter(p['ped_type'] for p in selected)
        quotas = area['original_quotas']
        if len(selected) > quotas['TOTAL'] or any(n > quotas.get(k, 0) for k, n in counts.items()):
            raise ValueError('Original area population quota exceeded')
    return True


def build(manifest_path):
    manifest = load_runtime_manifest(manifest_path)
    baseline_path = ROOT/'evidence/ambient-runtime-plan-v06.json'
    baseline = json.loads(baseline_path.read_text())
    for ref in baseline['sources'].values():
        checked(ref)
    population_path = ROOT/'evidence/world-population.json'
    population = json.loads(population_path.read_text())
    checked(population['ped_definitions_source'])
    peds = {p['id']: p for p in population['ped_definitions']}
    imported = {p['ped_id'] for p in manifest['npcs']}
    pop = read_population(checked(population['ped_population']['source']), len(POP_COLUMNS))
    if (any(value != population['ped_population'][key] for key, value in pop.items())
            or population['ped_population']['columns'] != POP_COLUMNS):
        raise ValueError('Original PedPop quota source differs from catalog')
    coverage_path = ROOT/'evidence/world-coverage.json'
    coverage = json.loads(coverage_path.read_text())
    for area_name, zone, script in AREAS.values():
        z = next(z for z in coverage['zones'] if z['name'] == zone)
        if area_name not in {r['area'] for r in z['classification_sources']}:
            raise ValueError('Original zone-to-area mapping differs')
    cache = {}
    def verified_blocks(ref):
        path = checked(ref)
        if path not in cache:
            cache[path] = read_trigger_blocks(path)
        return cache[path]
    def group(dat, name):
        rows = [g for g in population['poi_groups'] if Path(g['source']['path']).name == dat and g['name'] == name]
        if len(rows) != 1:
            raise ValueError('Ambiguous original POI group')
        g = rows[0]
        raw = [b for b in verified_blocks(g['source']) if b['type'] == 'POI' and b['line'] == g['line']]
        if (len(raw) != 1 or next(f['value'] for f in raw[0]['fields'] if f['key'] == 'NAME') != name
                or fields(raw[0])['AREACODE'] != g['area_code']
                or poi_points(raw[0]) != g['points']):
            raise ValueError('Original POI fields differ from catalog')
        return g
    placements = copy.deepcopy(baseline['placements'])
    # One occupant per exact original point. No radius offsets or duplicated
    # identities are invented to inflate population counts.
    assignments = [(8, 'eventsLibrary.dat', 'Hangouts', 0, False),
                   (7, 'eventsSchoolHalls.dat', 'More_Hangouts', 0, False),
                   (102, 'eventsSchoolHalls.dat', 'More_Hangouts', 2, False)]
    assignments += [(ped, 'eventsSchoolHalls.dat', 'Student_Hangouts', point, True)
                    for ped, point in zip((66, 70, 134, 139, 142), (0, 2, 3, 4, 8))]
    assignments += [(145, 'eventsSchoolHalls.dat', 'More_Hangouts', 5, True),
                    (146, 'eventsSchoolHalls.dat', 'More_Hangouts', 6, True)]
    activation_sources = {dat: activation(script, dat) for dat, script in
                          [('eventsLibrary.DAT', 'Library'), ('eventsSchoolHalls.DAT', 'SchoolHallways'),
                           ('PrefectsMainBuilding.DAT', 'SchoolHallways')]}
    for ped_id, dat, name, index, allow in assignments:
        if ped_id not in imported:
            raise ValueError('Requested pedestrian has no measured import manifest entry')
        placements.append(choose_poi(peds[ped_id], group(dat, name), index, permit_default=allow))
    for ped_id, route_name in ((51, '1F_Prefect01'), (52, '2F_Prefect01')):
        if ped_id not in imported or peds[ped_id]['ped_type'] != 'PREFECT':
            raise ValueError('Verified prefect identity required')
        route = next(r for r in population['paths'] if r['name'] == route_name and r['area_code'] == 2)
        original = [b for b in verified_blocks(route['source']) if b['type'] == 'PATH' and b['line'] == route['line']]
        if len(original) != 1 or original[0]['fields'] != route['fields'] or not route['point_count_valid']:
            raise ValueError('Original patrol fields/order changed')
        points = []
        for field in route['fields']:
            if field['key'] == 'PATHPOINT':
                points.append({'source_position': field['value'], 'ue_position_cm': ue_position(field['value']), 'line': field['line']})
            elif field['key'] == 'WAITTIME':
                points[-1]['WAITTIME'] = field['value']
        script = (ROOT/'local/decompiled/SchoolHallways.lua').read_text()
        token = '_'+route_name.upper()
        if not re.search(r'AreaInitPatrolPath\s+L1_2 = PATH\s+L1_2 = L1_2\.'+token+r'\b', script):
            raise ValueError('Original hallway script does not initialize this route')
        placements.append({'ped_id': ped_id, 'model': peds[ped_id]['model'], 'ped_type': 'PREFECT',
            'zone': 'ischool', 'area_code': 2, 'behavior': 'prefect_patrol',
            'points': [p['ue_position_cm'] for p in points], 'position_cm': points[0]['ue_position_cm'],
            'source_reference': f"PrefectsMainBuilding.dat:{route['line']}:{route_name}",
            'source_evidence': {'route': {**copy.deepcopy(route), 'points': points},
                                'activation': activation_sources['PrefectsMainBuilding.DAT']},
            'selection_policy': 'Distinct original prefect chosen for source initialized route outside mission6_03; ping-pong traversal/waits reconstructed, original docker and response AI incomplete'})
    contexts = []
    for code, (area_name, _, _) in AREAS.items():
        area = next(a for a in pop['areas'] if a['area'] == area_name)
        period = next(p for p in area['periods'] if p['period'] == 'DAY_OUTOFCLASS')
        contexts.append({'area_code': code, 'area_name': area_name, 'period': period['period'],
                         'line': period['line'], 'source': pop['source'],
                         'original_quotas': dict(zip(QUOTA_KEYS, period['values']))})
    result = {'schema': 'rea-bully-world-population-plan-v1', 'status': 'source_bound_reconstructed_spawn_plan',
        'ready_for_world_builder': True, 'runtime_spawn_validated': False, 'original_scheduler_recovered': False,
        'sources': {'baseline': reference(baseline_path), 'npc_manifest': reference(manifest_path),
                    'population': reference(population_path), 'coverage': reference(coverage_path), 'generator': reference(__file__)},
        'placements': placements, 'area_population_contexts': contexts, 'activation_evidence': activation_sources,
        'zone_counts': dict(Counter(p['zone'] for p in placements)),
        'imported_not_spawned': sorted(imported-{p['ped_id'] for p in placements}),
        'pending_candidates': manifest.get('pending_candidates', []),
        'limitations': ['All positions are original POI/path points; named selection, DEFAULT category mapping and traversal are project policies.',
            'Only a fixed DAY_OUTOFCLASS/AFTERNOON context is selected; the original clock, overlap precedence and event scheduler remain incomplete.',
            'Scripted prefect patrols are tracked separately from the ambient PedPop prefect quota, as in the campus reconstruction.',
            'Source POI activity clips beyond family idle, interactions and original AI are not implemented here.',
            'Library and hall placement/floor/navigation require runtime verification; an import-ready plan is not runtime proof.',
            'Further source areas/types and missing skins remain required for the full-world objective.']}
    validate(result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=ROOT/'evidence/npc-import-manifest-v07-r3.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('New population plan required')
    result = build(args.manifest)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({'placements': len(result['placements']), 'zones': result['zone_counts']}))

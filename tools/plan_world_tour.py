"""Choose original coordinates for a diagnostic traversal of every recovered area.

The selection/order is reconstructed. Source minigame points supply coordinates
only: this tool neither activates minigames nor asserts a walkable physics floor.
"""
import argparse
from collections import Counter
import json
from pathlib import Path

from audit_world_scope import ROOT, read, reference, verify
from catalog_population import fields, read_trigger_blocks, ue_position
from plan_world_doors import travel_yaw


EXTRA_POINTS = (
    ('TGOKart', 'GoKart.dat', 'GoKart_Ped1'),
    ('Midway', 'MGBaseballToss.dat', 'BaseballToss_Minigame_Entry'),
    ('Midway', 'MGShooting.dat', 'MGS_PlayerEnter'),
    ('iMGRaceA', 'ArcadeRace.dat', 'Arc1_RacePos1'),
    ('iMGRaceB', 'ArcadeRace2.dat', 'Arc2_RacePos1'),
    ('iMGRaceC', 'ArcadeRace3.dat', 'ARC3_RacePos1'),
)


def original_extra_points(area_codes):
    directory = ROOT/'local/extracted/DAT/Trigger'
    manifest_path = directory/'manifest.json'
    manifest = read(manifest_path)
    records = {e['name'].casefold(): e for e in manifest['entries']}
    selected = []
    for area, filename, name in EXTRA_POINTS:
        entry = records[filename.casefold()]
        path = directory/entry['path']
        if not entry['verified']:
            raise ValueError('DAT extraction is not verified')
        verify({'path': str(path), 'sha256': entry['extracted_sha256']})
        matches = [b for b in read_trigger_blocks(path) if b['type'] == 'POINT' and
                   str(fields(b).get('POINTNAME', '')).casefold() == name.casefold()]
        if len(matches) != 1:
            raise ValueError('Expected exactly one original point list: ' + name)
        block = matches[0]
        if fields(block)['AREACODE'] != area_codes[area]:
            raise ValueError('Original point area differs from intended zone')
        points = [f for f in block['fields'] if f['key'] == 'POINTPOINT']
        if len(points) != fields(block)['NPOINTPOINTS'] or len(points) != 1:
            raise ValueError('Expected one unambiguous source point: ' + name)
        point = points[0]
        selected.append({'name': area+'/'+name, 'area': area, 'position_cm': ue_position(point['value']),
            'source': {'kind': 'original_DAT_point_for_diagnostic_travel', 'file': reference(path),
                'manifest': reference(manifest_path), 'point_name': fields(block)['POINTNAME'],
                'line': point['line'], 'area_code': fields(block)['AREACODE'],
                'source_position_game_m': point['value'], 'source_role': 'pedestrian_or_minigame_entry',
                'original_free_roam_arrival_proven': False}, 'runtime_ground_verified': False})
    return selected


def plan(scope, extras):
    checkpoints, pending = [], []
    for area in scope['areas']:
        candidates = area['entry_candidates']
        extra = [e for e in extras if e['area'] == area['name']]
        if extra:
            checkpoints.extend(extra)
        elif candidates:
            selected = min(candidates, key=lambda c: (
                c['kind'] != 'original_transition_destination',
                not c.get('both_files_global_main_loads', False),
                c.get('trigger_kind') != 'DT', str(c.get('link_id', c.get('exit_id')))))
            checkpoints.append({'name': area['name']+'/'+selected.get('name', 'Exit'+str(selected.get('exit_id'))),
                'area': area['name'], 'position_cm': selected['position_cm'],
                'source': selected, 'runtime_ground_verified': False})
            if 'native_heading_radians' in selected:
                checkpoints[-1]['yaw_degrees'] = travel_yaw(selected['native_heading_radians'])
                checkpoints[-1]['facing_policy'] = 'original_transition_arrival_heading'
        else:
            pending.append({'area': area['name'], 'zones': area['zones'], 'reason': 'No original entry coordinate recovered'})
    for checkpoint in checkpoints:
        zones = [z for z in scope['zones'] if z['area'] == checkpoint['area']]
        contained = [z for z in zones if z.get('bounds_unreal_cm') and all(
            z['bounds_unreal_cm']['min'][i] <= checkpoint['position_cm'][i] <= z['bounds_unreal_cm']['max'][i]
            for i in range(3))]
        if len(zones) == 1:
            checkpoint['zone'] = zones[0]['name']
        elif len(contained) == 1:
            checkpoint['zone'] = contained[0]['name']
    return {'schema': 'rea-bully-world-tour-v1', 'status': 'original_coordinate_candidates_runtime_pending',
        'checkpoints': checkpoints, 'pending': pending,
        'summary': {'checkpoints': len(checkpoints), 'areas': len({c['area'] for c in checkpoints}),
            'source_coordinate_kinds': dict(Counter(c['source']['kind'] for c in checkpoints)),
            'pending_areas': len(pending)},
        'limitations': ['Diagnostic direct travel uses original coordinates but is not an original free-roam transition graph.',
            'All runtime floor, capsule, movement and streaming checks remain pending.',
            'One area checkpoint does not prove every zone, placement, collision or activity in that area.',
            'Minigame point coordinates do not enable original gameplay or missions.']}


def mainmap_checkpoints(scope, population):
    candidates = []
    for group in population['poi_groups']:
        if group['area_code'] != 0:
            continue
        for index, point in enumerate(group['points']):
            candidates.append({'position_cm': ue_position(point['POIPOINT']),
                'name': group['name']+'/'+str(index), 'rank': 0 if point['TYPE'] in ('Hang_Out', 'School_Grounds') else 1,
                'source': {'kind': 'original_DAT_POI_coordinate', 'file': group['source'],
                    'group_line': group['line'], 'point_line': point['line'], 'point_index': index,
                    'group_name': group['name'], 'poi_type': point['TYPE'],
                    'original_activity_activated': False}})
    # These two terrain bounds contain no catalogued POI. Use source PATH
    # vertices; neither the name nor this selection proves an active route.
    fallbacks = {'zone_indust6': '5_07_OmarWalk', 'zone_rich6': '2_S02_Hattrick_Path'}
    checkpoints, pending = [], []
    for zone in scope['zones']:
        if zone['area'] != 'MainMap':
            continue
        bounds = zone['bounds_unreal_cm']
        def contains(candidate):
            return all(bounds['min'][i] <= candidate['position_cm'][i] <= bounds['max'][i] for i in range(3))
        contained = [p for p in candidates if contains(p)]
        if not contained and zone['name'] in fallbacks:
            paths = [p for p in population['paths'] if p['area_code'] == 0 and p['name'] == fallbacks[zone['name']]]
            if len(paths) != 1 or not paths[0]['point_count_valid']:
                raise ValueError('Ambiguous source PATH fallback')
            path = paths[0]
            points = [f for f in path['fields'] if f['key'] == 'PATHPOINT']
            for index, point in enumerate(points):
                candidate = {'position_cm': ue_position(point['value']), 'name': path['name']+'/'+str(index), 'rank': 2,
                    'source': {'kind': 'original_DAT_PATH_coordinate', 'file': path['source'], 'line': point['line'],
                        'path_name': path['name'], 'point_index': index, 'original_activity_activated': False}}
                if contains(candidate):
                    contained.append(candidate)
        if not contained:
            pending.append({'zone': zone['name'], 'reason': 'No original POI/PATH coordinate inside source geometry bounds'})
            continue
        center = [(a+b)/2 for a, b in zip(bounds['min'], bounds['max'])]
        chosen = min(contained, key=lambda p: (p['rank'],
            sum((p['position_cm'][i]-center[i])**2 for i in (0, 1)), p['name']))
        verify(chosen['source']['file'])
        checkpoints.append({'name': 'MainMap/'+zone['name']+'/'+chosen['name'], 'area': 'MainMap',
            'zone': zone['name'], 'position_cm': chosen['position_cm'], 'source': chosen['source'],
            'selection_policy': 'Original coordinate inside target XYZ bounds; prefer Hang_Out/School_Grounds then closest XY to bounds center. No coordinate synthesized.',
            'runtime_ground_verified': False})
    return checkpoints, pending


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', type=Path, default=ROOT/'evidence/world-scope-v07.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mainmap-zones', action='store_true', help='Sample original POI/PATH points within each MainMap zone')
    args = parser.parse_args()
    areas_path = ROOT/'evidence/world-zone-links.json'
    area_codes = {a['area_name']: a['area_code'] for a in read(areas_path)['area_enum']['entries']}
    scope = read(args.scope)
    result = plan(scope, original_extra_points(area_codes))
    result['sources'] = [reference(args.scope), reference(areas_path)]
    if args.mainmap_zones:
        population_path = ROOT/'evidence/world-population.json'
        checkpoints, pending = mainmap_checkpoints(scope, read(population_path))
        result['checkpoints'] = [c for c in result['checkpoints'] if c['area'] != 'MainMap'] + checkpoints
        result['pending_mainmap_zones'] = pending
        result['summary'].update(checkpoints=len(result['checkpoints']), mainmap_zone_checkpoints=len(checkpoints),
            pending_mainmap_zones=len(pending), source_coordinate_kinds=dict(Counter(c['source']['kind'] for c in result['checkpoints'])))
        result['sources'].append(reference(population_path))
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result['summary']))


if __name__ == '__main__':
    main()

"""Catalog original ambient metadata without inventing spawns or behaviour.

Reads the verified local archive extraction and Steam text configuration only.
The report distinguishes source coordinates/flags from still-unknown runtime
semantics. No game installation file is opened for writing.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re

from export_character import CharacterNif, skeleton
from export_nif import matrix, matmul, IDENTITY

ROOT = Path(__file__).resolve().parents[1]
PERIODS = ['DAY_OUTOFCLASS', 'DAY_INCLASS', 'NIGHT_EVENING', 'NIGHT_CURFEW']
POP_COLUMNS = ['total', 'prefects', 'nerds', 'jocks', 'dropouts', 'greasers',
               'preppies', 'students', 'cops', 'teachers', 'townpeople', 'shopkeep', 'bully']


def source(path):
    path = Path(path)
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def text_lines(path):
    data = path.read_bytes()
    # Archive extraction retains allocation padding; accept only a zero tail.
    end = data.find(b'\0')
    if end >= 0:
        if any(data[end:]):
            raise ValueError(f'Non-zero data after text terminator: {path}')
        data = data[:end]
    return data.decode('utf-8-sig').splitlines()


def rows(path):
    for number, raw in enumerate(text_lines(path), 1):
        text = raw.split('#', 1)[0].strip()
        if text:
            yield number, text


def read_population(path, width):
    lines = list(rows(path))
    controls = []
    while lines and re.match(r'^[-\d]', lines[0][1]):
        number, text = lines.pop(0)
        controls.append({'line': number, 'values': [float(x) for x in text.replace(',', ' ').split()]})
    result = []
    while lines:
        line, name = lines.pop(0)
        periods = []
        for period in PERIODS:
            number, text = lines.pop(0)
            values = [int(x) for x in text.replace(',', ' ').split()]
            if len(values) != width:
                raise ValueError(f'{path}:{number}: expected {width} columns')
            periods.append({'period': period, 'line': number, 'values': values})
        result.append({'area': name, 'line': line, 'periods': periods})
    return {'source': source(path), 'controls': controls, 'areas': result}


def typed(value):
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value in {'TRUE', 'FALSE'}:
        return value == 'TRUE'
    if re.fullmatch(r'[-+]?\d+', value):
        return int(value)
    parts = value.replace(',', ' ').split()
    try:
        numbers = [float(x) for x in parts]
        return numbers[0] if len(numbers) == 1 else numbers
    except ValueError:
        return value


def read_trigger_blocks(path):
    """Parse explicit TYPE/BEGIN/END records, including nested population data."""
    result = []
    stack = []
    lines = list(rows(path))
    for index, (number, line) in enumerate(lines):
        if index+1 < len(lines) and lines[index+1][1] == 'BEGIN':
            stack.append({'type': line, 'line': number, 'fields': []})
        elif line == 'BEGIN':
            if not stack:
                raise ValueError(f'{path}:{number}: BEGIN without record')
        elif line == 'END':
            if not stack:
                raise ValueError(f'{path}:{number}: END without record')
            current = stack.pop()
            if stack:
                stack[-1]['fields'].append({'key': current['type'], 'value': current['fields'], 'line': current['line']})
            else:
                result.append(current)
        elif stack:
            cells = line.split(None, 1)
            key, value = cells[0], cells[1] if len(cells) > 1 else ''
            stack[-1]['fields'].append({'key': key, 'value': typed(value.strip()), 'line': number})
    if stack:
        raise ValueError(f'{path}: unclosed record')
    return result


def fields(block):
    return {f['key']: f['value'] for f in block['fields']}


def poi_points(block):
    """NPOIPOINTS is followed by one flat field group beginning with GENDER."""
    output = []
    point = None
    for field in block['fields']:
        if field['key'] == 'GENDER':
            point = {'line': field['line']}
            output.append(point)
        if point is not None:
            point[field['key']] = field['value']
    expected = fields(block).get('NPOIPOINTS')
    if len(output) != expected:
        raise ValueError(f'POI point count mismatch: {len(output)} != {expected}')
    for point in output:
        if len(point.get('POIPOINT', [])) != 3:
            raise ValueError('POI missing XYZ')
    return output


def read_peds(path):
    section = ''
    result = []
    for number, text in rows(path):
        cells = text.replace(',', ' ').split()
        if len(cells) == 1:
            section = cells[0].lower()
        elif section == 'peds':
            if len(cells) < 16:
                raise ValueError(f'{path}:{number}: short ped record')
            result.append({'id': int(cells[0]), 'model': cells[1], 'texture_dictionary': cells[2],
                'female': bool(int(cells[3])), 'size': cells[4], 'ped_type': cells[5],
                'stats': cells[6], 'animation_groups': cells[7:11], 'unique_raw': int(cells[11]),
                'action_root': cells[12], 'action_file': cells[13], 'ai_root': cells[14],
                'ai_file': cells[15], 'extra_tokens': cells[16:], 'line': number})
    return result


def index_world(world):
    manifest = json.loads((world / 'manifest.json').read_text())
    index = defaultdict(list)
    for entry in manifest['entries']:
        # Only the requested ped NIF/NFT files are verified below. Avoid reading
        # every unrelated animation/texture from the two-gigabyte archive.
        index[entry['name'].lower()].append({'archive_index': entry['index'],
            'path': str(world / entry['path']), 'sha256': entry['extracted_sha256']})
    return index


def analyze_skeleton(path, player_signature, player_bones):
    try:
        nif = CharacterNif(path)
        bones = skeleton(nif)
        names = {bone['source_block']: bone['name'] for bone in bones}
        signature = [(bone['name'], names.get(bone['parent_block'])) for bone in bones]
        player_by_name = {b['name']: b for b in player_bones}
        own_bind_errors = []
        for i, kind in enumerate(nif.types):
            if kind != 'NiSkinInstance':
                continue
            skin = nif.block(i)
            sd = nif.block(skin['data'])
            worlds = {b['source_block']: b['world_matrix'] for b in bones}
            if len(skin['bones']) != len(sd['bones']):
                raise ValueError('Skin inverse bind count mismatch')
            for block, stored in zip(skin['bones'], sd['bones']):
                if block not in worlds:
                    raise ValueError('Skin bone outside base hierarchy')
                product = matmul(worlds[block], matrix(stored))
                own_bind_errors.append(max(abs(a-b) for a,b in zip(product, IDENTITY)))
        comparable = [b for b in bones if b['name'] in player_by_name]
        translation_delta = max((max(abs(a-c) for a,c in zip(b['translation'], player_by_name[b['name']]['translation'])) for b in comparable), default=None)
        rotation_delta = max((max(abs(a-c) for a,c in zip(b['rotation'], player_by_name[b['name']]['rotation'])) for b in comparable), default=None)
        return {'bone_count': len(bones), 'names_and_parents_match_player': signature == player_signature,
                'bone_names': [bone['name'] for bone in bones],
                'hierarchy_sha256': hashlib.sha256(json.dumps(signature).encode()).hexdigest(),
                'own_skin_bind_identity_max_error': max(own_bind_errors, default=None),
                'own_skin_bind_matrices_checked': len(own_bind_errors),
                'rest_translation_max_delta_from_player_metres': translation_delta,
                'rest_rotation_matrix_max_delta_from_player': rotation_delta,
                'rest_pose_matches_player': signature == player_signature and translation_delta < 1e-6 and rotation_delta < 1e-6,
                'note': 'Same hierarchy permits name mapping; bind pose, proportions and animation deformation still require validation.'}
    except (ValueError, KeyError, IndexError, UnicodeDecodeError) as error:
        return {'error': str(error), 'names_and_parents_match_player': False}


def run(game, extracted, output):
    world = extracted / 'Stream/World'
    index = index_world(world)
    player = world / '10611/PLAYER.nif'
    bones = skeleton(CharacterNif(player))
    name_by_block = {bone['source_block']: bone['name'] for bone in bones}
    signature = [(bone['name'], name_by_block.get(bone['parent_block'])) for bone in bones]
    peds = read_peds(game / 'Objects/default.ide')
    cache = {}
    verified = set()
    for ped in peds:
        ped['model_candidates'] = index.get(ped['model'].lower()+'.nif', [])
        ped['texture_candidates'] = index.get(ped['texture_dictionary'].lower()+'.nft', [])
        for candidate in ped['model_candidates']+ped['texture_candidates']:
            if candidate['path'] not in verified:
                if source(candidate['path'])['sha256'] != candidate['sha256']:
                    raise ValueError('Ped source differs from extraction manifest')
                verified.add(candidate['path'])
        for candidate in ped['model_candidates']:
            key = candidate['sha256']
            if key not in cache:
                cache[key] = analyze_skeleton(Path(candidate['path']), signature, bones)
            candidate['skeleton'] = cache[key]
    triggers = []
    pois = []
    paths = []
    summaries = []
    failures = []
    for path in sorted((extracted / 'DAT/Trigger').glob('*.dat')):
        try:
            blocks = read_trigger_blocks(path)
        except ValueError as error:
            failures.append({'source': source(path), 'error': str(error)})
            continue
        counts = Counter(b['type'] for b in blocks)
        summaries.append({'source': source(path), 'record_counts': dict(counts)})
        preceding_perimeter = None
        for block in blocks:
            f = fields(block)
            if block['type'] == 'PERIMETER':
                preceding_perimeter = block
            elif block['type'] == 'POI':
                # NAME repeats for individual points: take the first group NAME.
                group = next(v['value'] for v in block['fields'] if v['key'] == 'NAME')
                pois.append({'source': source(path), 'line': block['line'], 'name': group,
                    'area_code': f.get('AREACODE'), 'points': poi_points(block)})
            elif block['type'] == 'PATH':
                points = [v['value'] for v in block['fields'] if v['key'] == 'PATHPOINT']
                paths.append({'source': source(path), 'line': block['line'], 'name': f.get('PATHNAME'),
                    'area_code': f.get('AREACODE'), 'patrol_type_raw': f.get('PATROLTYPE'),
                    'declared_point_count': f.get('NPATHPOINTS'),
                    'point_count_valid': len(points) == f.get('NPATHPOINTS'),
                    'points': points, 'fields': block['fields']})
            elif block['type'] == 'TRIGGER' and (f.get('HASPOPULATIONDATA') or f.get('HASAMBIENTVEHICLEDATA') or 'door' in path.stem.lower()):
                triggers.append({'source': source(path), 'line': block['line'], 'fields': block['fields'],
                    'preceding_perimeter_record': preceding_perimeter})
    pop = read_population(game / 'Config/dat/PedPop.dat', 13)
    pop['columns'] = POP_COLUMNS
    motion_path = game/'Config/dat/ped.dat'
    motion = []
    motion_columns = ['walk_speed','run_speed','fast_run_speed','flee_distance','heading_change','minimum_money_cents','maximum_money_cents']
    for line, text in rows(motion_path):
        cells = text.split()
        if len(cells) != 8:
            raise ValueError(f'Unexpected ped motion row: {line}')
        motion.append({'ped_type': cells[0], 'line':line,
            'original_values':dict(zip(motion_columns,map(float,cells[1:])))})
    result = {'schema_version': 1, 'classification': 'original_ambient_metadata_not_executed_gameplay',
        'game_root': str(game), 'ped_definitions_source': source(game / 'Objects/default.ide'),
        'ped_definitions': peds, 'player_skeleton_reference': source(player),
        'ped_population': pop, 'vehicle_population': read_population(game / 'Config/dat/VehPop.dat', 4),
        'ped_type_motion': {'source':source(motion_path), 'types':motion},
        'poi_groups': pois, 'paths': paths, 'population_and_door_triggers': triggers,
        'trigger_file_inventory': summaries, 'parse_failures': failures,
        'summary': {'ped_records': len(peds), 'ped_records_with_model': sum(bool(p['model_candidates']) for p in peds),
            'ped_records_with_player_hierarchy': sum(any(c['skeleton']['names_and_parents_match_player'] for c in p['model_candidates']) for p in peds),
            'poi_groups': len(pois), 'poi_points': sum(len(p['points']) for p in pois), 'paths': len(paths),
            'path_points': sum(len(p['points']) for p in paths), 'selected_triggers': len(triggers),
            'trigger_files_parsed': len(summaries), 'trigger_parse_failures': len(failures)},
        'limitations': ['No area population timing thresholds inferred from period names.',
            'unique_raw, PATROLTYPE and unnamed parameters retained without invented semantics.',
            'Coordinates and POI flags are original data; activation rules, navigation and AI are not reproduced by this catalog.',
            'DAT includes mission, test and ambient records. Presence alone does not prove a record is active in free roam.',
            'Same skeletal hierarchy does not prove identical bind pose or valid animation retargeting.']}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    write_school_runtime(result, ROOT/'evidence/school-ambient-source.json')
    return result['summary']


def ue_position(position):
    return [position[0]*100, -position[1]*100, position[2]*100]


def write_school_runtime(catalog, output):
    """Compact import input, with actual source flags and deliberately raw params."""
    area_report = json.loads((ROOT/'evidence/world-zone-links.json').read_text())
    areas = {a['area_code']: a['area_name'] for a in area_report['area_enum']['entries']}
    peds = [p for p in catalog['ped_definitions'] if 2 <= p['id'] < 110 and p['unique_raw'] > 0
            and any(c['skeleton']['names_and_parents_match_player'] for c in p['model_candidates'])]
    pois = []
    for group in catalog['poi_groups']:
        if Path(group['source']['path']).name.lower() != 'tschool_hangouts.dat':
            continue
        for index, point in enumerate(group['points']):
            ped_type = point['PEDTYPE']
            gender = point['GENDER'].lower()
            candidates = [p['id'] for p in peds if p['ped_type'] == ped_type
                          and (gender == 'both' or p['female'] == (gender == 'female'))]
            pois.append({'id': group['name']+'_'+str(index), 'group_name': group['name'],
                'source': group['source'], 'line': point['line'], 'area_code': group['area_code'],
                'area_name': areas.get(group['area_code']), 'source_position': point['POIPOINT'],
                'ue_position_cm': ue_position(point['POIPOINT']), 'ped_type': ped_type,
                'gender': point['GENDER'], 'maximum_raw': point['MAX'], 'type': point['TYPE'],
                'original_flags': {k:v for k,v in point.items() if isinstance(v,bool)},
                'yaw_pitch_roll_source_degrees': point['YAWPITCHROLL'],
                'radius_source_m': point.get('RADIUS'), 'override_limit_raw': point.get('OVERRIDELIMIT'),
                'candidate_ped_ids_same_hierarchy_only': candidates})
    patrols = []
    for path in catalog['paths']:
        if Path(path['source']['path']).stem.lower() not in {'prefectsschoolground','patrol_schoolgrounds'}:
            continue
        points = []
        for field in path['fields']:
            if field['key'] == 'PATHPOINT':
                points.append({'source_position': field['value'], 'ue_position_cm': ue_position(field['value']),
                               'line': field['line']})
            elif points and field['key'] in {'POINTYAWPITCHROLL','WAITTIME','UNID','NACTIONS'}:
                points[-1][field['key']] = field['value']
        patrols.append({'name': path['name'], 'source': path['source'], 'line': path['line'],
            'area_code': path['area_code'], 'area_name': areas.get(path['area_code']),
            'patrol_type_raw': path['patrol_type_raw'], 'point_count_valid': path['point_count_valid'],
            'points': points, 'candidate_ped_ids_same_hierarchy_only': [p['id'] for p in peds if p['ped_type']=='PREFECT']})
    decompiled = ROOT/'local/decompiled/MainMap.lua'
    text = decompiled.read_text()
    for match in re.finditer(r'L0_2 = AreaInitPatrolPath\s+(.*?)\s+L0_2\(([^\n]+)\)', text, re.S):
        body = match.group(1)
        name_match = re.search(r'L1_2 = L1_2\.(_\w+)', body)
        if not name_match:
            continue
        name = name_match.group(1).lstrip('_').lower()
        values = []
        for register in range(2,6):
            value_match = re.search(r'L'+str(register)+r'_2 = (-?\d+|true|false)\b', body)
            if value_match:
                values.append(typed(value_match.group(1).upper()))
        for patrol in patrols:
            if patrol['name'].lower() == name:
                patrol['mainmap_area_init_patrol_path_arguments_after_path_raw'] = values
    result = {'schema_version': 1, 'classification': 'original_school_ambient_positions_not_complete_ai',
        'source_catalog': source(ROOT/'evidence/world-population.json'),
        'area_enum_source': source(ROOT/'evidence/world-zone-links.json'),
        'poi_points': pois, 'patrol_paths': patrols,
        'ped_candidates': peds,
        'patrol_activation_evidence': [source(decompiled), source(ROOT/'local/disassembled/MainMap.luasm'),
                                       source(ROOT/'local/scripts/MainMap.lur')],
        'poi_global_load_evidence': [source(ROOT/'local/decompiled/main.lua'),
                                     source(ROOT/'local/disassembled/main.luasm'),
                                     source(ROOT/'local/scripts/main.lur')],
        'limitations': ['The four period/time-of-day flag names are original. Their clock thresholds are not inferred.',
            'Candidate lists filter original IDE faction/gender/unique_raw>0 and matching bone names/parents; source selection randomization is not recovered.',
            'Every NPC needs its own original inverse bind matrices. None of these candidates has PLAYER-identical rest pose.',
            'Hang_Out POI TYPE chooses original behaviour tree elsewhere; this file does not claim exact idle action.',
            'PATROLTYPE and AreaInitPatrolPath parameters remain raw; route loop/ping-pong, schedules and navigation cannot be inferred solely from coordinates.']}
    output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    write_population_regions(catalog, areas, ROOT/'evidence/ambient-population-regions.json')


def mapping(nested_fields):
    return {f['key']: mapping(f['value']) if isinstance(f['value'],list) and f['value'] and isinstance(f['value'][0],dict) else f['value'] for f in nested_fields}


def write_population_regions(catalog, areas, output):
    regions = []
    for trigger in catalog['population_and_door_triggers']:
        if Path(trigger['source']['path']).name.lower() != 'population.dat':
            continue
        f = mapping(trigger['fields'])
        if not (f.get('HASPOPULATIONDATA') or f.get('HASAMBIENTVEHICLEDATA')):
            continue
        perimeter = trigger['preceding_perimeter_record']
        pf = fields(perimeter) if perimeter else {}
        xs = [v['value'] for v in perimeter['fields'] if v['key']=='PERIMETERPOINTX'] if perimeter else []
        ys = [v['value'] for v in perimeter['fields'] if v['key']=='PERIMETERPOINTY'] if perimeter else []
        regions.append({'name': f['TRIGGERNAME'], 'source': trigger['source'], 'line': trigger['line'],
            'area_code': f['AREACODE'], 'area_name': areas.get(f['AREACODE']),
            'source_position': f['POSITION'], 'ue_position_cm': ue_position(f['POSITION']),
            'source_yaw_pitch_roll': f['YAWPITCHROLL'], 'source_yaw': f['YAW'], 'source_z_height_m': f['ZHEIGHT'],
            'population': f.get('POPULATIONDATA'), 'ambient_vehicles': f.get('AMBIENTVEHICLEDATA'),
            'force_population_read': f['FORCEPOPREAD'], 'force_vehicle_read': f['FORCEVEHREAD'],
            'perimeter_original': {'line': perimeter['line'] if perimeter else None,
                'source_local_xy': list(zip(xs,ys)), 'declared_count': pf.get('NPERIMETERPOINTS'),
                'count_valid': len(xs)==len(ys)==pf.get('NPERIMETERPOINTS'),
                'height_raw': pf.get('HEIGHT'), 'depth_raw': pf.get('DEPTH')}})
    result = {'schema_version':1, 'classification':'original_global_ambient_region_metadata',
        'regions': regions, 'global_load_provenance': source(ROOT/'local/decompiled/main.lua'),
        'limitations':['Local perimeter coordinates and original trigger transforms are separate; transformation and overlap priority need original runtime confirmation.',
            'POPULATION_DAY/CLASS/NIGHT/CURFEW and vehicle fields are source data; exact clock transitions, density control and placement rules are not established here.']}
    output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', type=Path, default=Path(r'C:\Program Files (x86)\Steam\steamapps\common\Bully Scholarship Edition'))
    parser.add_argument('--extracted', type=Path, default=ROOT/'local/extracted')
    parser.add_argument('--output', type=Path, default=ROOT/'evidence/world-population.json')
    args = parser.parse_args()
    print(json.dumps(run(args.game, args.extracted, args.output), indent=2))

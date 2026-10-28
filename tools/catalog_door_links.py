"""Recover proven original TRIGGER -> POINT door transitions with provenance.

REA 0x416150 pairs equal name hashes. 0x416f80 requests the destination
POINT's AREACODE and first coordinate. No ExitLocs/comment/nearest-door pairing.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import re

from catalog_population import ROOT, read_trigger_blocks, fields, source, ue_position


def name_hash(name):
    """Independent transcription of the arithmetic in REA FUN_00576ed0."""
    value = 0
    for char in name.upper().encode('ascii'):
        value = (value*0x83+char)&0xffffffff
    return value&0x7fffffff


def split_points(block):
    points = []
    for field in block['fields']:
        if field['key'] == 'POINTPOINT':
            points.append({'source_position':field['value'], 'ue_position_cm':ue_position(field['value']),
                           'line':field['line']})
        elif points and field['key'] == 'POINTYAWPITCHROLL':
            points[-1]['source_yaw_pitch_roll_degrees'] = field['value']
            # Original source heading: native 0x416f80 explicitly subtracts 90.
            points[-1]['native_heading_radians'] = math.radians(field['value'][0]-90)
    return points


def script_loads(scripts):
    result = defaultdict(list)
    global_files = set()
    for path in sorted(scripts.glob('*.lua')):
        text = path.read_text(encoding='utf-8-sig')
        for match in re.finditer(r'(L\d+_\d+) = DATLoad\s+L\d+_\d+ = "([^"\n]+\.DAT)"\s+L\d+_\d+ = (\d+)', text, re.I):
            filename = Path(match.group(2)).name.lower()
            result[filename].append({'script':str(path), 'line':text.count('\n',0,match.start())+1,
                                    'slot_raw':int(match.group(3)), 'global_main':path.name.lower()=='main.lua'})
            if path.name.lower() == 'main.lua':
                global_files.add(filename)
    return result, global_files


def script_lock_calls(scripts):
    """Record direct literal arguments, retaining context rather than evaluating Lua."""
    result = defaultdict(list)
    pattern = re.compile(r'(L\d+_\d+) = (AreaSetDoorLocked(?:ToPeds)?|AreaSetDoorOpen)\s+(.+?)\s+\1\(([^\n]*)\)',re.S)
    for path in sorted(scripts.glob('*.lua')):
        text = path.read_text(encoding='utf-8-sig')
        lines = text.splitlines()
        script_source = source(path)
        bytecode = ROOT/'local/scripts'/(path.stem+'.lur')
        disassembly = ROOT/'local/disassembled'/(path.stem+'.luasm')
        for match in pattern.finditer(text):
            # A decompilation register may be reassigned before a later call.
            body = match.group(3)
            if len(body.splitlines()) > 12:
                continue
            args = [x.strip() for x in match.group(4).split(',')]
            if len(args) < 2:
                continue
            register = re.escape(args[0])
            literal = re.search(register+r' = "([^"\n]+)"',body)
            symbolic = re.search(register+r' = '+register+r'\.(_?\w+)',body)
            name = literal.group(1) if literal else symbolic.group(1).lstrip('_') if symbolic else None
            if not name:
                continue
            lock = re.search(re.escape(args[1])+r' = (true|false)\b',body)
            line = text.count('\n',0,match.start())+1
            result[name_hash(name)].append({'script':str(path), 'line':line, 'api':match.group(2),
                'source':script_source,
                'bytecode_source':source(bytecode) if bytecode.exists() else None,
                'disassembly_source':source(disassembly) if disassembly.exists() else None,
                'door_name_argument':name, 'boolean_argument':lock.group(1)=='true' if lock else None,
                'condition_semantics':'context retained; not evaluated; mission/chapter/time state may change this call',
                'context_start_line':max(1,line-16),
                'context_lines':lines[max(0,line-17):line+len(match.group(0).splitlines())]})
    return result


def within_bounds(position, area, bounds):
    b = bounds.get(area)
    if not b or b['source_zeroed']:
        return None
    left,right,top,bottom,low,high = b['original_bounds']
    x,y,z=position
    return left <= x <= right and top <= y <= bottom and low <= z <= high


def build_links(triggers, points, global_files, area_names, bounds, lock_calls):
    by_hash = defaultdict(list)
    for trigger in triggers:
        by_hash[name_hash(trigger['name'])].append(trigger)
    links = []
    unmatched = []
    for point in points:
        kind = 'DT' if point['name'].startswith('DT_') else 'SV' if point['name'].startswith('SV_') else None
        if not kind:
            continue
        matches = by_hash.get(name_hash(point['name']),[])
        if not matches:
            unmatched.append({'name':point['name'], 'source':point['source'], 'line':point['line'],
                'area_code':point['area_code'], 'reason':'No TRIGGER with matching original hash; no automatic transition proven'})
        for trigger in matches:
            exact = point['name'].upper()==trigger['name'].upper()
            same_file = trigger['source']['path']==point['source']['path']
            global_pair = all(Path(record['source']['path']).name.lower() in global_files for record in [trigger,point])
            destinations = point['points']
            links.append({'id':str(len(links)), 'name':point['name'], 'original_name_hash':hex(name_hash(point['name'])),
                'pairing':'REA 0x416150 equal original name hashes', 'same_casefold_name':exact,
                'kind':kind, 'activation':'current animated door trigger selected by native door logic' if kind=='DT' else 'player enters source trigger volume',
                'source_trigger':trigger, 'source_area_name':area_names.get(trigger['area_code']),
                'destination_point_list':point, 'destination_area_name':area_names.get(point['area_code']),
                'destination_position':destinations[0] if destinations else None,
                'destination_camera_point':destinations[1] if len(destinations)>1 else None,
                'destination_inside_original_area_bounds':within_bounds(destinations[0]['source_position'],point['area_code'],bounds) if destinations else None,
                'source_and_target_area_differ':trigger['area_code']!=point['area_code'],
                'same_source_file':same_file, 'both_files_global_main_loads':global_pair,
                'sv_first_matching_trigger_pool_order_needed':kind=='SV' and len(matches)>1,
                'destination_coordinate_proven':bool(destinations) and point['point_count_valid'] and exact,
                'source_islocked_raw':trigger['islocked_raw'],
                'script_lock_state_calls':lock_calls.get(name_hash(trigger['name']),[]),
                'effective_lock_state':'unknown until native door default, saved overrides and script state are applied'})
    return links, unmatched


def run(extracted, scripts, area_report, output):
    areas=json.loads(area_report.read_text())
    names={a['area_code']:a['area_name'] for a in areas['area_enum']['entries']}
    bounds={a['area_code']:a for a in areas['bounds']}
    loads,global_files=script_loads(scripts)
    triggers=[];points=[]
    for path in sorted((extracted/'DAT/Trigger').glob('*.dat')):
        perimeter=None
        provenance=source(path)
        for block in read_trigger_blocks(path):
            f=fields(block)
            if block['type']=='PERIMETER':
                perimeter=block
            elif block['type']=='TRIGGER':
                triggers.append({'name':f['TRIGGERNAME'],'source':provenance,'line':block['line'],
                    'area_code':f.get('AREACODE'),'source_position':f.get('POSITION'),
                    'ue_position_cm':ue_position(f['POSITION']) if 'POSITION' in f else None,
                    'source_yaw_pitch_roll_degrees':f.get('YAWPITCHROLL'),'source_yaw_degrees':f.get('YAW'),
                    'source_height_raw':f.get('ZHEIGHT'),'islocked_raw':f.get('ISLOCKED'),
                    'is_mission_specific_raw':f.get('ISMISSIONSPECIFIC'),
                    'preceding_perimeter':perimeter, 'loaded_by_scripts':loads.get(path.name.lower(),[])})
            elif block['type']=='POINT':
                coordinates=split_points(block)
                points.append({'name':f['POINTNAME'],'source':provenance,'line':block['line'],
                    'area_code':f.get('AREACODE'),'points':coordinates,'declared_point_count':f.get('NPOINTPOINTS'),
                    'point_count_valid':len(coordinates)==f.get('NPOINTPOINTS'),
                    'loaded_by_scripts':loads.get(path.name.lower(),[])})
    lock_calls=script_lock_calls(scripts)
    links,unmatched=build_links(triggers,points,global_files,names,bounds,lock_calls)
    native_paths=[ROOT/'local/native/decompiled/bindings-seeded-20261020/functions/0x5bf570.c']
    for name in ['0x6d2350','0x416150','0x416f80','0x576ed0','0x6d6fa0','0x4ef400','0x4e9c10','0x4eed10','0x4e99f0','0x6d6570']:
        native_paths.append(ROOT/'local/native/decompiled/full-20261020/functions'/f'{name}.c')
    result={'schema_version':1,'classification':'static_native_proven_door_pairs_not_full_transition_runtime',
        'native_evidence':[source(p) for p in native_paths],
        'format_references':['https://github.com/LiquidSnake0/bully-re/blob/main/docs/zones.md',
                             'https://github.com/LiquidSnake0/bully-re/blob/main/docs/trigger.md'],
        'script_provenance':[source(scripts/'main.lua'),source(ROOT/'local/scripts/main.lur'),source(ROOT/'local/disassembled/main.luasm')],
        'area_provenance':source(area_report), 'links':links,'unmatched_prefixed_point_lists':unmatched,
        'summary':{'links':len(links),'kinds':dict(Counter(l['kind'] for l in links)),
            'proven_destinations':sum(l['destination_coordinate_proven'] for l in links),
            'globally_loaded_links':sum(l['both_files_global_main_loads'] for l in links),
            'globally_loaded_door_file_links':sum(l['both_files_global_main_loads'] and Path(l['source_trigger']['source']['path']).name.lower().endswith('_doors.dat') for l in links),
            'links_with_direct_script_lock_or_open_calls':sum(bool(l['script_lock_state_calls']) for l in links),
            'hash_only_collisions_different_names':sum(not l['same_casefold_name'] for l in links),
            'unmatched_prefixed_point_lists':len(unmatched),
            'destinations_outside_original_area_bounds':sum(l['destination_inside_original_area_bounds'] is False for l in links)},
        'limitations':['Only equal-hash TRIGGER/POINT pairs are linked; ExitLocs comments and proximity never create edges.',
            'Full DAT inventory includes mission/test data. Native only pairs currently loaded pool entries; loaded_by_scripts documents possible loaders, not live activation.',
            'DT transitions require the original animated door object/selected-trigger handshake; walking into the trigger is sufficient only for SV.',
            'ISLOCKED is preserved raw. Effective lock state depends on native initialization, saved overrides and currently executing scripts.',
            'Script contexts derive from decompilation drafts; original bytecode and round-trip disassembly paths/hashes are supplied where present. Control conditions are not executed or automatically validated.',
            'Points outside their area bounds are flagged and must not be silently used as validated destinations.']}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result['summary']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extracted',type=Path,default=ROOT/'local/extracted')
    parser.add_argument('--scripts',type=Path,default=ROOT/'local/decompiled')
    parser.add_argument('--areas',type=Path,default=ROOT/'evidence/world-zone-links.json')
    parser.add_argument('--output',type=Path,default=ROOT/'evidence/world-door-links.json')
    args=parser.parse_args()
    print(json.dumps(run(args.extracted,args.scripts,args.areas,args.output),indent=2))

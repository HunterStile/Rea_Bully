"""Recover original area enum, area bounds and exits with static provenance."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

from catalog_population import ROOT, source, rows, text_lines


def area_table(binary):
    """REA 0x4305d0 passes this 64-entry string pointer table to 0x73ac90.

    The latter returns the matching zero-based index. Read file-backed PE bytes
    only; these are not addresses from an executing process.
    """
    data = binary.read_bytes()
    if hashlib.sha256(data).hexdigest() != '457b595ae70e92031b04954de36525e52dd9ab5eb5f7364480ad2542e8a7d67b':
        raise ValueError('The REA-recovered table address applies only to the verified local analysis copy')
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    if data[:2] != b'MZ' or data[pe:pe+4] != b'PE\0\0':
        raise ValueError('Expected PE file')
    count = struct.unpack_from('<H', data, pe+6)[0]
    optional_size = struct.unpack_from('<H', data, pe+20)[0]
    optional = pe+24
    if struct.unpack_from('<H', data, optional)[0] != 0x10b:
        raise ValueError('Expected PE32')
    base = struct.unpack_from('<I', data, optional+28)[0]
    sections = [struct.unpack_from('<IIII', data, optional+optional_size+i*40+8) for i in range(count)]
    def offset(va):
        for virtual_size, rva, raw_size, raw in sections:
            delta = va-base-rva
            if 0 <= delta < raw_size:
                return raw+delta
        raise ValueError(f'VA {va:x} is not file-backed')
    table_va = 0x903058
    table_offset = offset(table_va)
    result = []
    for index in range(64):
        va = struct.unpack_from('<I', data, table_offset+index*4)[0]
        at = offset(va)
        end = data.index(b'\0', at, at+100)
        name = data[at:end].decode('ascii')
        if not re.fullmatch(r'[A-Za-z_0-9]+', name):
            raise ValueError('Unexpected area string')
        result.append({'area_code': index, 'area_name': name,
            'string_va': hex(va), 'pointer_va': hex(table_va+index*4)})
    return {'source': source(binary), 'table_va': hex(table_va), 'entries': result,
        'native_provenance': [source(ROOT/'local/native/decompiled/full-20261020/functions/0x4305d0.c'),
                              source(ROOT/'local/native/decompiled/full-20261020/functions/0x73ac90.c')]}


def run(game, binary, output):
    table = area_table(binary)
    code = {e['area_name']: e['area_code'] for e in table['entries']}
    bounds_path = game/'Config/dat/areabbox.dat'
    bounds = []
    for line, text in rows(bounds_path):
        cells = text.replace(',', ' ').split()
        if len(cells) != 8:
            raise ValueError(f'Unexpected bounds record at {line}')
        name = cells[0]
        values = list(map(float, cells[1:7]))
        left, right, top, bottom, low, high = values
        bounds.append({'area_name': name, 'area_code': code.get(name), 'line': line,
            'original_bounds': values, 'ue_bounds_cm_min': [left*100,-bottom*100,low*100],
            'ue_bounds_cm_max': [right*100,-top*100,high*100], 'radar_map': cells[7],
            'source_zeroed': not any(values)})
    exits_path = game/'Config/dat/ExitLocs.dat'
    exits = []
    for line, raw in enumerate(text_lines(exits_path), 1):
        text, _, comment = raw.partition('#')
        cells = text.replace(',', ' ').split()
        if not cells:
            continue
        if len(cells) != 5:
            raise ValueError(f'Unexpected exit record at {line}')
        position = list(map(float, cells[2:]))
        exits.append({'area_name': cells[0], 'area_code': code.get(cells[0]),
            'exit_id': int(cells[1]), 'source_position': position,
            'ue_position_cm': [position[0]*100,-position[1]*100,position[2]*100],
            'destination_comment_raw': comment.strip(), 'line': line})
    result = {'schema_version': 1, 'classification': 'original_area_metadata_not_teleport_graph',
        'area_enum': table, 'bounds_source': source(bounds_path), 'bounds': bounds,
        'exits_source': source(exits_path), 'exits': exits,
        'native_loaders': [source(ROOT/'local/native/decompiled/full-20261020/functions/0x5483a0.c'),
                           source(ROOT/'local/native/decompiled/full-20261020/functions/0x548560.c')],
        'limitations': ['ExitLocs gives arrival/exit positions indexed by source area and exit ID; it is not a complete paired teleport graph.',
            'Destination comments retain spelling, question marks and stale aliases. No automatic destination pairing is claimed.',
            'Area bounds describe broad regions and radar types, not collision volumes or navigable surfaces.']}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    return {'area_enum_entries': len(table['entries']), 'bounds': len(bounds), 'exits': len(exits)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', type=Path, default=Path(r'C:\Program Files (x86)\Steam\steamapps\common\Bully Scholarship Edition'))
    parser.add_argument('--binary', type=Path, default=ROOT/'local/native/Bully.steam-original.exe.unpacked.exe')
    parser.add_argument('--output', type=Path, default=ROOT/'evidence/world-zone-links.json')
    args = parser.parse_args()
    print(json.dumps(run(args.game, args.binary, args.output), indent=2))

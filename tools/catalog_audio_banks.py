"""Catalog original SEA wave banks without decoding, playing or importing audio.

SEA framing was investigated in oh-ashen-one/bully-remake at 675ace2e88f9718ded9085af9c45c70715fc1e4d.
WBND field definitions were cross-checked against Microsoft's WaveBankReader.cpp.
This reader is independently implemented, bounded, and tied to our Steam inventory.
The final two SEA header words are preserved as unknowns: they are NOT constants.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import struct

ROOT = Path(__file__).resolve().parents[1]
FORMAT_REFERENCE = 'https://github.com/microsoft/DirectXTK/blob/main/Audio/WaveBankReader.cpp'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def region(data, offset, length, label):
    if offset < 0 or length < 0 or offset + length > len(data):
        raise ValueError(f'{label} exceeds containing region')
    return data[offset:offset+length]


def integers(data, offset, count, label):
    return struct.unpack('<' + 'I'*count, region(data, offset, count*4, label))


def name_string(data):
    if b'\0' not in data:
        raise ValueError('Unterminated fixed-width wave-bank name')
    return data.split(b'\0', 1)[0].decode('ascii')


def parse_sea(data):
    sd_offset, sd_length, wb_length, unknown_a, unknown_b = integers(data, 0, 5, 'SEA header')
    wb_offset = sd_offset + sd_length
    if sd_offset < 20 or wb_offset + wb_length != len(data):
        raise ValueError('SEA sections do not cover the declared file extent')
    sound = region(data, sd_offset, sd_length, 'SDBK')
    bank = region(data, wb_offset, wb_length, 'WBND')
    if sound[:4] != b'SDBK' or bank[:4] != b'WBND':
        raise ValueError('SEA bank signatures do not match their declared offsets')
    version, header_version = integers(bank, 4, 2, 'WBND versions')
    if (version, header_version) != (44, 42):
        raise ValueError(f'Unverified WBND version: {(version, header_version)}')
    segments = [integers(bank, 12+8*i, 2, 'WBND segments') for i in range(5)]
    occupied = []
    for i, (offset, length) in enumerate(segments):
        region(bank, offset, length, f'WBND segment {i}')
        if length:
            if offset < 52:
                raise ValueError('WBND segment overlaps header')
            occupied.append((offset, offset+length))
    occupied.sort()
    if any(a[1] > b[0] for a, b in zip(occupied, occupied[1:])):
        raise ValueError('WBND segments overlap')
    bank_data = region(bank, *segments[0], 'bank data')
    if len(bank_data) != 96:
        raise ValueError('Unverified WBND bank-data layout')
    flags, count = integers(bank_data, 0, 2, 'bank flags/count')
    if flags & 0x20000:
        raise ValueError('Compact WBND metadata is not implemented')
    metadata_stride, name_stride, alignment, compact_raw = integers(bank_data, 72, 4, 'bank metadata sizes')
    if metadata_stride != 24 or segments[1][1] != count*metadata_stride:
        raise ValueError('WBND entry metadata size/count mismatch')
    if alignment < 4 or alignment > 65535:
        raise ValueError('Invalid WBND entry alignment')
    has_names = bool(flags & 0x10000)
    if (has_names and (not name_stride or segments[3][1] != count*name_stride)
            or not has_names and segments[3][1]):
        raise ValueError('WBND entry name size/count mismatch')
    entries = []
    wave_data = region(bank, *segments[4], 'wave data')
    for index in range(count):
        offset = segments[1][0] + index*24
        fd, fmt, play_offset, play_length, loop_start, loop_length = integers(bank, offset, 6, 'wave metadata')
        payload = region(wave_data, play_offset, play_length, 'wave play region')
        tag, channels, sample_rate = fmt & 3, (fmt >> 2) & 7, (fmt >> 5) & 0x3ffff
        stored_alignment = (fmt >> 23) & 0xff
        if not channels or not sample_rate:
            raise ValueError('Invalid wave channel count or sample rate')
        block_align = (stored_alignment+22)*channels if tag == 2 else stored_alignment if tag == 0 else None
        duration = fd >> 4
        capacity = None
        if tag == 2:
            blocks, tail = divmod(play_length, block_align)
            if tail and tail < 7*channels:
                raise ValueError('Incomplete ADPCM block header')
            capacity = blocks*(block_align*2//channels-12) + (tail*2//channels-12 if tail else 0)
            if duration > capacity:
                raise ValueError('ADPCM sample duration exceeds available blocks')
        name = name_string(region(bank, segments[3][0]+index*name_stride, name_stride, 'wave name')) if has_names else None
        entries.append({'index': index, 'name': name, 'metadata_offset': wb_offset+offset,
            'flags_raw': fd & 15, 'format_raw': fmt, 'codec': ('PCM', 'XMA', 'MS_ADPCM', 'WMA')[tag],
            'channels': channels, 'sample_rate': sample_rate, 'block_align_bytes': block_align,
            'pcm_bit_depth': (16 if fmt >> 31 else 8) if tag == 0 else None,
            'duration_samples': duration, 'duration_seconds': duration/sample_rate,
            'available_adpcm_samples': capacity, 'loop_start_samples_raw': loop_start,
            'loop_length_samples_raw': loop_length, 'loop_fits_duration': loop_start+loop_length <= duration,
            'payload_offset': wb_offset+segments[4][0]+play_offset, 'payload_bytes': play_length,
            'payload_sha256': digest(payload)})
    return {'bank_name': name_string(bank_data[8:72]), 'sea_header_unknown_words': [unknown_a, unknown_b],
        'sound_bank': {'offset': sd_offset, 'bytes': sd_length, 'sha256': digest(sound), 'cue_graph_parsed': False},
        'wave_bank': {'offset': wb_offset, 'bytes': wb_length, 'sha256': digest(bank),
            'version': version, 'header_version': header_version, 'flags_raw': flags,
            'alignment': alignment, 'compact_format_raw': compact_raw,
            'segments': [{'offset_relative_to_wbnd': o, 'bytes': n} for o, n in segments]},
        'entry_count': count, 'entries': entries}


def verified_bytes(path, relative, inventory):
    data = path.read_bytes()
    expected = inventory.get(relative)
    if not expected or expected['bytes'] != len(data) or expected['sha256'] != digest(data):
        raise ValueError('Source differs from restored Steam inventory: ' + relative)
    return data


def config_rows(data):
    result = []
    for line_number, raw in enumerate(data.decode('ascii').splitlines(), 1):
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        columns = line.split()
        if len(columns) != 2:
            raise ValueError(f'Unrecognized audio configuration row {line_number}: {raw!r}')
        result.append({'line': line_number, 'columns_raw': columns})
    return result


def catalog(inventory_path):
    inventory_data = inventory_path.read_bytes()
    original = json.loads(inventory_data)
    game = Path(original['game_root'])
    inventory = {r['path']: r for r in original['files']}
    paths = sorted((game/'audio/xact').glob('*.SEA'), key=lambda p: p.name.casefold())
    expected = {p for p in inventory if p.lower().startswith('audio/xact/') and p.lower().endswith('.sea')}
    if {p.relative_to(game).as_posix() for p in paths} != expected:
        raise ValueError('SEA file set differs from restored Steam inventory')
    banks = []
    for path in paths:
        relative = path.relative_to(game).as_posix()
        data = verified_bytes(path, relative, inventory)
        bank = parse_sea(data)
        bank['source'] = {'path': str(path), 'relative_path': relative, 'bytes': len(data), 'sha256': digest(data)}
        banks.append(bank)
    candidates = {}
    for bank in banks:
        stem = Path(bank['source']['relative_path']).stem.casefold()
        if bank['bank_name'].casefold() == stem:
            candidates.setdefault(stem, []).append(bank['source']['relative_path'])
    configs = []
    for filename, bank_column in [('areabank.txt', 1), ('bankinfo.txt', 0)]:
        relative = 'audio/CONFIG/' + filename
        data = verified_bytes(game/relative, relative, inventory)
        rows = config_rows(data)
        for row in rows:
            stem = PureWindowsPath(row['columns_raw'][bank_column]).stem.casefold()
            row['sea_candidates_by_exact_bank_name'] = candidates.get(stem, [])
        configs.append({'source': {'path': str(game/relative), 'sha256': digest(data)}, 'rows': rows})
    entries = [entry for bank in banks for entry in bank['entries']]
    header_relative = 'audio/xact/AlrmClk.h'
    header = verified_bytes(game/header_relative, header_relative, inventory)
    declared = re.findall(r'XACT_WAVEBANK_ALRMCLK_(\w+)\s*=\s*(\d+)', header.decode('ascii'))
    alarm = next(bank for bank in banks if bank['source']['relative_path'] == 'audio/xact/AlrmClk.SEA')
    header_count = int(re.search(r'#define XACT_WAVEBANK_ALRMCLK_ENTRY_COUNT (\d+)', header.decode('ascii'))[1])
    if header_count != alarm['entry_count'] or [int(i) for _, i in declared] != list(range(header_count)):
        raise ValueError('Original AlrmClk authoring header disagrees with wave count/indices')
    comparisons = [{'index': int(index), 'header_enum_name': name,
        'wave_name': alarm['entries'][int(index)]['name'],
        'name_matches_case_insensitive': name == alarm['entries'][int(index)]['name'].upper()}
        for name, index in declared]
    return {'schema_version': 1, 'status': 'verified_wave_metadata_pending_decode_and_runtime',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'inventory': {'path': str(inventory_path), 'sha256': digest(inventory_data)},
        'format_references': [FORMAT_REFERENCE,
            'https://github.com/oh-ashen-one/bully-remake/blob/675ace2e88f9718ded9085af9c45c70715fc1e4d/tools/importers/audio.py'],
        'summary': {'banks': len(banks), 'wave_entries': len(entries),
            'source_bytes': sum(b['source']['bytes'] for b in banks),
            'codecs': dict(Counter(e['codec'] for e in entries)),
            'channels': dict(Counter(e['channels'] for e in entries)),
            'configuration_rows': sum(len(c['rows']) for c in configs),
            'unresolved_configuration_rows': sum(not row['sea_candidates_by_exact_bank_name'] for c in configs for row in c['rows']),
            'loop_regions_outside_duration': sum(not e['loop_fits_duration'] for e in entries)},
        'limitations': ['No sound decoded, played, imported, or enabled in Unreal.',
            'Loop regions and bank load policies do not imply automatic playback.',
            'Cue graphs, spatial emitters, footstep events, streaming PLAYLIST and speech/music remain separate work.',
            'Configuration joins are exact basename/internal-bank-name candidates, not proven loader behavior.'],
        'authoring_header_crosscheck': {'source': {'path': str(game/header_relative), 'sha256': digest(header)},
            'count_and_indices_match': True, 'entries': comparisons,
            'note': 'Original header name at index 1 omits Sml found in SEA; no alias or correction is inferred.'},
        'banks': banks, 'configurations': configs, 'ready_for_unreal': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, default=ROOT/'evidence/local_inventory-restored.json')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        raise ValueError('Use a new report path')
    result = catalog(args.inventory)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result['summary']))


if __name__ == '__main__':
    main()

"""Audit the existing pilot PCM WAVs without decoding or playing any audio.

SEA metadata uses the retained catalog parser. WAV headers, PCM hashes and every
ADPCM block's two seed samples are checked here independently of the exporter.
The remaining ADPCM samples are not decoded a second time by this validator.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import struct
import wave

from catalog_audio_banks import digest, parse_sea, verified_bytes

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'evidence/audio-wave-banks-v05-verified.json'
CATALOG_SHA256 = 'ea6375886c1b0dd9deb0066b26c9ab9eb077540e726e93f84538bde4e557ef0a'
INVENTORY = ROOT / 'evidence/local_inventory-restored.json'
BANKS = ('FeetAll', 'FeetSCHL', 'Birds')
EXPORT_STATUS = 'decoded_pending_cue_mapping_and_runtime_validation'
COEFFICIENTS = ((256, 0), (512, -256), (0, 0), (192, 64),
                (240, 0), (460, -208), (392, -232))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def lower_priority():
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.GetPriorityClass.argtypes = [ctypes.c_void_p]
        require(kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000),
                'Cannot set BelowNormal process priority')
        require(kernel.GetPriorityClass(kernel.GetCurrentProcess()) == 0x4000,
                'BelowNormal process priority was not applied')
    return 'BelowNormal' if os.name == 'nt' else 'default'


def reference(path, data=None):
    path = Path(path).resolve()
    data = path.read_bytes() if data is None else data
    return {'path': str(path), 'bytes': len(data), 'sha256': digest(data)}


def bounded_path(path, parent):
    path, parent = Path(path).resolve(), Path(parent).resolve()
    require(path != parent and path.is_relative_to(parent), f'Path must be inside {parent}')
    return path


def original_banks(exported):
    """Recheck pinned inventory, original SEA bytes, and freshly parsed metadata."""
    catalog_data = CATALOG.read_bytes()
    require(digest(catalog_data) == CATALOG_SHA256, 'Pinned catalog SHA256 mismatch')
    require(Path(exported['metadata_catalog']['path']).resolve() == CATALOG.resolve()
            and exported['metadata_catalog']['sha256'] == CATALOG_SHA256,
            'Export report metadata catalog identity mismatch')
    catalog = json.loads(catalog_data)
    require(catalog['schema_version'] == 1
            and catalog['status'] == 'verified_wave_metadata_pending_decode_and_runtime'
            and catalog['ready_for_unreal'] is False, 'Unexpected catalog status')
    inventory_data = INVENTORY.read_bytes()
    require(exported['inventory'] == catalog['inventory']
            and Path(catalog['inventory']['path']).resolve() == INVENTORY.resolve()
            and digest(inventory_data) == catalog['inventory']['sha256'],
            'Original inventory identity mismatch')
    inventory = json.loads(inventory_data)
    original = {entry['path']: entry for entry in inventory['files']}
    game = Path(inventory['game_root']).resolve()
    banks = []
    for name in BANKS:
        matches = [bank for bank in catalog['banks'] if bank['bank_name'] == name]
        require(len(matches) == 1, f'Expected one original bank: {name}')
        bank = matches[0]
        relative = f'audio/xact/{name}.SEA'
        source = bank['source']
        path = game / relative
        require(source['relative_path'] == relative
                and Path(source['path']).resolve() == path.resolve(),
                f'Original bank source path mismatch: {name}')
        data = verified_bytes(path, relative, original)
        require(len(data) == source['bytes'] and digest(data) == source['sha256'],
                f'Original bank size or SHA256 mismatch: {name}')
        require(parse_sea(data) == {k: v for k, v in bank.items() if k != 'source'},
                f'Fresh original SEA metadata differs from catalog: {name}')
        banks.append((bank, data))
    return catalog, banks


def export_entry_index(entries, expected):
    result = {}
    for entry in entries:
        key = (entry['bank_name'], entry['index'])
        require(key not in result, f'Duplicate exported wave: {key}')
        result[key] = entry
    require(set(result) == set(expected), 'Exported waves do not exactly cover the selected original banks')
    return result


def wrapper_digest(entry, payload):
    """Reconstruct only the original RIFF input envelope, never decode it."""
    fmt = struct.pack('<HHIIHHHHH', 2, 1, entry['sample_rate'],
                      entry['sample_rate'] * 70 // 128, 70, 4, 32, 128, 7)
    fmt += b''.join(struct.pack('<hh', *pair) for pair in COEFFICIENTS)
    chunks = b'fmt ' + struct.pack('<I', len(fmt)) + fmt
    chunks += b'fact' + struct.pack('<II', 4, entry['duration_samples'])
    chunks += b'data' + struct.pack('<I', len(payload)) + payload
    return digest(b'RIFF' + struct.pack('<I', 4 + len(chunks)) + b'WAVE' + chunks)


def verify_wave(record, entry, payload, wav_data):
    require(entry['codec'] == 'MS_ADPCM' and entry['channels'] == 1
            and entry['block_align_bytes'] == 70 and 0 < entry['sample_rate'] < 262144
            and len(payload) == entry['payload_bytes'] > 0 and len(payload) % 70 == 0
            and entry['duration_samples'] == len(payload) // 70 * 128
            and entry['available_adpcm_samples'] == entry['duration_samples'],
            'Unexpected pilot ADPCM profile or sample duration')
    require(digest(payload) == entry['payload_sha256'], 'Original payload SHA256 mismatch')
    require(record['original_wave_metadata'] == entry and record['original_name'] == entry['name'],
            'Exported original wave metadata mismatch')
    require(record['status'] == EXPORT_STATUS, 'Unexpected exported wave status')
    require(wrapper_digest(entry, payload) == record['adpcm_riff_sha256'],
            'Original ADPCM RIFF wrapper SHA256 mismatch')
    require(len(wav_data) == record['wav']['bytes']
            and digest(wav_data) == record['wav']['sha256'], 'Saved WAV size or SHA256 mismatch')

    pcm_bytes = entry['duration_samples'] * 2
    expected_header = struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', 36 + pcm_bytes,
        b'WAVE', b'fmt ', 16, 1, 1, entry['sample_rate'], entry['sample_rate'] * 2,
        2, 16, b'data', pcm_bytes)
    require(len(wav_data) == 44 + pcm_bytes and wav_data[:44] == expected_header,
            'PCM WAV header, duration or chunk layout mismatch')
    with wave.open(io.BytesIO(wav_data), 'rb') as reader:
        require((reader.getnchannels(), reader.getsampwidth(), reader.getframerate(),
                 reader.getnframes(), reader.getcomptype())
                == (1, 2, entry['sample_rate'], entry['duration_samples'], 'NONE'),
                'PCM WAV reader format mismatch')
        pcm = reader.readframes(reader.getnframes())
    expected_pcm = {'encoding': 'signed_16_bit_little_endian', 'bytes': pcm_bytes,
        'sha256': digest(pcm), 'channels': 1, 'sample_rate': entry['sample_rate'],
        'samples_per_channel': entry['duration_samples'], 'duration_seconds': entry['duration_seconds']}
    require(len(pcm) == pcm_bytes and record['pcm'] == expected_pcm,
            'PCM content hash or metadata mismatch')
    block_count = len(payload) // 70
    for block in range(block_count):
        source_offset, pcm_offset = block * 70, block * 256
        require(pcm[pcm_offset:pcm_offset + 4]
                == payload[source_offset + 5:source_offset + 7] + payload[source_offset + 3:source_offset + 5],
                f'ADPCM seed sample order/content mismatch at block {block}')
    return {'adpcm_blocks': block_count, 'seed_samples_compared': block_count * 2,
            'pcm_bytes': pcm_bytes, 'wav_bytes': len(wav_data)}


def validate(export_report=ROOT / 'evidence/audio-waves-v05.json',
             wav_root=ROOT / 'local/converted/audio-v05',
             report=ROOT / 'evidence/audio-waves-validation-v05.json'):
    priority = lower_priority()
    report = bounded_path(report, ROOT / 'evidence')
    require(not report.exists(), f'Validation report already exists: {report}')
    export_report = bounded_path(export_report, ROOT / 'evidence')
    wav_root = bounded_path(wav_root, ROOT / 'local/converted')
    export_bytes = export_report.read_bytes()
    exported = json.loads(export_bytes)
    require(exported['schema_version'] == 1 and exported['status'] == EXPORT_STATUS
            and exported['ready_for_unreal'] is False, 'Unexpected export report status')
    catalog, banks = original_banks(exported)
    expected = {(bank['bank_name'], entry['index']): (bank, data, entry)
                for bank, data in banks for entry in bank['entries']}
    require(len(expected) == 207, 'Unexpected pilot wave count')
    records = export_entry_index(exported['entries'], expected)
    validations = []
    seen_paths = set()
    for key, (bank, data, entry) in expected.items():
        record = records[key]
        require(record['source'] == bank['source'], f'Exported SEA source mismatch: {key}')
        name = re.sub(r'[^a-zA-Z0-9_-]', '_', entry['name'] or 'unnamed')[:100]
        path = (wav_root / key[0] / f'{entry["index"]:04d}_{name}.wav').resolve()
        require(path.is_relative_to(wav_root) and Path(record['wav']['path']).resolve() == path
                and path not in seen_paths, f'WAV path mismatch or duplication: {key}')
        seen_paths.add(path)
        start, size = entry['payload_offset'], entry['payload_bytes']
        payload = data[start:start + size]
        checked = verify_wave(record, entry, payload, path.read_bytes())
        validations.append({'bank_name': key[0], 'index': key[1], 'original_name': entry['name'],
            'source_sea': bank['source'], 'source_payload': {'offset': start, 'bytes': size,
                'sha256': entry['payload_sha256']}, 'adpcm_riff_sha256': record['adpcm_riff_sha256'],
            'destination_wav': record['wav'], 'pcm': record['pcm'],
            'verified': checked, 'status': 'passed_existing_file_checks'})
    summary = {'banks': len(banks), 'waves': len(validations),
        **{field: sum(v['verified'][field] for v in validations)
           for field in ('adpcm_blocks', 'seed_samples_compared', 'pcm_bytes', 'wav_bytes')}}
    require(all(exported['summary'][field] == summary[field]
                for field in ('banks', 'waves', 'pcm_bytes', 'wav_bytes')),
            'Export report summary mismatch')
    result = {'schema_version': 1, 'status': 'passed_existing_pcm_validation_pending_cues_and_runtime',
        'created_at_utc': datetime.now(timezone.utc).isoformat(), 'process_priority': priority,
        'metadata_catalog': reference(CATALOG), 'export_report': reference(export_report, export_bytes),
        'inventory': catalog['inventory'],
        'tools_at_validation': {'validator': reference(__file__),
            'sea_parser': reference(ROOT / 'tools/catalog_audio_banks.py'),
            'exporter_read_only_reference': reference(ROOT / 'tools/export_audio_waves.py')},
        'summary': summary,
        'checks': [
            'Pinned catalog SHA256 and matching original Steam inventory SHA256.',
            'Each selected original SEA path, size and SHA256; freshly parsed metadata and payload hashes.',
            'Exactly one exported record and original filename per wave in all three selected banks.',
            'Reconstructed ADPCM input RIFF envelope SHA256, without running a decoder.',
            'Saved WAV path, size and SHA256; exact PCM RIFF header with fmt/data chunks only.',
            'Independent standard-library WAV read: mono signed 16-bit PCM, original sample rate and sample count.',
            'All existing PCM bytes hashed against the export report.',
            'Every block: PCM samples 0/1 equal ADPCM header sample2/sample1, using 70-byte/128-sample blocks.',
            'Export summary totals match verified files.'],
        'execution': {'new_decode_runs': 0, 'external_processes': 0, 'playback': False,
                      'unreal': False, 'gpu': False},
        'limitations': [
            'This is not a second complete ADPCM decode: non-seed samples are checked by retained PCM hashes only.',
            'SEA metadata parsing is shared with the catalog; WAV and block-seed checks are independent of the exporter.',
            'Tool hashes identify files at validation time; the export report did not bind its exporter source version.',
            'Cue graphs, surface mappings, looping, spatial emitters and runtime behavior remain unvalidated.'],
        'ready_for_unreal': False, 'entries': validations}
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export-report', type=Path, default=ROOT / 'evidence/audio-waves-v05.json')
    parser.add_argument('--wav-root', type=Path, default=ROOT / 'local/converted/audio-v05')
    parser.add_argument('--report', type=Path, default=ROOT / 'evidence/audio-waves-validation-v05.json')
    args = parser.parse_args()
    print(json.dumps(validate(args.export_report, args.wav_root, args.report)['summary']))


if __name__ == '__main__':
    main()

"""Export three verified original banks to local PCM WAV, without playback.

Microsoft ADPCM is decoded by the installed FFmpeg executable, never by a
project decoder. Cue graphs, loop policy and runtime integration stay pending.
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
import subprocess
import wave

from catalog_audio_banks import digest, parse_sea, region, verified_bytes

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT/'evidence/audio-wave-banks-v05-verified.json'
CATALOG_SHA256 = 'ea6375886c1b0dd9deb0066b26c9ab9eb077540e726e93f84538bde4e557ef0a'
INVENTORY = ROOT/'evidence/local_inventory-restored.json'
BANKS = ('FeetAll', 'FeetSCHL', 'Birds')
MAX_WAVES = 256
MAX_PCM_BYTES = 8*1024*1024
# Public Microsoft ADPCM constants: DirectXTK MINIWAVEFORMAT::AdpcmFillCoefficientTable.
COEFFICIENTS = ((256, 0), (512, -256), (0, 0), (192, 64),
                (240, 0), (460, -208), (392, -232))
REFERENCE = 'https://github.com/microsoft/DirectXTK/blob/main/Audio/WaveBankReader.cpp'


def lower_priority():
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.GetPriorityClass.argtypes = [ctypes.c_void_p]
        if not kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000):
            raise OSError('Cannot set BelowNormal process priority')
        if kernel.GetPriorityClass(kernel.GetCurrentProcess()) != 0x4000:
            raise OSError('BelowNormal process priority was not applied')
    return 'BelowNormal' if os.name == 'nt' else 'default'


def child_flags():
    return subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == 'nt' else 0


def check_profile(entry):
    """Limit this pilot to the exact mono ADPCM layout verified in these banks."""
    if (entry['codec'] != 'MS_ADPCM' or entry['channels'] != 1
            or entry['block_align_bytes'] != 70 or not 0 < entry['sample_rate'] < 262144
            or entry['payload_bytes'] <= 0 or entry['payload_bytes'] % 70
            or entry['duration_samples'] != entry['payload_bytes']//70*128
            or entry['available_adpcm_samples'] != entry['duration_samples']):
        raise ValueError('Unverified ADPCM profile or sample duration')


def check_budget(banks):
    entries = [e for b in banks for e in b['entries']]
    if not 0 < len(entries) <= MAX_WAVES:
        raise ValueError(f'Pilot export must contain 1..{MAX_WAVES} waves')
    total = sum(e['duration_samples']*e['channels']*2 for e in entries)
    if not 0 < total <= MAX_PCM_BYTES:
        raise ValueError('Pilot PCM byte budget exceeded')
    return total


def verified_banks(catalog_path=CATALOG, expected_sha256=CATALOG_SHA256):
    catalog_bytes = Path(catalog_path).read_bytes()
    if digest(catalog_bytes) != expected_sha256:
        raise ValueError('Audio metadata catalog SHA256 mismatch')
    catalog = json.loads(catalog_bytes)
    if (catalog['schema_version'] != 1 or catalog['status'] != 'verified_wave_metadata_pending_decode_and_runtime'
            or catalog['ready_for_unreal'] is not False):
        raise ValueError('Unverified audio metadata catalog status')
    inventory_bytes = INVENTORY.read_bytes()
    if (Path(catalog['inventory']['path']).resolve() != INVENTORY.resolve()
            or digest(inventory_bytes) != catalog['inventory']['sha256']):
        raise ValueError('Steam inventory identity differs from catalog')
    inventory = json.loads(inventory_bytes)
    game = Path(inventory['game_root']).resolve()
    original = {entry['path']: entry for entry in inventory['files']}
    selected = []
    for name in BANKS:
        matches = [b for b in catalog['banks'] if b['bank_name'] == name]
        if len(matches) != 1:
            raise ValueError(f'Expected exactly one original {name} bank')
        bank = matches[0]
        source = bank['source']
        relative = f'audio/xact/{name}.SEA'
        path = game/relative
        if source['relative_path'] != relative or Path(source['path']).resolve() != path.resolve():
            raise ValueError('Bank source is outside its original Steam inventory path')
        data = verified_bytes(path, relative, original)
        if len(data) != source['bytes'] or digest(data) != source['sha256']:
            raise ValueError('SEA source SHA256 differs from catalog')
        parsed = parse_sea(data)
        if parsed != {k: v for k, v in bank.items() if k != 'source'}:
            raise ValueError('Fresh SEA metadata or payload SHA256 differs from catalog')
        for entry in parsed['entries']:
            check_profile(entry)
        selected.append((bank, data))
    check_budget([bank for bank, _ in selected])
    return catalog, selected


def riff_chunk(tag, data):
    return tag + struct.pack('<I', len(data)) + data + (b'\0' if len(data) % 2 else b'')


def adpcm_wave(entry, payload):
    check_profile(entry)
    if len(payload) != entry['payload_bytes'] or digest(payload) != entry['payload_sha256']:
        raise ValueError('ADPCM payload SHA256 or size mismatch')
    # WAVEFORMATEX (18 bytes), then samples/block, coefficient count and 7 pairs (32 bytes).
    fmt = struct.pack('<HHIIHHH', 2, entry['channels'], entry['sample_rate'],
                      entry['sample_rate']*70//128, 70, 4, 32)
    fmt += struct.pack('<HH', 128, len(COEFFICIENTS))
    fmt += b''.join(struct.pack('<hh', *pair) for pair in COEFFICIENTS)
    chunks = (riff_chunk(b'fmt ', fmt)
              + riff_chunk(b'fact', struct.pack('<I', entry['duration_samples']))
              + riff_chunk(b'data', payload))
    return b'RIFF' + struct.pack('<I', 4+len(chunks)) + b'WAVE' + chunks


def discover_ffmpeg():
    import imageio_ffmpeg
    executable = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    if not executable.is_file():
        raise ValueError('The installed FFmpeg executable is missing')
    version_command = [str(executable), '-version']
    version = subprocess.run(version_command, capture_output=True, text=True, check=True,
                             timeout=10, creationflags=child_flags()).stdout.splitlines()[0]
    return {'path': str(executable), 'sha256': digest(executable.read_bytes()),
            'version': version, 'version_command': version_command,
            'imageio_ffmpeg_version': imageio_ffmpeg.__version__,
            'priority': 'BelowNormal' if os.name == 'nt' else 'default', 'threads': 1}


def decode_command(executable):
    return [str(executable), '-hide_banner', '-loglevel', 'error', '-nostdin', '-xerror',
            '-threads', '1', '-f', 'wav', '-c:a', 'adpcm_ms', '-i', 'pipe:0',
            '-map', '0:a:0', '-vn', '-sn', '-dn', '-threads', '1',
            '-c:a', 'pcm_s16le', '-f', 's16le', 'pipe:1']


def decode_pcm(entry, payload, command):
    wrapped = adpcm_wave(entry, payload)
    result = subprocess.run(command, input=wrapped, capture_output=True, timeout=10,
                            creationflags=child_flags())
    if result.returncode or result.stderr:
        raise ValueError(f'FFmpeg decode failed: {result.returncode}: '
                         + result.stderr.decode('utf-8', errors='replace')[:2000])
    pcm = result.stdout
    if len(pcm) != entry['duration_samples']*entry['channels']*2:
        raise ValueError('Decoded PCM sample count differs from original duration')
    # The first two ADPCM seed samples have a defined order; no decoder is implemented here.
    if pcm[:4] != payload[5:7] + payload[3:5]:
        raise ValueError('Decoded PCM seed samples differ from original ADPCM header')
    return pcm, digest(wrapped)


def pcm_wave(entry, pcm):
    if len(pcm) != entry['duration_samples']*entry['channels']*2:
        raise ValueError('PCM WAV sample count differs from original duration')
    out = io.BytesIO()
    with wave.open(out, 'wb') as writer:
        writer.setnchannels(entry['channels'])
        writer.setsampwidth(2)
        writer.setframerate(entry['sample_rate'])
        writer.writeframes(pcm)
    data = out.getvalue()
    with wave.open(io.BytesIO(data), 'rb') as reader:
        if ((reader.getnchannels(), reader.getsampwidth(), reader.getframerate(), reader.getnframes())
                != (entry['channels'], 2, entry['sample_rate'], entry['duration_samples'])
                or reader.readframes(reader.getnframes()) != pcm):
            raise ValueError('Written WAV did not preserve PCM format and samples')
    return data


def new_path(path, parent):
    path, parent = Path(path).resolve(), Path(parent).resolve()
    if path == parent or not path.is_relative_to(parent):
        raise ValueError(f'Output must be inside {parent}')
    if path.exists():
        raise ValueError(f'Output already exists; use a new path: {path}')
    return path


def export(output=ROOT/'local/converted/audio-v05', report=ROOT/'evidence/audio-waves-v05.json'):
    priority = lower_priority()
    output = new_path(output, ROOT/'local/converted')
    report = new_path(report, ROOT/'evidence')
    catalog, selected = verified_banks()
    ffmpeg = discover_ffmpeg()
    command = decode_command(ffmpeg['path'])
    # Complete all source checks before creating any output. An interrupted directory is never reused.
    output.mkdir(parents=True, exist_ok=False)
    exported = []
    for bank, data in selected:
        bank_dir = output/bank['bank_name']
        bank_dir.mkdir()
        for entry in bank['entries']:
            payload = region(data, entry['payload_offset'], entry['payload_bytes'], 'ADPCM payload')
            pcm, wrapped_sha = decode_pcm(entry, payload, command)
            wav = pcm_wave(entry, pcm)
            safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', entry['name'] or 'unnamed')[:100]
            target = bank_dir/f"{entry['index']:04d}_{safe_name}.wav"
            with target.open('xb') as stream:
                stream.write(wav)
            if digest(target.read_bytes()) != digest(wav):
                raise ValueError('Saved WAV SHA256 mismatch')
            exported.append({'bank_name': bank['bank_name'], 'index': entry['index'],
                'original_name': entry['name'], 'source': bank['source'],
                'original_wave_metadata': entry, 'adpcm_riff_sha256': wrapped_sha,
                'pcm': {'encoding': 'signed_16_bit_little_endian', 'bytes': len(pcm),
                    'sha256': digest(pcm), 'channels': entry['channels'], 'sample_rate': entry['sample_rate'],
                    'samples_per_channel': entry['duration_samples'], 'duration_seconds': entry['duration_seconds']},
                'wav': {'path': str(target), 'bytes': len(wav), 'sha256': digest(wav)},
                'status': 'decoded_pending_cue_mapping_and_runtime_validation'})
    result = {'schema_version': 1, 'status': 'decoded_pending_cue_mapping_and_runtime_validation',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'metadata_catalog': {'path': str(CATALOG), 'sha256': CATALOG_SHA256},
        'inventory': catalog['inventory'], 'ffmpeg': ffmpeg, 'decode_command': command,
        'input_transport': 'RIFF WAVE fmt MS_ADPCM / fact / data via stdin',
        'output_transport': 'raw pcm_s16le via stdout, then standard-library PCM WAV writer',
        'wrapper_reference': REFERENCE, 'process_priority': priority,
        'summary': {'banks': len(selected), 'waves': len(exported), 'wave_limit': MAX_WAVES,
                    'pcm_bytes': sum(e['pcm']['bytes'] for e in exported),
                    'wav_bytes': sum(e['wav']['bytes'] for e in exported)},
        'limitations': ['No sound was played or imported into Unreal.',
            'Only original sample rate, channel count and duration were preserved; no resampling or normalization.',
            'Original loop regions remain metadata; no loop or autoplay policy is written into WAV.',
            'Cue graphs, spatial emitters, animation events and runtime behavior are not validated.'],
        'ready_for_unreal': False, 'entries': exported}
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'local/converted/audio-v05')
    parser.add_argument('--report', type=Path, default=ROOT/'evidence/audio-waves-v05.json')
    args = parser.parse_args()
    print(json.dumps(export(args.output, args.report)['summary']))


if __name__ == '__main__':
    main()

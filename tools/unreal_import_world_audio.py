"""Import 207 verified WAVs and a transparent operational world-audio policy.

Run in UE Python after compiling ReaBullyWorldAudio. BULLY_UE_REPORT must be new.
Import and validation never play audio. The builder places one WorldAudio actor.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from validate_audio_waves import original_banks, export_entry_index, verify_wave
from unreal_import_npcs import verify_import_origin

EXPORT = ROOT / 'evidence/audio-waves-v05.json'
VALIDATION = ROOT / 'evidence/audio-waves-validation-v05.json'
VALIDATION_SHA256 = 'b8a1d6fb23f35e5d70211b1bc092ba0ed6ed18d67dc72aa99d90e9888c735c45'
PHASES = ROOT / 'evidence/locomotion-audio-static-v05-r2.json'
PHASES_SHA256 = '747ef3fe6c84ccf63a5cd89d2caa54ba3b62672b6e950d86da245b97c07eb14d'
FOLDER = '/Game/Original/Audio'
DATA_PATH = FOLDER + '/DA_WorldAudio'
POLICY = {
    'classification': 'operational_reconstruction_with_original_assets',
    'footstep_clock': 'actual_UE_BlendSpace_phase_crosses_original_CAT_markers_divided_by_HXD_clip_duration',
    'native_basis': ['0x731ce0 phase threshold producer', '0x60ed90 event latch consumer',
                     '0x60e730 ground/foot position lookup'],
    'gait_selection': 'nearest source gait speed to filtered BlendSpace input; original blend semantics incomplete',
    'cue_graph_recovered': False, 'COL_surface_to_audio_mapping_recovered': False,
    'exterior_steps': 'FeetAll indexes 60,61,62,63: original sneaker cement samples, round-robin reconstruction',
    'interior_steps': 'FeetSCHL indexes 42,43,44: original sneaker lino samples, round-robin reconstruction',
    'ambience': 'Birds index0 BirdLoop_8va repeats only while MainMap is ready; original emitter/schedule unknown',
    'source_loop_metadata': 'retained unchanged, not interpreted as general autoplay permission',
    'footstep_left_right_assignment': 'unknown; event A/B positions use the lower original foot bone',
    'phase_unit_conversion': 'CAT scalar / HXD duration is operational reconstruction, native blend/units not fully proved',
    'runtime_validation': 'pending_final_scene',
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def checked(record):
    path = ROOT / record['path']
    raw = path.read_bytes()
    if digest(raw) != record['sha256']:
        raise ValueError('Changed audio evidence: ' + str(path))
    return raw


def phase_records():
    source = json.loads(checked({'path': str(PHASES), 'sha256': PHASES_SHA256}))
    cat = next(s for s in source['cat_sources'] if s['path'].endswith('/GlobalLoco.cat'))
    cat_raw = checked(cat)
    checked(source['sources']['mainped_hxd'])
    for native in source['native_sources']:
        if native['address'] in ('0x731ce0', '0x60ed90', '0x60e730'):
            checked(native)
    result = []
    # Speeds are the existing original player BlendSpace samples, cm/s.
    for node, name, cue, speed in ((9581, 'C_PLAYER\\PLAYERWALK', 'Walk', 204.625),
                                   (6403, 'C_PLAYER\\PLAYERRUN', 'Run', 361.375),
                                   (6358, 'C_PLAYER\\RUNFAST', 'Fast_Run', 801.2)):
        tracks = [t for t in source['selected_cat_tracks'] if t['cat'] == 'GlobalLoco.cat' and t['node_file_offset'] == node]
        animation = next(t for t in tracks if t['track']['type'] == 'LocomotionAnimationNew')
        event = next(t for t in tracks if t['track']['type'] == 'LocomotionEvent')
        record = next(h for h in source['hxd_records_without_events'] if h['name'] == name)
        attrs = animation['track']['attrs']
        if attrs['24']['uint'] != record['name_hash'] or any(event['track']['attrs'][field]['string'] != cue for field in ('24', '36')):
            raise ValueError('CAT/HXD footstep identity differs')
        markers = [attrs[field]['float'] for field in ('32', '40')]
        for field, marker in zip(('32', '40'), markers):
            if struct.unpack_from('<f', cat_raw, attrs[field]['file_offset'])[0] != marker or not 0 <= marker < record['duration']:
                raise ValueError('Original CAT marker bytes differ or exceed clip duration')
        result.append({'original_clip': name, 'original_cue': cue, 'source_duration': record['duration'],
            'source_speed_cm': speed, 'source_markers': markers,
            'source_evidence': {'cat': cat, 'animation_track': animation, 'event_track': event,
                'hxd_record': record, 'phase_report_sha256': PHASES_SHA256,
                'conversion_policy': POLICY['phase_unit_conversion']}})
    return result


def select_wave_indices(entries):
    index = {(e['bank_name'], e['index']): i for i, e in enumerate(entries)}
    expected = [('FeetAll', 60, 'Sneaker Walk Cemnt_03'), ('FeetAll', 61, 'Sneaker Walk Cemnt_04'),
                ('FeetAll', 62, 'Sneaker Walk Cemnt_06'), ('FeetAll', 63, 'Sneaker Walk Cemnt_08'),
                ('FeetSCHL', 42, 'Sneaker Walk Lino 01_EQ8va'), ('FeetSCHL', 43, 'Sneaker Walk Lino 02_EQ8va'),
                ('FeetSCHL', 44, 'Sneaker Walk Lino 03_EQ8va'), ('Birds', 0, 'BirdLoop_8va')]
    selected = []
    for bank, number, name in expected:
        i = index[(bank, number)]
        if entries[i]['original_name'] != name:
            raise ValueError('Operational cue sample identity changed')
        selected.append(i)
    return selected[:4], selected[4:7], selected[7]


def read_sources():
    validation = json.loads(checked({'path': str(VALIDATION), 'sha256': VALIDATION_SHA256}))
    if validation['status'] != 'passed_existing_pcm_validation_pending_cues_and_runtime':
        raise ValueError('Independent WAV validation did not pass')
    export_bytes = checked(validation['export_report'])
    if Path(validation['export_report']['path']).resolve() != EXPORT.resolve():
        raise ValueError('Validation belongs to another export report')
    exported = json.loads(export_bytes)
    _, banks = original_banks(exported)
    expected = {(bank['bank_name'], e['index']): (bank, data, e) for bank, data in banks for e in bank['entries']}
    records = export_entry_index(exported['entries'], expected)
    if len(records) != 207:
        raise ValueError('Expected all 207 independently validated waves')
    imported = []
    for entry in exported['entries']:
        bank, data, original = expected[(entry['bank_name'], entry['index'])]
        if entry['source'] != bank['source']:
            raise ValueError('Original SEA source changed')
        path = Path(entry['wav']['path']).resolve()
        if not path.is_relative_to((ROOT / 'local/converted/audio-v05').resolve()):
            raise ValueError('WAV outside verified export directory')
        raw = path.read_bytes()
        start = original['payload_offset']
        verify_wave(entry, original, data[start:start + original['payload_bytes']], raw)
        imported.append({**entry, 'source_md5': hashlib.md5(raw).hexdigest(),
                         'pcm_md5': hashlib.md5(raw[44:]).hexdigest()})
    return imported, phase_records(), digest(export_bytes)


def asset_name(entry):
    safe = re.sub(r'[^a-zA-Z0-9_]', '_', entry['original_name'])[:100]
    return f'SW_{entry["index"]:04d}_{safe}'


def verify_native_phase_boundaries(unreal):
    cases = [(0.1, 0.2, 0.15, True), (0.1, 0.2, 0.1, False),
             (0.1, 0.2, 0.2, True), (0.9, 0.1, 0.0, True),
             (0.9, 0.1, 0.95, True), (0.9, 0.1, 0.5, False),
             (0.5, 0.5, 0.5, False), (-1.0, 0.2, 0.1, False),
             (0.1, 0.2, 1.0, False), (float('nan'), 0.2, 0.1, False)]
    for previous, current, marker, expected in cases:
        if unreal.ReaBullyWorldAudioTools.crossed_phase_marker(previous, current, marker) != expected:
            raise RuntimeError('Native footstep phase crossing semantics failed')
    return len(cases)


def main():
    import unreal
    report = Path(os.environ['BULLY_UE_REPORT'])
    if report.exists():
        raise ValueError('New audio import report required')
    if not hasattr(unreal, 'ReaBullyWorldAudioTools') or not hasattr(unreal, 'ReaBullyWorldAudioData'):
        raise RuntimeError('Compile ReaBullyWorldAudio before import')
    native_phase_tests = verify_native_phase_boundaries(unreal)
    entries, phases, export_sha = read_sources()
    exterior, interior, bird = select_wave_indices(entries)
    data = unreal.load_asset(DATA_PATH) if unreal.EditorAssetLibrary.does_asset_exist(DATA_PATH) else None
    if data and (not isinstance(data, unreal.ReaBullyWorldAudioData) or
                 unreal.EditorAssetLibrary.get_metadata_tag(data, 'REAOriginalAudioExportSHA256') != export_sha):
        raise RuntimeError('Preserve unrelated existing audio data asset')
    records, waves, imported = [], [], []
    for index, entry in enumerate(entries):
        folder = FOLDER + '/Waves/' + entry['bank_name']
        path = folder + '/' + asset_name(entry)
        asset = unreal.load_asset(path) if unreal.EditorAssetLibrary.does_asset_exist(path) else None
        if not asset:
            factory = unreal.SoundFactory()
            factory.set_editor_property('auto_create_cue', False)
            task = unreal.AssetImportTask()
            task.filename = entry['wav']['path']
            task.destination_path = folder
            task.destination_name = asset_name(entry)
            task.factory = factory
            task.automated = True
            task.replace_existing = False
            task.save = True
            unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
            asset = unreal.load_asset(path)
        if not isinstance(asset, unreal.SoundWave):
            raise RuntimeError('Expected original SoundWave: ' + path)
        origin = verify_import_origin(unreal, asset, Path(entry['wav']['path']), entry['source_md5'])
        pcm = entry['pcm']
        if not unreal.ReaBullyWorldAudioTools.verify_and_configure_wave(asset, pcm['sample_rate'],
                pcm['channels'], pcm['samples_per_channel'], entry['pcm_md5'], index == bird):
            raise RuntimeError('Imported PCM content/rate/channel/count differs: ' + path)
        previous = unreal.EditorAssetLibrary.get_metadata_tag(asset, 'REAOriginalWavSHA256')
        if previous and previous != entry['wav']['sha256']:
            raise RuntimeError('Existing wave has incompatible original provenance')
        for key, value in {'REAOriginalWavSHA256': entry['wav']['sha256'],
                'REAOriginalSeaSHA256': entry['source']['sha256'],
                'REAOriginalWaveName': entry['original_name'],
                'REAOriginalMetadata': json.dumps(entry['original_wave_metadata']),
                'REAPlaybackPolicy': 'reconstructed_MainMap_ambience_loop' if index == bird else 'one_shot_no_autoplay'}.items():
            unreal.EditorAssetLibrary.set_metadata_tag(asset, key, value)
        unreal.EditorAssetLibrary.save_loaded_asset(asset, only_if_is_dirty=False)
        row = unreal.ReaBullyOriginalWave()
        for key, value in {'bank': entry['bank_name'], 'original_index': entry['index'],
                'original_name': entry['original_name'], 'wave': asset, 'wav_sha256': entry['wav']['sha256'],
                'original_metadata_json': json.dumps({k: v for k, v in entry.items() if k not in ('source_md5', 'pcm_md5')})}.items():
            row.set_editor_property(key, value)
        waves.append(row)
        imported.append({'asset': asset.get_path_name(), 'bank': entry['bank_name'], 'index': entry['index'],
                         'original_name': entry['original_name'], 'wav_sha256': entry['wav']['sha256'],
                         'pcm_md5': entry['pcm_md5'], 'saved_import_origin': origin,
                         'saved_imported_pcm_verified': True, 'loop': index == bird})
    if not data:
        factory = unreal.DataAssetFactory()
        factory.set_editor_property('data_asset_class', unreal.ReaBullyWorldAudioData)
        data = unreal.AssetToolsHelpers.get_asset_tools().create_asset('DA_WorldAudio', FOLDER, unreal.ReaBullyWorldAudioData, factory)
    if not data:
        raise RuntimeError('Could not create original world audio data')
    for phase in phases:
        row = unreal.ReaBullyFootstepPhase()
        for key in ('original_clip', 'original_cue', 'source_duration', 'source_speed_cm'):
            row.set_editor_property(key, phase[key])
        row.set_editor_property('source_markers', unreal.Vector2D(*phase['source_markers']))
        row.set_editor_property('source_evidence_json', json.dumps(phase['source_evidence']))
        records.append(row)
    for key, value in {'export_report_sha256': export_sha, 'validation_report_sha256': VALIDATION_SHA256,
            'playback_policy_json': json.dumps(POLICY), 'waves': waves, 'footstep_phases': records,
            'exterior_step_wave_indices': exterior, 'interior_step_wave_indices': interior, 'bird_wave_index': bird}.items():
        data.set_editor_property(key, value)
    unreal.EditorAssetLibrary.set_metadata_tag(data, 'REAOriginalAudioExportSHA256', export_sha)
    unreal.EditorAssetLibrary.save_loaded_asset(data, only_if_is_dirty=False)
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open('x', encoding='utf-8') as stream:
        json.dump({'status': 'audio_assets_imported_pcm_verified_runtime_pending', 'data': data.get_path_name(),
            'waves': len(imported), 'export_report_sha256': export_sha, 'validation_report_sha256': VALIDATION_SHA256,
            'phase_report_sha256': PHASES_SHA256, 'policy': POLICY, 'phase_records': phases,
            'entries': imported, 'engine': unreal.SystemLibrary.get_engine_version(),
            'native_phase_boundary_tests_passed': native_phase_tests,
            'playback_during_import': False}, stream, indent=2)
    unreal.log('REA_WORLD_AUDIO_IMPORTED waves=207 data=' + data.get_path_name())


if __name__ == '__main__':
    main()

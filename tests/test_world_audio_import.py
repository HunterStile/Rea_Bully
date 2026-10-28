"""Original audio provenance and exact operational selection; never launch UE."""
import copy
import importlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import unreal_import_world_audio as audio
from validate_audio_waves import lower_priority


class WorldAudioImport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lower_priority()
        cls.entries, cls.phases, cls.export_sha = audio.read_sources()

    def test_all_verified_waves_and_exact_named_samples_are_selected(self):
        self.assertEqual(len(self.entries), 207)
        exterior, interior, bird = audio.select_wave_indices(self.entries)
        self.assertEqual(exterior, [60, 61, 62, 63])
        self.assertEqual(interior, [185, 186, 187])
        self.assertEqual(bird, 206)
        self.assertEqual(self.entries[bird]['original_name'], 'BirdLoop_8va')
        paths = [e['bank_name'] + '/' + audio.asset_name(e) for e in self.entries]
        self.assertEqual(len(set(paths)), 207)
        self.assertFalse(audio.POLICY['cue_graph_recovered'])
        self.assertFalse(audio.POLICY['COL_surface_to_audio_mapping_recovered'])

    def test_wave_name_cannot_be_substituted_at_same_original_bank_index(self):
        changed = copy.deepcopy(self.entries)
        changed[60]['original_name'] = 'Shoe_Grass_06'
        with self.assertRaisesRegex(ValueError, 'sample identity'):
            audio.select_wave_indices(changed)

    def test_cat_phase_values_match_exact_source_bytes_and_hxd_durations(self):
        self.assertEqual([p['original_cue'] for p in self.phases], ['Walk', 'Run', 'Fast_Run'])
        self.assertEqual([p['source_speed_cm'] for p in self.phases], [204.625, 361.375, 801.2])
        self.assertEqual(self.phases[2]['source_markers'], [0.0, 0.25])
        self.assertEqual(self.phases[2]['source_duration'], 0.5)
        for phase in self.phases:
            self.assertTrue(all(0 <= value < phase['source_duration'] for value in phase['source_markers']))
            self.assertEqual(phase['source_evidence']['hxd_record']['name'], phase['original_clip'])

    def test_changed_cat_marker_is_rejected_even_if_read_bypasses_hash(self):
        original_checked = audio.checked
        def changed(record):
            raw = original_checked(record)
            if record['path'].endswith('/GlobalLoco.cat'):
                raw = bytearray(raw)
                struct.pack_into('<f', raw, 20448, 0.125)
                return bytes(raw)
            return raw
        with patch.object(audio, 'checked', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'marker bytes'):
                audio.phase_records()

    def test_validation_report_tampering_fails_before_original_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'validation.json'
            path.write_bytes(audio.VALIDATION.read_bytes() + b' ')
            with patch.object(audio, 'VALIDATION', path), patch.object(audio, 'original_banks') as sources:
                with self.assertRaisesRegex(ValueError, 'Changed audio evidence'):
                    audio.read_sources()
                sources.assert_not_called()

    def test_import_has_no_source_reads_and_requires_no_unreal_module(self):
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('read during import')):
            importlib.reload(audio)


if __name__ == '__main__':
    unittest.main()

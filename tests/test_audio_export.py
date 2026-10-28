"""Source, sample count and no-overwrite checks; never play sound."""
import copy
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import export_audio_waves as audio


class AudioExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        audio.lower_priority()
        cls.catalog, cls.banks = audio.verified_banks()

    def test_selected_sources_metadata_and_budget_are_verified(self):
        self.assertEqual([b['bank_name'] for b, _ in self.banks], ['FeetAll', 'FeetSCHL', 'Birds'])
        self.assertEqual(sum(b['entry_count'] for b, _ in self.banks), 207)
        self.assertEqual(audio.check_budget([b for b, _ in self.banks]), 1703936)
        with self.assertRaisesRegex(ValueError, '1..256'):
            audio.check_budget([{'entries': [self.banks[0][0]['entries'][0]]*257}])

    def test_catalog_mutation_is_rejected_before_source_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'catalog.json'
            path.write_bytes(audio.CATALOG.read_bytes()+b' ')
            with self.assertRaisesRegex(ValueError, 'catalog SHA256'):
                audio.verified_banks(path)

    def test_resigned_catalog_cannot_change_original_metadata_or_source_path(self):
        for field in ('duration', 'source'):
            modified = copy.deepcopy(self.catalog)
            bank = next(b for b in modified['banks'] if b['bank_name'] == 'FeetAll')
            if field == 'duration':
                bank['entries'][0]['duration_samples'] -= 1
            else:
                bank['source']['path'] = str(ROOT/'not-the-original.SEA')
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'catalog.json'
                raw = json.dumps(modified).encode()
                path.write_bytes(raw)
                with self.assertRaisesRegex(ValueError, 'metadata|original Steam'):
                    audio.verified_banks(path, audio.digest(raw))

    def test_changed_payload_and_wrong_duration_cannot_reach_ffmpeg(self):
        bank, data = self.banks[0]
        entry = bank['entries'][0]
        payload = data[entry['payload_offset']:entry['payload_offset']+entry['payload_bytes']]
        changed = bytes([payload[0] ^ 1]) + payload[1:]
        with patch.object(audio.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'payload SHA256'):
                audio.decode_pcm(entry, changed, ['must-not-run'])
            wrong = copy.deepcopy(entry)
            wrong['duration_samples'] -= 1
            with self.assertRaisesRegex(ValueError, 'sample duration'):
                audio.decode_pcm(wrong, payload, ['must-not-run'])
            run.assert_not_called()

    def test_real_ffmpeg_preserves_three_original_bank_samples(self):
        ffmpeg = audio.discover_ffmpeg()
        command = audio.decode_command(ffmpeg['path'])
        for bank, data in self.banks:
            entry = bank['entries'][0]
            with self.subTest(bank=bank['bank_name']):
                payload = data[entry['payload_offset']:entry['payload_offset']+entry['payload_bytes']]
                pcm, _ = audio.decode_pcm(entry, payload, command)
                # Microsoft block header carries sample1 at byte3 and sample2 at byte5.
                first, second = struct.unpack_from('<hh', payload, 3)
                self.assertEqual(struct.unpack_from('<hh', pcm), (second, first))
                wav = audio.pcm_wave(entry, pcm)
                with wave.open(io.BytesIO(wav), 'rb') as stream:
                    self.assertEqual(stream.getnframes(), entry['duration_samples'])
                    self.assertEqual(stream.getframerate(), entry['sample_rate'])
                    self.assertEqual(stream.getnchannels(), 1)
                    self.assertEqual(stream.getsampwidth(), 2)
                    self.assertEqual(stream.readframes(stream.getnframes()), pcm)

    def test_decoder_short_output_is_not_accepted_or_padded(self):
        bank, data = self.banks[0]
        entry = bank['entries'][0]
        payload = data[entry['payload_offset']:entry['payload_offset']+entry['payload_bytes']]
        result = audio.subprocess.CompletedProcess([], 0, stdout=b'\0\0', stderr=b'')
        with patch.object(audio.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(ValueError, 'sample count'):
                audio.decode_pcm(entry, payload, ['not-executed'])

    def test_output_refuses_existing_paths_and_paths_outside_own_root(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            existing = parent/'existing'
            existing.mkdir()
            with self.assertRaisesRegex(ValueError, 'already exists'):
                audio.new_path(existing, parent)
            with self.assertRaisesRegex(ValueError, 'inside'):
                audio.new_path(parent/'..'/'other', parent)
            self.assertEqual(audio.new_path(parent/'new', parent), parent/'new')


if __name__ == '__main__':
    unittest.main()

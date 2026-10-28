"""Exercise the read-only WAV audit with tiny synthetic two-block fixtures."""
import copy
import importlib
import io
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import validate_audio_waves as audit


class AudioValidation(unittest.TestCase):
    def setUp(self):
        self.payload = (struct.pack('<Bhhh', 0, 16, 101, -102) + bytes(63)
                        + struct.pack('<Bhhh', 0, 16, 203, -204) + bytes(63))
        self.entry = {'name': 'synthetic', 'codec': 'MS_ADPCM', 'channels': 1,
            'block_align_bytes': 70, 'sample_rate': 14700, 'payload_bytes': 140,
            'payload_sha256': audit.digest(self.payload), 'duration_samples': 256,
            'available_adpcm_samples': 256, 'duration_seconds': 256 / 14700}
        self.pcm = (struct.pack('<hh', -102, 101) + bytes(252)
                    + struct.pack('<hh', -204, 203) + bytes(252))

    def record(self, pcm):
        out = io.BytesIO()
        with wave.open(out, 'wb') as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(14700)
            writer.writeframes(pcm)
        wav = out.getvalue()
        record = {'original_wave_metadata': copy.deepcopy(self.entry), 'original_name': 'synthetic',
            'status': audit.EXPORT_STATUS,
            'adpcm_riff_sha256': audit.wrapper_digest(self.entry, self.payload),
            'wav': {'bytes': len(wav), 'sha256': audit.digest(wav)},
            'pcm': {'encoding': 'signed_16_bit_little_endian', 'bytes': len(pcm),
                'sha256': audit.digest(pcm), 'channels': 1, 'sample_rate': 14700,
                'samples_per_channel': 256, 'duration_seconds': 256 / 14700}}
        return record, wav

    def test_every_block_seed_is_checked_in_original_order(self):
        record, wav = self.record(self.pcm)
        checked = audit.verify_wave(record, self.entry, self.payload, wav)
        self.assertEqual(checked['adpcm_blocks'], 2)
        self.assertEqual(checked['seed_samples_compared'], 4)
        # A rewritten PCM hash must not hide a bad seed in a later block.
        bad = self.pcm[:256] + struct.pack('<hh', 203, -204) + self.pcm[260:]
        record, wav = self.record(bad)
        with self.assertRaisesRegex(ValueError, 'seed.*block 1'):
            audit.verify_wave(record, self.entry, self.payload, wav)

    def test_pcm_and_header_changes_are_rejected_even_with_resigned_wav_hash(self):
        for offset, error in ((100, 'PCM content hash'), (24, 'PCM WAV header')):
            record, original = self.record(self.pcm)
            changed = bytearray(original)
            changed[offset] ^= 1
            record['wav']['sha256'] = audit.digest(changed)
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, error):
                audit.verify_wave(record, self.entry, self.payload, bytes(changed))

    def test_original_payload_changes_are_rejected(self):
        record, wav = self.record(self.pcm)
        changed = bytes([1]) + self.payload[1:]
        with self.assertRaisesRegex(ValueError, 'payload SHA256'):
            audit.verify_wave(record, self.entry, changed, wav)

    def test_missing_extra_and_duplicate_wave_records_are_rejected(self):
        record = {'bank_name': 'test', 'index': 0}
        expected = {('test', 0)}
        self.assertEqual(set(audit.export_entry_index([record], expected)), expected)
        for entries in ([], [record, record], [record, {'bank_name': 'test', 'index': 1}]):
            with self.subTest(entries=entries), self.assertRaisesRegex(ValueError, 'Duplicate|exactly cover'):
                audit.export_entry_index(entries, expected)

    def test_existing_report_is_rejected_before_original_source_reads(self):
        with patch.object(audit, 'lower_priority'), patch.object(audit, 'original_banks') as sources:
            with self.assertRaisesRegex(ValueError, 'already exists'):
                audit.validate(report=ROOT / 'evidence/audio-waves-v05.json')
            sources.assert_not_called()

    def test_import_does_not_read_files_or_change_priority(self):
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('import read a file')):
            with patch.object(audit.ctypes, 'WinDLL', side_effect=AssertionError('import changed priority'), create=True):
                importlib.reload(audit)


if __name__ == '__main__':
    unittest.main()

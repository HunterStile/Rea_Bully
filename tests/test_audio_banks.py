"""Compare original wave metadata to the game's independent generated C header.

No payload is decoded or played. Corruptions target real SEA boundaries, not a
synthetic encoder that repeats the parser's layout assumptions.
"""
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from catalog_audio_banks import config_rows, parse_sea, verified_bytes


class AudioBanks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        inventory = json.loads((ROOT/'evidence/local_inventory-restored.json').read_text())
        cls.game = Path(inventory['game_root'])
        cls.inventory = {r['path']: r for r in inventory['files']}
        relative = 'audio/xact/AlrmClk.SEA'
        if not (cls.game/relative).exists():
            raise unittest.SkipTest('Local original Steam audio is required')
        cls.data = verified_bytes(cls.game/relative, relative, cls.inventory)

    def test_original_generated_header_confirms_indices_but_has_one_stale_name(self):
        header = verified_bytes(self.game/'audio/xact/AlrmClk.h', 'audio/xact/AlrmClk.h', self.inventory).decode('ascii')
        expected = re.findall(r'XACT_WAVEBANK_ALRMCLK_(\w+)\s*=\s*(\d+)', header)
        bank = parse_sea(self.data)
        self.assertEqual([str(e['index']) for e in bank['entries']], [index for _, index in expected])
        self.assertEqual(bank['entries'][0]['name'].upper(), expected[0][0])
        # Preserve the actual discrepancy instead of normalizing away the suffix.
        self.assertEqual(expected[1][0], 'ALRMCLOCKEND16K')
        self.assertEqual(bank['entries'][1]['name'].upper(), 'ALRMCLOCKEND16KSML')
        count = int(re.search(r'#define XACT_WAVEBANK_ALRMCLK_ENTRY_COUNT (\d+)', header)[1])
        self.assertEqual(bank['entry_count'], count)
        for entry in bank['entries']:
            a, n = entry['payload_offset'], entry['payload_bytes']
            self.assertEqual(hashlib.sha256(self.data[a:a+n]).hexdigest(), entry['payload_sha256'])

    def test_source_inventory_rejects_changed_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'AlrmClk.SEA'
            changed = bytearray(self.data)
            changed[-1] ^= 1
            path.write_bytes(changed)
            with self.assertRaisesRegex(ValueError, 'inventory'):
                verified_bytes(path, 'audio/xact/AlrmClk.SEA', self.inventory)

    def test_container_extent_segment_overlap_and_wave_offset_are_bounded(self):
        wb_offset = sum(struct.unpack_from('<2I', self.data))
        metadata = struct.unpack_from('<I', self.data, wb_offset+20)[0]
        cases = []
        for offset, value in [(8, 2**32-1), (wb_offset+4, 40),
                              (wb_offset+20, 52), (wb_offset+metadata+8, 2**32-1)]:
            changed = bytearray(self.data)
            struct.pack_into('<I', changed, offset, value)
            cases.append(changed)
        cases.append(self.data[:-1])
        for changed in cases:
            with self.subTest(head=bytes(changed[:20]).hex()), self.assertRaises(ValueError):
                parse_sea(changed)

    def test_unknown_sea_words_and_loop_region_never_create_playback_policy(self):
        relative = 'audio/xact/FeetAll.SEA'
        bank = parse_sea(verified_bytes(self.game/relative, relative, self.inventory))
        self.assertEqual(bank['sea_header_unknown_words'], [137, 14])
        self.assertTrue(any(e['loop_length_samples_raw'] for e in bank['entries']))
        self.assertFalse(any('looping' in e or 'autoplay' in e for e in bank['entries']))

    def test_config_preserves_original_policy_and_duplicates_but_ignores_comments(self):
        rows = config_rows(b'# MainMap fake.bnk\r\nSchool_Hallways FEET\\FeetSCHL.bnk\r\n'
                           b'School_Hallways FEET\\FeetSCHL.bnk # duplicate retained\r\n')
        self.assertEqual([r['line'] for r in rows], [2, 3])
        self.assertEqual(rows[0]['columns_raw'], rows[1]['columns_raw'])
        with self.assertRaises(ValueError):
            config_rows(b'Area bank.bnk unexpected_column')


if __name__ == '__main__':
    unittest.main()

"""Parser/evidence regressions; these do not execute CATs or the game."""
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from catalog_prop_actions import Cat, clip_matches, garage_dat_bindings, garage_script_evidence, hashstring, verified_bytes


def synthetic_cat():
    root = b'b' + struct.pack('<IBH', hashstring('Root') | 0x80000000, 0, 1)
    leaf = b'l' + struct.pack('<IBBIH', hashstring('Leaf') | 0x80000000, 0, 1, 0, 0)
    tree = struct.pack('<II', 0, 0) + root + leaf
    end = 32 + len(tree)
    track = struct.pack('<HHIHI', 0, 5, hashstring('PropAnimation'), (24 << 3) | 4, hashstring('CARNI01\\CARNY_BARK'))
    size = end + len(track)
    return struct.pack('<8I', size, end, size, end, 0, 1, 0, 1) + tree + track


class PropActionTests(unittest.TestCase):
    def test_import_has_no_game_file_reads_or_scans(self):
        path = Path(__file__).resolve().parents[1] / 'tools/catalog_prop_actions.py'
        spec = importlib.util.spec_from_file_location('prop_catalog_import_probe', path)
        module = importlib.util.module_from_spec(spec)
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('unexpected read')), \
             patch.object(Path, 'read_text', side_effect=AssertionError('unexpected read')), \
             patch.object(Path, 'glob', side_effect=AssertionError('unexpected scan')):
            spec.loader.exec_module(module)

    def test_exact_hash_reference_preserves_absolute_offset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'Root.cat'
            path.write_bytes(synthetic_cat())
            cat = Cat(path, ['PropAnimation', 'Leaf'])
            leaf = list(cat.flat())[1]
            self.assertEqual(leaf['name'], 'Leaf')
            track = cat.track(leaf['track_offsets'][0])
            expected = hashstring('CARNI01\\CARNY_BARK')
            self.assertEqual(track['attrs']['24']['uint'], expected)
            self.assertEqual(track['attrs']['24']['file_offset'], cat.data + 10)
            self.assertEqual(clip_matches(track, {expected: ['x']}, {}),
                [{'kind': 'exact_hxd_full_name_hash', 'attribute': 24,
                  'file_offset': cat.data + 10, 'clip_ids': ['x']}])

    def test_short_name_is_not_full_hash_binding(self):
        track = {'attrs': {'88': {'string': 'OPEN', 'file_offset': 50, 'uint': 0}}}
        result = clip_matches(track, {123: ['specific']}, {'OPEN': ['door', 'garage']})
        self.assertEqual(result[0]['kind'], 'short_name_compatibility_only_model_binding_unproven')
        self.assertEqual(result[0]['clip_ids'], ['door', 'garage'])
        self.assertNotIn('specific', result[0]['clip_ids'])
        self.assertNotIn('autoplay', result[0])

    def test_rejects_header_extent_padding_and_track_overrun(self):
        raw = synthetic_cat()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.cat'
            path.write_bytes(raw + b'\x01')
            with self.assertRaises(ValueError):
                Cat(path)
            path.write_bytes(raw)
            cat = Cat(path)
            with self.assertRaises(ValueError):
                cat.track(cat.strings - cat.data)
            path.write_bytes(raw[:20])
            with self.assertRaises(ValueError):
                Cat(path)

    def test_source_hash_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source'
            path.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                verified_bytes(path, '0' * 64)

    @unittest.skipUnless((Path(__file__).resolve().parents[1] /
                         'local/extracted/Stream/World/06543/Door.cat').is_file(), 'local evidence absent')
    def test_original_door_inheritance_keeps_source_attribute_offset(self):
        path = Path(__file__).resolve().parents[1] / 'local/extracted/Stream/World/06543/Door.cat'
        cat = Cat(path, ['PropAnimation', 'Execute', 'DefaultOpening'])
        node = next(n for n in cat.flat() if n['offset'] == 5591)
        tracks = [cat.track(offset) for offset in node['track_offsets']]
        track = next(t for t in tracks if t['type_hash'] == f'{hashstring("PropAnimation"):08x}')
        self.assertEqual(track['attrs']['88']['string'], 'OPEN')
        self.assertEqual(track['attrs']['88']['file_offset'], 12504)

    @unittest.skipUnless((Path(__file__).resolve().parents[1] /
                         'local/extracted/DAT/Trigger/tBMX.dat').is_file(), 'local evidence absent')
    def test_garage_DAT_binding_is_BikeGar_not_shared_group_SBikeGar(self):
        root = Path(__file__).resolve().parents[1]
        props = json.loads((root / 'evidence/skinned-props-export-v04-rgb.json').read_text())
        bindings = garage_dat_bindings(root, props, garage_script_evidence(root))
        self.assertEqual({b['trigger_name'] for b in bindings}, {'BA_BMXGarage', 'BMX_GarageDoor'})
        self.assertEqual({b['area_code_raw'] for b in bindings}, {0, 62})
        self.assertEqual({b['model_key'] for b in bindings}, {'10121_bikegar'})
        self.assertTrue(all(b['source_isdoor_raw'] is False and not b['runtime_integrated'] for b in bindings))
        self.assertTrue(all(len(b['script_callbacks']) == 2 for b in bindings))


if __name__ == '__main__':
    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    except AttributeError:
        pass
    unittest.main()

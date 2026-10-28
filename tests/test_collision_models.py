"""Read actual COL extents and reject corruptions at native loader boundaries."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from catalog_collision_models import parse_models


class CollisionMetadata(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = ROOT/'local/extracted/Stream/World'
        if not (cls.world/'manifest.json').exists():
            raise unittest.SkipTest('Original indexed World extraction required')
        manifest = json.loads((cls.world/'manifest.json').read_text())
        cls.entries = {entry['name'].casefold(): entry for entry in manifest['entries'] if entry['name'].lower().endswith('.col')}

    def original(self, name):
        entry = self.entries[name.casefold()]
        data = (self.world/entry['path']).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), entry['extracted_sha256'])
        return data

    def test_original_wagon_uses_id_and_bounded_source_sections(self):
        data = self.original('70wagon.col')
        result = parse_models(data)
        model, = result['models']
        self.assertEqual(model['native_lookup'], {'kind': 'id', 'value': 294})
        self.assertEqual(model['sections']['spheres']['count'], 36)
        self.assertEqual(model['sections']['triangles']['count'], 22)
        self.assertFalse(model['runtime_integrated'])
        self.assertEqual(result['logical_bytes']+result['zero_padding_bytes'], len(data))
        for section in model['sections'].values():
            begin, size = section['offset'], section['bytes']
            self.assertEqual(hashlib.sha256(data[begin:begin+size]).hexdigest(), section['sha256'])
        faces = model['sections']['triangles']
        self.assertEqual(sum(model['triangle_surface_ids_raw'].values()), faces['count'])

    def test_col2_and_coll_identity_do_not_claim_payload_conversion(self):
        for filename, expected_tag in [('AniFooty.col', 'COL2'), ('bike.col', 'COLL')]:
            with self.subTest(filename=filename):
                models = parse_models(self.original(filename))['models']
                self.assertTrue(models)
                for model in models:
                    self.assertEqual(model['tag'], expected_tag)
                    self.assertEqual(model['status'], 'identity_verified_payload_decoder_pending')
                    self.assertNotIn('sections', model)

    def test_nonempty_name_takes_precedence_over_numeric_id(self):
        # Identity lookup is an explicit native loader branch, not a name/ID fallback.
        data = bytearray(self.original('70wagon.col'))
        data[12:32] = b'example_model\0'.ljust(20, b'\0')
        model, = parse_models(data)['models']
        self.assertEqual(model['native_lookup'], {'kind': 'name', 'value': 'example_model'})
        self.assertEqual(model['model_id_raw'], 294)

    def test_bad_extent_count_vertex_index_and_nonzero_padding_are_rejected(self):
        data = self.original('70wagon.col')
        result = parse_models(data)
        model, = result['models']
        cases = []
        changed = bytearray(data); struct.pack_into('<I', changed, 4, len(data)); cases.append(changed)
        changed = bytearray(data); struct.pack_into('<I', changed, model['body_offset']+48, 128); cases.append(changed)
        changed = bytearray(data); struct.pack_into('<H', changed, model['sections']['triangles']['offset'], 65535); cases.append(changed)
        changed = bytearray(data); changed[-1] = 1; cases.append(changed)
        cases.append(data[:model['bytes']-1])
        for index, damaged in enumerate(cases):
            with self.subTest(corruption=index), self.assertRaises(ValueError):
                parse_models(damaged)


if __name__ == '__main__':
    unittest.main()

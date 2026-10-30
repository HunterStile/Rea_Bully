"""Missing texture recovery cannot alter existing textures or mesh payloads."""
import copy
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from recover_world_material_geometry import annotate, chunks, encode


class MaterialRecoveryTests(unittest.TestCase):
    def fixture(self):
        document = {'asset': {'version': '2.0'}, 'materials': [
            {'name': 'resolved', 'pbrMetallicRoughness': {'baseColorTexture': {'index': 0},
                'baseColorFactor': [.2, .3, .4, 1]}, 'extras': {'original': 'keep'}},
            {'name': 'missing', 'pbrMetallicRoughness': {'baseColorFactor': [.8, .7, .6, .5]},
                'alphaMode': 'BLEND'}], 'meshes': [{'primitives': [{'indices': 0, 'material': 0}, {'indices': 1, 'material': 1}]}]}
        tail = struct.pack('<II', 8, 0x004e4942)+b'geometry'
        affected = [{'shape_name': 'missing', 'original_material': {'original': {'diffuse': [.8, .7, .6], 'alpha': .5}},
            'unresolved_base_maps': [{'original_filename': 'original.bmp'}], 'shading_pending': True}]
        return document, tail, affected

    def test_only_pending_metadata_changes_and_all_binary_data_remains_identical(self):
        document, tail, affected = self.fixture()
        raw, report = annotate(encode(document, tail), affected)
        result, actual_tail = chunks(raw)
        self.assertEqual(actual_tail, tail)
        self.assertEqual(result['materials'][0], document['materials'][0])
        self.assertEqual(result['meshes'], document['meshes'])
        self.assertEqual(result['materials'][1]['pbrMetallicRoughness'], document['materials'][1]['pbrMetallicRoughness'])
        self.assertEqual(result['materials'][1]['extras']['REA_unresolvedBaseMap'], affected[0])
        self.assertTrue(report['resolved_materials_unchanged'])

    def test_resolved_image_is_never_removed_or_relabelled_as_missing(self):
        document, tail, affected = self.fixture()
        affected[0]['shape_name'] = 'resolved'
        with self.assertRaisesRegex(ValueError, 'no invented texture'):
            annotate(encode(document, tail), affected)

    def test_invented_diffuse_and_unknown_shape_are_rejected(self):
        document, tail, affected = self.fixture()
        altered = copy.deepcopy(affected)
        altered[0]['original_material']['original']['diffuse'] = [1, 1, 1]
        with self.assertRaisesRegex(ValueError, 'original diffuse'):
            annotate(encode(document, tail), altered)
        affected[0]['shape_name'] = 'nonexistent'
        with self.assertRaisesRegex(ValueError, 'exactly one material'):
            annotate(encode(document, tail), affected)


if __name__ == '__main__':
    unittest.main()

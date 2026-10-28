"""Guard binary preservation and exact placement joins used by the Unreal world import."""
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from prepare_world_import import prepare
from plan_unreal_world import placement_identity, plan


class ImportContracts(unittest.TestCase):
    def test_renaming_preserves_binary_chunks_and_source_indices(self):
        document = {'asset': {'version': '2.0'}, 'nodes': [{'mesh': 0}], 'meshes': [{'name': 'same', 'primitives': [{'material': 1}]}],
            'materials': [{'name': 'same'}, {'name': 'same'}]}
        encoded = json.dumps(document).encode()
        encoded += b' ' * (-len(encoded) % 4)
        binary = struct.pack('<II', 8, 0x004E4942) + b'\x00\xff\x01\xfeDATA'
        raw = struct.pack('<IIIII', 0x46546C67, 2, 20+len(encoded)+len(binary), len(encoded), 0x4E4F534A)+encoded+binary
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory)/'source.glb', Path(directory)/'target.glb'
            source.write_bytes(raw)
            record = prepare(source, target)
            actual = target.read_bytes()
            size = struct.unpack_from('<I', actual, 12)[0]
            result = json.loads(actual[20:20+size])
            self.assertEqual(actual[20+size:], binary)
            self.assertEqual(source.read_bytes(), raw)
            self.assertEqual(result['meshes'][0]['primitives'][0]['material'], 1)
            self.assertEqual([m['name'] for m in result['materials']], ['REA_Mat_0000', 'REA_Mat_0001'])
            self.assertEqual(record['original_names']['materials'], ['same', 'same'])

    def test_exact_rotation_identity_and_distinct_nearby_placements(self):
        placement = {'model_key': 'source', 'unreal_transform': {'translation_cm': [1, 2, 3],
            'rotation_quaternion_xyzw': [0, 0, 0, 1], 'scale_xyz': [-1, 2, 3]}}
        other = copy.deepcopy(placement)
        other['unreal_transform']['rotation_quaternion_xyzw'] = [0, 0, 0, -1]
        self.assertEqual(placement_identity(placement), placement_identity(other))
        other['unreal_transform']['translation_cm'][0] += 1e-7
        self.assertNotEqual(placement_identity(placement), placement_identity(other))
        other['unreal_transform']['rotation_quaternion_xyzw'] = [0, 0, 0, 2]
        with self.assertRaises(ValueError):
            placement_identity(other)

    def test_hash_mismatch_is_pending_and_original_missing_is_not_invented(self):
        transform = {'translation_cm': [1, 2, 3], 'rotation_quaternion_xyzw': [0, 0, 0, 1], 'scale_xyz': [1, 1, 1]}
        catalog = {'models': [{'model_key': 'm', 'status': 'exported', 'output_sha256': 'current'}],
            'zones': [{'name': 'tschool', 'classification': 'outdoor', 'source_archive_index': 1,
                'source_ipb_sha256': 'ipb', 'placement_count': 2, 'placements': [
                    {'instance_index': i, 'model_key': 'm', 'unreal_transform': transform} for i in range(2)]}]}
        result = plan(catalog, {'m': {'source_sha256': 'old', 'meshes': [{'path': 'mesh'}]}})['zones'][0]
        self.assertEqual(len(result['placements']), 0)
        self.assertEqual(len(result['pending']), 1)
        self.assertEqual(len(result['duplicates']), 1)
        catalog['zones'][0]['supplementary_pending'] = [{
            'source': {'zone': 'tschool', 'section_index': 4, 'record_index': 0},
            'model_key_candidate': 'collectable', 'reason': 'not_in_baseline_model_catalog'}]
        result = plan(catalog, {})['zones'][0]
        self.assertEqual(result['source_count'], 3)
        self.assertEqual(len(result['pending']), 2)
        self.assertEqual(result['pending'][0]['source']['section_index'], 4)

    def test_original_item_geometry_does_not_invent_collision(self):
        catalog = {'models': [{'model_key': 'item', 'status': 'exported', 'output_sha256': 'same',
                              'definition': {'section': 'item'}}], 'zones': [{
            'name': 'tschool', 'classification': 'outdoor', 'source_archive_index': 1,
            'source_ipb_sha256': 'ipb', 'placement_count': 1, 'placements': [{
                'instance_index': 0, 'model_key': 'item', 'unreal_transform': {
                    'translation_cm': [1, 2, 3], 'rotation_quaternion_xyzw': [0, 0, 0, 1],
                    'scale_xyz': [1, 1, 1]}}]}]}
        imports = {'item': {'source_sha256': 'same', 'meshes': [{'path': 'mesh', 'visual_only': False}]}}
        placed = plan(catalog, imports)['zones'][0]['placements'][0]
        self.assertTrue(placed['meshes'][0]['visual_only'])
        self.assertFalse(imports['item']['meshes'][0]['visual_only'])


if __name__ == '__main__':
    unittest.main()

"""Exact winding adaptation and namespace isolation; no engine execution."""
import json
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from adapt_world_collision_winding import split_glb, swap_glb_winding
from unreal_import_world_collisions import import_target


class WorldCollisionWinding(unittest.TestCase):
    def source(self):
        document = {'asset': {'version': '2.0', 'generator': 'source'},
            'meshes': [{'primitives': [{'attributes': {'POSITION': 0}, 'indices': 1, 'mode': 4}]}],
            'accessors': [{'bufferView': 0, 'type': 'VEC3', 'componentType': 5126, 'count': 3},
                          {'bufferView': 1, 'type': 'SCALAR', 'componentType': 5123, 'count': 3}],
            'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': 36},
                            {'buffer': 0, 'byteOffset': 36, 'byteLength': 6}],
            'buffers': [{'byteLength': 44}], 'extras': {'source_collision': {'triangle_flags': [37]}}}
        blob = struct.pack('<9f3H', 0, 0, 0, 1, 0, 0, 0, 0, -1, 0, 1, 2)+b'\0\0'
        encoded = json.dumps(document).encode()
        encoded += b' '*((-len(encoded)) % 4)
        return (struct.pack('<III', 0x46546c67, 2, 28+len(encoded)+len(blob))
                +struct.pack('<II', len(encoded), 0x4e4f534a)+encoded
                +struct.pack('<II', len(blob), 0x004e4942)+blob)

    def test_exact_swap_preserves_positions_padding_and_original_metadata(self):
        raw = self.source()
        adapted, proof = swap_glb_winding(raw, {'policy': 'test'})
        original_doc, original_blob = split_glb(raw)
        document, blob = split_glb(adapted)
        self.assertEqual(blob[:36], original_blob[:36])
        self.assertEqual(blob[42:], original_blob[42:])
        self.assertEqual(struct.unpack_from('<3H', blob, 36), (0, 2, 1))
        self.assertEqual(document['extras']['source_collision'], original_doc['extras']['source_collision'])
        self.assertTrue(proof['all_non_index_bin_bytes_identical'])
        restored, _ = swap_glb_winding(adapted, {'policy': 'test'})
        self.assertEqual(split_glb(restored)[1], original_blob)

    def test_adapter_cannot_import_into_original_namespace_or_checkpoint(self):
        catalog = {'status': 'engine_winding_adapter_exported_runtime_pending',
                   'engine_target': 'ue_winding_adapter_v06', 'models': [{'winding_adapter': {'policy': 'test'}}]}
        root, checkpoint, pipeline = import_target(catalog)
        self.assertEqual(root, '/Game/Original/WorldCollisionUE')
        self.assertEqual(checkpoint.name, 'collisions-v06-ue')
        self.assertTrue(pipeline.endswith('OriginalCollisionUEPipeline'))
        with self.assertRaisesRegex(ValueError, 'provenance'):
            import_target(catalog, 'source')

    def test_source_catalog_cannot_silently_contain_adapted_models(self):
        with self.assertRaisesRegex(ValueError, 'Unverified original'):
            import_target({'status': 'source_triangles_exported_engine_pending',
                           'models': [{'winding_adapter': {'policy': 'test'}}]})


if __name__ == '__main__':
    unittest.main()

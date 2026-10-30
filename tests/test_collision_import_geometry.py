"""Exact original-face accounting for the UE coincident-position filter."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from collision_import_geometry import prove_coincident_vertex_pruning
from unreal_import_world_collisions import (
    prove_engine_triangle_adaptation, verify_engine_triangle_adaptation, triangle_adaptation_summary)
from plan_world_collisions import plan


class CollisionImportGeometry(unittest.TestCase):
    def fixture(self):
        # Face 1 has different vertex IDs but duplicate positions; face 2 is
        # exactly collinear with three distinct positions and must survive.
        vertices = [[0, 0, 0], [128, 0, 0], [0, 128, 0], [0, 0, 0], [256, 0, 0]]
        faces = [[0, 1, 2], [0, 3, 1], [0, 1, 4], [0, 1, 2]]
        geometry = {'available': True, 'mesh': '/Game/Test.Test', 'flip_normals': True,
                    'positions_cm': [[0, 0, 0], [100, 0, 0], [0, -100, 0], [200, 0, 0]],
                    'triangles': [[2, 1, 0], [0, 3, 1], [0, 2, 1]]}
        return vertices, faces, geometry, {'mesh': '/Game/Test.Test', 'index_mapping': [0, 2, 1],
                    'surface_face_indices': {'5': [0, 1], '7': [2, 3]}, 'triangle_flags': [4, 9, 2, 6]}

    def proof(self, fixture=None):
        vertices, faces, geometry, options = fixture or self.fixture()
        return prove_coincident_vertex_pruning(vertices, faces, geometry, **options)

    def test_only_coincident_positions_are_omitted_and_duplicate_faces_are_counted(self):
        result = self.proof()
        self.assertEqual(result['source_triangle_count'], 4)
        self.assertEqual(result['observed_triangle_count'], 3)
        self.assertEqual(result['omitted_source_face_indices'], [1])
        self.assertEqual(result['retained_distinct_collinear_source_face_indices'], [2])
        self.assertEqual(result['source_face_to_engine_face_candidates'], [[0, 2], [], [1], [0, 2]])
        self.assertEqual(result['omitted_source_faces'][0]['integer_cross_product'], [0, 0, 0])
        self.assertEqual(result['omitted_source_faces'][0]['surface_id'], 5)
        self.assertEqual(result['omitted_source_faces'][0]['triangle_flag'], 9)
        self.assertEqual(result['original_surface_face_indices'], {'5': [0, 1], '7': [2, 3]})
        self.assertEqual(result['original_triangle_flags'], [4, 9, 2, 6])

    def test_missing_valid_or_distinct_collinear_face_is_rejected(self):
        for index in (0, 1):
            args = self.fixture()
            args[2]['triangles'].pop(index)
            with self.assertRaisesRegex(ValueError, 'count|geometry'):
                self.proof(args)

    def test_winding_inversion_is_rejected_but_cyclic_rotation_is_allowed(self):
        args = self.fixture()
        args[2]['triangles'][0] = [0, 1, 2]
        with self.assertRaisesRegex(ValueError, 'geometry'):
            self.proof(args)
        args = self.fixture()
        args[2]['triangles'][0] = [1, 0, 2]
        self.assertEqual(self.proof(args)['observed_triangle_count'], 3)

    def test_duplicate_face_multiplicity_cannot_replace_another_face(self):
        args = self.fixture()
        args[2]['triangles'][1] = args[2]['triangles'][0][:]
        with self.assertRaisesRegex(ValueError, 'geometry'):
            self.proof(args)

    def test_no_coordinate_tolerance_or_nonfinite_coordinates(self):
        for value in (0.000001, float('nan'), float('inf'), True):
            args = self.fixture()
            args[2]['positions_cm'][0][0] = value
            with self.assertRaisesRegex(ValueError, 'coordinate|geometry'):
                self.proof(args)

    def test_invalid_indices_and_unreferenced_extra_geometry_fail(self):
        for value in (-1, 4, 0.0, True):
            args = self.fixture()
            args[2]['triangles'][0][0] = value
            with self.assertRaisesRegex(ValueError, 'indices'):
                self.proof(args)
        args = self.fixture()
        args[2]['positions_cm'].append([0, 0, 0])
        with self.assertRaisesRegex(ValueError, 'unreferenced'):
            self.proof(args)

    def test_asset_identity_flip_flag_and_adapter_are_required(self):
        for field, value in (('available', False), ('mesh', '/Game/Other.Other'), ('flip_normals', False)):
            args = self.fixture()
            args[2][field] = value
            with self.assertRaises(ValueError):
                self.proof(args)
        args = self.fixture()
        args[3]['index_mapping'] = [1, 0, 2]
        with self.assertRaisesRegex(ValueError, 'winding'):
            self.proof(args)

    def test_surface_and_flag_source_face_indices_are_not_reindexed(self):
        args = self.fixture()
        args[3]['surface_face_indices'] = {'5': [0], '7': [1, 2]}
        with self.assertRaisesRegex(ValueError, 'surface'):
            self.proof(args)
        args = self.fixture()
        args[3]['triangle_flags'].pop()
        with self.assertRaisesRegex(ValueError, 'flags'):
            self.proof(args)


class RetainedEngineCollisionGeometry(unittest.TestCase):
    """Replay the real failing UE readback, without importing assets or running UE."""
    @classmethod
    def setUpClass(cls):
        catalog = json.loads((ROOT / 'evidence/world-collisions-ue-winding-v07-r2.json').read_text())
        cls.model = next(m for m in catalog['models'] if m['model_key'] == '10451_clothlight02')
        cls.observed = json.loads((ROOT / 'evidence/world-collision-clothlight-geometry-v07.json').read_text())
        cls.geometry = cls.observed['asset_geometry']
        cls.proof = prove_engine_triangle_adaptation(cls.model, cls.geometry, cls.geometry['mesh'],
                                                    cls.observed['render_triangles'])

    def record(self):
        return {'triangles': 60, 'mesh': self.geometry['mesh'],
                'engine_triangle_adaptation': copy.deepcopy(self.proof)}

    def test_real_52_face_readback_retains_60_source_faces_and_proves_exact_omissions(self):
        verify_engine_triangle_adaptation(self.model, self.record())
        self.assertEqual(self.proof['omitted_source_face_indices'], [7, 25, 28, 30, 31, 47, 48, 50])
        self.assertEqual(self.proof['source_triangle_count'], 60)
        self.assertEqual(self.proof['observed_triangle_count'], 52)
        self.assertEqual(self.proof['original_surface_face_indices'], self.model['surface_face_indices'])
        self.assertEqual(self.proof['original_triangle_flags'], self.model['triangle_flags'])
        self.assertTrue(self.proof['engine_source_proofs'])

    def test_replay_rejects_changed_proof_counts_mapping_metadata_and_engine_hash(self):
        for field, value in (('observed_triangle_count', 51), ('omitted_source_face_indices', [7]),
                             ('source_face_to_engine_face_candidates', []), ('original_triangle_flags', []),
                             ('engine_source_proofs', []), ('source_model_sha256', '0'*64)):
            record = self.record()
            record['engine_triangle_adaptation'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_engine_triangle_adaptation(self.model, record)

    def test_renderer_count_must_match_all_face_readback(self):
        with self.assertRaisesRegex(ValueError, 'Render and collision-input'):
            prove_engine_triangle_adaptation(self.model, self.geometry, self.geometry['mesh'], 51)

    def test_replay_rechecks_original_col_bytes(self):
        model = copy.deepcopy(self.model)
        original = Path(model['source_col']['path']).read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / 'changed.col'
            changed.write_bytes(bytes([original[0] ^ 1]) + original[1:])
            model['source_col']['path'] = str(changed)
            with self.assertRaisesRegex(ValueError, 'Changed collision source'):
                verify_engine_triangle_adaptation(model, self.record())

    def planning_fixture(self):
        # Keep this independent of the separate floor/walk winding experiment:
        # use the byte-verified original model and undo its explicit index swap.
        model = copy.deepcopy(self.model['winding_adapter']['original_model'])
        geometry = copy.deepcopy(self.geometry)
        geometry['mesh'] = geometry['mesh'].replace('/WorldCollisionUE/', '/WorldCollision/')
        geometry['triangles'] = [[a, c, b] for a, b, c in geometry['triangles']]
        proof = prove_engine_triangle_adaptation(model, geometry, geometry['mesh'], 52)
        key = model['model_key']
        transform = {'translation_cm': [1., 2., 3.], 'rotation_quaternion_xyzw': [0., 0., 0., 1.],
                     'scale_xyz': [-1., 2., 1.]}
        source = {'zone': 'test', 'instance_index': 4}
        static = {'zones': [{'name': 'test', 'source_ipb_sha256': 'ipb', 'placements': [
            {'model_key': key, 'instance_index': 4, 'unreal_transform': transform}]}]}
        world = {'catalog_sha256': 'catalog', 'zones': [{'name': 'test', 'source_ipb_sha256': 'ipb',
            'placements': [{'source': source, 'model_key': key, 'transform': transform, 'meshes': [{}]}]}]}
        catalog = {'status': 'source_triangles_exported_engine_pending',
            'sources': {'world_catalog': {'sha256': 'catalog'}}, 'models': [model], 'pending': []}
        record = {'status': 'collision_assets_verified', 'model_key': key,
            'source_glb_sha256': model['output_sha256'], 'source_col': model['source_col'],
            'source_collision': model['source_collision'], 'replace_visual_collision': True,
            'body_policy': 'complex_as_simple_double_sided', 'nanite': False,
            'missing_primitive_counts': model['missing_primitive_counts'], 'triangles': 60,
            'mesh': geometry['mesh'], 'engine_triangle_adaptation': proof}
        return world, static, catalog, {key: record}

    def test_planner_reconstructs_proof_and_declares_source_vs_engine_counts(self):
        args = self.planning_fixture()
        placement = plan(*args)['zones'][0]['placements'][0]
        record = args[3]['10451_clothlight02']
        self.assertEqual(placement['triangles'], 60)
        self.assertEqual(placement['engine_triangle_adaptation'],
                         triangle_adaptation_summary(record['engine_triangle_adaptation']))
        self.assertEqual(placement['transform'], args[0]['zones'][0]['placements'][0]['transform'])

    def test_planner_rejects_missing_proof_and_altered_saved_geometry(self):
        for mode in ('missing', 'geometry', 'count', 'remap'):
            args = self.planning_fixture()
            record = args[3]['10451_clothlight02']
            if mode == 'missing':
                del record['engine_triangle_adaptation']
            elif mode == 'geometry':
                record['engine_triangle_adaptation']['geometry']['triangles'][0].reverse()
            elif mode == 'count':
                record['triangles'] = 52
            else:
                record['engine_triangle_adaptation']['source_face_to_engine_face_candidates'][0] = []
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                plan(*args)


if __name__ == '__main__':
    unittest.main()

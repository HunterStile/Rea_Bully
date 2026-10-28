"""Require source-bound adapter imports plus the exact isolated floor/walk proof."""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from plan_world_collisions import plan


class CollisionAdapterPlan(unittest.TestCase):
    def fixture(self):
        key = '05140_fgrd_sc1b11'
        transform = {'translation_cm': [1., 2., 3.], 'rotation_quaternion_xyzw': [0., 0., 0., 1.], 'scale_xyz': [-1., 2., 1.]}
        source = {'zone': 'tschool', 'instance_index': 4}
        original = {'model_key': key, 'output': str(ROOT/'local/source-floor.glb'), 'output_sha256': 'original-glb',
            'status': 'source_triangles_exported_engine_pending', 'source_col': {'sha256': 'col'},
            'source_collision': {'sha256': 'body'}, 'replace_visual_collision': True,
            'missing_primitive_counts': {'boxes': 0, 'spheres': 0, 'lines_uninterpreted': 0}, 'triangles': 454}
        export = {'path': 'original-export.json', 'sha256': 'original-export'}
        adapter = {'policy': 'ue58_chaos_swap_second_third_v06', 'index_mapping': [0, 2, 1],
            'source_export': export, 'source_glb': {'path': original['output'], 'sha256': original['output_sha256']},
            'original_model': copy.deepcopy(original)}
        model = {**original, 'output': 'adapter.glb', 'output_sha256': 'adapter-glb',
            'status': 'engine_winding_adapter_exported_runtime_pending', 'winding_adapter': adapter}
        catalog = {'status': 'engine_winding_adapter_exported_runtime_pending', 'engine_target': 'ue_winding_adapter_v06',
            'sources': {'world_catalog': {'sha256': 'catalog'}, 'original_export': export}, 'models': [model], 'pending': []}
        static = {'zones': [{'name': 'tschool', 'source_ipb_sha256': 'ipb',
            'placements': [{'model_key': key, 'instance_index': 4, 'unreal_transform': transform}]}]}
        world = {'catalog_sha256': 'catalog', 'zones': [{'name': 'tschool', 'source_ipb_sha256': 'ipb',
            'placements': [{'source': source, 'model_key': key, 'transform': transform, 'meshes': [{'visual_only': False}]}]}]}
        asset = '/Game/Original/WorldCollisionUE/'+key+'/test.test'
        imports = {key: {'status': 'collision_assets_verified', 'model_key': key, 'source_glb_sha256': 'adapter-glb',
            'mesh': asset, 'source_col': model['source_col'], 'source_collision': model['source_collision'],
            'replace_visual_collision': True, 'body_policy': 'complex_as_simple_double_sided', 'nanite': False,
            'missing_primitive_counts': model['missing_primitive_counts'], 'triangles': 454,
            'winding_adapter': copy.deepcopy(adapter)}}
        validation = {'passed': False, 'winding_experiment_floor_and_walk_passed': True,
            'grounded_on_original_instances': True, 'keyboard_moves_player': True,
            'winding_probe_instances': 1, 'walked_cm': 321., 'winding_probe_mesh': asset}
        return world, static, catalog, imports, validation

    def test_proven_adapter_retains_exact_placement_without_claiming_full_scene_pass(self):
        args = self.fixture()
        result = plan(*args)
        row = result['zones'][0]['placements'][0]
        self.assertEqual(row['transform'], args[0]['zones'][0]['placements'][0]['transform'])
        self.assertEqual(row['status'], 'ready_for_adapted_collision_full_scene_test')
        self.assertEqual(row['winding_adapter']['index_mapping'], [0, 2, 1])

    def test_absent_or_unsuccessful_or_unrelated_ab_cannot_authorize_adapter(self):
        for changes in (None, {'winding_experiment_floor_and_walk_passed': False},
                        {'grounded_on_original_instances': False}, {'keyboard_moves_player': False},
                        {'walked_cm': float('nan')}, {'winding_probe_instances': 2},
                        {'winding_probe_mesh': '/Game/Other.Other'}):
            world, static, catalog, imports, validation = self.fixture()
            if changes is None:
                validation = None
            else:
                validation.update(changes)
            with self.assertRaisesRegex(ValueError, '[Ww]inding'):
                plan(world, static, catalog, imports, validation)

    def test_adapter_source_and_import_checkpoint_must_match(self):
        args = self.fixture()
        args[3]['05140_fgrd_sc1b11']['winding_adapter']['index_mapping'] = [0, 1, 2]
        with self.assertRaisesRegex(ValueError, 'provenance'):
            plan(*args)

    def test_correct_adapter_cannot_use_the_original_asset_namespace(self):
        args = self.fixture()
        record = args[3]['05140_fgrd_sc1b11']
        record['mesh'] = record['mesh'].replace('WorldCollisionUE/', 'WorldCollision/')
        args[4]['winding_probe_mesh'] = record['mesh']
        with self.assertRaisesRegex(ValueError, 'this imported adapter'):
            plan(*args)

    def test_unadapted_catalog_cannot_adopt_an_adapter_asset_path(self):
        world, static, catalog, imports, _ = self.fixture()
        original = catalog['models'][0]['winding_adapter']['original_model']
        catalog['models'] = [original]
        catalog['status'] = original['status']
        record = imports[original['model_key']]
        del record['winding_adapter']
        record['source_glb_sha256'] = original['output_sha256']
        with self.assertRaisesRegex(ValueError, 'outside own model folder'):
            plan(world, static, catalog, imports)


if __name__ == '__main__':
    unittest.main()

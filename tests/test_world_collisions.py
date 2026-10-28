"""COL3 scale/identity boundaries and safe world-collider replacement rules."""
import copy
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import export_world_collisions as export
from plan_world_collisions import plan
from unreal_import_world_collisions import configure_collision_pipeline


class WorldCollisions(unittest.TestCase):
    def pipeline_fixture(self):
        class Reflected:
            def __init__(self, **properties):
                self.properties = properties
                self.writes = []

            def get_editor_property(self, name):
                return self.properties[name]

            def set_editor_property(self, name, value):
                if name not in self.properties:
                    raise KeyError(name)
                self.writes.append((name, value))
                self.properties[name] = value

        # Reflected names from UE 5.8 headers and PyGenUtil/CamelCaseBreakIterator.
        return Reflected(mesh_pipeline=Reflected(
            build_nanite=True, generate_lightmap_u_vs=True, collision=False,
            import_collision_according_to_mesh_name=True, fallback_collision_type=18),
            common_meshes_properties=Reflected(remove_degenerates=True, use_mikk_t_space=True),
            material_pipeline=Reflected(import_materials=True))

    def test_ue58_uv_acronym_and_complex_collision_policy(self):
        pipeline = self.pipeline_fixture()
        configure_collision_pipeline(pipeline, 0xff)
        mesh = pipeline.get_editor_property('mesh_pipeline')
        self.assertFalse(mesh.get_editor_property('generate_lightmap_u_vs'))
        self.assertFalse(mesh.get_editor_property('build_nanite'))
        self.assertTrue(mesh.get_editor_property('collision'))
        self.assertEqual(mesh.get_editor_property('fallback_collision_type'), 0xff)

    def test_missing_late_pipeline_property_fails_before_any_setting_changes(self):
        pipeline = self.pipeline_fixture()
        del pipeline.get_editor_property('material_pipeline').properties['import_materials']
        with self.assertRaisesRegex(RuntimeError, 'material_pipeline.import_materials'):
            configure_collision_pipeline(pipeline, 0xff)
        for owner in pipeline.properties.values():
            self.assertEqual(owner.writes, [])

    def fixture(self):
        vertices = struct.pack('<12h', 0, 0, 0, 128, 0, 0, 0, 128, 0, 0, 0, 128)
        faces = struct.pack('<3HBB3HBB', 0, 1, 2, 7, 255, 0, 2, 3, 18, 19)
        return vertices + faces, {'tag': 'COL3', 'sections': {
            'vertices_quantized': {'offset': 0, 'bytes': 24, 'count': 4, 'stride': 6, 'sha256': export.sha(vertices)},
            'triangles': {'offset': 24, 'bytes': 16, 'count': 2, 'stride': 8, 'sha256': export.sha(faces)}},
            'bounds_raw': {'ordered': True, 'minimum': [0, 0, 0], 'maximum': [1, 1, 1]},
            'triangle_surface_ids_raw': {'7': 1, '18': 1}}

    def test_scale_axis_conversion_and_surface_bytes_preserved(self):
        data, model = self.fixture()
        geometry = export.triangle_geometry(data, model)
        self.assertEqual(geometry['positions'], [(0, 0, 0), (1, 0, 0), (0, 0, -1), (0, 1, 0)])
        self.assertEqual(geometry['triangles'], [(0, 1, 2), (0, 2, 3)])
        self.assertEqual(geometry['surface_face_indices'], {'7': [0], '18': [1]})
        self.assertEqual(geometry['triangle_flags'], [255, 19])
        self.assertEqual(geometry['bounds_unreal_cm'], {'min': [0, -100, 0], 'max': [100, 0, 100]})

    def test_declared_bounds_allow_only_one_quantum(self):
        data, model = self.fixture()
        model['bounds_raw']['maximum'][0] = 1 - 1 / 128
        export.triangle_geometry(data, model)
        model['bounds_raw']['maximum'][0] = 1 - 2 / 128
        with self.assertRaisesRegex(ValueError, 'AABB'):
            export.triangle_geometry(data, model)

    def test_changed_face_bytes_and_out_of_bounds_indices_are_rejected(self):
        data, model = self.fixture()
        changed = data[:24] + struct.pack('<H', 99) + data[26:]
        with self.assertRaisesRegex(ValueError, 'hash changed'):
            export.triangle_geometry(changed, model)
        model['sections']['triangles']['sha256'] = export.sha(changed[24:])
        with self.assertRaisesRegex(ValueError, 'absent vertex'):
            export.triangle_geometry(changed, model)

    def placement_fixture(self):
        transform = {'translation_cm': [1., 2., 3.], 'rotation_quaternion_xyzw': [0., 0., 0., 1.], 'scale_xyz': [-1., 2., 1.]}
        source = {'zone': 'tschool', 'instance_index': 4}
        key = '01234_test'
        static = {'zones': [{'name': 'tschool', 'source_ipb_sha256': 'ipb', 'placements': [
            {'model_key': key, 'instance_index': 4, 'unreal_transform': transform}]}]}
        world = {'catalog_sha256': 'catalog', 'zones': [{'name': 'tschool', 'source_ipb_sha256': 'ipb',
            'placements': [{'source': source, 'model_key': key, 'transform': transform, 'meshes': [{'visual_only': False}]}]}]}
        model = {'model_key': key, 'output_sha256': 'glb', 'source_col': {'sha256': 'col'},
            'source_collision': {'sha256': 'body'}, 'missing_primitive_counts': {'boxes': 0, 'spheres': 0, 'lines_uninterpreted': 0},
            'replace_visual_collision': True, 'triangles': 2}
        collisions = {'sources': {'world_catalog': {'sha256': 'catalog'}}, 'models': [model], 'pending': []}
        imported = {key: {'status': 'collision_assets_verified', 'model_key': key, 'source_glb_sha256': 'glb',
            'source_col': model['source_col'], 'source_collision': model['source_collision'], 'replace_visual_collision': True,
            'body_policy': 'complex_as_simple_double_sided', 'nanite': False, 'missing_primitive_counts': model['missing_primitive_counts'],
            'triangles': 2, 'mesh': '/Game/Original/WorldCollision/' + key + '/test.test'}}
        return world, static, collisions, imported

    def test_exact_placement_and_negative_scale_are_retained(self):
        world, static, collisions, imported = self.placement_fixture()
        result = plan(world, static, collisions, imported)
        self.assertEqual(result['summary']['placements'], 1)
        self.assertEqual(result['zones'][0]['placements'][0]['transform'], static['zones'][0]['placements'][0]['unreal_transform'])
        changed = copy.deepcopy(world)
        changed['zones'][0]['placements'][0]['transform']['translation_cm'][0] += .01
        with self.assertRaisesRegex(ValueError, 'transform changed'):
            plan(changed, static, collisions, imported)

    def test_mixed_primitives_and_visual_only_models_never_replace_fallback(self):
        for mode in ('mixed', 'visual'):
            world, static, collisions, imported = self.placement_fixture()
            if mode == 'mixed':
                collisions['models'][0]['missing_primitive_counts']['boxes'] = 1
            else:
                world['zones'][0]['placements'][0]['meshes'][0]['visual_only'] = True
            result = plan(world, static, collisions, imported)
            self.assertEqual(result['summary']['placements'], 0)
            self.assertEqual(result['summary']['pending'], 1)

    def test_stale_import_and_catalog_are_rejected_and_missing_import_stays_pending(self):
        world, static, collisions, imported = self.placement_fixture()
        self.assertEqual(plan(world, static, collisions, {})['summary']['pending'], 1)
        changed = copy.deepcopy(imported)
        changed['01234_test']['source_glb_sha256'] = 'other'
        with self.assertRaisesRegex(ValueError, 'provenance'):
            plan(world, static, collisions, changed)
        world['catalog_sha256'] = 'other'
        with self.assertRaisesRegex(ValueError, 'different static catalogs'):
            plan(world, static, collisions, imported)


if __name__ == '__main__':
    unittest.main()

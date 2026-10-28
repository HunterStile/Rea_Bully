"""Verify original door source bindings without engine or visual validation."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import export_door_models as doors


class DoorModelExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = doors.plan()

    def test_all_six_actual_nifs_are_skinned_with_exact_source_groups(self):
        self.assertEqual([m['name'] for m in self.plan['models']], list(doors.NAMES))
        self.assertEqual(sum(m['placement_count'] for m in self.plan['models']), 20)
        for model in self.plan['models']:
            with self.subTest(model=model['name']):
                self.assertEqual(model['status'], 'source_compatible_pending_export')
                self.assertEqual(model['source_classification']['kind'], 'skinned')
                self.assertTrue(model['source_nif_archive']['fresh_archive_extent_matches'])
                self.assertTrue(model['source_texture_archive']['fresh_archive_extent_matches'])
                proof = model['animation_provenance']
                self.assertTrue(proof['agr_archive']['fresh_archive_extent_matches'])
                self.assertTrue(proof['fresh_agr_decode_matches_library'])
                self.assertTrue(all(s['hidden_bit_raw'] for s in model['source_classification']['unskinned_shapes']))
        ride = next(m for m in self.plan['models'] if m['name'] == 'RideGate')
        self.assertEqual(ride['definition']['animation_group'], 'DoorStr1')
        self.assertEqual(ride['animation_provenance']['source_group']['name'], 'DoorStr1')
        self.assertEqual(sum(len(m['animation_provenance']['clips']) for m in self.plan['models']), 27)

    def test_compatible_skeleton_does_not_allow_an_invented_group_alias(self):
        model = self.plan['models'][0]
        definition = copy.deepcopy(model['definition'])
        definition['animation_group'] = 'FDoorC'
        with self.assertRaisesRegex(ValueError, 'exact IDE'):
            doors.group_proof(model['animation_provenance']['source_group'], definition,
                              model['source_bones'], None, None, None)

    def test_static_classification_depends_on_mesh_and_controllers_not_panm_label(self):
        class Source:
            types = ['NiTriShape']
            def block(self, _):
                return {'name': 'original', 'skin': -1, 'flags': 22}
        source = Source()
        self.assertEqual(doors.source_kind(source)['kind'], 'static')
        source.types += ['NiTransformController']
        self.assertEqual(doors.source_kind(source)['kind'], 'unskinned_with_transform_controllers')

    def test_observatory_rejection_uses_actual_selected_uv_zero_not_valid_uv_one(self):
        model = next(m for m in self.plan['models'] if m['name'] == 'scObsDr')
        shape = doors.geometry_diagnostics(model)[0]
        self.assertEqual(shape['selected_uv_set'], 0)
        self.assertEqual(len(shape['uv_sets'][0]['nonfinite_vertex_indices']), 12)
        self.assertEqual(len(shape['uv_sets'][0]['affected_triangle_indices']), 10)
        self.assertEqual(shape['uv_sets'][1]['nonfinite_vertex_indices'], [])
        self.assertFalse(shape['uv_sets'][1]['selected_by_source_base_map'])

    def test_tadgates_rgb100_is_preserved_as_diagnostic_not_normalized(self):
        model = next(m for m in self.plan['models'] if m['name'] == 'TadGates')
        shapes = doors.geometry_diagnostics(model)
        self.assertTrue(all(s['all_rgb_exactly_100'] for s in shapes))
        self.assertEqual(sum(len(s['invalid_rgb_indices']) for s in shapes), 1286)
        self.assertEqual(sum(s['triangles'] for s in shapes), 801)
        self.assertTrue(all(s['selected_uv_set'] == 0 and not s['uv_sets'][0]['nonfinite_vertex_indices'] for s in shapes))

    def test_existing_paths_are_rejected_before_source_analysis_or_export(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            report = parent/'report.json'
            report.write_text('keep')
            with self.assertRaises(FileExistsError):
                doors.run(parent/'output', report, parent/'plan.json')
            self.assertEqual(report.read_text(), 'keep')
            self.assertFalse((parent/'output').exists())


if __name__ == '__main__':
    unittest.main()

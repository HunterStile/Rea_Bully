"""Promotion guards around the actual candidate, numeric and Blender evidence."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import prepare_npc_runtime_manifest as runtime


class NPCRuntimeManifest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = ROOT/'evidence/npc-blender-validation-v06.json'
        if not cls.validation.exists():
            raise unittest.SkipTest('Actual Blender CPU pose validation is required')
        cls.report = json.loads(cls.validation.read_text())
        cls.manifest = runtime.build(cls.validation)
        cls.candidates = json.loads(runtime.CANDIDATES.read_text())

    def test_combined_manifest_preserves_verified_bucky_and_seth_records(self):
        self.assertEqual(self.manifest['npcs'][:2], runtime.baseline()['npcs'])
        self.assertEqual([n['ped_id'] for n in self.manifest['npcs']], [8, 50, 9, 11, 72, 73, 85, 99])
        self.assertEqual(runtime.load_runtime_manifest(runtime.BASELINE), runtime.baseline())

    def test_candidate_only_selection_contains_exactly_six_and_five_clips_each(self):
        six = runtime.build(self.validation, include_baseline=False)
        self.assertEqual(six['selection'], 'six_candidates_only')
        self.assertEqual({n['ped_id'] for n in six['npcs']}, runtime.EXPECTED_IDS)
        for npc, candidate in zip(six['npcs'], self.candidates['npcs']):
            self.assertEqual(npc['clips'], candidate['clips'])
            self.assertGreaterEqual(2*runtime.half_height(npc), npc['idle_height_cm']+8)

    def test_unpromoted_candidate_manifest_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'promoted runtime manifest'):
            runtime.load_runtime_manifest(runtime.CANDIDATES)

    def test_failed_blender_report_cannot_promote_numeric_pass_alone(self):
        report = copy.deepcopy(self.report)
        report['passed'] = False
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'failed.json'
            path.write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, 'passing source-bound CPU Blender'):
                runtime.build(path)

    def test_tampered_runtime_capsule_is_rejected_before_engine_import(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['npcs'][2]['capsule_half_height_cm'] += 10
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'changed.json'
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'differs from its verified promotion'):
                runtime.load_runtime_manifest(path)

    def test_pose_proof_for_other_source_or_incomplete_clip_is_rejected(self):
        npc = self.candidates['npcs'][0]
        proof = copy.deepcopy(self.report['models'][0])
        proof['source_glb']['sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'different or invalid NPC skin'):
            runtime.capsule_measurements(npc, proof)
        proof = copy.deepcopy(self.report['models'][0])
        proof['clips'][0]['samples'] = proof['clips'][0]['samples'][:3]
        with self.assertRaisesRegex(ValueError, 'incomplete time coverage'):
            runtime.capsule_measurements(npc, proof)

    def test_nonfinite_bounds_are_not_converted_to_capsule_dimensions(self):
        proof = copy.deepcopy(self.report['models'][0])
        proof['clips'][0]['samples'][0]['bounds_max'][2] = float('nan')
        with self.assertRaisesRegex(ValueError, 'nonfinite'):
            runtime.capsule_measurements(self.candidates['npcs'][0], proof)

    def test_known_rest_and_partition_weight_differences_remain_explicit(self):
        ivan = next(n for n in self.manifest['npcs'] if n['ped_id'] == 72)
        known = ivan['validation_limitations']
        self.assertFalse(known['rest_matrix_loss_repaired'])
        self.assertGreater(known['unanimated_rest_max_vertex_difference_m'], .002)
        self.assertGreater(known['skin_partition_comparisons'][0]['max_absolute_weight_difference'], .6)
        self.assertEqual(known['original_runtime_weight_selection'], 'unresolved')
        self.assertFalse(self.manifest['runtime_validated'])


if __name__ == '__main__':
    unittest.main()

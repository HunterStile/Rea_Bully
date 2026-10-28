"""Independent skin equation and corruption controls on real original keys."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from catalog_npc_locomotion import lower_priority
lower_priority()
import numpy as np
from bully_agr import FormatError, decode_animation
from validate_npc_candidates import (animation_accessors, compare_track_keys, deform_gltf, verify)
from validate_player_actions import glb


class CandidateMathTests(unittest.TestCase):
    def test_weighted_skin_applies_inverse_bind_before_joint_world(self):
        world0 = np.eye(4)
        world0[0, 3] = 3
        world1 = np.array([[0., -1, 0, 0], [1, 0, 0, 4], [0, 0, 1, 0], [0, 0, 0, 1]])
        bind1 = np.eye(4)
        bind1[0, 3] = 1
        result = deform_gltf(np.array([[1., 2, 3]]), np.array([[0, 1, 0, 0]]),
            np.array([[.25, .75, 0, 0]]), [world0, world1], [np.eye(4), bind1])
        # Weighted points: (4,2,3)*1/4 + (-2,6,3)*3/4.
        np.testing.assert_allclose(result, [[-.5, 5, 3]], atol=1e-12)

    def test_actual_original_rotation_rejects_modified_glb_value_and_time(self):
        path = ROOT / "local/converted/npcs-v05/009_cornelius/animations.json"
        if not path.exists():
            self.skipTest("Local original NPC source unavailable")
        payload = json.loads(path.read_text())
        clip = payload["clips"][0]
        raw = Path(clip["provenance"]["agr"]["path"]).read_bytes()
        keys = decode_animation(raw[clip["agr_offset"]:clip["agr_offset"]+clip["agr_size"]], clip["duration"])["tracks"][0]["rotation"]
        doc, blob = glb(path.parent / "009_NDH2a_Cornelius.glb")
        times, values = animation_accessors(doc, blob, doc["animations"][0])[(0, "rotation")]
        self.assertEqual(compare_track_keys(keys, (times, values), "rotation"), len(keys))
        changed = values.copy()
        changed[0, 0] += .01
        with self.assertRaisesRegex(FormatError, "values changed"):
            compare_track_keys(keys, (times, changed), "rotation")
        changed_times = times.copy()
        changed_times[0] += .01
        with self.assertRaisesRegex(FormatError, "times changed"):
            compare_track_keys(keys, (changed_times, values), "rotation")

    def test_real_report_preserves_sampling_and_known_source_differences(self):
        path = ROOT / "evidence/npc-candidate-math-validation-v05.json"
        if not path.exists():
            self.skipTest("Local mathematical report unavailable")
        report = json.loads(path.read_text())
        self.assertTrue(report["passed"])
        self.assertFalse(report["ready_for_unreal"])
        self.assertFalse(report["scope"]["interpolation_validated"])
        self.assertEqual(report["visual_validation"], "pending")
        self.assertEqual(report["runtime_validation"], "pending")
        self.assertEqual(report["summary"]["animated_pose_samples"], 30)
        self.assertEqual(report["summary"]["sample_times_seconds"], [0.])
        self.assertEqual(report["summary"]["rest_pose_samples"], 6)
        self.assertEqual(report["summary"]["models_with_rest_matrix_loss_above_1e_minus_6"], [9, 11, 72, 85, 99])
        self.assertGreater(report["summary"]["max_rest_coordinate_error_m"], .002)
        self.assertLess(report["summary"]["max_animated_vertex_distance_m"], 2e-6)
        cache = {}
        for source in report["validator_sources"]:
            verify(source, cache)
        for model in report["models"]:
            for source in model["sources"].values():
                verify(source, cache)
            for clip in model["clips"]:
                verify(clip["source"]["agr"], cache)
                self.assertEqual([sample["time_seconds"] for sample in clip["pose_samples"]], [0.])
            if model["ped_id"] in (72, 73):
                difference = max(shape["skin_partition_comparison"]["max_absolute_weight_difference"] for shape in model["skin"]["shapes"])
                self.assertAlmostEqual(difference, .65 if model["ped_id"] == 72 else .2)


if __name__ == "__main__":
    unittest.main()

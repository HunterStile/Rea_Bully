"""A screenshot sample must never mutate or masquerade as its complete tour."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from plan_world_tour_visual_subset import validate


class VisualSubset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = json.loads((ROOT/'evidence/world-tour-visual-subset-v07.json').read_text())
        cls.parent = json.loads((ROOT/'evidence/world-tour-plan-v07-r6.json').read_text())

    def test_nine_exact_parent_checkpoints_and_eight_clear_geometric_samples(self):
        self.assertTrue(validate(self.plan))
        self.assertEqual(self.plan['summary']['checkpoints'], 9)
        self.assertEqual(self.plan['summary']['parent_checkpoints'], 85)
        self.assertEqual(self.plan['summary']['without_reported_geometry_risk'], 8)
        for point, selection in zip(self.plan['checkpoints'], self.plan['subset_selection']):
            self.assertEqual(point, self.parent['checkpoints'][selection['parent_index_zero_based']])
        self.assertFalse(self.plan['full_parent_tour_validated'])
        self.assertFalse(self.plan['screenshots_captured'])

    def test_changed_heading_cannot_reuse_original_checkpoint_proof(self):
        changed = copy.deepcopy(self.plan)
        changed['checkpoints'][0]['yaw_degrees'] = changed['checkpoints'][0].get('yaw_degrees', 0)+1
        with self.assertRaisesRegex(ValueError, 'differs'):
            validate(changed)

    def test_parent_index_and_full_coverage_claim_cannot_be_changed(self):
        for mutation in ('index', 'coverage'):
            changed = copy.deepcopy(self.plan)
            if mutation == 'index':
                changed['subset_selection'][0]['parent_index_zero_based'] += 1
            else:
                changed['summary']['checkpoints'] = 85
                changed['full_parent_tour_validated'] = True
            with self.assertRaisesRegex(ValueError, 'differs'):
                validate(changed)


if __name__ == '__main__':
    unittest.main()

"""Only the exact original booth floor can become an additive hidden helper."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from plan_collision_helpers import make_plan, validate


class CollisionHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = make_plan()

    def test_original_floor_keeps_visual_and_roof_collision(self):
        plan = self.plan
        self.assertTrue(validate(json.loads(json.dumps(plan))))
        self.assertEqual(plan['allowlisted_model_ids'], [7816])
        item, = plan['zones'][0]['placements']
        self.assertTrue(item['collision_only_helper'])
        self.assertFalse(item['replace_visual_collision'])
        self.assertEqual(item['triangles'], 2)
        self.assertAlmostEqual(plan['source_diagnostic']['floor_height_world_cm'], 996.7989921569824)

    def test_other_helper_or_modified_source_transform_is_rejected(self):
        for mode in ('id', 'position', 'disable_visual', 'add_nogo'):
            with self.subTest(mode=mode):
                plan = copy.deepcopy(self.plan)
                item = plan['zones'][0]['placements'][0]
                if mode == 'id':
                    plan['allowlisted_model_ids'].append(7817)
                elif mode == 'position':
                    item['transform']['translation_cm'][2] += 1
                elif mode == 'disable_visual':
                    item['replace_visual_collision'] = True
                else:
                    plan['zones'][0]['placements'].append(dict(item, model_key='07817_nogo_baltosop'))
                with self.assertRaisesRegex(ValueError, 'differs'):
                    validate(plan)


if __name__ == '__main__':
    unittest.main()

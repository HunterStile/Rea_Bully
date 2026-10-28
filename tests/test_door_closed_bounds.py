"""Real source-door regressions; no engine/render validation is implied."""
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from plan_world_doors import door_transform, load_models, read
from validate_door_closed_bounds import closed_bounds, make_report


class DoorClosedBoundsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = make_report()
        cls.bounds = {row['model_key']: row for row in cls.report['models']}

    def test_original_nif_agr_baseline_matches_all_eleven_closed_skins(self):
        checked = [r['independent_original_comparison'] for r in self.report['models']
                   if isinstance(r['independent_original_comparison'], dict)]
        self.assertEqual(len(checked), 11)
        self.assertLess(max(r['max_bound_coordinate_error_cm'] for r in checked), .002)
        self.assertEqual(self.report['summary']['models'], 16)
        self.assertEqual(self.report['summary']['vertices'], 5668)

    def test_front_reference_bounds_select_wrong_leaf_but_closed_skin_selects_left(self):
        source = next(d for d in read(ROOT/'evidence/door-objects-v05.json')['objects']
                      if d['name'] == 'DT_tschool_SchoolFrontDoorL')
        origin = door_transform(source['pose'])['translation_cm']
        # The original front doors share a pivot and native-derived UE yaw -90.
        # Apply that known basis independently from quaternion conversion.
        def world(local):
            return [origin[0]+local[1], origin[1]-local[0], origin[2]+local[2]]
        def approach_and_distances(field):
            left, right = [self.bounds[key][field] for key in ('10029_escdoorl', '10030_escdoorr')]
            left_world, right_world = world(left['center']), world(right['center'])
            # This is the actual integration probe's thinnest-horizontal-axis approach.
            normal = [0, -1, 0] if left['extent'][0] < left['extent'][1] else [1, 0, 0]
            pawn = [p+140*n for p, n in zip(left_world, normal)]
            return math.dist(pawn, left_world), math.dist(pawn, right_world)
        raw_left, raw_right = approach_and_distances('raw_reference_bounds_cm')
        closed_left, closed_right = approach_and_distances('closed_bounds_cm')
        self.assertAlmostEqual(raw_left, 140., places=8)
        self.assertLess(raw_right, raw_left)
        self.assertAlmostEqual(closed_left, 140., places=8)
        self.assertGreater(closed_right, closed_left)
        self.assertLess(closed_right, 220.)
        self.assertGreater(self.bounds['10029_escdoorl']['closed_bounds_cm']['extent'][2], 123.)
        self.assertLess(self.bounds['10029_escdoorl']['raw_reference_bounds_cm']['extent'][2], 11.)

    def test_changed_source_hash_cannot_produce_accepted_bounds(self):
        model = dict(load_models()['models'][0], output_sha256='0'*64)
        with self.assertRaisesRegex(ValueError, 'source changed'):
            closed_bounds(model)


if __name__ == '__main__':
    unittest.main()

"""Independent simple solids catch coordinate, floor and capsule diagnostic errors."""
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from preflight_world_tour_geometry import (capsule_distance, point_triangle_distance, segment_distance,
                                         transform_matrix, vertical_hits)


class TourGeometryPreflight(unittest.TestCase):
    def test_director_ray_returns_both_levels_and_preserves_winding_diagnostic(self):
        lower = [[-10, -10, 0], [10, -10, 0], [0, 10, 0]]
        upper = [[-10, -10, 180], [0, 10, 180], [10, -10, 180]]
        result = vertical_hits(np.array([lower, upper], dtype=float), 0, 0, -210, 240)
        self.assertEqual([r[1] for r in result], [0, 180])
        self.assertEqual([r[2] for r in result], [1, -1])
        self.assertEqual(vertical_hits(np.array([lower], dtype=float), 20, 20, -210, 240), [])

    def test_exact_trs_rotates_after_negative_nonuniform_scale(self):
        q = 2**-.5
        linear, translation = transform_matrix({'rotation_quaternion_xyzw': [0, 0, q, q],
            'scale_xyz': [-2, 3, 4], 'translation_cm': [100, 200, 300]})
        np.testing.assert_allclose(np.array([1, 2, 3]) @ linear.T + translation, [94, 198, 312], atol=1e-10)

    def test_capsule_has_two_cm_ground_clearance_but_overlaps_side_wall(self):
        floor = np.array([[-100, -100, 0], [100, -100, 0], [0, 100, 0]], dtype=float)
        wall = np.array([[20, -100, 0], [20, 100, 0], [20, 0, 200]], dtype=float)
        self.assertAlmostEqual(capsule_distance(floor, [0, 0, 0]), 36)
        self.assertAlmostEqual(capsule_distance(wall, [0, 0, 0]), 20)

    def test_intersection_through_triangle_interior_is_zero_even_far_from_edges(self):
        ceiling = np.array([[-1000, -1000, 100], [1000, -1000, 100], [0, 1000, 100]], dtype=float)
        self.assertEqual(capsule_distance(ceiling, [0, 0, 0]), 0)

    def test_degenerate_triangles_and_parallel_segments_remain_finite(self):
        line = np.array([[0, 0, 0], [0, 0, 0], [10, 0, 0]], dtype=float)
        self.assertAlmostEqual(point_triangle_distance(np.array([5, 3, 0]), line), 3)
        self.assertAlmostEqual(segment_distance(np.array([0, 0, 0]), np.array([10, 0, 0]),
                                                np.array([0, 3, 0]), np.array([10, 3, 0])), 3)


if __name__ == '__main__':
    unittest.main()

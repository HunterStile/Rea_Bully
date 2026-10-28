"""Prove the campus plan respects source quotas, eligibility and unique identities."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import plan_ambient_runtime as ambient


class AmbientRuntimePlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = ambient.build()
        cls.school = json.loads(ambient.SCHOOL.read_text())
        cls.peds = {p['id']: p for p in cls.school['ped_candidates']}
        cls.pois = {p['id']: p for p in cls.school['poi_points']}

    def test_exact_seven_unique_named_peds_and_no_interior_clones(self):
        self.assertEqual(self.plan['zone_counts'], {'tschool': 7, 'ischool': 0, 'isc_dorm': 0})
        ids = [p['ped_id'] for p in self.plan['placements']]
        self.assertEqual(set(ids), {9, 11, 72, 73, 85, 99, 50})
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(self.plan['imported_not_spawned'][0]['ped_id'], 8)
        self.assertFalse(self.plan['original_scheduler_recovered'])
        self.assertFalse(self.plan['runtime_spawn_validated'])

    def test_third_nerd_or_duplicate_named_ped_is_rejected(self):
        changed = copy.deepcopy(self.plan)
        next(p for p in changed['placements'] if p['ped_id'] == 73)['ped_type'] = 'NERD'
        with self.assertRaisesRegex(ValueError, 'population cap'):
            ambient.validate_plan(changed)
        changed = copy.deepcopy(self.plan)
        clone = copy.deepcopy(changed['placements'][0])
        clone['zone'] = 'ischool'
        changed['placements'].append(clone)
        with self.assertRaisesRegex(ValueError, 'duplicated'):
            ambient.validate_plan(changed)

    def test_zero_radius_student_poi_gets_one_hangout_and_separate_original_route_segment(self):
        ivan = next(p for p in self.plan['placements'] if p['ped_id'] == 72)
        trevor = next(p for p in self.plan['placements'] if p['ped_id'] == 73)
        self.assertEqual(ivan['source_evidence']['poi']['radius_source_m'], 0)
        self.assertEqual(ivan['source_evidence']['poi']['maximum_raw'], 2)
        self.assertEqual(ivan['points'], [])
        self.assertEqual(trevor['behavior'], 'reconstructed_walk')
        self.assertNotEqual(ivan['position_cm'], trevor['position_cm'])
        self.assertEqual(trevor['source_evidence']['original_point_indices'], [10, 11])
        self.assertEqual(trevor['points'], [p['ue_position_cm'] for p in trevor['source_evidence']['original_points']])
        self.assertTrue(trevor['source_evidence']['student_route_assignment_is_reconstructed'])
        self.assertNotIn(73, trevor['source_evidence']['route_original_candidate_ids'])
        self.assertIn(73, trevor['source_evidence']['eligibility_poi']['candidate_ped_ids_same_hierarchy_only'])

    def test_broad_candidate_list_never_overrides_actual_gender_or_faction(self):
        girls = copy.deepcopy(self.pois['tschool_girls_1'])
        self.assertTrue(ambient.eligible_at_poi(self.peds[85], girls))
        girls['gender'] = 'Female'
        self.assertFalse(ambient.eligible_at_poi(self.peds[85], girls))
        girls['gender'] = 'Both'
        girls['ped_type'] = 'STUDENT'
        self.assertFalse(ambient.eligible_at_poi(self.peds[85], girls))
        for p in self.plan['placements']:
            if p['ped_type'] == 'BULLY':
                self.assertEqual(p['source_evidence']['poi']['group_name'], 'tschool_bullies')

    def test_initial_capsule_overlap_is_rejected(self):
        changed = copy.deepcopy(self.plan)
        changed['placements'][1]['position_cm'] = list(changed['placements'][0]['position_cm'])
        with self.assertRaisesRegex(ValueError, 'overlap'):
            ambient.validate_plan(changed)

    def test_prefect_patrol_is_separate_from_zero_regional_prefect_quota(self):
        context = self.plan['population_context']
        self.assertEqual(context['original_quotas']['PREFECT'], 0)
        self.assertEqual(context['selected_ambient_counts'], {'NERD': 2, 'STUDENT': 2, 'BULLY': 2})
        self.assertEqual(context['separate_prefect_patrol_count'], 1)
        seth = next(p for p in self.plan['placements'] if p['ped_id'] == 50)
        self.assertEqual(seth['source_evidence']['route']['name'], 'SGD_Prefect1')
        self.assertEqual(len(seth['points']), 5)

    def test_repeated_point_name_does_not_hide_original_poi_group_name(self):
        poi = self.pois['tschool_nerds_0']
        blocks = ambient.read_trigger_blocks(Path(poi['source']['path']))
        ambient.verify_poi(poi, blocks)
        changed = copy.deepcopy(poi)
        changed['source_position'][0] += .01
        with self.assertRaisesRegex(ValueError, 'POI source fields'):
            ambient.verify_poi(changed, blocks)


if __name__ == '__main__':
    unittest.main()

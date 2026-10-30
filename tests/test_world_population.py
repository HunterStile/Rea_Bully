"""Source positions, quota limits and named identity survive world expansion."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from plan_world_population import choose_poi, validate
from world_npc_manifest import load_runtime_manifest


class WorldPopulation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = json.loads((ROOT/'evidence/ambient-runtime-plan-v07-r4.json').read_text())

    def test_twenty_own_skin_assets_and_nineteen_distinct_placements(self):
        manifest = load_runtime_manifest(ROOT/'evidence/npc-import-manifest-v07-r3.json')
        old = json.loads((ROOT/'evidence/npc-import-manifest-v06.json').read_text())
        self.assertEqual(manifest['npcs'][:8], old['npcs'])
        self.assertEqual(len(manifest['npcs']), 20)
        self.assertEqual(len(self.plan['placements']), 19)
        self.assertEqual(self.plan['zone_counts'], {'tschool': 7, 'isc_lib': 1, 'ischool': 11})
        self.assertTrue(validate(self.plan))
        self.assertFalse(self.plan['runtime_spawn_validated'])

    def test_library_bucky_uses_original_point_and_is_not_a_campus_clone(self):
        bucky = next(p for p in self.plan['placements'] if p['ped_id'] == 8)
        self.assertEqual(bucky['zone'], 'isc_lib')
        self.assertFalse(bucky['source_evidence']['default_poi_assignment_reconstructed'])
        changed = copy.deepcopy(self.plan)
        changed['placements'].append(copy.deepcopy(bucky))
        with self.assertRaisesRegex(ValueError, 'duplicated'):
            validate(changed)
        changed = copy.deepcopy(self.plan)
        next(p for p in changed['placements'] if p['ped_id'] == 8)['position_cm'][0] += 1
        with self.assertRaisesRegex(ValueError, 'original point'):
            validate(changed)

    def test_default_policy_never_overrides_explicit_category_or_gender(self):
        sample = next(p for p in self.plan['placements'] if p['ped_id'] == 66)
        evidence = sample['source_evidence']
        ped, group, index = evidence['original_ped'], copy.deepcopy(evidence['poi_group']), evidence['point_index']
        self.assertTrue(evidence['default_poi_assignment_reconstructed'])
        with self.assertRaisesRegex(ValueError, 'explicit POI type'):
            choose_poi(ped, group, index)
        group['points'][index]['PEDTYPE'] = 'NERD'
        with self.assertRaisesRegex(ValueError, 'explicit POI type'):
            choose_poi(ped, group, index, permit_default=True)
        group['points'][index]['PEDTYPE'] = 'DEFAULT'
        group['points'][index]['GENDER'] = 'Female'
        with self.assertRaisesRegex(ValueError, 'gender'):
            choose_poi(ped, group, index, permit_default=True)

    def test_quota_remains_a_real_cap_separate_from_scripted_prefects(self):
        changed = copy.deepcopy(self.plan)
        halls = next(c for c in changed['area_population_contexts'] if c['area_code'] == 2)
        halls['original_quotas']['STUDENT'] = 4
        with self.assertRaisesRegex(ValueError, 'quota'):
            validate(changed)
        self.assertEqual(sum(p['area_code'] == 2 and p['behavior'] == 'prefect_patrol' for p in self.plan['placements']), 2)

    def test_no_sitting_or_disabled_time_poi_is_used_as_standing_hangout(self):
        p = next(p for p in self.plan['placements'] if p['ped_id'] == 8)['source_evidence']
        group = copy.deepcopy(p['poi_group'])
        group['points'][p['point_index']]['TYPE'] = 'Sitting_Spot'
        with self.assertRaisesRegex(ValueError, 'Hang_Out'):
            choose_poi(p['original_ped'], group, p['point_index'])
        group['points'][p['point_index']]['TYPE'] = 'Hang_Out'
        group['points'][p['point_index']]['AFTERNOON'] = False
        with self.assertRaisesRegex(ValueError, 'afternoon'):
            choose_poi(p['original_ped'], group, p['point_index'])


if __name__ == '__main__':
    unittest.main()

"""MainMap Hang_Out refinements preserve originals and resolve source conflicts."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from refine_npc_hangout_placements import validate, eligible
from plan_town_population import inside_region


class NpcHangoutRefinement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous = json.loads((ROOT/'evidence/ambient-runtime-plan-v07-r6.json').read_text())
        cls.plan = json.loads((ROOT/'evidence/ambient-runtime-plan-v07-r7.json').read_text())
        cls.report = json.loads((ROOT/'evidence/npc-original-hangout-refinement-v07.json').read_text())

    def test_distinct_cast_quotas_and_unaffected_points_survive(self):
        old = {p['ped_id']: p for p in self.previous['placements']}
        self.assertEqual(len(self.plan['placements']), 31)
        self.assertEqual({p['ped_id'] for p in self.plan['placements']}, set(old))
        self.assertEqual(self.plan['zone_counts'], self.previous['zone_counts'])
        self.assertEqual(self.plan['regional_population_contexts'], self.previous['regional_population_contexts'])
        self.assertFalse(self.plan['runtime_spawn_validated'])
        for p in self.plan['placements']:
            if p['ped_id'] not in (100, 101, 21, 26):
                self.assertEqual(p, old[p['ped_id']])
        self.assertTrue(validate(self.plan))

    def test_all_targets_use_exact_original_faction_hangouts_in_same_regions(self):
        self.assertEqual(self.report['summary']['selected'], 4)
        self.assertFalse(self.plan['geometric_alternative_selection']['unresolved_conflicts'])
        originals = set()
        for target in self.report['targets']:
            p = target['selected']['placement']
            evidence = p['source_evidence']
            point = evidence['original_poi_group']['points'][evidence['point_index']]
            self.assertEqual(point['TYPE'], 'Hang_Out')
            self.assertEqual(point['PEDTYPE'], p['ped_type'])
            self.assertTrue(point['AFTERNOON'])
            self.assertGreaterEqual(point['MAX'], 1)
            self.assertEqual(Path(evidence['original_poi_group']['source']['path']).name, 'MainMap_hangouts.dat')
            xyz = point['POIPOINT']
            self.assertEqual(p['position_cm'], [100*xyz[0], -100*xyz[1], 100*xyz[2]])
            self.assertTrue(inside_region(xyz, evidence['population_region']))
            self.assertEqual(evidence['population_region'], target['previous_placement']['source_evidence']['population_region'])
            originals.add(tuple(p['position_cm']))
        self.assertEqual(len(originals), 4)

    def test_close_support_and_individual_capsule_clearance_are_mandatory(self):
        for target in self.report['targets']:
            selected = target['selected']
            geometry = selected['new_grounding_geometry']
            self.assertTrue(eligible(geometry, selected['other_npc_capsule_conflicts']))
            self.assertFalse(geometry['risks'])
            too_low = copy.deepcopy(geometry)
            too_low['first_surface']['source_feet_delta_cm'] = -40.01
            self.assertFalse(eligible(too_low, []))
            self.assertFalse(eligible(geometry, [{'ped_id': 26}]))
        final = {row['ped_id']: row for row in self.report['final_diagnostics']}
        self.assertEqual(len(final), 31)
        for ped_id in (100, 101, 21, 26, 85):
            self.assertFalse(final[ped_id]['new']['risks'])

    def test_shifted_coordinates_cannot_reuse_source_report(self):
        mutated = copy.deepcopy(self.plan)
        next(p for p in mutated['placements'] if p['ped_id'] == 26)['position_cm'][2] += 1
        with self.assertRaisesRegex(ValueError, 'differs from'):
            validate(mutated)


if __name__ == '__main__':
    unittest.main()

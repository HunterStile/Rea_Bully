"""Original POI alternatives must retain identities and expose unresolved conflicts."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from recover_npc_placements import validate


class NpcPlacementAlternatives(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous = json.loads((ROOT/'evidence/ambient-runtime-plan-v07-r5.json').read_text())
        cls.plan = json.loads((ROOT/'evidence/ambient-runtime-plan-v07-r6.json').read_text())
        cls.report = json.loads((ROOT/'evidence/npc-original-poi-alternatives-v07.json').read_text())

    def test_all_identities_and_unaffected_original_points_survive(self):
        previous = {p['ped_id']: p for p in self.previous['placements']}
        self.assertEqual(len(self.plan['placements']), 31)
        self.assertEqual({p['ped_id'] for p in self.plan['placements']}, set(previous))
        self.assertEqual(self.plan['zone_counts'], self.previous['zone_counts'])
        for p in self.plan['placements']:
            if p['ped_id'] not in (85, 100, 101, 21):
                self.assertEqual(p, previous[p['ped_id']])
        self.assertTrue(validate(self.plan))
        self.assertFalse(self.plan['runtime_spawn_validated'])

    def test_every_replacement_is_an_unmodified_original_point_with_geometric_clearance(self):
        for target in self.report['targets']:
            selected = target['selected']
            if selected is None:
                continue
            p = selected['placement']
            evidence = p['source_evidence']
            original = evidence['original_poi_group']['points'][evidence['point_index']]['POIPOINT']
            self.assertEqual(p['position_cm'], [original[0]*100, -original[1]*100, original[2]*100])
            geometry = selected['new_grounding_geometry']
            self.assertGreaterEqual(abs(geometry['first_surface']['geometric_normal_z']), .7)
            self.assertFalse(geometry['capsule_overlaps'])
            self.assertFalse(selected['other_npc_capsule_conflicts'])

    def test_lucky_keeps_explicit_conflict_instead_of_cloning_peanuts_point(self):
        pending = self.plan['geometric_alternative_selection']['unresolved_conflicts']
        self.assertEqual([p['ped_id'] for p in pending], [26])
        self.assertTrue(pending[0]['previous_source_preserved'])
        target = next(t for t in self.report['targets'] if t['ped_id'] == 26)
        self.assertIsNone(target['selected'])
        shared = next(c for c in target['candidates'] if ':smoking[1]' in c['placement']['source_reference'])
        self.assertEqual(shared['other_npc_capsule_conflicts'][0]['ped_id'], 21)
        self.assertFalse(shared['eligible_geometry_candidate'])

    def test_shifted_placement_cannot_reuse_source_clearance_report(self):
        changed = copy.deepcopy(self.plan)
        next(p for p in changed['placements'] if p['ped_id'] == 85)['position_cm'][0] += 1
        with self.assertRaisesRegex(ValueError, 'differs from'):
            validate(changed)


if __name__ == '__main__':
    unittest.main()

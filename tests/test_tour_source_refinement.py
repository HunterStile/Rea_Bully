"""Revised diagnostics keep all zones and use exact original coordinates/headings."""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from area_navigation_contracts import verify_references
from catalog_door_links import split_points
from catalog_population import fields, poi_points, read_trigger_blocks, ue_position
from plan_world_doors import travel_yaw
from refine_world_tour_sources import check_coverage, inside_zone, read


class TourSourceRefinement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = read(ROOT/'evidence/world-tour-plan-v07-r3.json')
        cls.revised = read(ROOT/'evidence/world-tour-plan-v07-r6.json')
        cls.report = read(ROOT/'evidence/world-tour-source-refinement-v07-r3.json')
        cls.scope = read(ROOT/'evidence/world-scope-v07.json')

    def test_every_original_zone_and_area_remains_covered(self):
        check_coverage(self.original['checkpoints'], self.revised['checkpoints'])
        self.assertEqual(len(self.revised['checkpoints']), 85)
        self.assertEqual(len({c['area'] for c in self.revised['checkpoints']}), 47)
        changed = copy.deepcopy(self.revised['checkpoints'])
        changed[0]['zone'] = 'not_the_original_zone'
        with self.assertRaisesRegex(ValueError, 'coverage'):
            check_coverage(self.original['checkpoints'], changed)

    def test_replacements_are_verbatim_original_points_within_the_same_zone(self):
        zones = {z['name']: z for z in self.scope['zones']}
        areas = {a['name']: a for a in self.scope['areas']}
        for change in self.report['changes']:
            old, point = change['before'], change['after']
            if old['position_cm'] == point['position_cm']:
                continue
            with self.subTest(point=point['name']):
                self.assertTrue(inside_zone(point['position_cm'], zones[old['zone']]))
                source = point['source']
                if 'record_type' not in source:
                    self.assertIn(source, areas[point['area']]['entry_candidates'])
                    self.assertEqual(point['position_cm'], source['position_cm'])
                    continue
                blocks = [b for b in read_trigger_blocks(Path(source['file']['path'])) if b['type'] == source['record_type']]
                name_key = source['record_type']+'NAME'
                block, = [b for b in blocks if fields(b).get(name_key) == source['record_name']]
                if source['record_type'] == 'POINT':
                    original = split_points(block)[source['point_index']]
                    position = original['source_position']
                    self.assertEqual(point['yaw_degrees'], travel_yaw(original['native_heading_radians']))
                else:
                    original = [f for f in block['fields'] if f['key'] == 'PATHPOINT'][source['point_index']]
                    position = original['value']
                self.assertEqual(source['line'], original['line'])
                self.assertEqual(source['source_position_game_m'], position)
                self.assertEqual(point['position_cm'], ue_position(position))

    def test_each_changed_coordinate_has_a_clean_offline_candidate_without_claiming_runtime(self):
        diagnostics = {d['selected_diagnostic']['name']: d['selected_diagnostic'] for d in self.report['diagnostics']}
        for change in self.report['changes']:
            if change['before']['position_cm'] != change['after']['position_cm']:
                self.assertEqual(diagnostics[change['after']['name']]['risks'], [])
        self.assertTrue(all(c['runtime_ground_verified'] is False for c in self.revised['checkpoints']))
        self.assertEqual(self.report['summary']['with_floor_ray_hit'], 85)

    def test_midway_uses_unchanged_original_point_and_separately_verified_floor_helper(self):
        name = 'Midway/BaseballToss_Minigame_Entry'
        old = next(c for c in self.original['checkpoints'] if c['name'] == name)
        current = next(c for c in self.revised['checkpoints'] if c['name'] == name)
        self.assertEqual(current['position_cm'], old['position_cm'])
        self.assertEqual(current['yaw_degrees'], -90)
        diagnostic = next(d['selected_diagnostic'] for d in self.report['diagnostics'] if d['original_checkpoint_name'] == name)
        floor = diagnostic['first_surfaces']['intended']['triangle']
        self.assertEqual(floor['model_key'], '07816_fgrd_nd_baltosop')
        self.assertTrue(floor['collision_only_helper'])

    def test_current_source_references_and_tool_versions_rehash(self):
        verify_references(self.revised)
        verify_references(self.report)


if __name__ == '__main__':
    unittest.main()

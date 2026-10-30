"""World-wide planning must keep source areas and uncertain arrival evidence distinct."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from audit_world_scope import entry_candidates
from plan_unreal_world import plan, zone_area
from plan_world_doors import assign_zone
from plan_world_tour import plan as tour_plan, original_extra_points
from audit_world_scope import read, ROOT


class WorldScopeTests(unittest.TestCase):
    def test_same_geometry_in_distinct_outdoor_areas_is_not_deduplicated(self):
        placement = {'model_key': 'test', 'instance_index': 0, 'unreal_transform': {
            'translation_cm': [0, 0, 0], 'rotation_quaternion_xyzw': [0, 0, 0, 1], 'scale_xyz': [1, 1, 1]}}
        zones = [{'name': name, 'classification': 'outdoor', 'source_archive_index': i,
            'source_ipb_sha256': 'fixture', 'classification_sources': [{'area': area}],
            'placements': [copy.deepcopy(placement)], 'placement_count': 1}
            for i, (name, area) in enumerate([('tschool', 'MainMap'), ('ftest', 'Island_3')])]
        catalog = {'zones': zones, 'models': [{'model_key': 'test', 'status': 'exported', 'output_sha256': 'fixture'}]}
        imports = {'test': {'source_sha256': 'fixture', 'meshes': [{'path': 'fixture'}]}}
        result = plan(catalog, imports)
        self.assertEqual([len(z['placements']) for z in result['zones']], [1, 1])
        self.assertEqual(result['zones'][1]['area'], 'Island_3')
        self.assertTrue(result['zones'][1]['runtime_enabled'])
        zones[1]['classification_sources'][0]['area'] = 'MainMap'
        self.assertEqual([len(z['placements']) for z in plan(catalog, imports)['zones']], [1, 0])

    def test_original_area_overrides_filename_and_unmapped_policy_is_explicit(self):
        self.assertEqual(zone_area({'name': 'zone_example', 'classification_sources': [{'area': 'Island_3'}]}),
                         ('Island_3', 'original_ColAreas_membership'))
        self.assertEqual(zone_area({'name': 'zone_example'}),
                         ('MainMap', 'reconstructed_supplemental_zone_streaming_membership'))
        self.assertEqual(zone_area({'name': 'unknown'}), ('Unresolved_unknown', 'unresolved_original_area'))

    def test_multiple_ipbs_in_one_area_get_a_containing_owner_without_changing_pose(self):
        obj = {'pose': {'position_unreal_cm': [5, 5, 5]}}
        zones = [{'name': 'large', 'area': 'Midway', 'bounds': {'min': [0, 0, 0], 'max': [10, 10, 10]}},
                 {'name': 'small', 'area': 'Midway', 'bounds': {'min': [4, 4, 4], 'max': [6, 6, 6]}}]
        before = copy.deepcopy(obj)
        self.assertEqual(assign_zone(obj, zones, 'Midway'), 'small')
        self.assertEqual(obj, before)
        obj['pose']['position_unreal_cm'] = [99, 99, 99]
        self.assertIsNone(assign_zone(obj, zones, 'Midway'))

    def test_entry_metadata_never_becomes_grounded_or_mission_trigger(self):
        source = {'path': 'fixture', 'sha256': 'fixture'}
        link = {'destination_area_name': 'Campus', 'destination_coordinate_proven': True,
            'source_trigger': {'is_mission_specific_raw': False}, 'destination_position': {
                'ue_position_cm': [1, 2, 3], 'native_heading_radians': 0, 'line': 1},
            'id': '1', 'name': 'DT_fixture', 'kind': 'DT', 'source_area_name': 'Room',
            'destination_point_list': {'source': source}, 'both_files_global_main_loads': True}
        mission = copy.deepcopy(link)
        mission['id'] = '2'
        mission['source_trigger']['is_mission_specific_raw'] = True
        exits = {'exits_source': source, 'exits': [{'area_name': 'Campus', 'exit_id': 9,
            'ue_position_cm': [4, 5, 6], 'line': 7, 'destination_comment_raw': 'raw text'}]}
        entries = entry_candidates('Campus', {'links': [link, mission]}, exits)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]['link_id'], '1')
        self.assertFalse(entries[1]['original_transition_pair_proven'])
        self.assertTrue(all(e['runtime_ground_verified'] is False for e in entries))

    def test_missing_original_entry_stays_pending_instead_of_using_bounds_centroid(self):
        result = tour_plan({'zones': [], 'areas': [{'name': 'missing', 'zones': ['a'], 'entry_candidates': [],
            'diagnostic_search_seed': {'position_cm': [0, 0, 0]}}]}, [])
        self.assertEqual(result['checkpoints'], [])
        self.assertEqual(result['pending'][0]['area'], 'missing')

    def test_transition_arrival_heading_is_preserved_for_the_walk(self):
        candidate = {'kind': 'original_transition_destination', 'link_id': 'entry',
            'position_cm': [1, 2, 3], 'native_heading_radians': 0.0}
        scope = {'zones': [], 'areas': [{'name': 'Room', 'entry_candidates': [candidate]}]}
        checkpoint, = tour_plan(scope, [])['checkpoints']
        self.assertEqual(checkpoint['yaw_degrees'], -90.0)
        self.assertEqual(checkpoint['source'], candidate)
        self.assertEqual(checkpoint['position_cm'], candidate['position_cm'])

    def test_actual_additional_dat_points_have_original_area_and_finite_coordinates(self):
        areas = read(ROOT/'evidence/world-zone-links.json')['area_enum']['entries']
        points = original_extra_points({a['area_name']: a['area_code'] for a in areas})
        self.assertEqual(len(points), 6)
        self.assertEqual({p['area'] for p in points}, {'TGOKart', 'Midway', 'iMGRaceA', 'iMGRaceB', 'iMGRaceC'})
        for point in points:
            xyz = point['source']['source_position_game_m']
            self.assertEqual(point['position_cm'], [100*xyz[0], -100*xyz[1], 100*xyz[2]])
            self.assertFalse(point['runtime_ground_verified'])


if __name__ == '__main__':
    unittest.main()

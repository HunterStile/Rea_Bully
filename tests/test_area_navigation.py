"""A navigation destination must have matching provenance and actual floor traversal."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from area_navigation_contracts import promote_entries, snapshot_scene


class AreaNavigation(unittest.TestCase):
    def setUp(self):
        self.points = [{'name': 'MainMap/campus', 'area': 'MainMap', 'zone': 'tschool',
                        'position_cm': [1.25, 20, 300], 'source': {'kind': 'original_POINT'}},
                       {'name': 'Library/entry', 'area': 'Library', 'zone': 'isc_lib',
                        'position_cm': [10, 20, 30], 'source': {'kind': 'original_DT'}}]
        self.plan = {'checkpoints': self.points}
        self.scope = {'areas': [{'name': 'MainMap'}, {'name': 'Library'}]}
        self.scene = {'unreal/Content/Maps/WorldExploration.umap': 'a'*64}
        self.run = {'tour_sha256': 'b'*64, 'runtime_sha256': 'c'*64,
                    'scene_asset_hashes': self.scene, 'scene_unchanged_during_run': True,
                    'runtime_unchanged_during_run': True, 'tour_unchanged_during_run': True, 'exit_code': 0}
        self.report = {'error': '', 'passed': True, 'checkpoints': [
            {'source_checkpoint': copy.deepcopy(p), 'passed': True,
             'grounded_on_recovered_geometry': True, 'expected_zone_visible': True,
             'active_area_matches_source': True, 'grounded_after_walk': True,
             'same_player_after_walk': True, 'active_area_matches_source_after_walk': True,
             'expected_zone_visible_after_walk': True,
             'grounded_after_walk_on_recovered_geometry': True, 'active_area': p['area'],
             'walked_cm': 120.5, 'floor_component': 'OriginalFloor', 'floor_component_after_walk': 'OriginalFloor'}
            for p in self.points]}

    def promote(self, **kwargs):
        return promote_entries(self.plan, self.report, self.run, self.scope, 'b'*64, 'c'*64, self.scene, **kwargs)

    def test_only_passed_areas_are_offered_and_all_is_a_separate_gate(self):
        self.report['checkpoints'][1]['passed'] = False
        self.report['passed'] = False
        self.run['exit_code'] = 1
        result = self.promote()
        self.assertEqual([e['area'] for e in result], ['MainMap'])
        self.assertEqual(result[0]['position_feet_cm'], self.points[0]['position_cm'])
        with self.assertRaisesRegex(ValueError, 'Library'):
            self.promote(require_all=True)

    def test_runtime_scene_plan_and_mutation_gates_all_apply(self):
        for field in ('runtime_sha256', 'tour_sha256', 'scene_asset_hashes',
                      'scene_unchanged_during_run', 'runtime_unchanged_during_run', 'tour_unchanged_during_run'):
            with self.subTest(field=field):
                old = self.run[field]
                self.run[field] = None
                with self.assertRaises(ValueError):
                    self.promote()
                self.run[field] = old

    def test_edited_original_coordinates_and_duplicate_records_are_rejected(self):
        self.report['checkpoints'][0]['source_checkpoint']['position_cm'][0] += 1
        with self.assertRaisesRegex(ValueError, 'source coordinate'):
            self.promote()
        self.report['checkpoints'][0]['source_checkpoint'] = copy.deepcopy(self.points[0])
        self.report['checkpoints'].append(copy.deepcopy(self.report['checkpoints'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicates'):
            self.promote()

    def test_a_claimed_pass_without_floor_walk_or_area_evidence_is_rejected(self):
        row = self.report['checkpoints'][0]
        for field, bad in [('walked_cm', 0), ('walked_cm', float('nan')), ('walked_cm', True),
                           ('active_area', 'Library'), ('floor_component', ''),
                           ('grounded_after_walk_on_recovered_geometry', False), ('expected_zone_visible', False),
                           ('same_player_after_walk', False), ('active_area_matches_source_after_walk', False),
                           ('expected_zone_visible_after_walk', False)]:
            with self.subTest(field=field, bad=bad):
                old = row[field]
                row[field] = bad
                with self.assertRaisesRegex(ValueError, 'area/floor/walk'):
                    self.promote()
                row[field] = old

    def test_incomplete_tour_and_unknown_area_are_rejected(self):
        last = self.report['checkpoints'].pop()
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.promote()
        self.report['checkpoints'].append(last)
        self.scope['areas'].pop()
        with self.assertRaisesRegex(ValueError, 'outside'):
            self.promote()

    def test_scene_snapshot_tracks_saved_maps_and_excludes_new_navigation_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ('Maps/WorldExploration.umap', 'World/DA_World.uasset', 'World/Zones/School.umap'):
                path = root/'unreal/Content'/relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'verified scene')
            before = snapshot_scene(root)
            nav = root/'unreal/Content/World/Navigation/DA_AreaNavigation.uasset'
            nav.parent.mkdir()
            nav.write_bytes(b'menu added after tour')
            self.assertEqual(snapshot_scene(root), before)
            (root/'unreal/Content/World/Zones/School.umap').write_bytes(b'changed geometry')
            self.assertNotEqual(snapshot_scene(root), before)


if __name__ == '__main__':
    unittest.main()

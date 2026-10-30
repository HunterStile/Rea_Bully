"""Synthetic release-evidence fixtures stay in temporary directories; no UE or release is published."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import record_world_release as release
from area_navigation_contracts import build_manifest


class WorldReleaseRecorder(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.inputs = {}

    def write(self, path, value):
        path = self.root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def bytes(self, path, value=b'fixture'):
        path = self.root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        return path

    def input(self, key, value):
        path = self.write('evidence/' + key + '.json', value)
        self.inputs[key] = path.relative_to(self.root).as_posix()
        return path

    def ref(self, path):
        path = (self.root / path).resolve()
        return {'path': str(path), 'sha256': release.digest(path)}

    def write_run(self, key, report, flags=(), plan_key=None, hash_key=None):
        path = self.input(key, report)
        command = ['UnrealEditor-Cmd.exe', '-game', '-ReaBullyWorldReport=' + str(path), *flags]
        data = {'command': command, 'exit_code': 0, 'runtime_sha256': self.baseline['runtime_sha256'],
            'scene_asset_hashes': self.baseline['scene_asset_hashes'], 'scene_unchanged_during_run': True,
            'runtime_unchanged_during_run': True, 'log': str(self.bytes('local/' + key + '.log')),
            'release_snapshot_path': str(self.snapshot_path), 'release_snapshot_sha256': release.digest(self.snapshot_path),
            'release_snapshot_unchanged_during_run': True}
        if plan_key:
            data[hash_key] = release.digest(self.root / self.inputs[plan_key])
            data[{'tour_sha256': 'tour_unchanged_during_run',
                  'interaction_plan_sha256': 'interaction_plan_unchanged_during_run',
                  'gesture_plan_sha256': 'gesture_plan_unchanged_during_run'}[hash_key]] = True
        self.write(path.with_suffix('.run.json'), data)
        return data

    def fixture(self):
        for folder in release.PACKAGE_ROOTS:
            self.bytes(folder + '/Fixture.uasset')
        self.bytes(release.RUNTIME, b'compiled fixture runtime')
        self.bytes(release.MAP)
        self.bytes('unreal/ReaBully.uproject', b'fixture project')
        self.bytes('unreal/Config/DefaultGame.ini', b'fixture config')
        self.bytes('unreal/Content/World/DA_World.uasset')
        self.bytes('tools/fixture.py')
        self.bytes('tests/test_fixture.py')
        zones = [{'name': f'z{i:03}', 'area': f'A{i % 47:02}', 'source_count': 1,
                  'placements': [{'model_key': 'fixture'}]} for i in range(85)]
        for z in zones:
            self.bytes(f"unreal/Content/World/Zones/{z['name']}.umap", z['name'].encode())
        world = self.input('world_plan', {'zones': zones})
        scope = self.input('scope', {'zones': zones, 'areas': [{'name': f'A{i:02}'} for i in range(47)]})
        self.input('scene_build', {'status': 'zones_saved', 'map': '/Game/Maps/WorldExploration',
            'plan_sha256': release.digest(world), 'zones': [{'zone': z['name']} for z in zones],
            'integration': {'npcs': 31, 'audio_waves': 207, 'prop_interactions': 2, 'prop_gestures': 6}})
        checkpoints = [{'name': 'c' + z['name'], 'zone': z['name'], 'area': z['area'],
            'position_cm': [0, 0, 0], 'source': {'kind': 'temporary synthetic test fixture'}} for z in zones]
        tour = self.input('tour_plan', {'checkpoints': checkpoints, 'sources': [self.ref(scope)]})
        self.input('visual_plan', {'parent_tour': self.ref(tour), 'checkpoints': checkpoints[:9]})
        garage = [{'source_id': 'tbusines_doors.dat:23'}, {'source_id': 'tBMX.dat:23'}]
        self.input('garage_plan', {'placements': garage, 'model': {'clips': {'OPEN': '/Game/Open.Open', 'CLOSE': '/Game/Close.Close'}}})
        gestures = [{'source_id': f'ipb:zone_carn1:{i}', 'model_key': 'fixture'} for i in (26, 27, 28, 29, 30, 36)]
        self.input('gesture_plan', {'placements': gestures, 'models': [{'model_key': 'fixture', 'durations': {'gesture': 1.0}}]})
        ambient = {'ready_for_world_builder': True, 'placements': [{'ped_id': i, 'zone': 'z000',
            'source_reference': f'fixture-original-{i}', 'points': [[0, 0, 0]], 'selection_policy': 'fixture'} for i in range(31)]}
        prop_rows = [{'model_key': 'fixture', 'source': {'instance_index': i}, 'playback': 'original idle',
                     'collision': 'none'} for i in range(533)]
        helpers = {'zones': [{'name': 'z000', 'placements': [{'model_key': '07816_fgrd_nd_baltosop',
                    'mesh': '/Game/Original/Helper.Helper', 'source': {'instance_index': 15}}]}]}
        self.input('ambient_plan', ambient)
        self.input('props_plan', {'zones': [{'name': 'z000', 'placements': prop_rows}]})
        self.input('collision_helpers', helpers)
        collision_rows = [{'model_key': 'original-floor', 'mesh': '/Game/Original/Floor.Floor',
                           'source': {'instance_index': i}} for i in range(3)]
        door_rows = [{'id': 'door-' + str(i), 'model_key': 'original-door', 'source': {'line': i},
                      'transition': {'area_in_world_plan': True}} for i in range(2)]
        self.input('collision_plan', {'zones': [{'name': 'z000', 'placements': collision_rows}],
                                     'source_reports': {'world_plan': self.ref(world)}})
        self.input('door_plan', {'zones': [{'name': 'z000', 'placements': door_rows}],
                                'source_catalogs': [self.ref(world)]})
        build = json.loads((self.root / self.inputs['scene_build']).read_text())
        for row in build['zones']:
            row.update(visible_placements=1, source_placements=1, npcs=[], props=[], original_collisions=[])
        build['zones'][0]['npcs'] = [{'ped_id': p['ped_id'], 'source': p['source_reference'],
            'route_points': 1, 'selection_policy': p['selection_policy']} for p in ambient['placements']]
        build['zones'][0]['props'] = prop_rows
        build['zones'][0]['original_collisions'] = [dict(helpers['zones'][0]['placements'][0], collision_only_helper=True)]
        build['zones'][0]['original_collisions'].extend(dict(p, collision_only_helper=False) for p in collision_rows)
        build['zones'][0]['doors'] = [dict(p, travel=True) for p in door_rows]
        build['integration'].update(props=533, original_collision_helpers=1, original_collision_instances=4, doors=2)
        build.update(prop_plan_sha256=release.digest(self.root / self.inputs['props_plan']),
            interaction_plan_sha256=release.digest(self.root / self.inputs['garage_plan']),
            gesture_plan_sha256=release.digest(self.root / self.inputs['gesture_plan']))
        self.write(self.inputs['scene_build'], build)
        self.input('audio_import', {'status': 'audio_assets_imported_pcm_verified_runtime_pending', 'waves': 207,
            'entries': [{'saved_imported_pcm_verified': True} for _ in range(207)]})
        self.baseline = release.create_snapshot(self.root)
        self.snapshot_path = self.write('local/release-snapshot.json', self.baseline)
        tour_gates = ('passed', 'grounded_on_recovered_geometry', 'expected_zone_visible', 'active_area_matches_source',
            'grounded_after_walk', 'grounded_after_walk_on_recovered_geometry', 'same_player_after_walk',
            'active_area_matches_source_after_walk', 'expected_zone_visible_after_walk')
        rows = [{**dict.fromkeys(tour_gates, True), 'source_checkpoint': c, 'active_area': c['area'],
            'walked_cm': 75, 'floor_component': 'fixture original floor', 'floor_component_after_walk': 'fixture original floor'}
            for c in checkpoints]
        self.write_run('tour_report', {'passed': True, 'error': '', 'checkpoints': rows},
            ['-ReaBullyWorldTour=' + str(tour)], 'tour_plan', 'tour_sha256')
        nav = build_manifest(tour, self.root / self.inputs['tour_report'], scope, root=self.root, require_all=True)
        nav_plan = self.input('navigation_plan', nav)
        nav_asset = self.bytes(release.NAV, b'new menu fixture')
        self.input('navigation_import', {'status': 'area_navigation_assets_verified_runtime_pending',
            'runtime_sha256': self.baseline['runtime_sha256'], 'scene_asset_hashes': self.baseline['scene_asset_hashes'],
            'entries': 47, 'pending_areas': [], 'manifest': self.ref(nav_plan), 'asset': self.ref(nav_asset)})
        self.write_run('navigation_report', {'passed': True, 'error': '', **dict.fromkeys(release.NAV_GATES, True),
            'available_entries': 47, 'travels': [{'source_id': e['id'], 'selected_area': e['area'],
                'actual_area': e['area'], 'passed': True, 'floor_component': 'original fixture'} for e in nav['entries'][:2]]},
            ['-ReaBullyNavigationProbe'])
        self.write_run('garage_report', {'passed': True, 'placements': [
            {**dict.fromkeys(release.GARAGE_GATES, True), 'source_placement': p, 'floor_component': 'original fixture',
             'f_action_count': 2, 'open_endpoint_seconds': 1., 'close_endpoint_seconds': 1.,
             'open_clip': '/Game/Open.Open', 'close_clip': '/Game/Close.Close'} for p in garage]},
            ['-ReaBullyInteractionProbe=' + str(self.root / self.inputs['garage_plan'])], 'garage_plan', 'interaction_plan_sha256')
        self.write_run('gesture_report', {'passed': True, 'placements': [
            {**dict.fromkeys(release.GESTURE_GATES, True), 'source_placement': p, 'floor_component': 'original fixture',
             'manual_f_actions': 2, 'completed_gestures': 2, 'original_gesture_endpoint_seconds': 1.} for p in gestures]},
            ['-ReaBullyGestureProbe=' + str(self.root / self.inputs['gesture_plan'])], 'gesture_plan', 'gesture_plan_sha256')
        # The image validator is mocked at this seam. These are explicitly NOT PNGs
        # and are never copied into repository evidence or claimed to be UE captures.
        image = self.bytes('local/render.fixture', b'temporary image-validator fixture')
        self.inputs['renderer_commands'] = ['sg.ShadowQuality 2']
        flags = ['-ReaBullyIntegrationProbe', '-AudioMixer', '-ReaBullyRequireAudio', '-RenderOffscreen', '-sm6',
                 '-ExecCmds=sg.ShadowQuality 2', '-ReaBullyScreenshot=' + str(image)]
        run = self.write_run('integrated_render', {'passed': True, **dict.fromkeys(release.INTEGRATED_GATES, True),
            'footstep_requests': 3, 'ambience_starts': 1}, flags)
        run['image'] = str(image)
        run['image_sha256'] = release.digest(image)
        run.update(image_capture_complete=True, image_capture=self.image_info(image))
        self.write((self.root / self.inputs['integrated_render']).with_suffix('.run.json'), run)
        captures = []
        for index, checkpoint in enumerate(checkpoints[:9]):
            path = self.bytes(f'local/visual/checkpoint-{index:03}.fixture', b'temporary captured-image-validator fixture')
            captures.append({**self.image_info(path), 'checkpoint_index': index, 'checkpoint_name': checkpoint['name']})
        visual = self.write_run('visual_report', {'passed': True, 'checkpoints': rows[:9]},
            ['-ReaBullyWorldTour=' + str(self.root / self.inputs['visual_plan']),
             '-RenderOffscreen', '-ExecCmds=sg.ShadowQuality 2'], 'visual_plan', 'tour_sha256')
        visual.update(captures_complete=True, tour_capture_directory=str(self.root / 'local/visual'), tour_captures=captures)
        self.write((self.root / self.inputs['visual_report']).with_suffix('.run.json'), visual)
        self.input('python_tests', {'passed': True, 'tests_run': 1, 'source_files_unchanged_during_run': True,
            'failures': [], 'errors': [], 'skipped': [],
            'tool_sources': [{'path': 'tools/fixture.py', 'sha256': release.digest(self.root / 'tools/fixture.py')}],
            'test_sources': [{'path': 'tests/test_fixture.py', 'sha256': release.digest(self.root / 'tests/test_fixture.py')}]})

    def image_info(self, path):
        return {'path': str(path), 'sha256': release.digest(path), 'bytes': path.stat().st_size,
                'width': 1600, 'height': 900}

    def captures(self, report, directory, checkpoints):
        return [{**self.image_info(directory / f'checkpoint-{index:03}.fixture'),
                 'checkpoint_index': index, 'checkpoint_name': checkpoint['name']}
                for index, checkpoint in enumerate(checkpoints)]

    def record(self, reviewed=True):
        with patch.object(release, 'png_capture_info', side_effect=self.image_info), \
                patch.object(release, 'inspect_tour_captures', side_effect=self.captures):
            return release.build_release(self.inputs, self.snapshot_path, root=self.root, reviewed_images=reviewed)

    def mutate(self, key, mutate, run=False):
        path = self.root / self.inputs[key]
        if run:
            path = path.with_suffix('.run.json')
        data = json.loads(path.read_text())
        mutate(data)
        self.write(path, data)

    def test_complete_proofs_include_dependency_roots_and_no_full_game_claim(self):
        self.fixture()
        result = self.record()
        self.assertTrue(result['passed'])
        self.assertFalse(result['full_game_goal_complete'])
        for path in (release.NAV, 'unreal/Content/RecoveredMaterials/Fixture.uasset', 'unreal/Content/Original/Fixture.uasset'):
            self.assertIn(path, result['world_asset_hashes'])
        self.assertEqual(len(result['scope']['zones']), 85)
        self.assertEqual(len(result['scope']['areas']), 47)
        self.assertEqual(result['scope']['ambient_npcs_placed'], 31)
        self.assertEqual(result['scope']['ambient_npc_runtime_observed_ids'], [])
        self.assertFalse(result['scope']['all_placed_npcs_runtime_certified'])

    def test_explicit_actual_image_review_is_required(self):
        self.fixture()
        with self.assertRaisesRegex(ValueError, 'reviewed-images'):
            self.record(False)

    def test_changed_material_and_added_dependency_cannot_be_released(self):
        self.fixture()
        original = self.root / 'unreal/Content/RecoveredMaterials/Fixture.uasset'
        original.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'dependency packages'):
            self.record()
        original.write_bytes(b'fixture')
        self.bytes('unreal/Content/Original/New.uasset')
        with self.assertRaisesRegex(ValueError, 'dependency packages'):
            self.record()

    def test_changed_runtime_or_navigation_asset_cannot_be_released(self):
        self.fixture()
        self.bytes(release.NAV, b'changed menu')
        with self.assertRaisesRegex(ValueError, 'Navigation import'):
            self.record()
        self.bytes(release.RUNTIME, b'changed runtime')
        with self.assertRaisesRegex(ValueError, 'Runtime changed'):
            self.record()

    def test_partial_scene_or_failed_tour_are_rejected(self):
        self.fixture()
        self.mutate('scene_build', lambda d: d['zones'].pop())
        with self.assertRaisesRegex(ValueError, 'Full scene build'):
            self.record()
        self.fixture()
        self.mutate('tour_report', lambda d: d['checkpoints'][0].update(passed=False))
        with self.assertRaisesRegex(ValueError, 'Every full-tour'):
            self.record()

    def test_garage_or_gesture_false_pass_cannot_hide_missing_checks(self):
        self.fixture()
        self.mutate('garage_report', lambda d: d['placements'][0].update(no_autoplay_before_input=False))
        with self.assertRaisesRegex(ValueError, 'garage'):
            self.record()
        self.fixture()
        self.mutate('gesture_report', lambda d: d['placements'][0].update(completed_gestures=1))
        with self.assertRaisesRegex(ValueError, 'Gesture'):
            self.record()

    def test_same_count_wrong_source_placement_cannot_pass(self):
        self.fixture()
        self.mutate('gesture_report', lambda d: d['placements'][0]['source_placement'].update(source_id='wrong'))
        with self.assertRaisesRegex(ValueError, 'source placement coverage'):
            self.record()

    def test_silent_or_null_render_cannot_certify_audio_and_images(self):
        for forbidden in ('-nosound', '-NullRHI'):
            self.fixture()
            self.mutate('integrated_render', lambda d: d['command'].append(forbidden), run=True)
            with self.subTest(flag=forbidden), self.assertRaisesRegex(ValueError, 'disabled device'):
                self.record()

    def test_missing_audio_gate_or_changed_renderer_are_rejected(self):
        self.fixture()
        self.mutate('integrated_render', lambda d: d.update(ambience_component_playing=False))
        with self.assertRaisesRegex(ValueError, 'audio probe'):
            self.record()
        self.fixture()
        self.inputs['renderer_commands'] = ['sg.ShadowQuality 3']
        with self.assertRaisesRegex(ValueError, 'tested profile'):
            self.record()

    def test_stale_test_suite_or_new_source_file_are_rejected(self):
        self.fixture()
        self.bytes('tools/uncovered.py')
        with self.assertRaisesRegex(ValueError, 'current tools'):
            self.record()

    def test_evidence_before_snapshot_and_scene_mutation_flag_are_rejected(self):
        self.fixture()
        self.baseline['completed_at_unix_ns'] += 10**12
        self.write(self.snapshot_path, self.baseline)
        with self.assertRaisesRegex(ValueError, 'predates'):
            self.record()
        self.fixture()
        self.mutate('tour_report', lambda d: d.update(scene_unchanged_during_run=False), run=True)
        with self.assertRaisesRegex(ValueError, 'scene proof'):
            self.record()

    def test_evidence_mutation_during_verification_is_rejected(self):
        self.fixture()

        def changed_during_image_check(path):
            self.mutate('audio_import', lambda d: d.update(waves=1))
            return {'path': str(path), 'sha256': release.digest(path), 'bytes': path.stat().st_size,
                    'width': 1600, 'height': 900}

        with patch.object(release, 'png_capture_info', side_effect=changed_during_image_check), \
                patch.object(release, 'inspect_tour_captures', side_effect=self.captures), \
                self.assertRaisesRegex(ValueError, 'evidence changed'):
            release.build_release(self.inputs, self.snapshot_path, root=self.root, reviewed_images=True)

    def test_missing_visual_tour_and_same_size_altered_capture_are_rejected(self):
        self.fixture()
        del self.inputs['visual_report']
        with self.assertRaisesRegex(ValueError, 'nine captures'):
            self.record()
        self.fixture()
        path = self.root / 'local/visual/checkpoint-000.fixture'
        path.write_bytes(b'X' * path.stat().st_size)
        with self.assertRaisesRegex(ValueError, 'capture hashes changed'):
            self.record()

    def test_integrated_image_must_match_hash_from_its_render_run(self):
        self.fixture()
        path = self.root / 'local/render.fixture'
        path.write_bytes(b'X' * path.stat().st_size)
        with self.assertRaisesRegex(ValueError, 'Integrated image changed'):
            self.record()

    def test_full_zone_count_cannot_hide_reduced_cast_props_or_helper(self):
        self.fixture()
        original = json.loads((self.root / self.inputs['scene_build']).read_text())
        for field in ('npcs', 'props', 'original_collisions', 'doors'):
            changed = copy.deepcopy(original)
            changed['zones'][0][field].pop()
            self.write(self.inputs['scene_build'], changed)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'coverage differs|helper placement differs'):
                self.record()

    def test_runtime_runs_require_same_unchanged_snapshot(self):
        self.fixture()
        for key in ('tour_report', 'garage_report', 'gesture_report', 'navigation_report',
                    'integrated_render', 'visual_report'):
            path = (self.root / self.inputs[key]).with_suffix('.run.json')
            original = json.loads(path.read_text())
            self.mutate(key, lambda d: d.update(release_snapshot_unchanged_during_run=False), run=True)
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'another release snapshot'):
                self.record()
            self.write(path, original)
        self.baseline['created_at_utc'] += ' different batch'
        self.write(self.snapshot_path, self.baseline)
        with self.assertRaisesRegex(ValueError, 'another release snapshot'):
            self.record()

    def test_legacy_collision_and_door_plans_cannot_represent_full_scene(self):
        self.fixture()
        for key, mutate in (
                ('collision_plan', lambda d: d['source_reports']['world_plan'].update(sha256='old-world')),
                ('door_plan', lambda d: d['source_catalogs'][0].update(sha256='old-world'))):
            original = json.loads((self.root / self.inputs[key]).read_text())
            self.mutate(key, mutate)
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'this complete world plan'):
                self.record()
            self.write(self.inputs[key], original)


if __name__ == '__main__':
    unittest.main()

"""A successful engine exit cannot certify a world that changed during traversal."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import run_unreal_world
import plan_prop_interactions
import plan_prop_gestures
from area_navigation_contracts import REQUIRED_SCENE_FILES, snapshot_scene


class TourRunEvidence(unittest.TestCase):
    def run_fixture(self, changed=None, engine_passed=True, probe='tour', capture=False, release=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'local').mkdir()
            (root/'evidence').mkdir()
            runtime = root/'unreal/Binaries/Win64/UnrealEditor-ReaBullyRuntime.dll'
            runtime.parent.mkdir(parents=True)
            runtime.write_bytes(b'original-runtime')
            scene = root/'unreal/Content/World/Zones/tschool.umap'
            for path in [scene, *(root/name for name in REQUIRED_SCENE_FILES)]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'original-scene')
            tour = root/'tour.json'
            tour.write_text(json.dumps({'checkpoints': [{'name': 'source-point'}]}), encoding='utf-8')
            report = root/'evidence/tour.json'
            original_tour = hashlib.sha256(tour.read_bytes()).hexdigest()
            baseline = root/'local/release-snapshot.json'
            baseline.write_text(json.dumps({'schema': 'rea-bully-release-snapshot-v1',
                'runtime_sha256': hashlib.sha256(runtime.read_bytes()).hexdigest(),
                'scene_asset_hashes': snapshot_scene(root)}), encoding='utf-8')
            baseline_sha = hashlib.sha256(baseline.read_bytes()).hexdigest()

            def engine(command, **kwargs):
                if capture:
                    self.assertIn('-RenderOffscreen', command)
                    self.assertIn('-sm6', command)
                    self.assertIn('-ReaBullyTourCapture=' + str(root/'local/captures'), command)
                    self.assertNotIn('-NullRHI', command)
                    self.assertFalse(any(value.startswith('-ReaBullyScreenshot=') for value in command))
                    self.assertTrue((root/'local/captures').is_dir())
                else:
                    self.assertIn('-NullRHI', command)
                    self.assertNotIn('-RenderOffscreen', command)
                if probe == 'navigation':
                    self.assertIn('-ReaBullyNavigationProbe', command)
                    self.assertEqual(kwargs['timeout'], 600)
                else:
                    flag = {'tour': '-ReaBullyWorldTour=', 'interaction': '-ReaBullyInteractionProbe=',
                            'gesture': '-ReaBullyGestureProbe='}[probe]
                    self.assertIn(flag + str(tour.resolve()), command)
                report.write_text(json.dumps({'passed': engine_passed}), encoding='utf-8')
                if changed:
                    {'runtime': runtime, 'tour': tour, 'scene': scene, 'release': baseline}[changed].write_bytes(b'changed-during-run')
                return SimpleNamespace(returncode=0)

            selection = ['--navigation-probe'] if probe == 'navigation' else [
                {'tour': '--tour', 'interaction': '--interaction-probe', 'gesture': '--gesture-probe'}[probe], str(tour)]
            if capture:
                selection += ['--tour-capture', str(root/'local/captures')]
            if release:
                selection += ['--release-snapshot', str(baseline)]
            with patch.object(run_unreal_world, 'ROOT', root), patch.object(sys, 'argv',
                    ['run_unreal_world.py', 'test', *selection, '--report', str(report)]), \
                    patch.object(plan_prop_interactions, 'make_plan', return_value=json.loads(tour.read_text())), \
                    patch.object(plan_prop_gestures, 'make_plan', return_value=json.loads(tour.read_text())), \
                    patch.object(run_unreal_world.subprocess, 'run', side_effect=engine) as process, \
                    contextlib.redirect_stdout(io.StringIO()):
                result = run_unreal_world.main()
            process.assert_called_once()
            run = json.loads(report.with_suffix('.run.json').read_text(encoding='utf-8'))
            if release:
                self.assertEqual(run['release_snapshot_sha256'], baseline_sha)
            if probe != 'navigation':
                self.assertEqual(run[{'tour': 'tour_sha256', 'interaction': 'interaction_plan_sha256',
                                     'gesture': 'gesture_plan_sha256'}[probe]], original_tour)
            self.assertEqual(run['runtime_sha256'], hashlib.sha256(b'original-runtime').hexdigest())
            self.assertEqual(run['scene_asset_hashes'][scene.relative_to(root).as_posix()],
                             hashlib.sha256(b'original-scene').hexdigest())
            return result, run

    def test_unchanged_success_records_original_snapshot(self):
        result, run = self.run_fixture()
        self.assertEqual(result, 0)
        self.assertTrue(all(run[name + '_unchanged_during_run'] for name in ('scene', 'runtime', 'tour')))

    def test_release_run_binds_the_specific_frozen_batch(self):
        result, run = self.run_fixture(release=True)
        self.assertEqual(result, 0)
        self.assertTrue(run['release_snapshot_unchanged_during_run'])

    def test_changed_release_snapshot_cannot_certify_an_engine_success(self):
        result, run = self.run_fixture(changed='release', release=True)
        self.assertEqual(result, 1)
        self.assertFalse(run['release_snapshot_unchanged_during_run'])

    def test_capture_requests_rendering_but_engine_success_without_pngs_is_not_certified(self):
        # No synthetic PNGs: this exercises launch routing and a missing-capture failure only.
        result, run = self.run_fixture(capture=True)
        self.assertEqual(result, 1)
        self.assertFalse(run['captures_complete'])
        self.assertIn('coverage', run['capture_error'])
        self.assertTrue(run['tour_unchanged_during_run'])

    def test_capture_rejects_wrong_mode_and_nonfresh_or_external_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'local').mkdir()
            cases = [
                ['test', '--tour-capture', str(root/'local/new')],
                ['render', '--tour', 'plan.json', '--tour-capture', str(root/'local/new')],
                ['test', '--tour', 'plan.json', '--tour-capture', str(root/'local')],
                ['test', '--tour', 'plan.json', '--tour-capture', str(root/'outside')],
                ['test', '--tour', 'plan.json', '--tour-capture', str(root/'local/new'), '--image', 'other.png'],
            ]
            for arguments in cases:
                with self.subTest(arguments=arguments), patch.object(run_unreal_world, 'ROOT', root), \
                        patch.object(sys, 'argv', ['run_unreal_world.py', *arguments]), \
                        patch.object(run_unreal_world.subprocess, 'run') as process, \
                        contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    run_unreal_world.main()
                process.assert_not_called()

    def test_runtime_change_rejects_engine_success(self):
        result, run = self.run_fixture('runtime')
        self.assertEqual(result, 1)
        self.assertFalse(run['runtime_unchanged_during_run'])

    def test_tour_change_rejects_engine_success(self):
        result, run = self.run_fixture('tour')
        self.assertEqual(result, 1)
        self.assertFalse(run['tour_unchanged_during_run'])

    def test_scene_change_rejects_engine_success(self):
        result, run = self.run_fixture('scene')
        self.assertEqual(result, 1)
        self.assertFalse(run['scene_unchanged_during_run'])

    def test_engine_report_failure_is_not_hidden_by_unchanged_files(self):
        result, run = self.run_fixture(engine_passed=False)
        self.assertEqual(result, 1)
        self.assertTrue(run['scene_unchanged_during_run'])

    def test_interaction_probe_records_and_enforces_original_plan(self):
        result, run = self.run_fixture(probe='interaction')
        self.assertEqual(result, 0)
        self.assertTrue(run['interaction_plan_unchanged_during_run'])
        result, run = self.run_fixture(changed='tour', probe='interaction')
        self.assertEqual(result, 1)
        self.assertFalse(run['interaction_plan_unchanged_during_run'])

    def test_navigation_probe_records_and_enforces_world_snapshot(self):
        result, run = self.run_fixture(probe='navigation')
        self.assertEqual(result, 0)
        self.assertTrue(run['navigation_probe'])
        result, run = self.run_fixture(changed='scene', probe='navigation')
        self.assertEqual(result, 1)
        self.assertFalse(run['scene_unchanged_during_run'])

    def test_gesture_probe_records_and_enforces_original_plan(self):
        result, run = self.run_fixture(probe='gesture')
        self.assertEqual(result, 0)
        self.assertTrue(run['gesture_plan_unchanged_during_run'])
        result, run = self.run_fixture(changed='tour', probe='gesture')
        self.assertEqual(result, 1)
        self.assertFalse(run['gesture_plan_unchanged_during_run'])

    def test_navigation_probe_rejects_mixed_runtime_modes_before_launch(self):
        for extra in (['--integrated'], ['--tour', 'source.json'], ['--interaction-probe', 'source.json'],
                      ['--gesture-probe', 'source.json'], ['--audio'], ['--collision-winding-probe', 'mesh']):
            with self.subTest(extra=extra), patch.object(sys, 'argv',
                    ['run_unreal_world.py', 'test', '--navigation-probe', *extra]), \
                    patch.object(run_unreal_world.subprocess, 'run') as process, \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                run_unreal_world.main()
            process.assert_not_called()


if __name__ == '__main__':
    unittest.main()

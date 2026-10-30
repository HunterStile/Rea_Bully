"""A launcher must open the scene/build that actually passed runtime validation."""
import hashlib
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import launch_world


class VerifiedSceneLaunch(unittest.TestCase):
    def test_v07_missing_release_blocks_before_any_engine_or_process_access(self):
        self.assertEqual(launch_world.RELEASE, launch_world.ROOT/'evidence/world-release-v07.json')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {name: root/(name.lower() + '.fixture') for name in ('ENGINE', 'PROJECT', 'RUNTIME', 'WORLD')}
            for path in paths.values():
                path.write_bytes(b'temporary launcher fixture')
            # A previous release must never act as an implicit fallback.
            (root/'world-release-v06.json').write_text(json.dumps({'passed': True}))
            paths.update(ROOT=root, RELEASE=root/'world-release-v07.json')
            with patch.multiple(launch_world, **paths), patch.object(sys, 'argv', ['launch_world.py', '--check']), \
                    patch.object(launch_world, 'project_processes') as processes, \
                    patch.object(launch_world, 'verify_release_artifacts') as verify, \
                    patch.object(launch_world.subprocess, 'Popen') as start:
                with self.assertRaisesRegex(RuntimeError, 'world-release-v07.json'):
                    launch_world.main()
                verify.assert_not_called()
                processes.assert_not_called()
                start.assert_not_called()

    def test_check_uses_release_renderer_and_never_starts_unreal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {name: root/(name.lower() + '.fixture') for name in ('ENGINE', 'PROJECT', 'RUNTIME', 'WORLD')}
            for path in paths.values():
                path.write_bytes(b'temporary verified launcher fixture')
            profile = ['sg.ShadowQuality 2', 'r.SkylightIntensityMultiplier 2']
            release = {'passed': True, 'renderer_commands': profile,
                       'runtime_sha256': hashlib.sha256(paths['RUNTIME'].read_bytes()).hexdigest(),
                       'world_asset_hashes': {paths['WORLD'].name: hashlib.sha256(paths['WORLD'].read_bytes()).hexdigest()}}
            release_path = root/'world-release-v07.json'
            release_path.write_text(json.dumps(release), encoding='utf-8')
            paths.update(ROOT=root, RELEASE=release_path)
            output = io.StringIO()
            with patch.multiple(launch_world, **paths), patch.object(sys, 'argv', ['launch_world.py', '--check']), \
                    patch.object(launch_world, 'project_processes', return_value=[]), \
                    patch.object(launch_world.subprocess, 'Popen') as start, redirect_stdout(output):
                self.assertEqual(launch_world.main(), 0)
                start.assert_not_called()
            result = json.loads(output.getvalue())
            self.assertEqual(result['status'], 'launch_configuration_verified')
            self.assertEqual(result['release'], str(release_path))
            self.assertEqual(result['renderer_commands'], profile + ['t.MaxFPS 60'])
            self.assertIn('-ExecCmds=' + ','.join(profile + ['t.MaxFPS 60']), result['command'])
            self.assertIn('/Game/Maps/WorldExploration', result['command'])

    def test_equivalent_workspace_path_is_accepted_but_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base/'workspace'
            (root/'alias').mkdir(parents=True)
            runtime = root/'runtime.dll'; runtime.write_bytes(b'compiled-and-tested')
            scene = root/'scene.umap'; scene.write_bytes(b'integrated-world')
            outside = base/'outside.umap'; outside.write_bytes(scene.read_bytes())
            release = {'runtime_sha256': hashlib.sha256(runtime.read_bytes()).hexdigest(),
                       'world_asset_hashes': {'scene.umap': hashlib.sha256(scene.read_bytes()).hexdigest()}}
            with patch.object(launch_world, 'ROOT', root/'alias'/'..'), patch.object(launch_world, 'RUNTIME', runtime):
                launch_world.verify_release_artifacts(release)
                release['world_asset_hashes'] = {'../outside.umap': hashlib.sha256(outside.read_bytes()).hexdigest()}
                with self.assertRaises(RuntimeError):
                    launch_world.verify_release_artifacts(release)

    def test_changed_map_or_binary_cannot_launch_as_the_verified_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root/'runtime.dll'; runtime.write_bytes(b'compiled-and-tested')
            scene = root/'scene.umap'; scene.write_bytes(b'integrated-world')
            release = {'runtime_sha256': hashlib.sha256(runtime.read_bytes()).hexdigest(),
                       'world_asset_hashes': {'scene.umap': hashlib.sha256(scene.read_bytes()).hexdigest()}}
            with patch.object(launch_world, 'ROOT', root), patch.object(launch_world, 'RUNTIME', runtime):
                launch_world.verify_release_artifacts(release)
                scene.write_bytes(b'previous-school-map')
                with self.assertRaises(RuntimeError):
                    launch_world.verify_release_artifacts(release)
                scene.write_bytes(b'integrated-world')
                runtime.write_bytes(b'untested-build')
                with self.assertRaises(RuntimeError):
                    launch_world.verify_release_artifacts(release)

    def test_incomplete_release_never_yields_a_graphics_launch_profile(self):
        with self.assertRaises(RuntimeError):
            launch_world.renderer_profile({'passed': False, 'renderer_commands': ['sg.ShadowQuality 2']})
        self.assertEqual(launch_world.renderer_profile({'passed': True, 'renderer_commands': ['sg.ShadowQuality 2']}),
                         ['sg.ShadowQuality 2', 't.MaxFPS 60'])


if __name__ == '__main__':
    unittest.main()

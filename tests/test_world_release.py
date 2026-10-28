"""A launcher must open the scene/build that actually passed runtime validation."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import launch_world


class VerifiedSceneLaunch(unittest.TestCase):
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

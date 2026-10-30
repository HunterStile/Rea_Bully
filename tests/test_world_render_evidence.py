"""A successful engine report must also bind a complete rendered PNG to its run."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import run_unreal_world


def synthetic_png():
    def chunk(tag, payload):
        return struct.pack('>I', len(payload)) + tag + payload + struct.pack('>I', zlib.crc32(tag + payload))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1600, 900, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' + b'\x12\x34\x56' * 1600) * 900)) + chunk(b'IEND', b''))


class RenderEvidence(unittest.TestCase):
    def run_render(self, payload):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'local').mkdir()
            runtime = root / 'unreal/Binaries/Win64/UnrealEditor-ReaBullyRuntime.dll'
            runtime.parent.mkdir(parents=True)
            runtime.write_bytes(b'synthetic-runtime')
            report, image = root / 'report.json', root / 'local/render.png'

            def engine(command, **kwargs):
                self.assertIn('-RenderOffscreen', command)
                report.write_text(json.dumps({'passed': True}))
                if payload is not None:
                    image.write_bytes(payload)
                return SimpleNamespace(returncode=0)

            with patch.object(run_unreal_world, 'ROOT', root), patch.object(sys, 'argv', [
                    'run_unreal_world.py', 'render', '--report', str(report), '--image', str(image)]), \
                    patch.object(run_unreal_world.subprocess, 'run', side_effect=engine), \
                    contextlib.redirect_stdout(io.StringIO()):
                result = run_unreal_world.main()
            return result, json.loads(report.with_suffix('.run.json').read_text())

    def test_complete_image_is_bound_to_engine_run_by_hash_and_dimensions(self):
        payload = synthetic_png()
        result, run = self.run_render(payload)
        self.assertEqual(result, 0)
        self.assertTrue(run['image_capture_complete'])
        self.assertEqual(run['image_sha256'], hashlib.sha256(payload).hexdigest())
        self.assertEqual(run['image_capture']['sha256'], run['image_sha256'])
        self.assertEqual((run['image_capture']['width'], run['image_capture']['height']), (1600, 900))

    def test_engine_success_cannot_hide_missing_or_truncated_image(self):
        for payload in (None, synthetic_png()[:-12]):
            with self.subTest(missing=payload is None):
                result, run = self.run_render(payload)
                self.assertEqual(result, 1)
                self.assertFalse(run['image_capture_complete'])
                self.assertIn('image_capture_error', run)


if __name__ == '__main__':
    unittest.main()

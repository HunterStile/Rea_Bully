"""Synthetic process orchestration; every engine call is replaced by a temp-file writer."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import run_unreal_world_build as coordinator
from unreal_assemble_world import integration_counts, validate_index


class WorldBuildCoordinator(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.calls = []
        self.fail_zone = None
        self.fail_assembly = False
        self.mutate_input = False
        self.wrong_final = False
        self.failed = False
        self.config = self.root / 'local/config.json'
        self.work = self.root / 'local/batches'
        self.report = self.root / 'evidence/scene.json'
        self.data = {key: f'local/inputs/{key}.json' for key in coordinator.CONFIG_KEYS}
        for value in self.data.values():
            self.write(value, {})
        self.zones = [{'name': f'z{i:03}', 'area': f'area{i % 47:02}', 'placements': [{}],
            'source_count': 1, 'bounds': {'min': [i * 100, 0, 0], 'max': [i * 100 + 50, 50, 10]},
            'duplicates': [], 'runtime_enabled': False, 'always_loaded': False} for i in range(85)]
        self.write(self.data['plan'], {'zones': self.zones})
        self.write(self.data['ambient'], {'placements': []})
        self.write(self.data['npcs'], {'status': 'npc_assets_verified', 'npcs': []})
        self.write(self.config, self.data)
        for name in (coordinator.RUNTIME, coordinator.BUILDER, coordinator.WRAPPER):
            self.bytes(name, b'synthetic tool/runtime')

    def bytes(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        return path

    def write(self, name, value):
        return self.bytes(name, json.dumps(value).encode())

    def engine(self, command, **kwargs):
        self.calls.append(command)
        self.assertEqual(kwargs, {'cwd': self.root, 'check': False})
        self.assertEqual(command[1], str(self.root / coordinator.WRAPPER))
        value = lambda flag: command[command.index(flag) + 1]
        for key in coordinator.CONFIG_KEYS:
            self.assertEqual(value('--' + key.replace('_', '-')), str(self.root / self.data[key]))
        report = Path(value('--report'))
        self.assertFalse(report.exists())
        self.assertFalse(report.with_suffix('.run.json').exists())
        inputs = coordinator.read_config(self.root, self.config)
        runtime = coordinator.digest(self.root / coordinator.RUNTIME)
        builder = coordinator.digest(self.root / coordinator.BUILDER)
        code = 0
        if command[2] == 'prepare':
            self.assertIn('--zone-batch', command)
            names = value('--zones').split(',')
            rows = []
            for name in names:
                source = next(z for z in self.zones if z['name'] == name)
                path = self.bytes(f'unreal/Content/World/Zones/{name}.umap', ('synthetic map ' + name).encode())
                rows.append({'zone': name, 'map': '/Game/World/Zones/' + name,
                    'map_sha256': coordinator.digest(path), 'source_placements': source['source_count'],
                    'visible_placements': len(source['placements']), 'duplicates': [], 'runtime_enabled': False,
                    **{key: [] for key in ('components', 'pending', 'npcs', 'doors', 'props',
                                          'prop_interactions', 'prop_gestures', 'original_collisions')}})
            document = {'status': 'zone_assets_saved', 'map': None, 'build_inputs': inputs,
                'runtime_sha256': runtime, 'builder_sha256': builder, 'plan_sha256': inputs['plan']['sha256'],
                'prop_plan_sha256': inputs['props']['sha256'],
                'interaction_plan_sha256': inputs['interactions']['sha256'],
                'gesture_plan_sha256': inputs['gestures']['sha256'], 'zones': rows,
                'integration': integration_counts(rows)}
            if self.fail_zone in names and not self.failed:
                code = 1
                self.failed = True
                document['status'] = 'synthetic_failure'
        else:
            self.assertEqual(command[2], 'assemble')
            index_path = Path(value('--build-batches'))
            index = json.loads(index_path.read_text())
            rows = validate_index(index, root=self.root)
            document = {'status': 'zones_saved', 'map': '/Game/Maps/WorldExploration',
                'plan_sha256': inputs['plan']['sha256'], 'build_inputs': inputs,
                'runtime_sha256': runtime, 'builder_sha256': builder,
                'build_batches': coordinator.reference(self.root, index_path), 'zones': rows}
            if self.fail_assembly and not self.failed:
                code = 1
                self.failed = True
                document['status'] = 'synthetic_assembly_failure'
            if self.wrong_final:
                document['builder_sha256'] = '0' * 64
        self.write(report, document)
        self.write(report.with_suffix('.run.json'), {'exit_code': code, 'runtime_sha256': runtime})
        if self.mutate_input:
            self.write(self.data['props'], {'changed': True})
        return SimpleNamespace(returncode=code)

    def run_build(self, batch_size=3, resume_index=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return coordinator.run_build(self.config, self.work, self.report, root=self.root,
                                         batch_size=batch_size, resume_index=resume_index, runner=self.engine)

    def seed(self):
        report = self.root / 'evidence/existing-probe.json'
        flags = [item for key in coordinator.CONFIG_KEYS
                 for item in ('--' + key.replace('_', '-'), str(self.root / self.data[key]))]
        self.engine([sys.executable, str(self.root / coordinator.WRAPPER), 'prepare', *flags,
                     '--zones', 'z000,z001,z002', '--zone-batch', '--report', str(report)],
                    cwd=self.root, check=False)
        index = {'schema': 'rea-bully-world-build-batches-v1',
            'inputs': coordinator.read_config(self.root, self.config),
            'runtime_sha256': coordinator.digest(self.root / coordinator.RUNTIME),
            'builder_sha256': coordinator.digest(self.root / coordinator.BUILDER),
            'batches': [{'zones': ['z000', 'z001', 'z002'], 'report': coordinator.reference(self.root, report),
                         'run': coordinator.reference(self.root, report.with_suffix('.run.json'))}]}
        self.calls.clear()
        return self.write('local/seed-index.json', index)

    def test_external_probe_is_reused_by_reference_without_copying_reports(self):
        seed = self.seed()
        original = (self.root / 'evidence/existing-probe.json').read_bytes()
        result = self.run_build(resume_index=seed)
        self.assertEqual(result['resumed_batches'], 1)
        self.assertEqual(len(self.calls), 29)
        self.assertFalse((self.work / 'batch-000.json').exists())
        index = json.loads((self.root / result['index']['path']).read_text())
        self.assertEqual(index['batches'][0]['report']['path'], 'evidence/existing-probe.json')
        self.assertEqual((self.root / 'evidence/existing-probe.json').read_bytes(), original)

    def test_stale_or_changed_seed_evidence_is_rejected_before_launch(self):
        seed = self.seed()
        self.bytes('unreal/Content/World/Zones/z001.umap', b'changed map')
        with self.assertRaisesRegex(ValueError, 'hash changed'):
            self.run_build(resume_index=seed)
        self.assertFalse(self.calls)

    def test_default_three_zone_processes_cover_85_once_then_assemble(self):
        result = self.run_build()
        self.assertEqual(len(self.calls), 30)
        self.assertEqual([c[2] for c in self.calls], ['prepare'] * 29 + ['assemble'])
        selections = [c[c.index('--zones') + 1].split(',') for c in self.calls[:-1]]
        self.assertTrue(all(1 <= len(zones) <= 3 for zones in selections))
        self.assertEqual([name for group in selections for name in group], [z['name'] for z in self.zones])
        self.assertEqual(result['zones'], 85)
        self.assertEqual(result['resumed_batches'], 0)

    def test_failed_batch_retry_preserves_failure_and_resumes_verified_prefix(self):
        self.fail_zone = 'z003'
        with self.assertRaisesRegex(ValueError, 'Zone batch failed'):
            self.run_build()
        self.assertEqual(len(self.calls), 2)
        failed = self.work / 'batch-001.json'
        old = failed.read_bytes()
        self.calls.clear()
        result = self.run_build()
        self.assertEqual(result['resumed_batches'], 1)
        self.assertEqual(failed.read_bytes(), old)
        self.assertTrue((self.work / 'batch-001-attempt-001.json').exists())
        self.assertEqual(len(self.calls), 29)

    def test_changed_map_invalidates_only_its_checkpoint(self):
        self.run_build()
        self.bytes('unreal/Content/World/Zones/z004.umap', b'changed after successful batch')
        self.calls.clear()
        result = self.run_build()
        self.assertEqual(result['resumed_batches'], 28)
        self.assertEqual([c[2] for c in self.calls], ['prepare', 'assemble'])
        self.assertEqual(self.calls[0][self.calls[0].index('--zones') + 1], 'z003,z004,z005')
        self.assertEqual(result['report']['path'], 'evidence/scene-attempt-001.json')

    def test_source_builder_and_runtime_changes_reject_old_batches(self):
        self.run_build(batch_size=85)
        for name in (self.data['props'], coordinator.BUILDER, coordinator.RUNTIME):
            self.bytes(name, b'{"revision":"different"}')
            self.calls.clear()
            with self.subTest(changed=name):
                result = self.run_build(batch_size=85)
                self.assertEqual(result['resumed_batches'], 0)
                self.assertEqual([c[2] for c in self.calls], ['prepare', 'assemble'])

    def test_orphan_run_and_corrupt_report_are_preserved(self):
        self.write(self.work / 'batch-000.run.json', {'exit_code': 1})
        self.bytes(self.work / 'batch-000-attempt-001.json', b'not JSON')
        self.write(self.work / 'batch-000-attempt-001.run.json', {'exit_code': 0})
        result = self.run_build(batch_size=85)
        self.assertEqual(result['resumed_batches'], 0)
        self.assertTrue((self.work / 'batch-000-attempt-002.json').is_file())
        self.assertEqual((self.work / 'batch-000-attempt-001.json').read_bytes(), b'not JSON')

    def test_mid_batch_source_change_stops_before_any_next_engine_process(self):
        self.mutate_input = True
        with self.assertRaisesRegex(ValueError, 'changed during this coordinator'):
            self.run_build()
        self.assertEqual(len(self.calls), 1)
        self.assertFalse((self.work / 'index.json').exists())

    def test_failed_assembly_retry_preserves_reports_and_reuses_batches(self):
        self.fail_assembly = True
        with self.assertRaisesRegex(ValueError, 'Persistent scene assembly failed'):
            self.run_build(batch_size=85)
        previous = self.report.read_bytes()
        self.calls.clear()
        result = self.run_build(batch_size=85)
        self.assertEqual(result['resumed_batches'], 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.report.read_bytes(), previous)
        self.assertEqual(result['report']['path'], 'evidence/scene-attempt-001.json')

    def test_zero_exit_cannot_hide_wrong_final_provenance(self):
        self.wrong_final = True
        with self.assertRaisesRegex(ValueError, 'Assembled scene report'):
            self.run_build(batch_size=85)

    def test_invalid_config_and_incomplete_plan_fail_before_launch(self):
        self.write(self.config, {**self.data, 'unexpected': 'file.json'})
        with self.assertRaisesRegex(ValueError, 'exactly these'):
            self.run_build()
        self.write(self.config, self.data)
        self.write(self.data['plan'], {'zones': self.zones[:-1]})
        with self.assertRaisesRegex(ValueError, '85 distinct zones'):
            self.run_build()
        self.assertFalse(self.calls)


if __name__ == '__main__':
    unittest.main()

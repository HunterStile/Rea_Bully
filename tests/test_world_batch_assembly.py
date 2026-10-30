"""Synthetic saved batches must prove complete coverage before final scene mutation."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from unreal_assemble_world import (BUILDER, RUNTIME, checked, eligible_zones, integration_counts,
    reference, sha256, soft_zone_manifest, validate_batch, validate_index)


class WorldBatchAssembly(unittest.TestCase):
    def fixture(self, root):
        def write(name, value):
            path = root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding='utf-8')
            return reference(path, root)
        for relative in (RUNTIME, BUILDER):
            path = root/relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'synthetic version')
        source = write('local/source.json', {'synthetic': True})
        zones, rows = [], []
        for i in range(85):
            name = f'zone_{i:03d}'
            zones.append({'name': name, 'placements': [{'synthetic': True}],
                'bounds': {'min': [0, 0, 0], 'max': [1, 2, 3]}, 'source_count': 1,
                'duplicates': [], 'runtime_enabled': True, 'always_loaded': False, 'area': 'MainMap'})
            path = root/'unreal/Content/World/Zones'/f'{name}.umap'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('synthetic '+name).encode())
            rows.append({'zone': name, 'map': '/Game/World/Zones/'+name, 'map_sha256': sha256(path),
                'source_placements': 1, 'visible_placements': 1, 'duplicates': [], 'runtime_enabled': True,
                'components': [], 'pending': [], 'npcs': [], 'doors': [], 'props': [],
                'prop_interactions': [], 'prop_gestures': [], 'original_collisions': []})
        plan = {'zones': zones, 'limitations': ['Synthetic fixture'], 'source_catalogs': [source]}
        inputs = {'plan': write('local/plan.json', plan)}
        index = {'schema': 'rea-bully-world-build-batches-v1', 'inputs': inputs,
                 'runtime_sha256': sha256(root/RUNTIME), 'builder_sha256': sha256(root/BUILDER), 'batches': []}
        for batch_id, start in enumerate(range(0, 85, 30)):
            subset = rows[start:start+30]
            report = {'status': 'zone_assets_saved', 'map': None, 'build_inputs': inputs,
                'builder_sha256': index['builder_sha256'], 'runtime_sha256': index['runtime_sha256'],
                'plan_sha256': inputs['plan']['sha256'], 'prop_plan_sha256': None,
                'interaction_plan_sha256': None, 'gesture_plan_sha256': None,
                'zones': subset, 'integration': integration_counts(subset)}
            index['batches'].append({'zones': [r['zone'] for r in subset],
                'report': write(f'local/batch-{batch_id}.json', report),
                'run': write(f'local/batch-{batch_id}.run.json', {'exit_code': 0, 'runtime_sha256': index['runtime_sha256']})})
        return index

    def rewrite(self, root, ref, transform):
        path = root/ref['path']
        value = json.loads(path.read_text())
        transform(value)
        path.write_text(json.dumps(value), encoding='utf-8')
        ref['sha256'] = sha256(path)

    def test_complete_saved_batches_return_exact_plan_order_without_unreal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = self.fixture(root)
            index['batches'].reverse()
            rows = validate_index(index, root)
            self.assertEqual([r['zone'] for r in rows], [f'zone_{i:03d}' for i in range(85)])
            self.assertNotIn('unreal', sys.modules)
            first = index['batches'][0]
            report = validate_batch(root, first['zones'], first['report'], first['run'], index['inputs'],
                                    index['runtime_sha256'], index['builder_sha256'])
            self.assertEqual(report['status'], 'zone_assets_saved')

    def test_changed_input_original_source_map_runtime_and_builder_are_rejected(self):
        for relative in ('local/plan.json', 'local/source.json', 'unreal/Content/World/Zones/zone_001.umap', RUNTIME, BUILDER):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                index = self.fixture(root)
                with (root/relative).open('ab') as stream:
                    stream.write(b'changed')
                with self.assertRaises((ValueError, json.JSONDecodeError)):
                    validate_index(index, root)

    def test_missing_and_duplicate_zone_batches_cannot_form_a_complete_world(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = self.fixture(root)
            missing = copy.deepcopy(index)
            missing['batches'].pop()
            with self.assertRaisesRegex(ValueError, 'missing eligible'):
                validate_index(missing, root)
            duplicate = copy.deepcopy(index)
            duplicate['batches'].append(copy.deepcopy(duplicate['batches'][0]))
            with self.assertRaisesRegex(ValueError, 'duplicates a zone'):
                validate_index(duplicate, root)

    def test_report_and_run_provenance_cannot_be_relabelled(self):
        changes = [('report', 'builder_sha256', '0'*64), ('report', 'runtime_sha256', '0'*64),
                   ('report', 'status', 'zones_saved'), ('report', 'build_inputs', {}),
                   ('run', 'exit_code', 1), ('run', 'exit_code', False), ('run', 'runtime_sha256', '0'*64)]
        for kind, field, value in changes:
            with self.subTest(kind=kind, field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                index = self.fixture(root)
                self.rewrite(root, index['batches'][0][kind], lambda r: r.update({field: value}))
                with self.assertRaisesRegex(ValueError, 'provenance|runtime'):
                    validate_index(index, root)

    def test_source_zone_counts_and_integration_totals_cannot_change(self):
        for mode in ('count', 'map', 'zone', 'aggregate'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                index = self.fixture(root)
                def mutate(report):
                    if mode == 'count': report['zones'][0]['visible_placements'] += 1
                    elif mode == 'map': report['zones'][0]['map'] = '/Game/OtherMap'
                    elif mode == 'zone': report['zones'][0]['zone'] = 'zone_084'
                    else: report['integration']['npcs'] = 1
                self.rewrite(root, index['batches'][0]['report'], mutate)
                with self.assertRaises(ValueError):
                    validate_index(index, root)

    def test_index_references_cannot_escape_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            for path in ('../outside', '/absolute', 'C:/absolute', 'local\\plan.json', 'local/../plan.json'):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    checked({'path': path, 'sha256': '0'*64}, root)

    def test_named_npc_source_and_route_must_survive_saved_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = self.fixture(root)
            documents = {'ambient': {'placements': [{'ped_id': 9, 'zone': 'zone_000',
                'source_reference': 'synthetic_original_point', 'points': [[0, 0, 0], [1, 2, 3]]}]},
                'npcs': {'status': 'npc_assets_verified', 'npcs': [{'ped_id': 9}]}}
            for name, document in documents.items():
                path = root/'local'/f'{name}.json'
                path.write_text(json.dumps(document))
                index['inputs'][name] = reference(path, root)
            for batch in index['batches']:
                def join(report):
                    report['build_inputs'] = index['inputs']
                    for row in report['zones']:
                        if row['zone'] == 'zone_000':
                            row['npcs'] = [{'ped_id': 9, 'source': 'synthetic_original_point', 'route_points': 2}]
                    report['integration'] = integration_counts(report['zones'])
                self.rewrite(root, batch['report'], join)
            self.assertEqual(integration_counts(validate_index(index, root))['npcs'], 1)
            self.rewrite(root, index['batches'][0]['report'],
                         lambda report: report['zones'][0]['npcs'][0].update(source='another_point'))
            with self.assertRaisesRegex(ValueError, 'NPC source or route'):
                validate_index(index, root)

    def test_manifest_binds_soft_paths_without_object_getter_or_asset_load(self):
        instances = []
        class FakeZone:
            def __init__(self):
                self.text = ''
                self.values = {}
                instances.append(self)
            def import_text(self, text):
                self.text = text
                return True
            def export_text(self):
                return self.text
            def set_editor_property(self, name, value):
                if name == 'level': raise AssertionError('Soft field must not use UObject setter')
                self.values[name] = value
            def get_editor_property(self, name):
                raise AssertionError('Getter would load a soft World')
        fake = SimpleNamespace(ReaBullyWorldZone=FakeZone, Vector=lambda *values: values)
        zone = {'name': 'tschool', 'bounds': {'min': [1, 2, 3], 'max': [4, 5, 6]},
                'runtime_enabled': True, 'always_loaded': False, 'area': 'MainMap'}
        result = soft_zone_manifest(fake, [zone])
        self.assertEqual(result, instances)
        self.assertEqual(result[0].text, '(Level="/Game/World/Zones/tschool.tschool")')
        self.assertEqual(result[0].values['minimum'], (1, 2, 3))
        self.assertEqual(result[0].values['area'], 'MainMap')

    def test_soft_import_failure_cannot_return_partial_manifest(self):
        fake = SimpleNamespace(ReaBullyWorldZone=lambda: SimpleNamespace(import_text=lambda text: False))
        with self.assertRaisesRegex(ValueError, 'Unable to bind'):
            soft_zone_manifest(fake, [{'name': 'tschool'}])


if __name__ == '__main__':
    unittest.main()

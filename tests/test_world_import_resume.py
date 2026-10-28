"""Interrupted imports retain source identity and already recovered materials."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from world_import_contracts import material_index, verify_checkpoint, verify_material_path


class ImportIdentity(unittest.TestCase):
    def test_source_slots_and_recovered_additive_resolve_same_index(self):
        key = '05165_fgrd_sc1b22'
        self.assertEqual(material_index(key, 4, 'REA_Mat_0001', 'REA_Mat_0001'), 1)
        self.assertEqual(material_index(key, 4, 'M_'+key+'_0001', 'REA_Mat_0001'), 1)

    def test_conflicting_foreign_or_out_of_range_materials_are_rejected(self):
        for names in [('REA_Mat_0001', 'REA_Mat_0002'), ('M_other_0001', 'None'),
                      ('M_other_0001', 'REA_Mat_0001', 'REA_Mat_0001'), ('REA_Mat_0009',)]:
            with self.subTest(names=names), self.assertRaises(ValueError):
                material_index('05165_fgrd_sc1b22', 4, *names)

    def test_another_models_material_cannot_hide_behind_original_slot_names(self):
        key = '05165_fgrd_sc1b22'
        prefix = f'/Game/Original/World/Models/{key}/{key}/'
        verify_material_path(key, 1, prefix+'Materials/REA_Mat_0001.REA_Mat_0001', prefix)
        name = f'M_{key}_0001'
        verify_material_path(key, 1, f'/Game/RecoveredMaterials/World/{name}.{name}', prefix)
        for path in ['/Game/Original/World/Models/other/other/Materials/REA_Mat_0001.REA_Mat_0001',
                     '/Game/RecoveredMaterials/World/M_other_0001.M_other_0001',
                     prefix+'Materials/REA_Mat_0002.REA_Mat_0002']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                verify_material_path(key, 1, path, prefix)

    def test_a_checkpoint_must_match_both_source_and_asset_folder(self):
        model = {'model_key': 'model', 'output_sha256': 'source-digest'}
        record = {'status': 'imported', 'model_key': 'model', 'source_sha256': 'source-digest',
                  'meshes': [{'path': '/Game/Original/World/Models/model/model/StaticMeshes/model.model'}]}
        verify_checkpoint(model, record)
        for field, value in [('status', 'partial'), ('source_sha256', 'other'), ('meshes', []),
                             ('meshes', [{'path': '/Game/Original/World/Models/other/mesh.mesh'}])]:
            changed = copy.deepcopy(record)
            changed[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                verify_checkpoint(model, changed)


def recovered_material_case(source_text, wrong_signature=False):
    """Execute the actual recover() body with only the engine asset boundary stubbed."""
    source = {'extras': {'REA_originalAlpha': {
        'flags': 1, 'sourceBlend': 0, 'destinationBlend': 0, 'testEnabled': False}}}
    signature = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
    material = SimpleNamespace(get_path_name=lambda: '/Game/RecoveredMaterials/World/M_model_0001')
    editor = SimpleNamespace(does_asset_exist=lambda path: True, load_asset=lambda path: material,
        get_metadata_tag=lambda asset, tag: 'wrong' if wrong_signature else signature)
    fake_unreal = SimpleNamespace(EditorAssetLibrary=editor, load_asset=editor.load_asset)
    library = SimpleNamespace(get_texture_parameter_names=lambda asset: ['OriginalTexture'])
    node = next(node for node in ast.parse(source_text).body if isinstance(node, ast.FunctionDef) and node.name == 'recover')
    namespace = {'unreal': fake_unreal, 'lib': library, 'hashlib': hashlib, 'json': json,
                 'ROOT': '/Game/RecoveredMaterials/World', 'ensure_instancing': lambda asset: asset}
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'actual_recover_function', 'exec'), namespace)
    return namespace['recover'](material, source, 'model', 1), material


class MaterialResume(unittest.TestCase):
    def test_recovered_material_uses_its_existing_original_texture_parameter(self):
        result, material = recovered_material_case((ROOT/'tools/unreal_world_materials.py').read_text())
        self.assertEqual(result, (material, True))

    def test_recovered_material_with_different_source_signature_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'source changed'):
            recovered_material_case((ROOT/'tools/unreal_world_materials.py').read_text(), wrong_signature=True)


if __name__ == '__main__':
    unittest.main()

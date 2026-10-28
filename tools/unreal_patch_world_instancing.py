"""Repair HISM material usage for verified world checkpoints; execute in UE Python."""
import hashlib
import json
import os
from pathlib import Path
import sys
import unreal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from unreal_world_materials import ensure_instancing, instancing_changes


def main():
    report = Path(os.environ['BULLY_UE_REPORT'])
    if report.exists():
        raise RuntimeError('New evidence path required')
    records = []
    for checkpoint in sorted((ROOT/'local/world-import/models').glob('*.json')):
        raw = checkpoint.read_bytes()
        model = json.loads(raw)
        if model['status'] != 'imported':
            raise RuntimeError('World checkpoint is not a verified import: ' + str(checkpoint))
        meshes = []
        for record in model['meshes']:
            mesh = unreal.load_asset(record['path'])
            if not isinstance(mesh, unreal.StaticMesh):
                raise RuntimeError('World checkpoint mesh missing: ' + record['path'])
            if unreal.EditorAssetLibrary.get_metadata_tag(mesh, 'REAOriginalGLBSHA256') != model['source_sha256']:
                raise RuntimeError('World checkpoint provenance mismatch: ' + record['path'])
            slots = list(mesh.get_editor_property('static_materials'))
            if len(slots) != len(record['materials']):
                raise RuntimeError('World checkpoint material count changed: ' + record['path'])
            for slot, expected in zip(slots, record['materials']):
                material = slot.material_interface
                if material is None or material.get_path_name() != expected['material']:
                    raise RuntimeError('World checkpoint material binding changed: ' + record['path'])
                local_material = ensure_instancing(material)
                if local_material != material:
                    # Existing checkpoints use project MIs, so their paths stay intact.
                    # Reject a direct external-material binding rather than silently
                    # making the historical checkpoint disagree with the mesh.
                    raise RuntimeError('Direct external world material needs an explicit checkpoint migration')
                if not unreal.MaterialEditingLibrary.has_material_usage(local_material, unreal.MaterialUsage.MATUSAGE_INSTANCED_STATIC_MESHES):
                    raise RuntimeError('HISM material usage still absent: ' + material.get_path_name())
            meshes.append({'path': record['path'], 'materials_verified': len(slots),
                           'source_sha256_preserved': model['source_sha256']})
        if hashlib.sha256(checkpoint.read_bytes()).digest() != hashlib.sha256(raw).digest():
            raise RuntimeError('World checkpoint changed during repair')
        records.append({'model_key': model['model_key'], 'checkpoint_sha256': hashlib.sha256(raw).hexdigest(),
                        'meshes': meshes})
        unreal.log('REA_WORLD_INSTANCING_VERIFIED ' + model['model_key'])
    unreal.AutomationLibrary.finish_loading_before_screenshot()
    report.write_text(json.dumps({'status': 'world_instancing_materials_verified',
        'models': records, 'materials': instancing_changes(),
        'engine': unreal.SystemLibrary.get_engine_version(),
        'external_assets_modified': False}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()

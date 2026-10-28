"""Import a bounded immutable job of original entity-space models; run in UE Python."""
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import unreal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from unreal_world_materials import recover
from unreal_import_npcs import verify_import_origin
from world_import_contracts import material_index, verify_material_path

job = json.loads(Path(os.environ['REA_WORLD_IMPORT_JOB']).read_text(encoding='utf-8'))
report_path = Path(os.environ['BULLY_UE_REPORT'])
if report_path.exists():
    raise RuntimeError('New report required')
records = []
# Keep the stock glTF material translator, with a private process-local copy of
# its asset pipeline. Nanite is unused by these source-alpha materials and HISM
# components; building it for every low-polygon shape wastes import time/disk.
pipeline_path = '/Game/World/Import/WorldAssetsPipeline'
pipeline = (unreal.load_asset(pipeline_path) if unreal.EditorAssetLibrary.does_asset_exist(pipeline_path)
    else unreal.EditorAssetLibrary.duplicate_loaded_asset(
        unreal.load_asset('/Interchange/Pipelines/DefaultGLTFAssetsPipeline'), pipeline_path))
if pipeline is None:
    raise RuntimeError('Could not create project-local glTF import pipeline')
pipeline.get_editor_property('mesh_pipeline').set_editor_property('build_nanite', False)
unreal.EditorAssetLibrary.save_loaded_asset(pipeline, only_if_is_dirty=False)
options = unreal.InterchangePipelineStackOverride()
options.set_editor_property('override_pipelines', [unreal.SoftObjectPath(pipeline.get_path_name()),
    unreal.SoftObjectPath('/Interchange/Pipelines/DefaultGLTFPipeline.DefaultGLTFPipeline')])
for record in job['models']:
    key, folder = record['model_key'], record['asset_folder']
    source = Path(record['prepared'])
    source_bytes = source.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != record['prepared_sha256']:
        raise RuntimeError('Prepared source hash changed')
    existing = list(unreal.EditorAssetLibrary.list_assets(folder, recursive=True, include_folder=False))
    task = None
    if not existing:
        task = unreal.AssetImportTask()
        task.filename = str(source)
        task.destination_path = folder
        task.automated = True
        task.replace_existing = False
        task.save = True
        task.options = options
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    unreal.AutomationLibrary.finish_loading_before_screenshot()
    assets = [unreal.load_asset(path) for path in
        unreal.EditorAssetLibrary.list_assets(folder, recursive=True, include_folder=False)]
    prefix = folder + '/' + source.stem + '/'
    if any(asset is None or not asset.get_path_name().startswith(prefix) for asset in assets):
        raise RuntimeError('Unrelated or unreadable asset in existing world import: ' + key)
    imported_meshes = [asset for asset in assets if isinstance(asset, unreal.StaticMesh)]
    if len(imported_meshes) != record['shape_count']:
        raise RuntimeError('Preserve incomplete import with unexpected shape count: ' + key)
    # Validate every saved mesh before changing any of them. A prior failed run
    # can have imported the full source but stopped during material recovery.
    provenance = []
    for asset in imported_meshes:
        provenance.append(verify_import_origin(unreal, asset, source, hashlib.md5(source_bytes).hexdigest()))
        previous = unreal.EditorAssetLibrary.get_metadata_tag(asset, 'REAOriginalGLBSHA256')
        if previous and previous != record['source_sha256']:
            raise RuntimeError('Existing world mesh belongs to different source bytes: ' + key)
    meshes = []
    for asset in imported_meshes:
        path = asset.get_path_name()
        setup = asset.get_editor_property('body_setup')
        if setup is None:
            raise RuntimeError(f'Missing collision body {path}')
        setup.set_editor_property('collision_trace_flag', unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE)
        setup.set_editor_property('double_sided_geometry', True)
        only_additive = True
        materials = []
        for index, slot in enumerate(asset.get_editor_property('static_materials')):
            original = slot.material_interface
            if original is None:
                raise RuntimeError(f'Cannot map exact original material index for {path}')
            source_index = material_index(key, len(record['materials']), original.get_name(),
                slot.get_editor_property('material_slot_name'), slot.get_editor_property('imported_material_slot_name'))
            verify_material_path(key, source_index, original.get_path_name(), prefix)
            material, additive = recover(original, record['materials'][source_index], key, source_index)
            if additive:
                asset.set_material(index, material)
            only_additive &= additive
            materials.append({'source_index': source_index, 'material': material.get_path_name(), 'recovered_additive': additive})
        unreal.EditorAssetLibrary.set_metadata_tag(asset, 'REAOriginalGLBSHA256', record['source_sha256'])
        unreal.EditorAssetLibrary.save_loaded_asset(asset, only_if_is_dirty=False)
        bounds = asset.get_bounds()
        origin = [bounds.origin.x, bounds.origin.y, bounds.origin.z]
        extent = [bounds.box_extent.x, bounds.box_extent.y, bounds.box_extent.z]
        if not all(math.isfinite(value) for value in origin + extent):
            raise RuntimeError('Imported mesh has nonfinite bounds: ' + key)
        meshes.append({'path': asset.get_path_name(), 'visual_only': only_additive,
                       'bounds': str(bounds), 'bounds_cm': {
                           'min': [origin[i] - extent[i] for i in range(3)],
                           'max': [origin[i] + extent[i] for i in range(3)]},
                       'materials': materials})
    if len(meshes) != record['shape_count']:
        raise RuntimeError(f'Original shape count changed: {key}: {len(meshes)} vs {record["shape_count"]}')
    bounds_error = None
    if record.get('source_bounds_cm'):
        combined = {side: [function(mesh['bounds_cm'][side][axis] for mesh in meshes)
                          for axis in range(3)] for side, function in [('min', min), ('max', max)]}
        bounds_error = max(abs(combined[side][axis] - record['source_bounds_cm'][side][axis])
                           for side in ('min', 'max') for axis in range(3))
        if bounds_error > .1:
            raise RuntimeError(f'Imported coordinate frame/bounds changed: {key}: {bounds_error} cm')
    result = {'status': 'imported', 'model_key': key, 'source_sha256': record['source_sha256'],
              'prepared_sha256': record['prepared_sha256'], 'meshes': meshes,
              'source_bounds_max_error_cm': bounds_error,
              'import_mode': 'resumed_verified_source' if existing else 'new_import',
              'import_provenance': provenance}
    model_report = ROOT / 'local/world-import/models' / (key + '.json')
    model_report.parent.mkdir(parents=True, exist_ok=True)
    with model_report.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    records.append(result)
    unreal.log(f'REA_WORLD_IMPORTED {key} {len(meshes)}')
    task = asset = setup = original = material = None
    assets = imported_meshes = None
    unreal.SystemLibrary.collect_garbage()
report_path.write_text(json.dumps({'status': 'imported_and_verified', 'models': records,
    'engine': unreal.SystemLibrary.get_engine_version()}, indent=2), encoding='utf-8')

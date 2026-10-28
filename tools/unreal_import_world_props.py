"""Import original PANM skins and clips with isolated skeletons; run in UE Python.

Requires the compiled ReaBullyWorldProp types. Set BULLY_UE_REPORT to a new JSON
path and optionally REA_WORLD_PROPS_KEYS to comma-separated exact model keys.
Writes one resumable checkpoint per verified model under local/world-import/props.
No exported gameplay/event clip is automatically looped merely for being cyclic.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from plan_world_props import ambient_loop
from unreal_import_npcs import verify_import_origin


def read_source(model):
    source = ROOT/model['output']
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != model['output_sha256']:
        raise RuntimeError('Verified original prop GLB changed: ' + model['model_key'])
    magic, version, size, json_size, kind = struct.unpack_from('<IIIII', raw)
    if (magic, version, size, kind) != (0x46546C67, 2, len(raw), 0x4E4F534A):
        raise RuntimeError('Invalid original prop GLB')
    document = json.loads(raw[20:20+json_size])
    if len(document['skins']) != 1 or len(document['meshes']) != 1:
        raise RuntimeError('Expected one original object skin and mesh')
    joints = document['skins'][0]['joints']
    names = [document['nodes'][joint]['name'] for joint in joints]
    if names != model['bone_names'] or len(joints) != model['bones']:
        raise RuntimeError('Original prop bone inventory changed')
    # UE 5.8 InterchangeHelper.cpp SanitizeName(bIsJoint=true) replaces spaces
    # with dashes. These source hierarchies contain no other invalid characters.
    if any(not re.fullmatch(r'[A-Za-z0-9_ ]+', name) for name in names):
        raise RuntimeError('Source joint requires an unverified UE name conversion')
    imported_names = [name.replace(' ', '-') for name in names]
    if len(set(imported_names)) != len(imported_names):
        raise RuntimeError('Original bone names collide after UE sanitization')
    parents = {child: parent for parent, node in enumerate(document['nodes']) for child in node.get('children', [])}
    expected_parents = [joints.index(parents[joint]) if parents.get(joint) in joints else -1 for joint in joints]
    if [animation['name'] for animation in document.get('animations', [])] != [clip['glb_name'] for clip in model['clips']]:
        raise RuntimeError('Original prop animation inventory changed')
    return source, imported_names, expected_parents


def map_clips(model, sequences, skeleton, source_stem):
    if len(sequences) != len(model['clips']):
        raise RuntimeError('Original prop animation count changed')
    mapped = {}
    for clip in model['clips']:
        name = clip['glb_name']
        allowed_names = {name, source_stem + name, source_stem + '_' + name}
        # InterchangeGenericAssetsPipeline.cpp renames the sole imported
        # animation to <source>_Anim when importing its mesh too. The exact
        # single-clip source inventory and saved import digest are checked;
        # this must never become a fallback for an unidentified multi-clip file.
        if len(model['clips']) == 1:
            allowed_names.add(source_stem + '_Anim')
        matching = [sequence for sequence in sequences if sequence.get_name() in allowed_names]
        if len(matching) != 1 or matching[0].get_editor_property('skeleton') != skeleton:
            raise RuntimeError('Original prop animation identity/skeleton is ambiguous: ' + clip['name'])
        sequence = matching[0]
        duration = sequence.get_editor_property('sequence_length')
        if not math.isfinite(duration) or abs(duration - clip['duration']) > .034:
            raise RuntimeError('Original prop animation duration changed: ' + clip['name'])
        mapped[clip['name']] = sequence
    if len({sequence.get_path_name() for sequence in mapped.values()}) != len(mapped):
        raise RuntimeError('Two source clips resolve to the same imported animation')
    return mapped


def main(*, door_models=False):
    import unreal

    if door_models:
        # Explicit engine-integration candidates. The five new exports retain
        # their independent pose/visual-review gap; they are not relabelled as
        # the validated baseline. Shared importer still checks the GLB digest,
        # source-file tags, joint hierarchy, isolated skeleton and every clip.
        from plan_world_doors import load_models
        catalog = load_models()
        verified_keys = {model['model_key'] for model in catalog['models']}
        catalog_sha256 = hashlib.sha256(json.dumps(catalog, sort_keys=True).encode()).hexdigest()
        validation_sha256 = None
    else:
        catalog_path = Path(os.environ.get('REA_WORLD_PROPS_CATALOG', ROOT/'evidence/skinned-props-export-v04-rgb.json'))
        catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
        validation_path = ROOT/'evidence/skinned-props-validation-v04-rgb-final.json'
        validation = json.loads(validation_path.read_text(encoding='utf-8'))
        if validation.get('status') != 'passed' or validation.get('failures'):
            raise RuntimeError('Original prop validation has not passed')
        if (ROOT/validation['source_report']).resolve() != catalog_path.resolve():
            raise RuntimeError('Prop validation belongs to a different source catalog')
        verified_keys = {model['model_key'] for model in validation['models']}
        catalog_sha256 = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
        validation_sha256 = hashlib.sha256(validation_path.read_bytes()).hexdigest()
    report_path = Path(os.environ['BULLY_UE_REPORT'])
    if report_path.exists():
        raise RuntimeError('New prop import report required')
    if not hasattr(unreal, 'ReaBullyWorldPropTools') or not hasattr(unreal, 'ReaBullyWorldPropData'):
        raise RuntimeError('Compile ReaBullyWorldProp runtime types before importing props')
    selected = set(filter(None, os.environ.get('REA_WORLD_PROPS_KEYS', '').split(',')))
    models = [model for model in catalog['models'] if not selected or model['model_key'] in selected]
    if selected and selected != {model['model_key'] for model in models}:
        raise RuntimeError('Requested prop model is missing from source catalog')
    if not models or any(model['status'] != 'exported' or model['model_key'] not in verified_keys for model in models):
        raise RuntimeError('Only exported and validated original props may be imported')

    pipeline_path = '/Game/World/Import/WorldPropAssetsPipeline'
    pipeline = (unreal.load_asset(pipeline_path) if unreal.EditorAssetLibrary.does_asset_exist(pipeline_path)
        else unreal.EditorAssetLibrary.duplicate_loaded_asset(
            unreal.load_asset('/Interchange/Pipelines/DefaultGLTFAssetsPipeline'), pipeline_path))
    if not pipeline:
        raise RuntimeError('Could not create isolated prop import pipeline')
    mesh_pipeline = pipeline.get_editor_property('mesh_pipeline')
    mesh_pipeline.set_editor_property('create_physics_asset', False)
    mesh_pipeline.set_editor_property('use_high_precision_skin_weights', True)
    mesh_pipeline.set_editor_property('update_skeleton_reference_pose', False)
    common = pipeline.get_editor_property('common_skeletal_meshes_and_animations_properties')
    common.set_editor_property('import_only_animations', False)
    common.set_editor_property('try_auto_select_skeleton', False)
    common.set_editor_property('skeleton', None)
    common.set_editor_property('use_t0_as_ref_pose', False)
    unreal.EditorAssetLibrary.save_loaded_asset(pipeline, only_if_is_dirty=False)
    options = unreal.InterchangePipelineStackOverride()
    options.set_editor_property('override_pipelines', [unreal.SoftObjectPath(pipeline.get_path_name()),
        unreal.SoftObjectPath('/Interchange/Pipelines/DefaultGLTFPipeline.DefaultGLTFPipeline')])
    records = []
    checkpoint_folder = ROOT/'local/world-import/props'
    checkpoint_folder.mkdir(parents=True, exist_ok=True)
    for model in models:
        source, bone_names, bone_parents = read_source(model)
        key = model['model_key']
        loop_name = ambient_loop(model)
        folder = '/Game/Original/World/Props/' + key
        checkpoint_path = checkpoint_folder/(key+'.json')
        checkpoint = json.loads(checkpoint_path.read_text(encoding='utf-8')) if checkpoint_path.exists() else None
        if checkpoint and (checkpoint.get('source_sha256') != model['output_sha256'] or
                           checkpoint.get('status') != 'prop_assets_verified'):
            raise RuntimeError('Prior prop import does not match verified source: ' + key)
        exists = bool(unreal.EditorAssetLibrary.list_assets(folder, recursive=True, include_folder=False))
        if not exists:
            if checkpoint:
                raise RuntimeError('Verified prop asset folder is missing: ' + folder)
            task = unreal.AssetImportTask()
            task.filename = str(source)
            task.destination_path = folder
            task.automated = True
            task.replace_existing = False
            task.save = True
            task.options = options
            unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
            unreal.AutomationLibrary.finish_loading_before_screenshot()
        assets = [unreal.load_asset(path) for path in unreal.EditorAssetLibrary.list_assets(folder, recursive=True, include_folder=False)]
        if any(asset is None for asset in assets):
            raise RuntimeError('Original prop folder contains unreadable assets')
        meshes = [asset for asset in assets if isinstance(asset, unreal.SkeletalMesh)]
        skeletons = [asset for asset in assets if isinstance(asset, unreal.Skeleton)]
        sequences = [asset for asset in assets if isinstance(asset, unreal.AnimSequence)]
        if len(meshes) != 1 or len(skeletons) != 1:
            raise RuntimeError('Expected one imported mesh for original prop ' + key)
        mesh = meshes[0]
        skeleton = mesh.get_editor_property('skeleton')
        if not skeleton or skeleton != skeletons[0] or not skeleton.get_path_name().startswith(folder + '/'):
            raise RuntimeError('Prop does not have its own isolated skeleton: ' + key)
        source_md5 = hashlib.md5(source.read_bytes()).hexdigest()
        # The shared native SourceFile-tag check proves the saved imported bytes,
        # including when an earlier run stopped before writing its checkpoint.
        provenance = [verify_import_origin(unreal, asset, source, source_md5) for asset in [mesh, *sequences]]
        data_path = folder + '/DA_OriginalProp'
        source_prefix = folder + '/' + source.stem + '/'
        imported_types = (unreal.SkeletalMesh, unreal.Skeleton, unreal.AnimSequence,
                          unreal.Material, unreal.MaterialInstanceConstant, unreal.Texture2D)
        for asset in assets:
            package = asset.get_path_name().split('.')[0]
            if package != data_path and (not package.startswith(source_prefix) or not isinstance(asset, imported_types)):
                raise RuntimeError('Unrelated asset in original prop folder: ' + package)
        if list(unreal.ReaBullyWorldPropTools.get_mesh_bone_names(mesh)) != bone_names:
            raise RuntimeError('Original prop bone names/order changed: ' + key)
        if list(unreal.ReaBullyWorldPropTools.get_mesh_bone_parents(mesh)) != bone_parents:
            raise RuntimeError('Original prop hierarchy changed: ' + key)
        clips = map_clips(model, sequences, skeleton, source.stem)
        for asset in [mesh, skeleton, *sequences]:
            previous = unreal.EditorAssetLibrary.get_metadata_tag(asset, 'REAOriginalPropSHA256')
            if checkpoint and previous != model['output_sha256']:
                raise RuntimeError('Verified prop provenance metadata changed: ' + asset.get_path_name())
            unreal.EditorAssetLibrary.set_metadata_tag(asset, 'REAOriginalPropSHA256', model['output_sha256'])
            unreal.EditorAssetLibrary.save_loaded_asset(asset, only_if_is_dirty=False)
        if unreal.EditorAssetLibrary.does_asset_exist(data_path):
            data = unreal.load_asset(data_path)
            if not isinstance(data, unreal.ReaBullyWorldPropData):
                raise RuntimeError('Existing prop data asset has wrong type')
            if unreal.EditorAssetLibrary.get_metadata_tag(data, 'REAOriginalPropSHA256') != model['output_sha256']:
                raise RuntimeError('Existing prop data asset has different provenance')
        else:
            factory = unreal.DataAssetFactory()
            factory.set_editor_property('data_asset_class', unreal.ReaBullyWorldPropData)
            data = unreal.AssetToolsHelpers.get_asset_tools().create_asset('DA_OriginalProp', folder, unreal.ReaBullyWorldPropData, factory)
        if not data:
            raise RuntimeError('Could not create original prop data asset')
        for name, value in {'model_key': key, 'source_sha256': model['output_sha256'], 'mesh': mesh,
                'ambient_loop': clips[loop_name] if loop_name else None,
                'ambient_loop_original': loop_name or ''}.items():
            data.set_editor_property(name, value)
        unreal.EditorAssetLibrary.set_metadata_tag(data, 'REAOriginalPropSHA256', model['output_sha256'])
        unreal.EditorAssetLibrary.save_loaded_asset(data, only_if_is_dirty=False)
        record = {'status': 'prop_assets_verified', 'model_key': key, 'source_sha256': model['output_sha256'],
            'mesh': mesh.get_path_name(), 'own_skeleton': skeleton.get_path_name(), 'data': data.get_path_name(),
            'bone_names': bone_names, 'bone_parents': bone_parents,
            'import_provenance': provenance,
            'clips': {name: sequence.get_path_name() for name, sequence in clips.items()},
            'ambient_loop_original': loop_name, 'playback': 'original_ambient_loop' if loop_name else 'original_bind_pose',
            'source_wrapper_transform': model.get('preserved_global_skin_transform'),
            'collision': 'disabled_original_object_collision_not_recovered'}
        if checkpoint and record != checkpoint:
            raise RuntimeError('Previously verified prop assets changed: ' + key)
        if not checkpoint:
            with checkpoint_path.open('x', encoding='utf-8') as stream:
                json.dump(record, stream, indent=2)
        records.append(record)
        unreal.log('REA_WORLD_PROP_VERIFIED ' + key)
        assets = meshes = sequences = clips = mesh = skeleton = data = None
        unreal.SystemLibrary.collect_garbage()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open('x', encoding='utf-8') as stream:
        json.dump({'status': 'prop_assets_verified', 'models': records,
            'catalog_sha256': catalog_sha256,
            'validation_sha256': validation_sha256,
            'door_candidate_sources': catalog.get('source_catalogs') if door_models else None,
            'engine': unreal.SystemLibrary.get_engine_version(),
            'limitations': ['Visual original props only; collision, interaction and conditional scheduling remain incomplete.',
                           'Asset identity and hierarchy checks do not replace rendered pose verification.']}, stream, indent=2)


if __name__ == '__main__':
    main()

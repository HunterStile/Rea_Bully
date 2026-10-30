"""Attach source-verified manual gestures without reimporting original carnival skins."""
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from plan_prop_gestures import CLIPS, PROPS, make_plan
from plan_world_doors import read, reference
from unreal_import_world_props import map_clips, read_source, verify_import_origin


def main():
    import unreal
    report_path = Path(os.environ['BULLY_UE_REPORT'])
    plan_path = Path(os.environ['REA_PROP_GESTURE_PLAN'])
    if report_path.exists():
        raise RuntimeError('New carnival gesture import report required')
    if not hasattr(unreal, 'ReaBullyPropGestureData'):
        raise RuntimeError('Compile carnival gesture data type before importing')
    plan = read(plan_path)
    if plan != make_plan():
        raise RuntimeError('Carnival gesture plan differs from freshly verified original sources')
    digest = reference(plan_path)['sha256']
    source_models = {m['model_key']: m for m in read(PROPS)['models']}
    verified = []
    # Preflight all original model/clip bindings before creating data assets.
    for model in plan['models']:
        original = source_models[model['model_key']]
        imported = read(ROOT/'local/world-import/props'/f'{model["model_key"]}.json')
        source, names, parents = read_source(original)
        mesh = unreal.load_asset(model['mesh'])
        prop = unreal.load_asset(model['prop_data'])
        clips = {role: unreal.load_asset(path) for role, path in model['clips'].items()}
        if (not isinstance(mesh, unreal.SkeletalMesh) or not isinstance(prop, unreal.ReaBullyWorldPropData) or
                any(not isinstance(clip, unreal.AnimSequence) for clip in clips.values())):
            raise RuntimeError('Original carnival mesh, prop or selected clips missing')
        skeleton = mesh.get_editor_property('skeleton')
        if (not skeleton or skeleton.get_path_name() != model['own_skeleton'] or
                prop.get_editor_property('mesh') != mesh or prop.get_editor_property('model_key') != model['model_key'] or
                prop.get_editor_property('source_sha256') != model['source_sha256'] or
                prop.get_editor_property('ambient_loop') != clips['idle'] or
                prop.get_editor_property('ambient_loop_original') != CLIPS['idle'] or
                list(unreal.ReaBullyWorldPropTools.get_mesh_bone_names(mesh)) != names or
                list(unreal.ReaBullyWorldPropTools.get_mesh_bone_parents(mesh)) != parents):
            raise RuntimeError('Original carnival skin/skeleton or idle binding changed')
        all_sequences = [unreal.load_asset(path) for path in imported['clips'].values()]
        if any(not isinstance(clip, unreal.AnimSequence) for clip in all_sequences):
            raise RuntimeError('An existing original carnival clip is missing')
        mapped = map_clips(original, all_sequences, skeleton, source.stem)
        if any(mapped[CLIPS[role]] != clip for role, clip in clips.items()):
            raise RuntimeError('Carnival clip identity differs from source GLB')
        md5 = hashlib.md5(source.read_bytes()).hexdigest()
        origins = {row['asset']: row for row in imported['import_provenance']}
        for asset in (mesh, *clips.values()):
            if verify_import_origin(unreal, asset, source, md5) != origins.get(asset.get_path_name()):
                raise RuntimeError('Carnival source import origin changed')
        for asset in (mesh, skeleton, *clips.values()):
            if unreal.EditorAssetLibrary.get_metadata_tag(asset, 'REAOriginalPropSHA256') != model['source_sha256']:
                raise RuntimeError('Carnival source asset metadata changed')
        verified.append((model, clips))
    records = []
    for model, clips in verified:
        asset_path = model['gesture_data']
        if unreal.EditorAssetLibrary.does_asset_exist(asset_path):
            data = unreal.load_asset(asset_path)
            if (not isinstance(data, unreal.ReaBullyPropGestureData) or
                    unreal.EditorAssetLibrary.get_metadata_tag(data, 'REAGestureEvidenceSHA256') != digest):
                raise RuntimeError('Existing gesture asset belongs to another source plan')
        else:
            folder, name = asset_path.rsplit('/', 1)
            factory = unreal.DataAssetFactory()
            factory.set_editor_property('data_asset_class', unreal.ReaBullyPropGestureData)
            data = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, unreal.ReaBullyPropGestureData, factory)
        if not data:
            raise RuntimeError('Could not create carnival gesture data')
        properties = {'model_key': model['model_key'], 'source_sha256': model['source_sha256'],
            'evidence_sha256': digest, 'idle_clip': clips['idle'], 'gesture_clip': clips['gesture'],
            'idle_original': CLIPS['idle'], 'gesture_original': CLIPS['gesture'],
            'idle_pose_center_cm': unreal.Vector(*model['idle_bounds_cm']['center']),
            'idle_pose_extent_cm': unreal.Vector(*model['idle_bounds_cm']['extent'])}
        for key, value in properties.items():
            data.set_editor_property(key, value)
        unreal.EditorAssetLibrary.set_metadata_tag(data, 'REAGestureEvidenceSHA256', digest)
        if not unreal.EditorAssetLibrary.save_loaded_asset(data, only_if_is_dirty=False) or any(
                data.get_editor_property(key) != value for key, value in properties.items()):
            raise RuntimeError('Saved carnival gesture data differs from original bindings')
        records.append({'model_key': model['model_key'], 'source_sha256': model['source_sha256'],
            'prop_data': model['prop_data'], 'clips': model['clips'], 'data': data.get_path_name()})
    result = {'status': 'prop_gesture_assets_verified_runtime_pending', 'source_plan': reference(plan_path),
        'models': records, 'placements': len(plan['placements']), 'source_models_reimported': False,
        'runtime_validated': False, 'new_autoplay_clips': 0, 'limitations': plan['limitations']}
    with report_path.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({'status': result['status'], 'models': len(records), 'placements': result['placements']}))


if __name__ == '__main__':
    main()

"""Bind source-verified garage clips to an exploration interaction data asset.

No mesh/animation is reimported and no historical prop checkpoint is rewritten.
REA_PROP_INTERACTION_PLAN selects the source plan; BULLY_UE_REPORT must be new.
"""
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from plan_prop_interactions import IMPORTS, PROPS, make_plan
from plan_world_doors import read, reference
from unreal_import_world_props import map_clips, read_source, verify_import_origin


def main():
    import unreal
    report_path = Path(os.environ['BULLY_UE_REPORT'])
    plan_path = Path(os.environ.get('REA_PROP_INTERACTION_PLAN', ROOT/'evidence/prop-interactions-plan-v07.json'))
    if report_path.exists():
        raise RuntimeError('A new interaction import report is required')
    if not hasattr(unreal, 'ReaBullyPropInteractionData'):
        raise RuntimeError('Compile the prop interaction runtime before importing')
    plan = read(plan_path)
    if plan != make_plan():
        raise RuntimeError('Interaction plan differs from freshly verified original sources')
    source_digest = reference(plan_path)['sha256']
    source_model = next(m for m in read(PROPS)['models'] if m['model_key'] == '10121_bikegar')
    imported = read(IMPORTS)
    model = plan['model']
    source, names, parents = read_source(source_model)
    mesh = unreal.load_asset(model['mesh'])
    clips = {action: unreal.load_asset(path) for action, path in model['clips'].items()}
    prop_data = unreal.load_asset(model['prop_data'])
    if (not isinstance(mesh, unreal.SkeletalMesh) or
            not isinstance(prop_data, unreal.ReaBullyWorldPropData) or
            any(not isinstance(c, unreal.AnimSequence) for c in clips.values())):
        raise RuntimeError('An original prop or animation asset is unavailable')
    skeleton = mesh.get_editor_property('skeleton')
    if (not skeleton or skeleton.get_path_name() != model['own_skeleton'] or
            prop_data.get_editor_property('mesh') != mesh or
            prop_data.get_editor_property('source_sha256') != model['source_sha256'] or
            prop_data.get_editor_property('model_key') != model['model_key'] or
            prop_data.get_editor_property('ambient_loop') is not None or
            any(c.get_editor_property('skeleton') != skeleton for c in clips.values()) or
            list(unreal.ReaBullyWorldPropTools.get_mesh_bone_names(mesh)) != names or
            list(unreal.ReaBullyWorldPropTools.get_mesh_bone_parents(mesh)) != parents):
        raise RuntimeError('Original garage mesh, skeleton or base prop data changed')
    exact_clips = map_clips(source_model, list(clips.values()), skeleton, source.stem)
    if any(exact_clips[model['original_clips'][action]] != clip for action, clip in clips.items()):
        raise RuntimeError('Garage clip direction differs from the original GLB animation identity')
    digest_md5 = hashlib.md5(source.read_bytes()).hexdigest()
    expected_origins = {x['asset']: x for x in imported['import_provenance']}
    for asset in (mesh, *clips.values()):
        if verify_import_origin(unreal, asset, source, digest_md5) != expected_origins.get(asset.get_path_name()):
            raise RuntimeError('Original garage import provenance changed')
    for asset in (mesh, skeleton, *clips.values()):
        if unreal.EditorAssetLibrary.get_metadata_tag(asset, 'REAOriginalPropSHA256') != model['source_sha256']:
            raise RuntimeError('Original garage source metadata changed')
    asset_folder = '/Game/World/Interactions/10121_bikegar'
    asset_path = asset_folder + '/DA_GarageInteraction'
    if unreal.EditorAssetLibrary.does_asset_exist(asset_path):
        data = unreal.load_asset(asset_path)
        if (not isinstance(data, unreal.ReaBullyPropInteractionData) or
                unreal.EditorAssetLibrary.get_metadata_tag(data, 'REAInteractionEvidenceSHA256') != source_digest):
            raise RuntimeError('Existing interaction data has different source evidence')
    else:
        factory = unreal.DataAssetFactory()
        factory.set_editor_property('data_asset_class', unreal.ReaBullyPropInteractionData)
        data = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            'DA_GarageInteraction', asset_folder, unreal.ReaBullyPropInteractionData, factory)
    if not data:
        raise RuntimeError('Could not create original garage interaction data')
    properties = {'model_key': model['model_key'], 'source_sha256': model['source_sha256'],
        'evidence_sha256': source_digest, 'closed_clip': clips['IDLE'], 'open_clip': clips['OPEN'],
        'close_clip': clips['CLOSE'], 'closed_original': model['original_clips']['IDLE'],
        'open_original': model['original_clips']['OPEN'], 'close_original': model['original_clips']['CLOSE'],
        'closed_pose_center_cm': unreal.Vector(*model['closed_bounds_cm']['center']),
        'closed_pose_extent_cm': unreal.Vector(*model['closed_bounds_cm']['extent'])}
    for key, value in properties.items():
        data.set_editor_property(key, value)
    unreal.EditorAssetLibrary.set_metadata_tag(data, 'REAInteractionEvidenceSHA256', source_digest)
    if not unreal.EditorAssetLibrary.save_loaded_asset(data, only_if_is_dirty=False):
        raise RuntimeError('Could not save garage interaction data')
    if any(data.get_editor_property(k) != v for k, v in properties.items()):
        raise RuntimeError('Saved interaction properties differ')
    report = {'status': 'prop_interaction_assets_verified_runtime_pending',
        'source_plan': reference(plan_path), 'data': data.get_path_name(),
        'model_key': model['model_key'], 'source_sha256': model['source_sha256'],
        'clips': model['clips'], 'prop_data': prop_data.get_path_name(), 'source_model_reimported': False,
        'runtime_validated': False, 'placements': len(plan['placements']), 'limitations': plan['limitations']}
    with report_path.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps({'status': report['status'], 'placements': report['placements']}))


if __name__ == '__main__':
    main()

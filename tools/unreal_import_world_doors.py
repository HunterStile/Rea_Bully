"""Import verified door candidates and bind their own IDLE/OPEN/CLOSE clips in UE.

Set BULLY_UE_REPORT to a new report, optionally REA_WORLD_DOORS_KEYS to exact
comma-separated model keys. Existing verified prop assets are reused. This
script creates no world actors and never starts gameplay or mission logic.
REA_WORLD_DOORS_BOUNDS_ONLY=1 upgrades existing door data only, preserving
mesh/clip assets and all original door import checkpoints.
"""
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from plan_world_doors import load_models, action_clips, reference
from unreal_import_world_props import main as import_props, read_source, verify_import_origin
from validate_door_closed_bounds import closed_bounds


def main():
    import unreal
    report_path = Path(os.environ['BULLY_UE_REPORT'])
    prop_report_path = report_path.with_name(report_path.stem+'-assets.json')
    bounds_only = os.environ.get('REA_WORLD_DOORS_BOUNDS_ONLY') == '1'
    if report_path.exists() or prop_report_path.exists():
        raise RuntimeError('New door import report paths required')
    if not hasattr(unreal, 'ReaBullyWorldDoorData'):
        raise RuntimeError('Compile ReaBullyWorldDoor before importing door data')
    catalog = load_models()
    selected = set(filter(None, os.environ.get('REA_WORLD_DOORS_KEYS', '').split(',')))
    models = [m for m in catalog['models'] if not selected or m['model_key'] in selected]
    if not models or (selected and selected != {m['model_key'] for m in models}):
        raise RuntimeError('Requested door model is not in source catalog')
    if not bounds_only:
        previous = os.environ.get('REA_WORLD_PROPS_KEYS')
        try:
            os.environ['BULLY_UE_REPORT'] = str(prop_report_path)
            os.environ['REA_WORLD_PROPS_KEYS'] = ','.join(m['model_key'] for m in models)
            import_props(door_models=True)
        finally:
            os.environ['BULLY_UE_REPORT'] = str(report_path)
            if previous is None:
                os.environ.pop('REA_WORLD_PROPS_KEYS', None)
            else:
                os.environ['REA_WORLD_PROPS_KEYS'] = previous
    records = []
    folder = ROOT/'local/world-import/doors'
    folder.mkdir(parents=True, exist_ok=True)
    bounds_folder = ROOT/'local/world-import/door-bounds'
    bounds_folder.mkdir(parents=True, exist_ok=True)
    for model in models:
        key = model['model_key']
        checkpoint_path = ROOT/'local/world-import/props'/(key+'.json')
        imported = json.loads(checkpoint_path.read_text(encoding='utf-8'))
        if (imported['status'] != 'prop_assets_verified' or imported['source_sha256'] != model['output_sha256']
                or imported['model_key'] != key):
            raise RuntimeError('Door prop import checkpoint mismatch')
        names = action_clips(model)
        measurement = closed_bounds(model)
        if measurement['original_clip'] != names['IDLE']:
            raise RuntimeError('Closed bounds belong to a different source pose')
        mesh = unreal.load_asset(imported['mesh'])
        clips = {name: unreal.load_asset(imported['clips'][original]) for name, original in names.items()}
        if not isinstance(mesh, unreal.SkeletalMesh) or any(not isinstance(c, unreal.AnimSequence) for c in clips.values()):
            raise RuntimeError('Missing imported original door assets')
        skeleton = mesh.get_editor_property('skeleton')
        if any(c.get_editor_property('skeleton') != skeleton for c in clips.values()):
            raise RuntimeError('Door animation uses another model skeleton')
        if bounds_only:
            source, bone_names, bone_parents = read_source(model)
            if (not skeleton or skeleton.get_path_name() != imported['own_skeleton'] or
                    list(unreal.ReaBullyWorldPropTools.get_mesh_bone_names(mesh)) != bone_names or
                    list(unreal.ReaBullyWorldPropTools.get_mesh_bone_parents(mesh)) != bone_parents):
                raise RuntimeError('Saved door hierarchy differs from the verified source')
            source_md5 = hashlib.md5(source.read_bytes()).hexdigest()
            prior_provenance = {p['asset']: p for p in imported['import_provenance']}
            for asset in [mesh, *clips.values()]:
                if verify_import_origin(unreal, asset, source, source_md5) != prior_provenance.get(asset.get_path_name()):
                    raise RuntimeError('Saved door mesh/clip import provenance changed')
            for asset in [mesh, skeleton, *clips.values()]:
                if unreal.EditorAssetLibrary.get_metadata_tag(asset, 'REAOriginalPropSHA256') != model['output_sha256']:
                    raise RuntimeError('Saved door source metadata changed')
        asset_folder = '/Game/World/Doors/Data/' + key
        asset_path = asset_folder + '/DA_OriginalDoor'
        if bounds_only and not unreal.EditorAssetLibrary.does_asset_exist(asset_path):
            raise RuntimeError('Bounds-only upgrade requires the existing verified door data')
        if unreal.EditorAssetLibrary.does_asset_exist(asset_path):
            data = unreal.load_asset(asset_path)
            if (not isinstance(data, unreal.ReaBullyWorldDoorData) or
                    unreal.EditorAssetLibrary.get_metadata_tag(data, 'READoorSourceSHA256') != model['output_sha256']):
                raise RuntimeError('Existing door data differs from original source')
        else:
            factory = unreal.DataAssetFactory()
            factory.set_editor_property('data_asset_class', unreal.ReaBullyWorldDoorData)
            data = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
                'DA_OriginalDoor', asset_folder, unreal.ReaBullyWorldDoorData, factory)
        if not data:
            raise RuntimeError('Could not create original door data')
        record = {'status': 'door_assets_verified', 'model_key': key, 'source_sha256': model['output_sha256'],
            'data': data.get_path_name(), 'mesh': mesh.get_path_name(), 'own_skeleton': skeleton.get_path_name(),
            'original_clips': names, 'clips': {n: c.get_path_name() for n, c in clips.items()},
            'prop_checkpoint': reference(checkpoint_path), 'source_validation': model['door_validation'],
            'material_policy': model.get('material_policy', 'exported_original_materials'),
            'shading_pending': model.get('shading_pending', False), 'visual_validation': 'pending_engine_pose_review'}
        destination = folder/(key+'.json')
        if bounds_only and not destination.exists():
            raise RuntimeError('Bounds-only upgrade requires the original door checkpoint')
        if destination.exists() and json.loads(destination.read_text(encoding='utf-8')) != record:
            raise RuntimeError('Saved door checkpoint differs from verified assets')
        previous_bounds_hash = unreal.EditorAssetLibrary.get_metadata_tag(data, 'READoorClosedPoseSourceSHA256')
        if previous_bounds_hash and previous_bounds_hash != model['output_sha256']:
            raise RuntimeError('Existing closed-pose bounds differ from original source')
        bounds_record = {'status': 'door_closed_pose_bounds_verified', 'model_key': key,
            'data': data.get_path_name(), 'source_sha256': model['output_sha256'],
            'measurement': measurement, 'source_validation': model['door_validation'],
            'policy': 'Reconstructed closed AABB from original IDLE(0); minimum runtime half-extent 2cm.',
            'runtime_interaction_validation': 'pending'}
        bounds_destination = bounds_folder/(key+'.json')
        if (bounds_destination.exists() and
                json.loads(bounds_destination.read_text(encoding='utf-8')) != bounds_record):
            raise RuntimeError('Existing closed-pose bounds checkpoint differs')
        original_values = {'model_key': key, 'source_sha256': model['output_sha256'], 'mesh': mesh,
                'open_clip': clips['OPEN'], 'close_clip': clips['CLOSE'], 'closed_clip': clips['IDLE'],
                'open_original': names['OPEN'], 'close_original': names['CLOSE'], 'closed_original': names['IDLE']}
        for name, value in original_values.items():
            if bounds_only:
                if data.get_editor_property(name) != value:
                    raise RuntimeError('Existing door data field changed: '+name)
            else:
                data.set_editor_property(name, value)
        for name, value in {
                'closed_pose_center_cm': unreal.Vector(*measurement['closed_bounds_cm']['center']),
                'closed_pose_extent_cm': unreal.Vector(*measurement['closed_bounds_cm']['extent']),
                'closed_pose_source_sha256': model['output_sha256'], 'closed_pose_original_clip': names['IDLE']}.items():
            data.set_editor_property(name, value)
        unreal.EditorAssetLibrary.set_metadata_tag(data, 'READoorSourceSHA256', model['output_sha256'])
        unreal.EditorAssetLibrary.set_metadata_tag(data, 'READoorClosedPoseSourceSHA256', model['output_sha256'])
        unreal.EditorAssetLibrary.set_metadata_tag(data, 'READoorClosedPoseOriginalClip', names['IDLE'])
        for name, expected in [('closed_pose_center_cm', measurement['closed_bounds_cm']['center']),
                               ('closed_pose_extent_cm', measurement['closed_bounds_cm']['extent'])]:
            value = data.get_editor_property(name)
            if max(abs(a-b) for a, b in zip((value.x, value.y, value.z), expected)) > .0001:
                raise RuntimeError('UE door bounds do not match the original closed skin')
        if not unreal.EditorAssetLibrary.save_loaded_asset(data, only_if_is_dirty=False):
            raise RuntimeError('Could not save source-bound door data')
        if not destination.exists():
            with destination.open('x', encoding='utf-8') as stream:
                json.dump(record, stream, indent=2)
        if not bounds_destination.exists():
            with bounds_destination.open('x', encoding='utf-8') as stream:
                json.dump(bounds_record, stream, indent=2)
        # Original door checkpoint schema/content is deliberately unchanged.
        record = {**record, 'closed_pose_bounds_checkpoint': reference(bounds_destination)}
        records.append(record)
        unreal.log('REA_WORLD_DOOR_VERIFIED ' + key)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open('x', encoding='utf-8') as stream:
        json.dump({'status': 'door_assets_verified', 'models': records, 'source_catalogs': catalog['source_catalogs'],
            'bounds_only': bounds_only,
            'prop_import_report': reference(prop_report_path) if not bounds_only else None,
            'engine': unreal.SystemLibrary.get_engine_version(),
            'limitations': ['Import identity, hierarchy and clip verification are not visual gameplay validation.',
                'DAT placements and reconstructed interaction are applied separately by the world builder.']}, stream, indent=2)


if __name__ == '__main__':
    main()

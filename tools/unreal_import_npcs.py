"""Import verified original ped skins and their CAT-linked locomotion into independent assets."""
import hashlib
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def map_roles(npc, sequences, skeleton, source_stem):
    """Map only exact importer names belonging to this source, never arbitrary suffixes."""
    roles = {}
    for clip in npc['clips']:
        name = clip['glb_animation_name']
        # Interchange 5.8 concatenates the source mesh and animation display names.
        # Also accept the two exact standalone/separated variants used by importers.
        expected_names = {name, source_stem + name, source_stem + '_' + name}
        matching = [a for a in sequences if a.get_name() in expected_names]
        if len(matching) != 1 or matching[0].get_editor_property('skeleton') != skeleton:
            raise RuntimeError(f'Original NPC animation mapping/skeleton is ambiguous: {source_stem}/{name}; '
                               f'matched {[a.get_name() for a in matching]}')
        sequence = matching[0]
        duration = sequence.get_editor_property('sequence_length')
        if not math.isfinite(duration) or abs(duration - clip['duration']) > .034:
            raise RuntimeError('Original NPC animation duration changed')
        if clip['role'] in roles or any(pair[0] is sequence for pair in roles.values()):
            raise RuntimeError('Original NPC animation role or sequence is duplicated')
        roles[clip['role']] = (sequence, clip['source_speed_cm_per_second'])
    if len(roles) != len(sequences):
        raise RuntimeError('Unmapped NPC animations found; preserve the existing import')
    return roles


def verify_source_record(source, md5, resolved_filenames, registry_json):
    """Require the original saved import digest, not merely today's file at that path."""
    filenames = [Path(str(filename)).resolve() for filename in resolved_filenames]
    records = json.loads(registry_json)
    if filenames != [source.resolve()] or not isinstance(records, list) or len(records) != 1:
        raise RuntimeError('NPC asset has an unexpected import source')
    if records[0].get('FileMD5', '').lower() != md5.lower():
        raise RuntimeError('NPC asset was imported from different or unrecorded source bytes')
    return {'path': str(filenames[0]), 'imported_md5': records[0]['FileMD5'].lower()}


def verify_import_origin(unreal, asset, source, md5):
    import_data = asset.get_editor_property('asset_import_data')
    if not import_data:
        raise RuntimeError(f'NPC import provenance is missing: {asset.get_path_name()}')
    tags = {str(key): value for key, value in unreal.EditorAssetLibrary.get_tag_values(asset.get_path_name()).items()}
    # UObject::SourceFileTagName() is "AssetImportData" in the installed engine.
    record = verify_source_record(source, md5, import_data.extract_filenames(), tags.get('AssetImportData', '[]'))
    return {'asset': asset.get_path_name(), **record}


def verify_generated_asset(unreal, asset, expected_type, source_sha256):
    if asset and (not isinstance(asset, expected_type) or
                  unreal.EditorAssetLibrary.get_metadata_tag(asset, 'REAOriginalNPCSHA256') != source_sha256):
        raise RuntimeError(f'Preserve unrelated derived NPC asset: {asset.get_path_name()}')


def main():
    import unreal
    sys.path.insert(0, str(ROOT/'tools'))
    from prepare_npc_runtime_manifest import half_height, load_runtime_manifest

    manifest_path = Path(os.environ.get('BULLY_NPC_MANIFEST', str(ROOT/'evidence/npc-import-manifest-v04.json')))
    if not manifest_path.is_absolute():
        manifest_path = ROOT/manifest_path
    # Complete promotion/source preflight before any engine asset mutation.
    manifest = load_runtime_manifest(manifest_path)
    report = Path(os.environ['BULLY_UE_REPORT'])
    if report.exists():
        raise RuntimeError('New evidence path required')
    records = []
    for npc in manifest['npcs']:
        source = ROOT/npc['source_glb']['path']
        source_bytes = source.read_bytes()
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        source_md5 = hashlib.md5(source_bytes).hexdigest()
        if source_sha256 != npc['source_glb']['sha256']:
            raise RuntimeError('Verified original NPC GLB changed')
        folder = f'/Game/Original/NPCs/{npc["ped_id"]:03d}_{npc["model"]}'
        existing = list(unreal.EditorAssetLibrary.list_assets(folder, recursive=True, include_folder=False))
        if not existing:
            task = unreal.AssetImportTask()
            task.filename = str(source)
            task.destination_path = folder
            task.automated = True
            task.replace_existing = False
            task.save = True
            unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
        unreal.AutomationLibrary.finish_loading_before_screenshot()
        assets = [unreal.load_asset(path) for path in unreal.EditorAssetLibrary.list_assets(folder, recursive=True, include_folder=False)]
        if any(asset is None for asset in assets):
            raise RuntimeError('Existing NPC folder contains unreadable assets')
        meshes = [asset for asset in assets if isinstance(asset, unreal.SkeletalMesh)]
        skeletons = [asset for asset in assets if isinstance(asset, unreal.Skeleton)]
        sequences = [asset for asset in assets if isinstance(asset, unreal.AnimSequence)]
        if len(meshes) != 1 or len(skeletons) != 1 or len(sequences) != len(npc['clips']):
            raise RuntimeError('Original NPC mesh/clip count changed')
        mesh = meshes[0]
        skeleton = mesh.get_editor_property('skeleton')
        if mesh.get_name() != source.stem or skeleton != skeletons[0] or skeleton.get_name() != source.stem+'_Skeleton':
            raise RuntimeError('NPC does not use its own imported mesh and skeleton')
        mesh_tags = {str(key): value for key, value in unreal.EditorAssetLibrary.get_tag_values(mesh.get_path_name()).items()}
        bone_count = int(mesh_tags.get('Bones', '-1'))
        if bone_count != npc['bone_count']:
            raise RuntimeError(f'NPC bone count changed: {bone_count} instead of {npc["bone_count"]}')
        provenance = [verify_import_origin(unreal, asset, source, source_md5) for asset in [mesh, *sequences]]
        roles = map_roles(npc, sequences, skeleton, source.stem)
        # Only these two project-generated assets may be changed during a resume.
        generated_paths = {folder+'/BS_Locomotion': unreal.BlendSpace1D,
                           folder+'/DA_Ambient': unreal.ReaBullyAmbientPedData}
        generated = {}
        for path, asset_type in generated_paths.items():
            asset = unreal.load_asset(path) if unreal.EditorAssetLibrary.does_asset_exist(path) else None
            verify_generated_asset(unreal, asset, asset_type, source_sha256)
            generated[path] = asset
        source_prefix = folder+'/'+source.stem+'/'
        imported_types = (unreal.SkeletalMesh, unreal.Skeleton, unreal.AnimSequence,
                          unreal.PhysicsAsset, unreal.Material, unreal.MaterialInstanceConstant, unreal.Texture2D)
        for asset in assets:
            package = asset.get_path_name().split('.')[0]
            if package not in generated_paths and (not package.startswith(source_prefix) or not isinstance(asset, imported_types)):
                raise RuntimeError(f'Unrelated asset in NPC folder: {package}')
        ordered = sorted((roles[role] for role in ('idle', 'walk', 'run', 'sprint')), key=lambda pair: pair[1])
        locomotion = unreal.ReaBullyCharacterTools.create_recovered_locomotion(folder+'/BS_Locomotion',
            [pair[0] for pair in ordered], [pair[1] for pair in ordered])
        if not locomotion:
            raise RuntimeError('Could not create NPC locomotion from its own four clips')
        data = generated[folder+'/DA_Ambient']
        if data is None:
            factory = unreal.DataAssetFactory()
            factory.set_editor_property('data_asset_class', unreal.ReaBullyAmbientPedData)
            data = unreal.AssetToolsHelpers.get_asset_tools().create_asset('DA_Ambient', folder, unreal.ReaBullyAmbientPedData, factory)
        if not data:
            raise RuntimeError('Could not create NPC ambient data')
        for key, value in {'ped_id': npc['ped_id'], 'mesh': mesh, 'locomotion': locomotion,
            'ambient_idle': roles['ambient_idle'][0], 'walk_speed': roles['walk'][1],
            'half_height': half_height(npc)}.items():
            data.set_editor_property(key, value)
        for asset in (locomotion, data):
            unreal.EditorAssetLibrary.set_metadata_tag(asset, 'REAOriginalNPCSHA256', source_sha256)
            if not unreal.EditorAssetLibrary.save_loaded_asset(asset, only_if_is_dirty=False):
                raise RuntimeError(f'Could not save NPC asset: {asset.get_path_name()}')
        records.append({'ped_id': npc['ped_id'], 'data': data.get_path_name(), 'mesh': mesh.get_path_name(),
            'own_skeleton': skeleton.get_path_name(), 'source_sha256': source_sha256,
            'bone_count': bone_count,
            'import_mode': 'resumed_existing_verified_source' if existing else 'new_import',
            'import_provenance': provenance,
            'half_height': half_height(npc), 'clips': {role: pair[0].get_path_name() for role, pair in roles.items()}})
    report.write_text(json.dumps({'status': 'npc_assets_verified', 'npcs': records,
        'source_manifest': {'path': str(manifest_path.resolve()), 'sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest()},
        'engine': unreal.SystemLibrary.get_engine_version()}, indent=2), encoding='utf-8')


if __name__ == "__main__":
    main()

"""Editor-only import of original Jimmy geometry, skin and recovered animation tracks."""
import hashlib
import json
import os
from pathlib import Path
import unreal

source = Path(os.environ['BULLY_GLB']).resolve(strict=True)
report_path = Path(os.environ['BULLY_UE_REPORT'])
folder = os.environ['BULLY_CHARACTER_FOLDER']
install_actions = os.environ.get('BULLY_CHARACTER_ACTIONS') == '1'
if report_path.exists():
    raise RuntimeError('Choose a new evidence report')
if folder in ('/Game/Original/Jimmy', '/Game/Original/JimmyActions') and unreal.EditorAssetLibrary.does_asset_exist(folder + '/SK_Jimmy'):
    raise RuntimeError('Verified runtime Jimmy already installed. Reuse it; import experiments into a new --folder to preserve existing references.')
task = unreal.AssetImportTask()
task.filename = str(source)
task.destination_path = folder
task.automated = True
task.replace_existing = True
task.save = True
unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
unreal.AutomationLibrary.finish_loading_before_screenshot()
assets = []
meshes = []
clips = {}
for path in unreal.EditorAssetLibrary.list_assets(folder, recursive=True, include_folder=False):
    obj = unreal.load_asset(path)
    record = {'path': str(path), 'class': obj.get_class().get_name()}
    if isinstance(obj, unreal.SkeletalMesh):
        meshes.append(obj)
        record['skeleton'] = str(obj.get_editor_property('skeleton').get_path_name())
        record['bounds'] = str(obj.get_bounds())
        record['materials'] = [str(x.material_interface.get_path_name()) for x in obj.materials]
    if isinstance(obj, unreal.AnimSequence):
        record['duration_seconds'] = obj.get_editor_property('sequence_length')
        record['skeleton'] = obj.get_editor_property('skeleton').get_path_name()
        clips[obj.get_name()] = obj
    assets.append(record)
character = None
locomotion = None
action_set = None
if folder in ('/Game/Original/Jimmy', '/Game/Original/JimmyActions'):
    expected_count = 10 if install_actions else 4
    if len(meshes) != 1 or len(clips) != expected_count:
        raise RuntimeError(f'Expected single original Jimmy and {expected_count} clips; got {len(meshes)} meshes, {list(clips)}')
    mesh = meshes[0]
    bounds = mesh.get_bounds()
    min_z = bounds.origin.z - bounds.box_extent.z
    max_z = bounds.origin.z + bounds.box_extent.z
    # Fail on a translated/incorrect bind pose, rather than concealing it at runtime.
    if abs(min_z) > 2 or not 160 < max_z < 180:
        raise RuntimeError(f'Original Jimmy stature/ground origin changed: minZ={min_z}, maxZ={max_z}')
    target = folder + '/SK_Jimmy'
    if mesh.get_path_name().split('.')[0] != target:
        if not unreal.EditorAssetLibrary.rename_loaded_asset(mesh, target):
            raise RuntimeError('Could not name the verified skeletal mesh')
    character = mesh.get_path_name()
    unreal.EditorAssetLibrary.set_metadata_tag(mesh, 'ReaBullySourceSHA256', hashlib.sha256(source.read_bytes()).hexdigest())
    def exact_clip(suffix):
        matching = [value for key, value in clips.items() if key.endswith('_' + suffix)]
        if len(matching) != 1:
            raise RuntimeError(f'Original clip {suffix} missing or ambiguous')
        if matching[0].get_editor_property('skeleton') != mesh.get_editor_property('skeleton'):
            raise RuntimeError(f'Clip {suffix} has a different skeleton')
        return matching[0]
    ordered = [exact_clip(suffix) for suffix in ('IDLE', 'PLAYERWALK', 'PLAYERRUN', 'RUNFAST')]
    locomotion = unreal.ReaBullyCharacterTools.create_recovered_locomotion(
        folder + '/BS_Locomotion', ordered, [0., 204.625, 361.375, 801.2])
    if not locomotion or not unreal.EditorAssetLibrary.save_loaded_asset(locomotion, only_if_is_dirty=False):
        raise RuntimeError('Could not build and save native locomotion blend space')
    if install_actions:
        factory = unreal.DataAssetFactory()
        factory.set_editor_property('data_asset_class', unreal.ReaBullyPlayerActions)
        data = unreal.AssetToolsHelpers.get_asset_tools().create_asset('DA_PlayerActions', folder,
            unreal.ReaBullyPlayerActions, factory)
        if not data:
            raise RuntimeError('Could not create recovered action set')
        data.set_editor_property('mesh', mesh)
        data.set_editor_property('locomotion', locomotion)
        # JOG segments/rates are decoded CAT facts. Standing air boundaries are
        # measured ARROW zero crossings, a reconstruction choice rather than CAT events.
        for name, suffix, spec in [
            ('standing', 'IDLEJUMP', [0.1333333424, 0.5662100842, 0.5662100842, 0.9333333969, 1.0]),
            ('jog_left', 'JOGJUMP_L', [0.1333333333, 0.5333333333, 0.5666666667, 0.7666666667, 1.0]),
            ('jog_right', 'JOGJUMP_R', [0.1333333333, 0.5333333333, 0.5666666667, 0.7666666667, 1.0]),
        ]:
            profile = unreal.ReaBullyJumpProfile()
            profile.set_editor_property('clip', exact_clip(suffix))
            for field, value in zip(('windup_end', 'flight_end', 'landing_start', 'landing_end', 'ground_play_rate'), spec):
                profile.set_editor_property(field, value)
            data.set_editor_property(name, profile)
        for field, suffix in [('fall', 'FALL'), ('fall_flail', 'FALL_FLAIL'), ('fall_land', 'FALL_LAND')]:
            data.set_editor_property(field, exact_clip(suffix))
        data.set_editor_property('blend_seconds', 0.066667)
        data.set_editor_property('sprint_ground_play_rate', 1.25)
        data.set_editor_property('sprint_landing_end', 0.7)
        unreal.EditorAssetLibrary.set_metadata_tag(data, 'ReaBullySourceSHA256', hashlib.sha256(source.read_bytes()).hexdigest())
        if not unreal.EditorAssetLibrary.save_loaded_asset(data, only_if_is_dirty=False):
            raise RuntimeError('Could not save recovered action set')
        action_set = data.get_path_name()
    locomotion = locomotion.get_path_name()
unreal.EditorAssetLibrary.save_directory(folder, only_if_is_dirty=False, recursive=True)
report_path.parent.mkdir(parents=True, exist_ok=True)
report_path.write_text(json.dumps({'status': 'imported_and_saved',
    'engine': unreal.SystemLibrary.get_engine_version(), 'source': str(source),
    'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'assets': assets,
    'character': character, 'locomotion': locomotion, 'action_set': action_set}, indent=2), encoding='utf-8')

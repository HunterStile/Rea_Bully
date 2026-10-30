"""Save the persistent scene after its independently built zone levels are ready."""
import unreal


def save_scene(manifest, ambient_plan, audio_report, plan_sha256):
    level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    data_path = '/Game/World/DA_World'
    if unreal.EditorAssetLibrary.does_asset_exist(data_path):
        data = unreal.load_asset(data_path)
    else:
        factory = unreal.DataAssetFactory()
        factory.set_editor_property('data_asset_class', unreal.ReaBullyWorldData)
        data = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            'DA_World', '/Game/World', unreal.ReaBullyWorldData, factory)
    data.set_editor_property('zones', manifest)
    start = None
    if ambient_plan:
        first = next(p for p in ambient_plan['placements'] if p['ped_id'] == 9)
        start = unreal.Vector(*first['position_cm']) + unreal.Vector(250, 0, 120)
        data.set_editor_property('start', start)
    unreal.EditorAssetLibrary.set_metadata_tag(data, 'REAWorldPlanSHA256', plan_sha256)
    if not unreal.EditorAssetLibrary.save_loaded_asset(data, only_if_is_dirty=False):
        raise RuntimeError('Failed saving original world manifest')

    # Retain the verified lighting and game mode; zone geometry is streamed.
    if not level.load_level('/Game/Maps/SchoolExploration'):
        raise RuntimeError('Verified lighting and player setup missing')
    unreal.AutomationLibrary.finish_loading_before_screenshot()
    for actor in actors.get_all_level_actors():
        if isinstance(actor, unreal.StaticMeshActor):
            actors.destroy_actor(actor)
        elif start is not None and isinstance(actor, unreal.PlayerStart):
            actor.set_actor_location(start, False, True)
            actor.set_actor_rotation(unreal.Rotator(pitch=0, yaw=210, roll=0), False)
    director = actors.spawn_actor_from_class(unreal.ReaBullyWorldDirector, unreal.Vector())
    director.set_editor_property('data', data)
    director.set_actor_label('REA_OriginalWorld')
    if audio_report:
        if audio_report.get('status') != 'audio_assets_imported_pcm_verified_runtime_pending' or audio_report.get('waves') != 207:
            raise RuntimeError('Verified imported audio report required')
        audio_data = unreal.load_asset(audio_report['data'])
        if not audio_data or len(audio_data.get_editor_property('waves')) != 207:
            raise RuntimeError('Verified recovered audio data missing')
        audio = actors.spawn_actor_from_class(unreal.ReaBullyWorldAudio, unreal.Vector())
        audio.set_editor_property('data', audio_data)
        audio.set_actor_label('REA_RecoveredWorldAudio')
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    if not unreal.EditorLoadingAndSavingUtils.save_map(world, '/Game/Maps/WorldExploration'):
        raise RuntimeError('Failed saving world exploration map')
    return 'Exploration start 2.5m from original Cornelius hangout' if start else 'previous_verified_start'

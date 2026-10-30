"""Create the area menu only from a tour-verified manifest for the current scene.

REA_AREA_NAVIGATION_PLAN selects the promoted manifest. BULLY_UE_REPORT must be new.
The separate navigation asset does not resave or alter any toured map.
"""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from area_navigation_contracts import build_manifest, reference, snapshot_scene


def main():
    import unreal
    plan_path = Path(os.environ['REA_AREA_NAVIGATION_PLAN'])
    report_path = Path(os.environ['BULLY_UE_REPORT'])
    if report_path.exists():
        raise ValueError('A new area navigation import report is required')
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    sources = plan['sources']
    rebuilt = build_manifest(sources['tour_plan']['path'], sources['tour_report']['path'], sources['scope']['path'])
    if rebuilt != plan:
        raise ValueError('Navigation manifest differs from current scene and verified traversal evidence')
    if not hasattr(unreal, 'ReaBullyAreaNavigationData'):
        raise ValueError('Compile the area navigation runtime before importing')
    folder, name = '/Game/World/Navigation', 'DA_AreaNavigation'
    asset_path = folder + '/' + name
    if unreal.EditorAssetLibrary.does_asset_exist(asset_path):
        data = unreal.load_asset(asset_path)
        if not isinstance(data, unreal.ReaBullyAreaNavigationData):
            raise ValueError('Unexpected asset class at the navigation destination')
    else:
        factory = unreal.DataAssetFactory()
        factory.set_editor_property('data_asset_class', unreal.ReaBullyAreaNavigationData)
        data = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, unreal.ReaBullyAreaNavigationData, factory)
    if not data:
        raise RuntimeError('Could not create area navigation data')
    entries = []
    for entry in plan['entries']:
        item = unreal.ReaBullyAreaEntry()
        for key, value in {'source_id': entry['id'], 'area': unreal.Name(entry['area']),
                           'display_name': entry['display_name'],
                           'position_feet': unreal.Vector(*entry['position_feet_cm']),
                           'yaw_degrees': entry['yaw_degrees']}.items():
            item.set_editor_property(key, value)
        entries.append(item)
    digest = reference(plan_path)['sha256']
    data.set_editor_property('entries', entries)
    data.set_editor_property('runtime_sha256', plan['runtime_sha256'])
    data.set_editor_property('evidence_sha256', digest)
    data.set_editor_property('scene_asset_hashes', plan['scene_asset_hashes'])
    unreal.EditorAssetLibrary.set_metadata_tag(data, 'REAAreaNavigationEvidenceSHA256', digest)
    if not unreal.EditorAssetLibrary.save_loaded_asset(data, only_if_is_dirty=False):
        raise RuntimeError('Could not save area navigation data')
    if (data.get_editor_property('runtime_sha256') != plan['runtime_sha256'] or
            data.get_editor_property('evidence_sha256') != digest or
            dict(data.get_editor_property('scene_asset_hashes')) != plan['scene_asset_hashes'] or
            len(data.get_editor_property('entries')) != len(entries)):
        raise RuntimeError('Saved navigation properties differ from the verified manifest')
    for actual, expected in zip(data.get_editor_property('entries'), plan['entries']):
        position = actual.get_editor_property('position_feet')
        if (str(actual.get_editor_property('area')) != expected['area'] or
                actual.get_editor_property('source_id') != expected['id'] or
                actual.get_editor_property('display_name') != expected['display_name'] or
                any(abs(a-b) > .001 for a, b in zip((position.x, position.y, position.z), expected['position_feet_cm'])) or
                abs(actual.get_editor_property('yaw_degrees') - expected['yaw_degrees']) > .0001):
            raise RuntimeError('Saved navigation entry differs from its original coordinate')
    if snapshot_scene(ROOT) != plan['scene_asset_hashes']:
        raise RuntimeError('World scene changed during navigation import')
    output = {'status': 'area_navigation_assets_verified_runtime_pending', 'manifest': reference(plan_path),
        'data': data.get_path_name(), 'entries': len(entries), 'pending_areas': plan['pending_areas'],
        'asset': reference(ROOT/'unreal/Content/World/Navigation/DA_AreaNavigation.uasset'),
        'runtime_sha256': plan['runtime_sha256'], 'scene_asset_hashes': plan['scene_asset_hashes'],
        'runtime_menu_tested': False}
    with report_path.open('x', encoding='utf-8') as stream:
        json.dump(output, stream, indent=2)
    print(json.dumps({'status': output['status'], 'entries': len(entries)}))


if __name__ == '__main__':
    main()

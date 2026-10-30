"""Freeze and record a fully tested local world release; never run Unreal.

snapshot: after final scene construction, before runtime checks.
record: recheck the frozen package batch and all required runtime evidence.
Navigation is the sole package allowed to appear after the successful tour.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time

from area_navigation_contracts import build_manifest, snapshot_scene
from launch_world import renderer_profile
from run_unreal_world import png_capture_info, inspect_tour_captures

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = 'unreal/Binaries/Win64/UnrealEditor-ReaBullyRuntime.dll'
MAP = 'unreal/Content/Maps/WorldExploration.umap'
NAV = 'unreal/Content/World/Navigation/DA_AreaNavigation.uasset'
PACKAGE_ROOTS = ('unreal/Content/Original', 'unreal/Content/World', 'unreal/Content/RecoveredMaterials')
PACKAGE_SUFFIXES = {'.uasset', '.umap', '.uexp', '.ubulk'}
INTEGRATED_GATES = ('world_streaming_ready', 'grounded_on_original_instances', 'original_player_skin',
    'keyboard_moves_player', 'ambient_own_skins', 'original_patrol_moves', 'reset_waits_for_original_floor',
    'expanded_original_cast', 'ambient_on_original_floor', 'original_collision_instances',
    'original_door_open_close', 'door_keyboard_input', 'door_travel_arrives_in_area', 'return_from_interior',
    'dorm_door_keyboard_input', 'dorm_travel_arrives_in_area', 'return_from_dorm', 'recovered_audio_assets',
    'footstep_playback_requested', 'ambience_component_playing')
GARAGE_GATES = ('passed', 'no_autoplay_before_input', 'f_open_completed', 'f_close_completed',
    'open_endpoint_stable', 'closed_endpoint_stable', 'unrelated_props_unchanged',
    'same_player_throughout_input_check', 'no_automatic_area_travel')
GESTURE_GATES = ('passed', 'no_gesture_autoplay', 'first_f_gesture_completed', 'second_f_gesture_completed',
    'original_idle_loop_resumed', 'holding_f_did_not_retrigger', 'unrelated_props_unchanged',
    'same_player_throughout_input_check', 'no_automatic_area_travel')
NAV_GATES = ('tab_opened_and_locked_gameplay', 'movement_look_and_jump_suppressed',
    'escape_closed_and_restored_inputs', 'game_continued_and_walk_restored',
    'reopen_and_cancel_after_travel', 'same_player_throughout_check')


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    """Bounded-memory SHA, including a per-file concurrent-write check."""
    path = Path(path)
    before = path.stat()
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(block)
    after = path.stat()
    require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
            'File changed while hashing: ' + str(path))
    return sha.hexdigest()


def local_path(root, name):
    path = (root / name).resolve()
    require(path.is_relative_to(root.resolve()), 'Release evidence or asset escapes repository')
    return path


def frozen_files(root):
    files = {local_path(root, MAP), local_path(root, 'unreal/ReaBully.uproject')}
    for relative in PACKAGE_ROOTS:
        folder = local_path(root, relative)
        require(folder.is_dir(), 'Missing package root: ' + relative)
        files.update(local_path(root, p) for p in folder.rglob('*') if p.is_file() and p.suffix.lower() in PACKAGE_SUFFIXES)
    files.update((root / 'unreal/Config').glob('*.ini'))
    files.discard(local_path(root, NAV))
    require(all(p.is_file() for p in files), 'Persistent world or project file is missing')
    return sorted(files)


def asset_hashes(root):
    files = frozen_files(root)
    hashes = {p.relative_to(root).as_posix(): digest(p) for p in files}
    require(frozen_files(root) == files, 'Package inventory changed during snapshot')
    return hashes


def create_snapshot(root=ROOT):
    root = Path(root).resolve()
    result = {'schema': 'rea-bully-release-snapshot-v1', 'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'package_roots': list(PACKAGE_ROOTS), 'navigation_excluded_until_verified_import': NAV,
        'runtime_sha256': digest(root / RUNTIME), 'world_asset_hashes': asset_hashes(root),
        'scene_asset_hashes': snapshot_scene(root)}
    require(result['runtime_sha256'] == digest(root / RUNTIME), 'Runtime changed during snapshot')
    result['completed_at_unix_ns'] = time.time_ns()
    return result


def all_true(row, gates, label):
    require(all(row.get(key) is True for key in gates), label + ' is missing a passing runtime gate')


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def verify_scene_contents(world, build, ambient, props, helpers, collisions, doors):
    """A map per area cannot stand in for the requested complete placement set."""
    def rows(values, fields):
        return Counter(json.dumps({k: row[k] for k in fields}, sort_keys=True) for row in values)
    npcs = ambient['placements']
    prop_count = sum(len(z['placements']) for z in props['zones'])
    helper_count = sum(len(z['placements']) for z in helpers['zones'])
    require(ambient.get('ready_for_world_builder') is True and len(npcs) == 31
            and len({p['ped_id'] for p in npcs}) == 31 and prop_count == 533
            and helper_count == 1 and helpers['zones'][0]['placements'][0]['model_key'] == '07816_fgrd_nd_baltosop',
            'Release requires the full 31-NPC, 533-prop and original helper plans')
    expected_totals = {'npcs': 31, 'props': 533, 'original_collision_helpers': 1,
                       'prop_interactions': 2, 'prop_gestures': 6,
                       'original_collision_instances': helper_count + sum(len(z['placements']) for z in collisions['zones']),
                       'doors': sum(len(z['placements']) for z in doors['zones'])}
    require(expected_totals['original_collision_instances'] > helper_count and expected_totals['doors'] > 0,
            'Complete original collision and door plans are required')
    require(all(build['integration'].get(k) == v for k, v in expected_totals.items()),
            'Scene integration counts differ from the complete placement plans')
    built = {z['zone']: z for z in build['zones']}
    prop_zones = {z['name']: z['placements'] for z in props['zones']}
    helper_zones = {z['name']: z['placements'] for z in helpers['zones']}
    collision_zones = {z['name']: z['placements'] for z in collisions['zones']}
    door_zones = {z['name']: z['placements'] for z in doors['zones']}
    for zone in world['zones']:
        name, record = zone['name'], built[zone['name']]
        require(record.get('visible_placements') == len(zone['placements'])
                and record.get('source_placements') == zone['source_count'], 'Static placement coverage differs: ' + name)
        expected_npcs = [{'ped_id': p['ped_id'], 'source': p['source_reference'],
                          'route_points': len(p['points']), 'selection_policy': p['selection_policy']}
                         for p in npcs if p['zone'] == name]
        require(rows(record.get('npcs', []), ('ped_id', 'source', 'route_points', 'selection_policy'))
                == rows(expected_npcs, ('ped_id', 'source', 'route_points', 'selection_policy')),
                'NPC source placement coverage differs: ' + name)
        require(rows(record.get('props', []), ('model_key', 'source', 'playback', 'collision'))
                == rows(prop_zones.get(name, []), ('model_key', 'source', 'playback', 'collision')),
                'Prop source placement coverage differs: ' + name)
        actual_helpers = [p for p in record.get('original_collisions', []) if p.get('collision_only_helper')]
        require(rows(actual_helpers, ('model_key', 'mesh', 'source'))
                == rows(helper_zones.get(name, []), ('model_key', 'mesh', 'source')),
                'Original collision helper placement differs: ' + name)
        actual_collisions = [p for p in record.get('original_collisions', []) if not p.get('collision_only_helper')]
        require(rows(actual_collisions, ('model_key', 'mesh', 'source'))
                == rows(collision_zones.get(name, []), ('model_key', 'mesh', 'source')),
                'Original collision placement coverage differs: ' + name)
        expected_doors = [dict(p, travel=bool(p['transition'] and p['transition']['area_in_world_plan']))
                          for p in door_zones.get(name, [])]
        require(rows(record.get('doors', []), ('id', 'model_key', 'source', 'travel'))
                == rows(expected_doors, ('id', 'model_key', 'source', 'travel')),
                'Original door placement coverage differs: ' + name)


def build_release(inputs, snapshot_path, *, root=ROOT, reviewed_images=False):
    root = Path(root).resolve()
    snapshot_path = local_path(root, snapshot_path)
    snapshot_sha = digest(snapshot_path)
    baseline = json.loads(snapshot_path.read_text(encoding='utf-8'))
    require(reviewed_images is True, 'Explicit --reviewed-images is required after inspecting actual images')
    require(inputs.get('visual_plan') and inputs.get('visual_report'), 'The v07 visual tour and its nine captures are required')
    require(baseline.get('schema') == 'rea-bully-release-snapshot-v1'
            and baseline.get('package_roots') == list(PACKAGE_ROOTS)
            and baseline.get('navigation_excluded_until_verified_import') == NAV,
            'Complete pre-test package snapshot required')
    require(type(baseline.get('completed_at_unix_ns')) is int, 'Snapshot completion time required')
    runtime = digest(root / RUNTIME)
    require(runtime == baseline['runtime_sha256'], 'Runtime changed after pre-test snapshot')
    current_assets = asset_hashes(root)
    require(current_assets == baseline.get('world_asset_hashes'), 'Frozen dependency packages changed after snapshot')
    scene = snapshot_scene(root)
    require(scene == baseline.get('scene_asset_hashes'), 'Saved world scene changed after snapshot')
    proofs = {snapshot_path.relative_to(root).as_posix(): snapshot_sha}

    def read(name, fresh=False):
        path = local_path(root, name)
        if fresh:
            require(path.stat().st_mtime_ns >= baseline['completed_at_unix_ns'],
                    'Runtime proof predates the package snapshot: ' + str(name))
        proofs[path.relative_to(root).as_posix()] = digest(path)
        return json.loads(path.read_text(encoding='utf-8-sig'))

    def flag(command, name, value=None):
        expected = name if value is None else name + '=' + str(local_path(root, value))
        require(expected in command, 'Required tested runtime command missing: ' + expected)

    def run_report(key, *, scene_bound=True, plan_key=None, plan_hash_key=None, plan_flag=None):
        path = local_path(root, inputs[key])
        report = read(path, fresh=True)
        run = read(path.with_suffix('.run.json'), fresh=True)
        require(report.get('passed') is True and not report.get('error') and run.get('exit_code') == 0,
                'Runtime evidence did not pass: ' + key)
        require(run.get('runtime_sha256') == runtime, 'Runtime evidence belongs to another DLL: ' + key)
        require(run.get('release_snapshot_path') == str(snapshot_path)
                and run.get('release_snapshot_sha256') == snapshot_sha
                and run.get('release_snapshot_unchanged_during_run') is True,
                'Runtime evidence belongs to another release snapshot: ' + key)
        command = run.get('command')
        require(isinstance(command, list) and all(isinstance(v, str) for v in command), 'Runtime command missing')
        flag(command, '-ReaBullyWorldReport', path)
        if scene_bound:
            require(run.get('scene_asset_hashes') == scene and run.get('scene_unchanged_during_run') is True
                    and run.get('runtime_unchanged_during_run') is True, 'Runtime scene proof differs: ' + key)
        if plan_key:
            plan_path = local_path(root, inputs[plan_key])
            require(run.get(plan_hash_key) == digest(plan_path), 'Runtime source plan hash differs: ' + key)
            unchanged = {'tour_sha256': 'tour_unchanged_during_run',
                         'interaction_plan_sha256': 'interaction_plan_unchanged_during_run',
                         'gesture_plan_sha256': 'gesture_plan_unchanged_during_run'}[plan_hash_key]
            require(run.get(unchanged) is True, 'Runtime source plan changed during probe')
            flag(command, plan_flag, plan_path)
        log = local_path(root, run['log'])
        proofs[log.relative_to(root).as_posix()] = digest(log)
        return report, run

    world, scope, build = (read(inputs[k]) for k in ('world_plan', 'scope', 'scene_build'))
    zones = {z['name'] for z in world['zones']}
    areas = {z['area'] for z in world['zones']}
    require(len(world['zones']) == len(zones) == 85 and len(areas) == 47
            and zones == {z['name'] for z in scope['zones']}
            and areas == {a['name'] for a in scope['areas']}, 'Release requires all 85 source zones and 47 areas')
    require(build.get('status') == 'zones_saved' and build.get('map') == '/Game/Maps/WorldExploration'
            and build.get('plan_sha256') == digest(local_path(root, inputs['world_plan']))
            and len(build.get('zones', [])) == 85 and {z['zone'] for z in build['zones']} == zones,
            'Full scene build does not match the current world plan')
    require(set(scene) == {MAP, 'unreal/Content/World/DA_World.uasset',
                          *(f'unreal/Content/World/Zones/{z}.umap' for z in zones)},
            'Saved zone-map inventory differs from the full source scene')
    ambient, props, helpers, collisions, doors = (read(inputs[k]) for k in (
        'ambient_plan', 'props_plan', 'collision_helpers', 'collision_plan', 'door_plan'))
    world_ref = {'path': str(local_path(root, inputs['world_plan'])),
                 'sha256': digest(local_path(root, inputs['world_plan']))}
    require(collisions.get('source_reports', {}).get('world_plan') == world_ref
            and world_ref in doors.get('source_catalogs', []),
            'Collision and door plans must derive from this complete world plan')
    verify_scene_contents(world, build, ambient, props, helpers, collisions, doors)
    require(build.get('prop_plan_sha256') == digest(local_path(root, inputs['props_plan']))
            and build.get('interaction_plan_sha256') == digest(local_path(root, inputs['garage_plan']))
            and build.get('gesture_plan_sha256') == digest(local_path(root, inputs['gesture_plan'])),
            'Scene prop/interaction/gesture source plan hashes differ')
    tour = read(inputs['tour_plan'])
    require(len(tour['checkpoints']) == 85 and {c['zone'] for c in tour['checkpoints']} == zones
            and {c['area'] for c in tour['checkpoints']} == areas, 'Full source tour coverage differs')
    tour_report, _ = run_report('tour_report', plan_key='tour_plan', plan_hash_key='tour_sha256', plan_flag='-ReaBullyWorldTour')
    require(len(tour_report.get('checkpoints', [])) == 85
            and all(r.get('passed') is True for r in tour_report['checkpoints']), 'Every full-tour checkpoint must pass')
    rebuilt_nav = build_manifest(local_path(root, inputs['tour_plan']), local_path(root, inputs['tour_report']),
                                 local_path(root, inputs['scope']), root=root, require_all=True)
    navigation = read(inputs['navigation_plan'])
    require(navigation == rebuilt_nav and len(navigation['entries']) == 47 and not navigation['pending_areas'],
            'Navigation is not the complete current tour-derived manifest')
    nav_import = read(inputs['navigation_import'], fresh=True)
    require(nav_import.get('status') == 'area_navigation_assets_verified_runtime_pending'
            and nav_import.get('runtime_sha256') == runtime and nav_import.get('scene_asset_hashes') == scene
            and nav_import.get('entries') == 47 and not nav_import.get('pending_areas')
            and nav_import.get('manifest') == {'path': str(local_path(root, inputs['navigation_plan'])),
                                              'sha256': digest(local_path(root, inputs['navigation_plan']))}
            and local_path(root, nav_import['asset']['path']) == root / NAV
            and nav_import['asset']['sha256'] == digest(root / NAV), 'Navigation import asset/provenance differs')
    require(local_path(root, inputs['navigation_import']).stat().st_mtime_ns
            >= local_path(root, inputs['tour_report']).stat().st_mtime_ns, 'Navigation import must follow the full tour')
    nav_report, nav_run = run_report('navigation_report')
    flag(nav_run['command'], '-ReaBullyNavigationProbe')
    all_true(nav_report, NAV_GATES, 'Navigation keyboard probe')
    entries = {e['id']: e['area'] for e in navigation['entries']}
    travels = nav_report.get('travels', [])
    require(nav_report.get('available_entries') == 47 and len(travels) == 2
            and len({t['selected_area'] for t in travels}) == 2
            and all(t.get('passed') is True and t['selected_area'] == t['actual_area'] == entries.get(t['source_id'])
                    and t.get('floor_component') for t in travels), 'Navigation must prove two distinct source-bound arrivals')
    require(local_path(root, inputs['navigation_report']).stat().st_mtime_ns
            >= local_path(root, inputs['navigation_import']).stat().st_mtime_ns, 'Navigation probe predates the imported menu')

    for kind, count, gates, hash_key, probe_flag in (
            ('garage', 2, GARAGE_GATES, 'interaction_plan_sha256', '-ReaBullyInteractionProbe'),
            ('gesture', 6, GESTURE_GATES, 'gesture_plan_sha256', '-ReaBullyGestureProbe')):
        plan = read(inputs[kind + '_plan'])
        report, _ = run_report(kind + '_report', plan_key=kind + '_plan', plan_hash_key=hash_key, plan_flag=probe_flag)
        rows = report.get('placements', [])
        require(len(plan['placements']) == len(rows) == count
                and [r['source_placement'] for r in rows] == plan['placements'], kind + ' source placement coverage differs')
        for row in rows:
            all_true(row, gates, kind)
            require(row.get('floor_component'), kind + ' floor evidence missing')
            if kind == 'garage':
                require(row.get('f_action_count') == 2 and positive(row.get('open_endpoint_seconds'))
                        and positive(row.get('close_endpoint_seconds'))
                        and row.get('open_clip') == plan['model']['clips']['OPEN']
                        and row.get('close_clip') == plan['model']['clips']['CLOSE'], 'Garage source clips/actions differ')
            else:
                model = next(m for m in plan['models'] if m['model_key'] == row['source_placement']['model_key'])
                endpoint = row.get('original_gesture_endpoint_seconds')
                require(row.get('manual_f_actions') == row.get('completed_gestures') == 2 and positive(endpoint)
                        and abs(endpoint - model['durations']['gesture']) <= .001, 'Gesture source endpoint/actions differ')

    audio = read(inputs['audio_import'])
    require(audio.get('status') == 'audio_assets_imported_pcm_verified_runtime_pending' and audio.get('waves') == 207
            and len(audio.get('entries', [])) == 207 and all(e.get('saved_imported_pcm_verified') is True for e in audio['entries'])
            and build.get('integration', {}).get('audio_waves') == 207, 'All 207 verified imported audio samples must be in the scene')
    integrated, integrated_run = run_report('integrated_render', scene_bound=False)
    all_true(integrated, INTEGRATED_GATES, 'Integrated rendered audio probe')
    for required in ('-ReaBullyIntegrationProbe', '-AudioMixer', '-ReaBullyRequireAudio', '-RenderOffscreen', '-sm6'):
        flag(integrated_run['command'], required)
    require(not {'-nosound', '-NullRHI'} & set(integrated_run['command']), 'Audio/render proof used a disabled device')
    require(positive(integrated.get('footstep_requests')) and positive(integrated.get('ambience_starts')),
            'No actual footstep/ambience requests recorded')
    profile = inputs['renderer_commands']
    renderer_profile({'passed': True, 'renderer_commands': profile})
    require('-ExecCmds=' + ','.join(profile) in integrated_run['command'], 'Release renderer differs from the tested profile')
    image = local_path(root, integrated_run['image'])
    flag(integrated_run['command'], '-ReaBullyScreenshot', image)
    images = [png_capture_info(image)]
    require(integrated_run.get('image_capture_complete') is True
            and integrated_run.get('image_capture') == images[0]
            and integrated_run.get('image_sha256') == images[0]['sha256'],
            'Integrated image changed after its verified render run')
    visual_plan = read(inputs['visual_plan'])
    require(visual_plan.get('parent_tour') == {'path': str(local_path(root, inputs['tour_plan'])),
                                             'sha256': digest(local_path(root, inputs['tour_plan']))}
            and len(visual_plan.get('checkpoints', [])) == 9
            and len({c['name'] for c in visual_plan['checkpoints']}) == 9
            and all(c in tour['checkpoints'] for c in visual_plan['checkpoints']),
            'Visual subset must contain nine distinct exact checkpoints from the full tested tour')
    visual, visual_run = run_report('visual_report', plan_key='visual_plan', plan_hash_key='tour_sha256', plan_flag='-ReaBullyWorldTour')
    require(visual_run.get('captures_complete') is True and '-ExecCmds=' + ','.join(profile) in visual_run['command'],
            'Visual tour captures/profile did not verify')
    directory = local_path(root, visual_run['tour_capture_directory'])
    captures = inspect_tour_captures(local_path(root, inputs['visual_report']), directory, visual_plan['checkpoints'])
    require(captures == visual_run.get('tour_captures'), 'Visual capture hashes changed after their verified tour run')
    images.extend(captures)
    for info in images:
        path = local_path(root, info['path'])
        require(path.stat().st_mtime_ns >= baseline['completed_at_unix_ns'], 'Reviewed image predates tested assets')
        proofs[path.relative_to(root).as_posix()] = info['sha256']
    tests = read(inputs['python_tests'])
    require(tests.get('passed') is True and positive(tests.get('tests_run'))
            and tests.get('source_files_unchanged_during_run') is True
            and not any(tests.get(k) for k in ('failures', 'errors', 'skipped', 'unexpected_successes')),
            'Current full Python suite must pass without skips')
    for folder, pattern, field in (('tests', 'test_*.py', 'test_sources'), ('tools', '*.py', 'tool_sources')):
        expected = {p.relative_to(root).as_posix(): digest(p) for p in (root / folder).rglob(pattern)}
        require(expected and {r['path']: r['sha256'] for r in tests.get(field, [])} == expected,
                'Python suite does not cover current ' + folder + ' sources')
    # Final recheck catches modifications while the evidence graph was being examined.
    require(asset_hashes(root) == current_assets and digest(root / RUNTIME) == runtime,
            'Release dependencies changed while verifying evidence')
    current_assets[NAV] = digest(root / NAV)
    require(current_assets[NAV] == nav_import['asset']['sha256'], 'Navigation asset changed during release verification')
    require(all(digest(local_path(root, name)) == expected for name, expected in proofs.items()),
            'Release evidence changed while being verified')
    observed_npcs = sorted({sample['ped_id'] for checkpoint in tour_report['checkpoints']
        for sample in checkpoint.get('ambient_ped_samples', [])
        if type(sample.get('ped_id')) is int and sample['ped_id'] >= 0})
    return {'schema': 'rea-bully-world-release-v1', 'version': 'v07', 'passed': True,
        'created_at_utc': datetime.now(timezone.utc).isoformat(), 'map': '/Game/Maps/WorldExploration',
        'runtime_sha256': runtime, 'world_asset_hashes': current_assets, 'renderer_commands': profile,
        'snapshot': {'path': str(snapshot_path), 'sha256': snapshot_sha},
        'proofs': proofs, 'visual_review': {'reviewed': True, 'images': images,
            'visual_quality_superior_to_original_established': False},
        'scope': {'zones': sorted(zones), 'areas': sorted(areas), 'tour_checkpoints': 85,
            'integration_counts': build['integration'], 'ambient_npcs_placed': build['integration'].get('npcs', 0),
            'ambient_npc_runtime_observed_ids': observed_npcs, 'all_placed_npcs_runtime_certified': False,
            'npc_runtime_observation_policy': 'Placed/imported counts are not all-NPC runtime certification. Observed IDs are passive tour samples, not evidence that every skin/animation/floor/route gate passed.'},
        'full_game_goal_complete': False,
        'limitations': ['Local release requiring Unreal and original recovered assets; not a standalone distributed game.',
            'Tour verifies sampled source positions, streaming, floor and short walks, not exhaustive traversal or all NPC routes.',
            'Garage and carnival F controls, area menu, streaming and audio selection are exploration reconstruction; original autonomous schedulers are incomplete.',
            '207 audio samples are imported; runtime proof covers selected footsteps and Birds ambience.',
            'Missions, combat, vehicles, saves and complete original AI remain incomplete.',
            'Fixed-step renders are not performance benchmarks or proof of superiority to the original.',
            'Package batch equality and scene checks before/after probes do not prove that no intermediate change-and-reversion occurred.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    snapshot = sub.add_parser('snapshot')
    snapshot.add_argument('--output', type=Path, required=True)
    record = sub.add_parser('record')
    record.add_argument('--snapshot', type=Path, required=True)
    record.add_argument('--evidence', type=Path, required=True, help='Input report paths, or preflight JSON containing release_inputs')
    record.add_argument('--output', type=Path, required=True)
    record.add_argument('--reviewed-images', action='store_true')
    args = parser.parse_args()
    require(not args.output.exists(), 'New output required; preserve existing release evidence')
    if args.action == 'snapshot':
        result = create_snapshot()
    else:
        inputs = json.loads(args.evidence.read_text(encoding='utf-8-sig'))
        result = build_release(inputs.get('release_inputs', inputs), args.snapshot,
                               reviewed_images=args.reviewed_images)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
    print(json.dumps({'output': str(args.output), 'action': args.action, 'packages': len(result['world_asset_hashes'])}))


if __name__ == '__main__':
    main()

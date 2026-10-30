"""Run bounded world preparation, NPC import and real runtime verification in Unreal."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zlib

ROOT = Path(__file__).resolve().parents[1]
ENGINE = Path('C:/Program Files/Epic Games/UE_5.8/Engine/Binaries/Win64/UnrealEditor-Cmd.exe')


def png_capture_info(path):
    """Verify a completed engine PNG, including chunk CRCs and the compressed image stream.

    This certifies file completion, not appearance; screenshots still need visual review.
    """
    data = path.read_bytes()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Screenshot is not PNG: ' + str(path))
    offset, compressed, header, ended = 8, bytearray(), None, False
    while offset + 12 <= len(data):
        size = int.from_bytes(data[offset:offset + 4], 'big')
        tag = data[offset + 4:offset + 8]
        end = offset + 12 + size
        if end > len(data):
            raise ValueError('Truncated PNG chunk: ' + str(path))
        payload = data[offset + 8:offset + 8 + size]
        if zlib.crc32(tag + payload) != int.from_bytes(data[end - 4:end], 'big'):
            raise ValueError('PNG chunk checksum mismatch: ' + str(path))
        if header is None:
            if tag != b'IHDR' or size != 13:
                raise ValueError('PNG header missing: ' + str(path))
            header = payload
        elif tag == b'IHDR':
            raise ValueError('Duplicate PNG header: ' + str(path))
        if tag == b'IDAT':
            compressed.extend(payload)
        if tag == b'IEND':
            ended = size == 0 and end == len(data)
            break
        offset = end
    if not ended or not header or not compressed:
        raise ValueError('PNG is incomplete: ' + str(path))
    width, height = int.from_bytes(header[:4], 'big'), int.from_bytes(header[4:8], 'big')
    depth, color, compression, filtering, interlace = header[8:]
    if (width, height) != (1600, 900) or depth != 8 or color not in (2, 6) or any((compression, filtering, interlace)):
        raise ValueError('Unexpected engine screenshot dimensions or PNG format: ' + str(path))
    row_bytes = width * (3 if color == 2 else 4) + 1
    decoder = zlib.decompressobj()
    pixels = decoder.decompress(compressed, row_bytes * height + 1)
    if (not decoder.eof or decoder.unused_data or len(pixels) != row_bytes * height or
            any(pixels[row * row_bytes] > 4 for row in range(height))):
        raise ValueError('Incomplete PNG image stream: ' + str(path))
    return {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest(),
            'bytes': len(data), 'width': width, 'height': height}


def inspect_tour_captures(report, capture_directory, checkpoints):
    """Bind every actual PNG to its unchanged selected checkpoint and engine result."""
    evidence = json.loads(report.read_text(encoding='utf-8-sig'))
    records = evidence.get('checkpoints', [])
    count = len(checkpoints)
    if (evidence.get('captures_expected') != count or evidence.get('captures_completed') != count or
            len(records) != count or Path(evidence.get('capture_directory', '')).resolve() != capture_directory):
        raise ValueError('Tour screenshot coverage does not match the selected plan')
    images = []
    for index, (record, checkpoint) in enumerate(zip(records, checkpoints)):
        image = capture_directory/f'checkpoint-{index:03d}.png'
        if (record.get('source_checkpoint') != checkpoint or not record.get('screenshot_complete') or
                not record.get('screenshot_request_processed') or
                not record.get('capture_same_player_area_zone_recovered_floor_stationary') or
                Path(record.get('screenshot_path', '')).resolve() != image):
            raise ValueError('Screenshot is not bound to its stationary source checkpoint: ' + str(index))
        info = png_capture_info(image)
        if (record.get('screenshot_bytes'), record.get('screenshot_width'), record.get('screenshot_height')) != (
                info['bytes'], info['width'], info['height']):
            raise ValueError('Screenshot changed after the engine recorded it: ' + str(index))
        images.append(dict(info, checkpoint_index=index, checkpoint_name=checkpoint['name']))
    if set(capture_directory.iterdir()) != {Path(image['path']) for image in images}:
        raise ValueError('Unexpected files in the new tour screenshot directory')
    return images


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'assemble', 'import-npcs', 'import-props', 'import-doors', 'import-audio',
        'import-collisions', 'import-integration', 'import-interactions', 'import-gestures', 'import-navigation', 'diagnose-collisions', 'frame-start', 'patch-materials', 'test', 'render'])
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--zones', help='Comma-separated exact source zone names for a bounded probe')
    parser.add_argument('--zone-batch', action='store_true', help='Save only the selected zone levels, leaving the persistent scene unchanged')
    parser.add_argument('--build-batches', type=Path, help='Verified batch index for lightweight final scene assembly')
    parser.add_argument('--npcs', type=Path, default=ROOT/'evidence/npc-unreal-import-v04.json')
    parser.add_argument('--npc-manifest', type=Path, help='Promoted verified NPC import manifest')
    parser.add_argument('--ambient', type=Path, help='Source-bound reconstructed NPC placement plan')
    parser.add_argument('--doors', type=Path, help='Source-bound door placement plan')
    parser.add_argument('--collisions', type=Path, help='Verified collision placement plan')
    parser.add_argument('--collision-helpers', type=Path, help='Individually verified original collision-only helper plan')
    parser.add_argument('--collision-export', type=Path, help='Verified original collision geometry export')
    parser.add_argument('--audio-data', type=Path, help='Verified world audio import report')
    parser.add_argument('--interactions', type=Path, help='Source-bound non-mission prop interaction plan')
    parser.add_argument('--interaction-import', type=Path, help='Verified prop interaction data import report')
    parser.add_argument('--gestures', type=Path, help='Source-bound carnival prop gesture plan')
    parser.add_argument('--gesture-import', type=Path, help='Verified carnival gesture data import report')
    parser.add_argument('--gesture-probe', type=Path, help='Actual F-input verification of the six original carnival attendants')
    parser.add_argument('--navigation', type=Path, help='Area navigation plan promoted from matching runtime traversal')
    parser.add_argument('--navigation-probe', action='store_true', help='Verify actual menu input and two-area navigation')
    parser.add_argument('--audio', action='store_true', help='Enable the real audio device during runtime verification')
    parser.add_argument('--integrated', action='store_true', help='Require NPCs, doors, travel, audio assets and original collisions')
    parser.add_argument('--tour', type=Path, help='Source-authored cross-area checkpoints for streaming and floor traversal')
    parser.add_argument('--tour-capture', type=Path, help='New directory under local/ for one rendered PNG per selected tour checkpoint; test --tour only')
    parser.add_argument('--interaction-probe', type=Path, help='Verified original garage plan for a separate actual F-input probe')
    parser.add_argument('--collision-winding-probe', help='Explicit diagnostic mesh for a one-ground-instance orientation A/B')
    parser.add_argument('--props', type=Path, help='Verified original skinned-prop placement plan')
    parser.add_argument('--prop-model', action='append', help='Exact original prop key for a bounded import')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--release-snapshot', type=Path, help='Bind this test/render to a specific frozen release batch')
    parser.add_argument('--image', type=Path)
    parser.add_argument('--render-command', action='append', help='Process-local rendering CVar experiment; may be repeated')
    args = parser.parse_args()
    if args.zone_batch and (args.action != 'prepare' or not args.zones):
        parser.error('--zone-batch requires prepare with explicit --zones')
    if args.action == 'assemble' and (not args.build_batches or args.zones or args.zone_batch):
        parser.error('assemble requires --build-batches and the complete world plan')
    if args.build_batches and args.action != 'assemble':
        parser.error('--build-batches applies only to assemble')
    if args.release_snapshot and args.action not in ('test', 'render'):
        parser.error('--release-snapshot applies only to runtime test/render evidence')
    timeout = 1800
    capture_directory = None
    if args.tour_capture:
        if args.action != 'test' or not args.tour or args.image:
            parser.error('--tour-capture requires test --tour and uses its own new image directory')
        capture_directory = args.tour_capture.resolve()
        if not capture_directory.is_relative_to((ROOT/'local').resolve()) or capture_directory.exists():
            parser.error('--tour-capture requires a new directory inside this repository local/')
    if args.navigation_probe:
        if (args.action != 'test' or args.tour or args.integrated or args.audio or
                args.interaction_probe or args.gesture_probe or args.collision_winding_probe):
            parser.error('--navigation-probe requires a separate test from other runtime probes')
        timeout = 600
    if args.interaction_probe:
        if args.action not in ('test', 'render') or args.integrated or args.tour or args.gesture_probe or args.collision_winding_probe:
            parser.error('--interaction-probe requires a separate test/render from tour/integration/winding probes')
        from plan_prop_interactions import make_plan
        if json.loads(args.interaction_probe.read_text(encoding='utf-8')) != make_plan():
            parser.error('Interaction probe requires the freshly source-verified garage plan')
    if args.gesture_probe:
        if args.action not in ('test', 'render') or args.integrated or args.tour or args.collision_winding_probe:
            parser.error('--gesture-probe requires a separate test/render from other runtime probes')
        from plan_prop_gestures import make_plan as make_gesture_plan
        if json.loads(args.gesture_probe.read_text(encoding='utf-8')) != make_gesture_plan():
            parser.error('Gesture probe requires the freshly source-verified carnival plan')
    if args.tour:
        if args.action != 'test' or args.integrated or args.audio or args.collision_winding_probe:
            parser.error('--tour requires test and a separate report from integration/audio/winding probes')
        checkpoints = json.loads(args.tour.read_text(encoding='utf-8'))['checkpoints']
        if not checkpoints:
            parser.error('Tour must contain source checkpoints')
        timeout = max(timeout, (250 if capture_directory else 130) * len(checkpoints) + 300)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    report = (args.report or ROOT/f'evidence/world-{args.action}-{stamp}.json').resolve()
    image = (args.image or ROOT/f'local/world-{stamp}.png').resolve()
    log = ROOT/f'local/world-{args.action}-{stamp}.log'
    if report.exists() or (args.action == 'render' and image.exists()):
        parser.error('New evidence paths required')
    env = os.environ.copy()
    env['UE-LocalDataCachePath'] = str(ROOT/'unreal/DerivedDataCache')
    env['BULLY_UE_REPORT'] = str(report)
    if args.npc_manifest:
        env['BULLY_NPC_MANIFEST'] = str(args.npc_manifest.resolve())
    if args.collision_export:
        env['REA_WORLD_COLLISIONS_CATALOG'] = str(args.collision_export.resolve())
    if args.interactions:
        if not args.interactions.is_file():
            parser.error('Verified interaction plan required')
        env['REA_PROP_INTERACTION_PLAN'] = str(args.interactions.resolve())
    if args.gestures:
        if not args.gestures.is_file():
            parser.error('Verified gesture plan required')
        env['REA_PROP_GESTURE_PLAN'] = str(args.gestures.resolve())
    if args.navigation:
        if not args.navigation.is_file():
            parser.error('Verified area navigation plan required')
        env['REA_AREA_NAVIGATION_PLAN'] = str(args.navigation.resolve())
    command = [str(ENGINE), str(ROOT/'unreal/ReaBully.uproject')]
    if args.action in ('prepare', 'assemble'):
        command += ['/Engine/Maps/Entry']
        if not args.plan or not args.plan.is_file():
            parser.error('Verified world plan required')
        if args.ambient and not args.npcs.is_file():
            parser.error('--ambient requires an existing verified --npcs import report')
        if bool(args.interactions) != bool(args.interaction_import):
            parser.error('--interactions and --interaction-import must be supplied together for preparation')
        if bool(args.gestures) != bool(args.gesture_import):
            parser.error('--gestures and --gesture-import must be supplied together for preparation')
        env['REA_WORLD_PLAN'] = str(args.plan.resolve())
        env['REA_WORLD_ZONES'] = args.zones or ''
        # Always set this flag: an inherited shell value cannot suppress scene saving.
        env['REA_WORLD_ZONE_BATCH'] = '1' if args.zone_batch else '0'
        if args.build_batches:
            if not args.build_batches.is_file():
                parser.error('Verified world batch index required')
            env['REA_WORLD_BUILD_BATCHES'] = str(args.build_batches.resolve())
        for value, name in ((args.ambient, 'REA_WORLD_AMBIENT_PLAN'), (args.doors, 'REA_WORLD_DOORS_PLAN'),
                            (args.collisions, 'REA_WORLD_COLLISION_PLAN'), (args.audio_data, 'REA_WORLD_AUDIO_REPORT'),
                            (args.collision_helpers, 'REA_WORLD_COLLISION_HELPERS'),
                            (args.interaction_import, 'REA_PROP_INTERACTION_IMPORT'),
                            (args.gesture_import, 'REA_PROP_GESTURE_IMPORT')):
            if value:
                if not value.is_file():
                    parser.error('Verified integration plan/report required: ' + str(value))
                env[name] = str(value.resolve())
        if args.npcs.exists():
            env['REA_WORLD_NPCS'] = str(args.npcs.resolve())
        if args.props:
            if not args.props.is_file():
                parser.error('Verified prop plan required')
            env['REA_WORLD_PROPS_PLAN'] = str(args.props.resolve())
        script = 'unreal_assemble_world.py' if args.action == 'assemble' else 'unreal_build_world.py'
        command += ['-NullRHI', '-ExecutePythonScript='+str(ROOT/'tools'/script)]
        expected = 'zone_assets_saved' if args.zone_batch else 'zones_saved'
    elif args.action == 'import-npcs':
        command += ['/Engine/Maps/Entry']
        command += ['-NullRHI', '-ExecutePythonScript='+str(ROOT/'tools/unreal_import_npcs.py')]
        expected = 'npc_assets_verified'
    elif args.action == 'import-props':
        command += ['/Engine/Maps/Entry', '-NullRHI',
                    '-ExecutePythonScript='+str(ROOT/'tools/unreal_import_world_props.py')]
        env['REA_WORLD_PROPS_KEYS'] = ','.join(args.prop_model or [])
        expected = 'prop_assets_verified'
    elif args.action in ('import-doors', 'import-audio', 'import-collisions', 'import-integration', 'import-interactions', 'import-gestures', 'import-navigation', 'diagnose-collisions', 'frame-start'):
        if args.action == 'import-navigation' and not args.navigation:
            parser.error('import-navigation requires --navigation with a promoted source plan')
        if args.action == 'import-gestures' and not args.gestures:
            parser.error('import-gestures requires --gestures with its verified source plan')
        script, expected = {
            'import-doors': ('unreal_import_world_doors.py', 'door_assets_verified'),
            'import-audio': ('unreal_import_world_audio.py', 'audio_assets_imported_pcm_verified_runtime_pending'),
            'import-collisions': ('unreal_import_world_collisions.py', 'collision_assets_verified_runtime_pending'),
            'import-integration': ('unreal_import_integration.py', 'integration_assets_verified'),
            'import-interactions': ('unreal_import_prop_interactions.py', 'prop_interaction_assets_verified_runtime_pending'),
            'import-gestures': ('unreal_import_prop_gestures.py', 'prop_gesture_assets_verified_runtime_pending'),
            'import-navigation': ('unreal_import_area_navigation.py', 'area_navigation_assets_verified_runtime_pending'),
            'diagnose-collisions': ('unreal_diagnose_world_collisions.py', 'editor_query_diagnostic'),
            'frame-start': ('unreal_frame_world_start.py', 'world_start_framed'),
        }[args.action]
        command += ['/Engine/Maps/Entry', '-NullRHI', '-ExecutePythonScript='+str(ROOT/'tools'/script)]
    elif args.action == 'patch-materials':
        command += ['/Engine/Maps/Entry', '-NullRHI',
                    '-ExecutePythonScript='+str(ROOT/'tools/unreal_patch_world_instancing.py')]
        expected = 'world_instancing_materials_verified'
    else:
        command += ['/Game/Maps/WorldExploration', '-game', '-ReaBullyWorldReport='+str(report)]
        if args.tour:
            command += ['-ReaBullyWorldTour=' + str(args.tour.resolve())]
        if capture_directory:
            command += ['-ReaBullyTourCapture=' + str(capture_directory)]
        if args.interaction_probe:
            command += ['-ReaBullyInteractionProbe=' + str(args.interaction_probe.resolve())]
        if args.gesture_probe:
            command += ['-ReaBullyGestureProbe=' + str(args.gesture_probe.resolve())]
        if args.navigation_probe:
            command += ['-ReaBullyNavigationProbe']
        if args.action == 'render' or capture_directory:
            command += ['-RenderOffscreen', '-sm6', '-ForceRes', '-windowed', '-ResX=1600', '-ResY=900',
                '-UseFixedTimeStep', '-FPS=60']
            if args.action == 'render':
                command += ['-ReaBullyScreenshot='+str(image)]
        else:
            command += ['-NullRHI']
        if (args.action == 'render' or capture_directory) and args.render_command:
            command += ['-ExecCmds=' + ','.join(args.render_command)]
        if args.integrated:
            command += ['-ReaBullyIntegrationProbe']
        if args.collision_winding_probe:
            command += ['-ReaBullyCollisionWindingProbe=' + args.collision_winding_probe]
        if args.audio:
            command += ['-AudioMixer', '-ReaBullyRequireAudio']
        expected = None
    if not (args.action in ('test', 'render') and args.audio):
        command += ['-nosound']
    command += ['-unattended', '-nop4', '-nosplash', '-NoCrashDialog',
        '-DDC=InstalledNoZenLocalFallback', '-LocalDataCachePath='+env['UE-LocalDataCachePath'],
        '-abslog='+str(log), '-userdir='+str(ROOT/'unreal/Saved/User')]
    runtime = ROOT/'unreal/Binaries/Win64/UnrealEditor-ReaBullyRuntime.dll'
    sha = hashlib.sha256(runtime.read_bytes()).hexdigest()
    release_snapshot_sha = None
    if args.release_snapshot:
        args.release_snapshot = args.release_snapshot.resolve()
        if not args.release_snapshot.is_relative_to(ROOT.resolve()):
            parser.error('Release snapshot must be inside this repository')
        raw_snapshot = args.release_snapshot.read_bytes()
        baseline = json.loads(raw_snapshot.decode('utf-8'))
        from area_navigation_contracts import snapshot_scene
        if (baseline.get('schema') != 'rea-bully-release-snapshot-v1' or
                baseline.get('runtime_sha256') != sha or
                baseline.get('scene_asset_hashes') != snapshot_scene(ROOT)):
            parser.error('Release snapshot does not match the current runtime and world scene')
        release_snapshot_sha = hashlib.sha256(raw_snapshot).hexdigest()
    tour_sha = hashlib.sha256(args.tour.read_bytes()).hexdigest() if args.tour else None
    interaction_sha = hashlib.sha256(args.interaction_probe.read_bytes()).hexdigest() if args.interaction_probe else None
    gesture_sha = hashlib.sha256(args.gesture_probe.read_bytes()).hexdigest() if args.gesture_probe else None
    scene_hashes = None
    if args.tour or args.interaction_probe or args.navigation_probe or args.gesture_probe:
        from area_navigation_contracts import snapshot_scene
        scene_hashes = snapshot_scene(ROOT)
    if capture_directory:
        capture_directory.mkdir(parents=True, exist_ok=False)
    print(json.dumps({'action': args.action, 'report': str(report), 'log': str(log)}), flush=True)
    with log.with_suffix('.stdout.log').open('xb') as stream:
        result = subprocess.run(command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
    run = {'command': command, 'exit_code': result.returncode,
        'runtime_sha256': sha, 'log': str(log), 'image': str(image) if args.action == 'render' else None,
        'tour_sha256': tour_sha, 'interaction_plan_sha256': interaction_sha,
        'navigation_probe': args.navigation_probe, 'gesture_plan_sha256': gesture_sha}
    if args.release_snapshot:
        run.update(release_snapshot_path=str(args.release_snapshot),
            release_snapshot_sha256=release_snapshot_sha,
            release_snapshot_unchanged_during_run=(args.release_snapshot.is_file() and
                hashlib.sha256(args.release_snapshot.read_bytes()).hexdigest() == release_snapshot_sha))
    if args.action == 'render':
        run['image_capture_complete'] = False
        try:
            run['image_capture'] = png_capture_info(image)
            run['image_sha256'] = run['image_capture']['sha256']
            run['image_capture_complete'] = True
        except (OSError, ValueError, zlib.error) as error:
            run['image_capture_error'] = str(error)
    if args.tour or args.interaction_probe or args.navigation_probe or args.gesture_probe:
        run.update(scene_asset_hashes=scene_hashes,
            scene_unchanged_during_run=snapshot_scene(ROOT) == scene_hashes,
            runtime_unchanged_during_run=hashlib.sha256(runtime.read_bytes()).hexdigest() == sha)
    if args.tour:
        run['tour_unchanged_during_run'] = hashlib.sha256(args.tour.read_bytes()).hexdigest() == tour_sha
    if args.interaction_probe:
        run['interaction_plan_unchanged_during_run'] = hashlib.sha256(args.interaction_probe.read_bytes()).hexdigest() == interaction_sha
    if args.gesture_probe:
        run['gesture_plan_unchanged_during_run'] = hashlib.sha256(args.gesture_probe.read_bytes()).hexdigest() == gesture_sha
    if capture_directory:
        run.update(tour_capture_directory=str(capture_directory), captures_complete=False,
            capture_scope='Selected checkpoint renders only; PNG completion is not visual quality approval.')
        try:
            run['tour_captures'] = inspect_tour_captures(report, capture_directory, checkpoints)
            run['captures_complete'] = True
        except (OSError, ValueError, KeyError, TypeError, zlib.error) as error:
            run['capture_error'] = str(error)
    report.with_suffix('.run.json').write_text(json.dumps(run, indent=2), encoding='utf-8')
    passed = result.returncode == 0 and report.exists()
    if args.release_snapshot:
        passed &= run['release_snapshot_unchanged_during_run']
    if args.tour or args.interaction_probe or args.navigation_probe or args.gesture_probe:
        passed &= run['scene_unchanged_during_run'] and run['runtime_unchanged_during_run']
    if args.tour:
        passed &= run['tour_unchanged_during_run']
    if capture_directory:
        passed &= run['captures_complete']
    if args.interaction_probe:
        passed &= run['interaction_plan_unchanged_during_run']
    if args.gesture_probe:
        passed &= run['gesture_plan_unchanged_during_run']
    if passed:
        evidence = json.loads(report.read_text(encoding='utf-8-sig'))
        passed = evidence.get('status') == expected if expected else evidence.get('passed') is True
    if args.action == 'render':
        passed &= run['image_capture_complete']
    print(json.dumps({'passed': passed, 'report': str(report)}), flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())

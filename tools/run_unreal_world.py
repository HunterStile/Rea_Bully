"""Run bounded world preparation, NPC import and real runtime verification in Unreal."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ENGINE = Path('C:/Program Files/Epic Games/UE_5.8/Engine/Binaries/Win64/UnrealEditor-Cmd.exe')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'import-npcs', 'import-props', 'import-doors', 'import-audio',
        'import-collisions', 'import-integration', 'diagnose-collisions', 'frame-start', 'patch-materials', 'test', 'render'])
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--zones', help='Comma-separated exact source zone names for a bounded probe')
    parser.add_argument('--npcs', type=Path, default=ROOT/'evidence/npc-unreal-import-v04.json')
    parser.add_argument('--npc-manifest', type=Path, help='Promoted verified NPC import manifest')
    parser.add_argument('--ambient', type=Path, help='Source-bound reconstructed NPC placement plan')
    parser.add_argument('--doors', type=Path, help='Source-bound door placement plan')
    parser.add_argument('--collisions', type=Path, help='Verified collision placement plan')
    parser.add_argument('--collision-export', type=Path, help='Verified original collision geometry export')
    parser.add_argument('--audio-data', type=Path, help='Verified world audio import report')
    parser.add_argument('--audio', action='store_true', help='Enable the real audio device during runtime verification')
    parser.add_argument('--integrated', action='store_true', help='Require NPCs, doors, travel, audio assets and original collisions')
    parser.add_argument('--collision-winding-probe', help='Explicit diagnostic mesh for a one-ground-instance orientation A/B')
    parser.add_argument('--props', type=Path, help='Verified original skinned-prop placement plan')
    parser.add_argument('--prop-model', action='append', help='Exact original prop key for a bounded import')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--image', type=Path)
    parser.add_argument('--render-command', action='append', help='Process-local rendering CVar experiment; may be repeated')
    args = parser.parse_args()
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
    command = [str(ENGINE), str(ROOT/'unreal/ReaBully.uproject')]
    if args.action == 'prepare':
        command += ['/Engine/Maps/Entry']
        if not args.plan or not args.plan.is_file():
            parser.error('Verified world plan required')
        if args.ambient and not args.npcs.is_file():
            parser.error('--ambient requires an existing verified --npcs import report')
        env['REA_WORLD_PLAN'] = str(args.plan.resolve())
        env['REA_WORLD_ZONES'] = args.zones or ''
        for value, name in ((args.ambient, 'REA_WORLD_AMBIENT_PLAN'), (args.doors, 'REA_WORLD_DOORS_PLAN'),
                            (args.collisions, 'REA_WORLD_COLLISION_PLAN'), (args.audio_data, 'REA_WORLD_AUDIO_REPORT')):
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
        command += ['-NullRHI', '-ExecutePythonScript='+str(ROOT/'tools/unreal_build_world.py')]
        expected = 'zones_saved'
    elif args.action == 'import-npcs':
        command += ['/Engine/Maps/Entry']
        command += ['-NullRHI', '-ExecutePythonScript='+str(ROOT/'tools/unreal_import_npcs.py')]
        expected = 'npc_assets_verified'
    elif args.action == 'import-props':
        command += ['/Engine/Maps/Entry', '-NullRHI',
                    '-ExecutePythonScript='+str(ROOT/'tools/unreal_import_world_props.py')]
        env['REA_WORLD_PROPS_KEYS'] = ','.join(args.prop_model or [])
        expected = 'prop_assets_verified'
    elif args.action in ('import-doors', 'import-audio', 'import-collisions', 'import-integration', 'diagnose-collisions', 'frame-start'):
        script, expected = {
            'import-doors': ('unreal_import_world_doors.py', 'door_assets_verified'),
            'import-audio': ('unreal_import_world_audio.py', 'audio_assets_imported_pcm_verified_runtime_pending'),
            'import-collisions': ('unreal_import_world_collisions.py', 'collision_assets_verified_runtime_pending'),
            'import-integration': ('unreal_import_integration.py', 'integration_assets_verified'),
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
        command += ['-RenderOffscreen', '-sm6', '-ForceRes', '-windowed', '-ResX=1600', '-ResY=900',
            '-UseFixedTimeStep', '-FPS=60', '-ReaBullyScreenshot='+str(image)] if args.action == 'render' else ['-NullRHI']
        if args.action == 'render' and args.render_command:
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
    print(json.dumps({'action': args.action, 'report': str(report), 'log': str(log)}), flush=True)
    with log.with_suffix('.stdout.log').open('xb') as stream:
        result = subprocess.run(command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=1800)
    report.with_suffix('.run.json').write_text(json.dumps({'command': command, 'exit_code': result.returncode,
        'runtime_sha256': sha, 'log': str(log), 'image': str(image) if args.action == 'render' else None}, indent=2), encoding='utf-8')
    passed = result.returncode == 0 and report.exists()
    if passed:
        evidence = json.loads(report.read_text(encoding='utf-8-sig'))
        passed = evidence.get('status') == expected if expected else evidence.get('passed') is True
    if args.action == 'render':
        passed &= image.exists()
    print(json.dumps({'passed': passed, 'report': str(report)}), flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())

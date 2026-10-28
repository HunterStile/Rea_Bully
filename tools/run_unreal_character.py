"""Import the independently decoded Jimmy GLB in the installed Unreal editor."""
from __future__ import annotations
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--folder')
    parser.add_argument('--actions', action='store_true', help='Install the v0.3 jump/fall set into its own asset folder')
    args = parser.parse_args()
    args.source = args.source or ROOT / ('local/converted/jimmy/jimmy_actions_v03_final.glb' if args.actions
                                        else 'local/converted/jimmy/jimmy_final_animated.glb')
    args.folder = args.folder or ('/Game/Original/JimmyActions' if args.actions else '/Game/Original/Jimmy')
    if args.report.exists() or not args.source.is_file():
        parser.error('Existing source and new report required')
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    log = ROOT / f'local/unreal-character-{stamp}.log'
    env = os.environ.copy()
    env.update(BULLY_GLB=str(args.source.resolve()), BULLY_UE_REPORT=str(args.report.resolve()),
               BULLY_CHARACTER_FOLDER=args.folder)
    env['BULLY_CHARACTER_ACTIONS'] = '1' if args.actions else '0'
    env['UE-LocalDataCachePath'] = str(ROOT / 'unreal/DerivedDataCache')
    command = ['C:/Program Files/Epic Games/UE_5.8/Engine/Binaries/Win64/UnrealEditor-Cmd.exe',
               str(ROOT / 'unreal/ReaBully.uproject'), '-NullRHI', '-unattended', '-nop4', '-nosplash',
               '-nosound', '-NoCrashDialog', '-DDC=InstalledNoZenLocalFallback',
               '-LocalDataCachePath=' + env['UE-LocalDataCachePath'],
               '-ExecutePythonScript=' + str(ROOT / 'tools/unreal_import_character.py'),
               '-abslog=' + str(log), '-userdir=' + str(ROOT / 'unreal/Saved/User')]
    with log.with_suffix('.stdout.log').open('xb') as stream:
        result = subprocess.run(command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=1200)
    args.report.with_suffix('.run.json').write_text(json.dumps({
        'command': command, 'engine_exit_code': result.returncode, 'log': str(log)}, indent=2), encoding='utf-8')
    passed = not result.returncode and args.report.is_file()
    if passed:
        passed = json.loads(args.report.read_text(encoding='utf-8'))['status'] == 'imported_and_saved'
    print(json.dumps({'passed': passed, 'report': str(args.report), 'log': str(log)}))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())

"""Launch the verified local WorldExploration release using its tested renderer profile."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT/'unreal/ReaBully.uproject'
ENGINE = Path('C:/Program Files/Epic Games/UE_5.8/Engine/Binaries/Win64/UnrealEditor.exe')
RELEASE = ROOT/'evidence/world-release-v06.json'
RUNTIME = ROOT/'unreal/Binaries/Win64/UnrealEditor-ReaBullyRuntime.dll'
WORLD = ROOT/'unreal/Content/Maps/WorldExploration.umap'
CVAR = re.compile(r'([A-Za-z][A-Za-z0-9_.]*)\s+(?:[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\Z')


def renderer_profile(release):
    if release.get('passed') is not True:
        raise RuntimeError('La versione del mondo non ha ancora superato il collaudo.')
    commands = release.get('renderer_commands')
    if not isinstance(commands, list) or not commands:
        raise RuntimeError('Il rapporto di rilascio non contiene il profilo grafico verificato.')
    names = set()
    profile = []
    for command in commands:
        match = CVAR.fullmatch(command) if isinstance(command, str) else None
        if not match:
            raise RuntimeError('Profilo grafico non valido: ogni voce deve contenere una CVar e un valore numerico.')
        name = match.group(1).lower()
        if name in names:
            raise RuntimeError('Il profilo grafico contiene una CVar duplicata.')
        names.add(name)
        if name == 't.maxfps':
            if float(command.split()[1]) != 60:
                raise RuntimeError('Il profilo verificato deve consentire il limite di 60 FPS.')
        else:
            profile.append(command)
    return [*profile, 't.MaxFPS 60']


def verify_release_artifacts(release):
    if hashlib.sha256(RUNTIME.read_bytes()).hexdigest() != release.get('runtime_sha256'):
        raise RuntimeError('Il modulo compilato è cambiato dopo il collaudo della scena.')
    artifacts = release.get('world_asset_hashes')
    if not isinstance(artifacts, dict) or not artifacts:
        raise RuntimeError('Il rapporto non identifica la mappa integrata verificata.')
    root = ROOT.resolve()
    for relative, expected in artifacts.items():
        path = (root/relative).resolve()
        if not path.is_relative_to(root) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError('Un asset della scena è cambiato dopo il collaudo: ' + relative)


def project_processes():
    # Read only. Other Unreal projects are never stopped or reconfigured.
    query = ("$ErrorActionPreference = 'Stop'; Get-CimInstance Win32_Process | "
             "Where-Object { $_.Name -like 'UnrealEditor*' } | "
             "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress")
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', query],
                            capture_output=True, text=True, check=True, timeout=20,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    rows = json.loads(result.stdout) if result.stdout.strip() else []
    if isinstance(rows, dict):
        rows = [rows]
    project_path = str(PROJECT).replace('\\', '/').casefold()
    return [row for row in rows if project_path in (row.get('CommandLine') or '').replace('\\', '/').casefold()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Validate release and command without launching Unreal')
    args = parser.parse_args()
    for path in (ENGINE, PROJECT, RUNTIME, WORLD, RELEASE):
        if not path.is_file():
            raise RuntimeError('File necessario assente: ' + str(path))
    release_bytes = RELEASE.read_bytes()
    release = json.loads(release_bytes.decode('utf-8-sig'))
    profile = renderer_profile(release)
    verify_release_artifacts(release)
    active = project_processes()
    if active:
        ids = ', '.join(str(row['ProcessId']) for row in active)
        raise RuntimeError(f'Rea_Bully e gia aperto o sta elaborando asset (PID {ids}). Chiudi quella sessione prima di avviarne un altra.')
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    log = ROOT/f'local/world-launch-{stamp}.log'
    cache = ROOT/'unreal/DerivedDataCache'
    user = ROOT/'unreal/Saved/User'
    command = [str(ENGINE), str(PROJECT), '/Game/Maps/WorldExploration', '-game',
               '-windowed', '-ResX=1600', '-ResY=900', '-sm6', '-nop4', '-nosplash',
               '-NoCrashDialog', '-DDC=InstalledNoZenLocalFallback',
               '-LocalDataCachePath='+str(cache), '-userdir='+str(user), '-abslog='+str(log),
               '-ExecCmds='+','.join(profile)]
    if args.check:
        print(json.dumps({'status': 'launch_configuration_verified', 'release': str(RELEASE),
                          'renderer_commands': profile, 'command': command}, ensure_ascii=False))
        return 0
    log.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env['UE-LocalDataCachePath'] = str(cache)
    process = subprocess.Popen(command, cwd=ROOT, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    record = {'status': 'interactive_process_started', 'pid': process.pid,
              'started_at_utc': datetime.now(timezone.utc).isoformat(),
              'release': str(RELEASE), 'release_sha256': hashlib.sha256(release_bytes).hexdigest(),
              'runtime_sha256': hashlib.sha256(RUNTIME.read_bytes()).hexdigest(),
              'renderer_commands': profile, 'command': command, 'log': str(log)}
    with log.with_suffix('.json').open('x', encoding='utf-8') as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
    print(f'Avviato Rea_Bully (PID {process.pid}). Log: {log}')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('Avvio non riuscito: ' + str(error), file=sys.stderr)
        raise SystemExit(1)

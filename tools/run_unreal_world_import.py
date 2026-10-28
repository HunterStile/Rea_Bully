"""Resume world model import in bounded, separately logged Unreal processes."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
from prepare_world_import import prepare
from world_import_contracts import verify_checkpoint

ROOT = Path(__file__).resolve().parents[1]
ENGINE = Path('C:/Program Files/Epic Games/UE_5.8/Engine/Binaries/Win64/UnrealEditor-Cmd.exe')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=ROOT/'evidence/world-export.json')
    parser.add_argument('--batch-size', type=int, default=25)
    parser.add_argument('--batches', type=int, default=1)
    parser.add_argument('--only-model', action='append', help='Import only these exact model keys (diagnostic probe)')
    parser.add_argument('--priority-zone', action='append', help='Prefer these zones in order; may be repeated (default tschool)')
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 100 or args.batches < 1:
        parser.error('Positive bounded batches required')
    catalog = json.loads(args.catalog.read_text(encoding='utf-8'))
    ready = [m for m in catalog['models'] if m['status'] == 'exported']
    if args.only_model:
        ready = [m for m in ready if m['model_key'] in args.only_model]
        if {m['model_key'] for m in ready} != set(args.only_model):
            raise ValueError('Requested model is not exported in this catalog')
    priorities = args.priority_zone or ['tschool']
    ready.sort(key=lambda m: (next((i for i, zone in enumerate(priorities) if zone in m['zones']),
                                  len(priorities)), m['model_key']))
    for batch in range(args.batches):
        pending = []
        for model in ready:
            checkpoint = ROOT/'local/world-import/models'/(model['model_key']+'.json')
            if checkpoint.exists():
                verify_checkpoint(model, json.loads(checkpoint.read_text(encoding='utf-8')))
            else:
                pending.append(model)
        selected = pending[:args.batch_size]
        if not selected:
            print(json.dumps({'status': 'all_available_models_imported', 'models': len(ready)}), flush=True)
            return 0
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        records = []
        for model in selected:
            source = Path(model['output'])
            if hashlib.sha256(source.read_bytes()).hexdigest() != model['output_sha256']:
                raise ValueError('Source GLB no longer matches exporter evidence')
            destination = ROOT/'local/world-import/prepared'/(model['model_key']+'.glb')
            record = prepare(source, destination)
            record.update(model_key=model['model_key'], prepared=str(destination),
                          source_bounds_cm=model['bounds_entity_unreal_cm'],
                          asset_folder='/Game/Original/World/Models/'+model['model_key'])
            records.append(record)
        job = ROOT/f'local/world-import/jobs/{stamp}.json'
        job.parent.mkdir(parents=True, exist_ok=True)
        job.write_text(json.dumps({'models': records}, indent=2), encoding='utf-8')
        report = ROOT/f'evidence/world-import-{stamp}.json'
        log = ROOT/f'local/world-import-{stamp}.log'
        env = os.environ.copy()
        env.update(REA_WORLD_IMPORT_JOB=str(job), BULLY_UE_REPORT=str(report))
        env['UE-LocalDataCachePath'] = str(ROOT/'unreal/DerivedDataCache')
        command = [str(ENGINE), str(ROOT/'unreal/ReaBully.uproject'), '/Engine/Maps/Entry', '-NullRHI', '-unattended', '-nop4',
            '-nosplash', '-nosound', '-NoCrashDialog', '-DDC=InstalledNoZenLocalFallback',
            '-LocalDataCachePath='+env['UE-LocalDataCachePath'], '-ExecutePythonScript='+str(ROOT/'tools/unreal_import_world_models.py'),
            '-abslog='+str(log), '-userdir='+str(ROOT/'unreal/Saved/User')]
        print(json.dumps({'batch': batch+1, 'models': len(records), 'remaining': len(pending), 'log': str(log)}), flush=True)
        with log.with_suffix('.stdout.log').open('xb') as stream:
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=1800)
        report.with_suffix('.run.json').write_text(json.dumps({'command': command, 'engine_exit_code': result.returncode,
            'job': str(job), 'log': str(log)}, indent=2), encoding='utf-8')
        if result.returncode or not report.exists() or json.loads(report.read_text())['status'] != 'imported_and_verified':
            print(json.dumps({'status': 'failed', 'report': str(report), 'log': str(log)}), flush=True)
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

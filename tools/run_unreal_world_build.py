"""Build the full original world in sequential bounded Unreal processes.

Successful batches resume only while source inputs, runtime, builder and saved
zone maps still match. Failed reports remain untouched; retries use new paths.
This coordinator assumes its caller owns the single Unreal process slot.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from unreal_assemble_world import validate_batch, validate_index

ROOT = Path(__file__).resolve().parents[1]
CONFIG_KEYS = ('plan', 'npcs', 'ambient', 'props', 'doors', 'collisions',
               'collision_helpers', 'audio_data', 'interactions', 'interaction_import',
               'gestures', 'gesture_import')
RUNTIME = 'unreal/Binaries/Win64/UnrealEditor-ReaBullyRuntime.dll'
BUILDER = 'tools/unreal_build_world.py'
WRAPPER = 'tools/run_unreal_world.py'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def local_path(root, value):
    path = (root / value).resolve()
    require(path.is_relative_to(root), 'Build path escapes this repository')
    return path


def digest(path):
    before = path.stat()
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(block)
    after = path.stat()
    require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
            'Build input changed while hashing: ' + str(path))
    return sha.hexdigest()


def reference(root, path):
    path = local_path(root, path)
    return {'path': path.relative_to(root).as_posix(), 'sha256': digest(path)}


def read_config(root, config_path):
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    require(isinstance(config, dict) and set(config) == set(CONFIG_KEYS),
            'Config requires exactly these input paths: ' + ', '.join(CONFIG_KEYS))
    inputs = {}
    for key in CONFIG_KEYS:
        value = config[key]
        require(isinstance(value, str) and value and not Path(value).is_absolute(),
                'Config paths must be repository-relative strings: ' + key)
        path = local_path(root, value)
        require(path.is_file(), 'Missing build input: ' + key)
        inputs[key] = reference(root, path)
    return inputs


def eligible_zones(root, inputs):
    plan = json.loads((root / inputs['plan']['path']).read_text(encoding='utf-8-sig'))
    zones = [z['name'] for z in plan['zones'] if z.get('placements') and z.get('bounds')]
    require(len(zones) == len(set(zones)) == 85 and len(plan['zones']) == 85,
            'Full build requires 85 distinct zones with imported placements and bounds')
    require(all(isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_]+', name) for name in zones),
            'Zone names cannot contain separators or command delimiters')
    return zones


def fresh_path(base):
    """Preserve both successful and failed engine reports, including orphan runs."""
    if not base.exists() and not base.with_suffix('.run.json').exists():
        return base
    attempt = 1
    while True:
        path = base.with_name(f'{base.stem}-attempt-{attempt:03d}{base.suffix}')
        if not path.exists() and not path.with_suffix('.run.json').exists():
            return path
        attempt += 1


def batch_candidates(work_dir, number):
    pattern = re.compile(r'^batch-' + f'{number:03d}' + r'(?:-attempt-(\d+))?\.json$')
    found = []
    for path in work_dir.glob(f'batch-{number:03d}*.json'):
        match = pattern.fullmatch(path.name)
        if match:
            found.append((int(match.group(1) or 0), path))
    return [path for _, path in sorted(found, reverse=True)]


def run_build(config_path, work_dir, report_path, *, batch_size=3, resume_index=None, root=ROOT, runner=None):
    root = Path(root).resolve()
    require(type(batch_size) is int and 1 <= batch_size <= 85, 'Batch size must be between 1 and 85')
    config_path, work_dir, report_path = [local_path(root, p) for p in (config_path, work_dir, report_path)]
    require(work_dir.is_relative_to(root / 'local') and work_dir != root / 'local',
            'Dedicated batch work directory must be below local/')
    require(report_path.suffix == '.json', 'Final report must be a JSON path')
    require((root / WRAPPER).is_file(), 'Unreal world wrapper is missing')
    config_sha = digest(config_path)
    inputs = read_config(root, config_path)
    runtime_sha, builder_sha = digest(root / RUNTIME), digest(root / BUILDER)
    zones = eligible_zones(root, inputs)
    groups = [zones[offset:offset + batch_size] for offset in range(0, len(zones), batch_size)]
    seeded = {}
    seed_path, seed_sha = None, None
    if resume_index is not None:
        seed_path = local_path(root, resume_index)
        seed_sha = digest(seed_path)
        seed = json.loads(seed_path.read_text(encoding='utf-8-sig'))
        require(seed.get('schema') == 'rea-bully-world-build-batches-v1' and seed.get('inputs') == inputs
                and seed.get('runtime_sha256') == runtime_sha and seed.get('builder_sha256') == builder_sha,
                'Resume index belongs to another build input/runtime/builder batch')
        require(isinstance(seed.get('batches'), list), 'Resume index requires a batch list')
        for batch in seed['batches']:
            names = batch['zones']
            require(names in groups and tuple(names) not in seeded, 'Resume index zone grouping differs or is duplicated')
            validate_batch(root, names, batch['report'], batch['run'], inputs, runtime_sha, builder_sha)
            seeded[tuple(names)] = batch
    work_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    runner = runner or subprocess.run
    flags = [item for key in CONFIG_KEYS for item in ('--' + key.replace('_', '-'), str(root / inputs[key]['path']))]

    def unchanged():
        require(digest(config_path) == config_sha and read_config(root, config_path) == inputs
                and digest(root / RUNTIME) == runtime_sha and digest(root / BUILDER) == builder_sha,
                'Build config, source inputs, runtime or builder changed during this coordinator run')
        require(seed_path is None or digest(seed_path) == seed_sha, 'Resume index changed during this coordinator run')

    def checked_batch(names, path):
        pair = {'zones': names, 'report': reference(root, path),
                'run': reference(root, path.with_suffix('.run.json'))}
        validate_batch(root, names, pair['report'], pair['run'], inputs, runtime_sha, builder_sha)
        return pair

    batches = []
    resumed = 0
    for number, names in enumerate(groups):
        unchanged()
        chosen = seeded.get(tuple(names))
        if chosen:
            validate_batch(root, names, chosen['report'], chosen['run'], inputs, runtime_sha, builder_sha)
            resumed += 1
            print(json.dumps({'status': 'batch_resumed_from_index', 'zones': names,
                              'report': chosen['report']['path']}), flush=True)
        for path in ([] if chosen else batch_candidates(work_dir, number)):
            try:
                chosen = checked_batch(names, path)
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                print(json.dumps({'status': 'checkpoint_rejected', 'report': path.relative_to(root).as_posix(),
                                  'reason': str(error)}), flush=True)
                continue
            resumed += 1
            print(json.dumps({'status': 'batch_resumed', 'zones': names,
                              'report': path.relative_to(root).as_posix()}), flush=True)
            break
        if chosen is None:
            path = fresh_path(work_dir / f'batch-{number:03d}.json')
            command = [sys.executable, str(root / WRAPPER), 'prepare', *flags,
                       '--zones', ','.join(names), '--zone-batch', '--report', str(path)]
            result = runner(command, cwd=root, check=False)
            unchanged()
            require(result.returncode == 0, 'Zone batch failed; preserve diagnostics and retry with a fresh attempt: ' + str(path))
            chosen = checked_batch(names, path)
            print(json.dumps({'status': 'batch_verified', 'zones': names,
                              'report': path.relative_to(root).as_posix()}), flush=True)
        batches.append(chosen)

    unchanged()
    index = {'schema': 'rea-bully-world-build-batches-v1', 'inputs': inputs,
             'runtime_sha256': runtime_sha, 'builder_sha256': builder_sha, 'batches': batches}
    expected_rows = validate_index(index, root=root)
    index_path = work_dir / 'index.json'
    if index_path.exists():
        try:
            same_index = json.loads(index_path.read_text(encoding='utf-8-sig')) == index
        except (OSError, ValueError):
            same_index = False
        if not same_index:
            index_path = fresh_path(index_path)
    if not index_path.exists():
        with index_path.open('x', encoding='utf-8') as stream:
            json.dump(index, stream, indent=2)
            stream.write('\n')
    index_ref = reference(root, index_path)
    final_report = fresh_path(report_path)
    command = [sys.executable, str(root / WRAPPER), 'assemble', *flags,
               '--build-batches', str(index_path), '--report', str(final_report)]
    result = runner(command, cwd=root, check=False)
    unchanged()
    require(reference(root, index_path) == index_ref, 'Batch index changed during assembly')
    require(result.returncode == 0, 'Persistent scene assembly failed; retain diagnostics: ' + str(final_report))
    final = json.loads(final_report.read_text(encoding='utf-8-sig'))
    run_path = final_report.with_suffix('.run.json')
    run = json.loads(run_path.read_text(encoding='utf-8-sig'))
    require(final.get('status') == 'zones_saved' and final.get('map') == '/Game/Maps/WorldExploration'
            and final.get('plan_sha256') == inputs['plan']['sha256'] and final.get('build_inputs') == inputs
            and final.get('build_batches') == index_ref and final.get('runtime_sha256') == runtime_sha
            and final.get('builder_sha256') == builder_sha and final.get('zones') == expected_rows
            and type(run.get('exit_code')) is int and run['exit_code'] == 0
            and run.get('runtime_sha256') == runtime_sha, 'Assembled scene report does not match the verified batch index')
    validate_index(index, root=root)  # Assembling the persistent world must not rewrite zone packages.
    summary = {'status': 'world_scene_build_verified', 'zones': len(zones), 'batches': len(batches),
               'resumed_batches': resumed, 'index': index_ref, 'report': reference(root, final_report),
               'run': reference(root, run_path)}
    print(json.dumps(summary), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--batch-size', type=int, default=3)
    parser.add_argument('--resume-index', type=Path, help='Optional partial/full verified index; reuse its report references without copying evidence')
    args = parser.parse_args()
    run_build(args.config, args.work_dir, args.report, batch_size=args.batch_size, resume_index=args.resume_index)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print('World build stopped: ' + str(error), file=sys.stderr)
        raise SystemExit(1)

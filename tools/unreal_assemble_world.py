"""Assemble a complete world from source-bound saved batches without loading zone worlds.

Validation is standard-library-only and can also be used by the batch coordinator.
Unreal is imported only after every checkpoint, input, executable and map hash passes.
"""
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
INPUT_ENV = {'plan': 'REA_WORLD_PLAN', 'npcs': 'REA_WORLD_NPCS', 'ambient': 'REA_WORLD_AMBIENT_PLAN',
    'props': 'REA_WORLD_PROPS_PLAN', 'doors': 'REA_WORLD_DOORS_PLAN', 'collisions': 'REA_WORLD_COLLISION_PLAN',
    'collision_helpers': 'REA_WORLD_COLLISION_HELPERS', 'audio_data': 'REA_WORLD_AUDIO_REPORT',
    'interactions': 'REA_PROP_INTERACTION_PLAN', 'interaction_import': 'REA_PROP_INTERACTION_IMPORT',
    'gestures': 'REA_PROP_GESTURE_PLAN', 'gesture_import': 'REA_PROP_GESTURE_IMPORT'}
RUNTIME = 'unreal/Binaries/Win64/UnrealEditor-ReaBullyRuntime.dll'
BUILDER = 'tools/unreal_build_world.py'
EXPECTED_ZONES = 85


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def reference(path, root=ROOT):
    path, root = Path(path).resolve(), Path(root).resolve()
    return {'path': path.relative_to(root).as_posix(), 'sha256': sha256(path)}


def checked(ref, root=ROOT):
    """Index references are canonical workspace-relative POSIX file paths."""
    root = Path(root).resolve()
    if not isinstance(ref, dict) or not isinstance(ref.get('path'), str):
        raise ValueError('Invalid batch file reference')
    relative = ref['path']
    pure = PurePosixPath(relative)
    if (not relative or pure.is_absolute() or '\\' in relative or ':' in relative or
            '..' in pure.parts or pure.as_posix() != relative):
        raise ValueError('Batch reference must be a canonical relative path')
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('Batch reference is missing or outside workspace: ' + relative)
    if not re.fullmatch(r'[0-9a-f]{64}', str(ref.get('sha256', ''))) or sha256(path) != ref['sha256']:
        raise ValueError('Batch file hash changed: ' + relative)
    return path


def check_source_references(value, root=ROOT, seen=None):
    """Rehash source references inside input JSON; original asset paths may be absolute."""
    if seen is None:
        seen = set()
    if isinstance(value, dict):
        if isinstance(value.get('path'), str) and re.fullmatch(r'[0-9a-f]{64}', str(value.get('sha256', ''))):
            path = Path(value['path'])
            path = path if path.is_absolute() else Path(root)/path
            key = (str(path.resolve()), value['sha256'])
            if key not in seen:
                if not path.is_file() or sha256(path) != value['sha256']:
                    raise ValueError('Original source reference changed: ' + str(path))
                seen.add(key)
        for child in value.values():
            check_source_references(child, root, seen)
    elif isinstance(value, list):
        for child in value:
            check_source_references(child, root, seen)


def load_inputs(inputs, root=ROOT, *, check_sources=False):
    if not isinstance(inputs, dict) or 'plan' not in inputs or set(inputs)-set(INPUT_ENV):
        raise ValueError('Unknown or missing world build inputs')
    result = {key: read(checked(ref, root)) for key, ref in inputs.items()}
    if check_sources:
        check_source_references(result, root)
    for plan_key, import_key in (('interactions', 'interaction_import'), ('gestures', 'gesture_import')):
        if (plan_key in inputs) != (import_key in inputs):
            raise ValueError('World interaction/gesture plan and import must be paired')
    if 'ambient' in result:
        from world_integration_contracts import require_ambient_npcs
        require_ambient_npcs(result['ambient'], result.get('npcs'))
    return result


def eligible_zones(plan):
    zones = plan.get('zones', [])
    names = [z.get('name') for z in zones]
    if any(not isinstance(n, str) or not re.fullmatch(r'[A-Za-z0-9_]+', n) for n in names) or len(set(names)) != len(names):
        raise ValueError('World plan contains duplicate or invalid zone names')
    eligible = [z for z in zones if z.get('placements') and z.get('bounds')]
    for zone in eligible:
        for key in ('min', 'max'):
            values = zone['bounds'].get(key, [])
            if len(values) != 3 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
                raise ValueError('World zone bounds are not finite XYZ')
        if any(a > b for a, b in zip(zone['bounds']['min'], zone['bounds']['max'])):
            raise ValueError('World zone bounds are inverted')
    return eligible


def integration_counts(rows, *, audio=False, start_policy='zone_batch_only'):
    return {'npcs': sum(len(r['npcs']) for r in rows), 'doors': sum(len(r['doors']) for r in rows),
        'original_collision_instances': sum(len(r['original_collisions']) for r in rows),
        'original_collision_helpers': sum(bool(c['collision_only_helper']) for r in rows for c in r['original_collisions']),
        'audio_waves': 207 if audio else 0, 'props': sum(len(r['props']) for r in rows),
        'prop_interactions': sum(len(r['prop_interactions']) for r in rows),
        'prop_gestures': sum(len(r['prop_gestures']) for r in rows), 'start_policy': start_policy}


def validate_batch(root, zones, report_ref, run_ref, inputs, runtime_sha256, builder_sha256):
    """Validate one exact completed batch; never infer success from saved maps alone."""
    root = Path(root).resolve()
    if sha256(root/RUNTIME) != runtime_sha256 or sha256(root/BUILDER) != builder_sha256:
        raise ValueError('Batch runtime or builder no longer matches current files')
    documents = load_inputs(inputs, root)
    plan = documents['plan']
    eligible = {z['name']: z for z in eligible_zones(plan)}
    if not isinstance(zones, list) or not zones or len(set(zones)) != len(zones) or set(zones)-set(eligible):
        raise ValueError('Batch zones must be distinct eligible source zones')
    report, run = read(checked(report_ref, root)), read(checked(run_ref, root))
    if (report.get('status') != 'zone_assets_saved' or report.get('map') is not None or
            report.get('build_inputs') != inputs or report.get('builder_sha256') != builder_sha256 or
            report.get('runtime_sha256') != runtime_sha256 or report.get('plan_sha256') != inputs['plan']['sha256']):
        raise ValueError('Batch report source/runtime/builder provenance differs')
    if (type(run.get('exit_code')) is not int or run['exit_code'] != 0 or
            run.get('runtime_sha256') != runtime_sha256):
        raise ValueError('Batch run did not succeed with the current runtime')
    for key, field in (('props', 'prop_plan_sha256'), ('interactions', 'interaction_plan_sha256'), ('gestures', 'gesture_plan_sha256')):
        if report.get(field) != inputs.get(key, {}).get('sha256'):
            raise ValueError('Batch overlay source hash differs: ' + key)
    rows = report.get('zones', [])
    names = [r.get('zone') for r in rows]
    if len(names) != len(zones) or len(set(names)) != len(names) or set(names) != set(zones):
        raise ValueError('Batch report does not cover its exact zone selection')
    for row in rows:
        name = row['zone']
        source = eligible[name]
        if (row.get('map') != '/Game/World/Zones/'+name or row.get('source_placements') != source['source_count'] or
                row.get('visible_placements') != len(source['placements']) or
                row.get('duplicates') != source['duplicates'] or row.get('runtime_enabled') != source['runtime_enabled']):
            raise ValueError('Saved zone record differs from source plan: ' + name)
        for field in ('components', 'pending', 'npcs', 'doors', 'props', 'prop_interactions', 'prop_gestures', 'original_collisions'):
            if not isinstance(row.get(field), list):
                raise ValueError('Incomplete zone integration record: ' + field)
        checked({'path': 'unreal/Content/World/Zones/'+name+'.umap', 'sha256': row.get('map_sha256')}, root)
        if 'ambient' in documents:
            expected = {p['ped_id']: p for p in documents['ambient']['placements'] if p['zone'] == name}
            actual = {n['ped_id']: n for n in row['npcs']}
            if len(actual) != len(row['npcs']) or set(actual) != set(expected):
                raise ValueError('Saved zone lost or duplicated its named NPC cast: ' + name)
            for ped_id, placement in expected.items():
                if actual[ped_id].get('source') != placement['source_reference'] or actual[ped_id].get('route_points') != len(placement['points']):
                    raise ValueError('Saved NPC source or route differs from placement plan')
    if report.get('integration') != integration_counts(rows):
        raise ValueError('Batch aggregate integration counts differ from saved rows')
    return report


def validate_index(index, root=ROOT):
    """Return the 85 saved zone records in source-plan order after all gates pass."""
    if index.get('schema') != 'rea-bully-world-build-batches-v1':
        raise ValueError('Unsupported world batch index schema')
    documents = load_inputs(index.get('inputs'), root, check_sources=True)
    expected = eligible_zones(documents['plan'])
    if len(expected) != EXPECTED_ZONES:
        raise ValueError('Final assembly requires exactly 85 eligible source zones')
    rows, seen = [], set()
    batches = index.get('batches')
    if not isinstance(batches, list) or not batches:
        raise ValueError('World assembly has no completed batches')
    for batch in batches:
        report = validate_batch(root, batch['zones'], batch['report'], batch['run'], index['inputs'],
                                index['runtime_sha256'], index['builder_sha256'])
        for row in report['zones']:
            if row['zone'] in seen:
                raise ValueError('World batch index duplicates a zone')
            seen.add(row['zone'])
            rows.append(row)
    if seen != {z['name'] for z in expected}:
        raise ValueError('World batch index is missing eligible source zones')
    by_name = {r['zone']: r for r in rows}
    return [by_name[z['name']] for z in expected]


def soft_zone_manifest(unreal, zones):
    """Use native text import; Python's object getter would synchronously load Level.

UE5.8 PyConversion.cpp:877 accepts UObject, not SoftObjectPath, for this setter;
its getter at 1144 calls LoadObjectPropertyValue. PyWrapperStruct.cpp:1292 and
PropertySoftObjectPtr.cpp:138 support native ImportText without loading a World.
"""
    manifest = []
    for zone in zones:
        name = zone['name']
        item = unreal.ReaBullyWorldZone()
        object_path = '/Game/World/Zones/'+name+'.'+name
        if not item.import_text('(Level="'+object_path+'")') or object_path not in item.export_text():
            raise ValueError('Unable to bind unloaded source zone path: ' + name)
        for key, value in {'name': name, 'display_name': name,
                'minimum': unreal.Vector(*zone['bounds']['min']), 'maximum': unreal.Vector(*zone['bounds']['max']),
                'exterior': zone['runtime_enabled'], 'always_loaded': zone['always_loaded'], 'area': zone['area']}.items():
            item.set_editor_property(key, value)
        manifest.append(item)
    return manifest


def main():
    sys.path.insert(0, str(ROOT/'tools'))
    report_path = Path(os.environ['BULLY_UE_REPORT'])
    if report_path.exists():
        raise FileExistsError('New final assembly report required')
    index_path = Path(os.environ['REA_WORLD_BUILD_BATCHES'])
    index_ref = reference(index_path)
    index = read(index_path)
    environment_inputs = {key: reference(os.environ[name]) for key, name in INPUT_ENV.items() if os.environ.get(name)}
    if environment_inputs != index.get('inputs'):
        raise ValueError('Assembly environment differs from completed batch inputs')
    rows = validate_index(index)
    documents = load_inputs(index['inputs'])
    plan = documents['plan']
    if reference(index_path) != index_ref:
        raise ValueError('Batch index changed during validation')
    import unreal
    from unreal_world_scene import save_scene
    manifest = soft_zone_manifest(unreal, eligible_zones(plan))
    start_policy = save_scene(manifest, documents.get('ambient'), documents.get('audio_data'), index['inputs']['plan']['sha256'])
    result = {'status': 'zones_saved', 'plan_sha256': index['inputs']['plan']['sha256'],
        'prop_plan_sha256': index['inputs'].get('props', {}).get('sha256'),
        'interaction_plan_sha256': index['inputs'].get('interactions', {}).get('sha256'),
        'gesture_plan_sha256': index['inputs'].get('gestures', {}).get('sha256'),
        'map': '/Game/Maps/WorldExploration', 'zones': rows,
        'integration': integration_counts(rows, audio=bool(documents.get('audio_data')), start_policy=start_policy),
        'limitations': plan['limitations'], 'engine': unreal.SystemLibrary.get_engine_version(),
        'build_batches': index_ref, 'build_inputs': index['inputs'], 'builder_sha256': index['builder_sha256'],
        'runtime_sha256': index['runtime_sha256'], 'assembler': reference(__file__),
        'scene_helper': reference(ROOT/'tools/unreal_world_scene.py'),
        'assembly_policy': 'All85 saved map hashes checked; native soft Level paths avoid loading zone worlds during final assembly.'}
    with report_path.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    unreal.log('REA_WORLD_ASSEMBLED 85 source-bound saved zones')


if __name__ == '__main__':
    main()

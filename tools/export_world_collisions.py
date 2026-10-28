"""Export selected original COL3 triangles, with byte proofs and no physics run.

One GLB mesh per exact model key; int16 XYZ / 128 -> glTF (X,Z,-Y) metres.
Surfaces and flags remain source metadata. Mixed primitive bodies never authorize
replacement of the existing visual collider until boxes/spheres/lines are recovered.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import struct

from catalog_collision_models import parse_models, sha
from export_nif import Glb
from validate_audio_waves import lower_priority

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'evidence/collision-models-v05.json'
LEGACY = ROOT / 'evidence/collision-legacy-analysis-v05.json'
WORLD = ROOT / 'local/world-integration-v06/static-catalog.json'
DEFAULT_REPORT = ROOT / 'evidence/world-collisions-export-v06.json'
QUANTUM = 1 / 128


def require(condition, message):
    if not condition:
        raise ValueError(message)


def reference(path, raw=None):
    path = Path(path).resolve()
    raw = path.read_bytes() if raw is None else raw
    return {'path': str(path), 'sha256': sha(raw)}


def checked(record):
    raw = Path(record['path']).read_bytes()
    require(sha(raw) == record['sha256'], 'Changed collision source: ' + record['path'])
    return raw


def bounded_new(path, parent):
    path, parent = Path(path).resolve(), Path(parent).resolve()
    require(path != parent and path.is_relative_to(parent), 'Output must stay inside ' + str(parent))
    require(not path.exists(), 'New output path required: ' + str(path))
    return path


def native_scale_proof(legacy):
    raw = checked(legacy['sources']['native_analysis_executable'])
    for source in legacy['sources']['retained_native']:
        checked(source)
    for anchor in legacy['native_abi']['anchors']:
        start, size = anchor['file_offset'], anchor['bytes']
        require(sha(raw[start:start + size]) == anchor['sha256'], 'Retained native anchor bytes changed')
    scale = legacy['native_abi']['legacy_float_quantization_multiplier']
    require(scale['value'] == 128.0, 'Unapproved native collision quantization')
    pe = struct.unpack_from('<I', raw, 0x3c)[0]
    require(raw[:2] == b'MZ' and raw[pe:pe + 4] == b'PE\0\0', 'Expected retained PE executable')
    sections = struct.unpack_from('<H', raw, pe + 6)[0]
    optional_size = struct.unpack_from('<H', raw, pe + 20)[0]
    optional = pe + 24
    require(struct.unpack_from('<H', raw, optional)[0] == 0x10b, 'Expected native PE32')
    image_base = struct.unpack_from('<I', raw, optional + 28)[0]
    rva = int(scale['va'], 16) - image_base
    offsets = []
    for i in range(sections):
        size, virtual, raw_size, offset = struct.unpack_from('<4I', raw, optional + optional_size + i * 40 + 8)
        if virtual <= rva and rva + 8 <= virtual + raw_size:
            offsets.append(offset + rva - virtual)
    require(len(offsets) == 1, 'Native scale constant has no unique file extent')
    value = raw[offsets[0]:offsets[0] + 8]
    require(struct.unpack('<d', value)[0] == 128.0 and value.hex() == scale['bytes_hex']
            and sha(value) == scale['sha256'], 'Native scale constant bytes differ')
    corroboration = legacy['col3_divide_by_128_corroboration']
    require(corroboration['divisor_tested'] == 128 and
            corroboration['tolerance_in_declared_bound_units'] == QUANTUM, 'COL3 scale corroboration changed')
    return {'native_legacy_multiplier': scale, 'native_constant_file_offset': offsets[0],
            'col3_interpretation': corroboration['classification'],
            'policy': 'Explicit authorized /128 COL3 interpretation with per-model AABB containment; physics remains pending'}


def section_bytes(data, section):
    start, size = section['offset'], section['bytes']
    require(start >= 0 and size >= 0 and start + size <= len(data), 'Collision section exceeds source')
    raw = data[start:start + size]
    require(sha(raw) == section['sha256'], 'Collision section hash changed')
    return raw


def triangle_geometry(data, model):
    require(model['tag'] == 'COL3' and model['sections']['triangles']['count'] > 0, 'Expected COL3 triangles')
    vertices = list(struct.iter_unpack('<3h', section_bytes(data, model['sections']['vertices_quantized'])))
    faces_raw = section_bytes(data, model['sections']['triangles'])
    stride = model['sections']['triangles']['stride']
    require(stride in (8, 12), 'Unsupported original COL3 triangle stride')
    faces = [struct.unpack_from('<3HBB', faces_raw, offset) for offset in range(0, len(faces_raw), stride)]
    require(len(vertices) == model['sections']['vertices_quantized']['count']
            and len(faces) == model['sections']['triangles']['count'], 'COL3 section count mismatch')
    bounds = model['bounds_raw']
    require(bounds['ordered'], 'Unordered COL3 declared AABB')
    points = [(x / 128, y / 128, z / 128) for x, y, z in vertices]
    require(all(bounds['minimum'][axis] - QUANTUM <= row[axis] <= bounds['maximum'][axis] + QUANTUM
                for row in points for axis in range(3)), 'COL3 frame exceeds declared AABB by more than 1/128')
    require(all(max(face[:3]) < len(vertices) for face in faces), 'COL3 face references absent vertex')
    groups = defaultdict(list)
    flags = []
    zero_area = []
    for i, (a, b, c, surface, flag) in enumerate(faces):
        groups[str(surface)].append(i)
        flags.append(flag)
        p, q, r = vertices[a], vertices[b], vertices[c]
        u, v = [q[k] - p[k] for k in range(3)], [r[k] - p[k] for k in range(3)]
        if (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]) == (0, 0, 0):
            zero_area.append(i)
    counts = {str(key): count for key, count in model['triangle_surface_ids_raw'].items()}
    require({key: len(value) for key, value in groups.items()} == counts, 'Original surface byte counts differ')
    referenced = sorted({v for face in faces for v in face[:3]})
    unreal = [(points[i][0]*100, -points[i][1]*100, points[i][2]*100) for i in referenced]
    return {'positions': [(x, z, -y) for x, y, z in points], 'triangles': [f[:3] for f in faces],
            'surface_face_indices': dict(groups), 'triangle_flags': flags, 'zero_area_source_faces': zero_area,
            'bounds_unreal_cm': {'min': [min(p[k] for p in unreal) for k in range(3)],
                                 'max': [max(p[k] for p in unreal) for k in range(3)]}}


def load_sources(world_path=WORLD):
    world_raw = Path(world_path).read_bytes()
    world = json.loads(world_raw)
    legacy_raw = LEGACY.read_bytes()
    legacy = json.loads(legacy_raw)
    require(Path(legacy['sources']['collision_catalog']['path']).resolve() == CATALOG.resolve(),
            'Legacy proof references another collision catalog')
    catalog_raw = checked(legacy['sources']['collision_catalog'])
    catalog = json.loads(catalog_raw)
    scale = native_scale_proof(legacy)
    manifest = json.loads(checked(catalog['sources']['archive_manifest']))
    require(world['source_catalog_sha256'] == catalog['sources']['world_coverage']['sha256'],
            'World and collision identity catalogs differ')
    checked(catalog['sources']['world_coverage'])
    inventory_path = ROOT / 'evidence/local_inventory-restored.json'
    inventory = json.loads(inventory_path.read_text())
    game = Path(inventory['game_root']).resolve()
    original = {entry['path']: entry for entry in inventory['files']}
    for kind in ('image', 'directory'):
        source = manifest['source'][kind]
        path = Path(source['path']).resolve()
        restored = original[path.relative_to(game).as_posix()]
        require(source['sha256'] == restored['sha256'] and source['size_bytes'] == restored['bytes']
                and path.stat().st_size == restored['bytes'], 'World archive identity differs from restored inventory')
    require(world['source_world_sha256'] == manifest['source']['image']['sha256']
            == catalog['sources']['original_archive']['sha256'], 'World archive identity mismatch')
    directory = checked(manifest['source']['directory'])
    by_index = {entry['index']: entry for entry in manifest['entries']}
    keys = {p['model_key'] for zone in world['zones'] for p in zone['placements']}
    definitions = {m['model_key']: m for m in world['models']}
    deny = {(o['source']['sha256'], o['model_offset']) for o in legacy['col3_divide_by_128_corroboration']['outliers']}
    models, pending, seen = [], [], set()
    with Path(manifest['source']['image']['path']).open('rb') as archive:
        for file in catalog['files']:
            relevant = [(m, sorted(keys.intersection(m['world_model_candidates']))) for m in file['models']]
            relevant = [(m, matches) for m, matches in relevant if matches]
            if not relevant:
                continue
            data = checked(file['source'])
            entry = by_index[file['source']['archive_index']]
            path = Path(file['source']['path']).resolve()
            expected_path = Path(catalog['sources']['archive_manifest']['path']).parent / entry['path']
            require(path == expected_path.resolve() and entry['verified'] and
                    len(data) == entry['allocated_bytes'] == file['source']['allocated_bytes'] and
                    sha(data) == entry['extracted_sha256'] == entry['source_extent_sha256'], 'COL extraction proof mismatch')
            offset, sectors, name = struct.unpack_from('<II24s', directory, entry['index'] * 32)
            require(offset * 2048 == entry['offset_bytes'] == file['source']['archive_offset']
                    and sectors * 2048 == len(data) and name.split(b'\0', 1)[0].decode('ascii') == entry['name'],
                    'Original COL directory entry mismatch')
            archive.seek(entry['offset_bytes'])
            require(archive.read(len(data)) == data, 'Original IMG extent differs from COL source')
            parsed = json.loads(json.dumps(parse_models(data)))
            require(parsed['models'] == [{k: v for k, v in m.items() if k != 'world_model_candidates'} for m in file['models']],
                    'Fresh COL parsing differs from retained catalog')
            for model, matches in relevant:
                for key in matches:
                    require(key not in seen, 'Ambiguous multiple original collision bodies for ' + key)
                    seen.add(key)
                    definition = definitions[key]
                    lookup = model['native_lookup']
                    require((lookup['kind'] == 'id' and lookup['value'] == definition['model_id']) or
                            (lookup['kind'] == 'name' and lookup['value'].casefold() == definition['name'].casefold()),
                            'COL native identity differs from model')
                    if model['tag'] != 'COL3' or not model['sections']['triangles']['count']:
                        pending.append({'model_key': key, 'reason': 'no_supported_COL3_triangle_body', 'source_collision': model})
                        continue
                    if (file['source']['sha256'], model['offset']) in deny:
                        pending.append({'model_key': key, 'reason': 'known_source_frame_anomaly', 'source_collision': model})
                        continue
                    try:
                        geometry = triangle_geometry(data, model)
                    except ValueError as exc:
                        pending.append({'model_key': key, 'reason': str(exc), 'source_collision': model})
                        continue
                    mixed = {name: model['sections'][name]['count'] for name in ('spheres', 'boxes', 'lines_uninterpreted')}
                    models.append({'model_key': key, 'model_id': definition['model_id'], 'name': definition['name'],
                        'source_col': file['source'], 'source_collision': model,
                        'original_img_extent_reverified': True, 'missing_primitive_counts': mixed,
                        'replace_visual_collision': not any(mixed.values()), 'geometry': geometry})
    pending.extend({'model_key': key, 'reason': 'no_original_collision_identity_match'} for key in sorted(keys - seen))
    sources = {'world_catalog': reference(world_path, world_raw), 'collision_catalog': reference(CATALOG, catalog_raw),
        'native_legacy_analysis': reference(LEGACY, legacy_raw), 'inventory': reference(inventory_path),
        'archive_manifest': catalog['sources']['archive_manifest'], 'native_scale_proof': scale,
        'original_img': {**manifest['source']['image'], 'fresh_whole_file_hash': False,
                         'selected_extents_rechecked': True}}
    return models, pending, sources


def export(world_path=WORLD, output=ROOT / 'local/converted/world-collisions-v06', report=DEFAULT_REPORT):
    priority = lower_priority()
    output = bounded_new(output, ROOT / 'local/converted')
    report = bounded_new(report, ROOT / 'evidence')
    models, pending, sources = load_sources(world_path)
    output.mkdir(parents=True)
    results = []
    for model in models:
        geometry = model['geometry']
        metadata = {k: v for k, v in model.items() if k != 'geometry'}
        metadata.update(surface_face_indices=geometry['surface_face_indices'],
                        triangle_flags=geometry['triangle_flags'], zero_area_source_faces=geometry['zero_area_source_faces'])
        glb = Glb()
        glb.json['asset']['generator'] = 'Rea_Bully original COL3 triangle exporter /128'
        glb.json['extras'] = {'source_collision': metadata,
                             'coordinate_conversion': 'Original int16 XYZ /128 -> glTF(X,Z,-Y) metres',
                             'physics_or_surface_semantics_validated': False}
        positions = glb.accessor(geometry['positions'], 'f', 'VEC3', 5126)
        indices = glb.accessor([(v,) for face in geometry['triangles'] for v in face], 'H', 'SCALAR', 5123)
        glb.json['meshes'] = [{'name': model['model_key'], 'primitives': [{'attributes': {'POSITION': positions}, 'indices': indices, 'mode': 4}]}]
        glb.json['nodes'] = [{'name': model['model_key'], 'mesh': 0}]
        glb.json['scenes'][0]['nodes'] = [0]
        target = output / (model['model_key'] + '.glb')
        glb.write(target)
        results.append({**metadata, 'output': str(target), 'output_sha256': sha(target.read_bytes()),
            'vertices': len(geometry['positions']), 'triangles': len(geometry['triangles']),
            'bounds_unreal_cm': geometry['bounds_unreal_cm'],
            'status': 'source_triangles_exported_engine_pending'})
    result = {'schema': 'rea-bully-world-collision-export-v1', 'status': 'source_triangles_exported_engine_pending',
        'process_priority': priority, 'sources': sources, 'models': results, 'pending': pending,
        'summary': {'models': len(results), 'triangles': sum(m['triangles'] for m in results),
                    'pure_triangle_models': sum(m['replace_visual_collision'] for m in results),
                    'mixed_primitive_models': sum(not m['replace_visual_collision'] for m in results),
                    'pending': len(pending)},
        'limitations': ['No Unreal or physics was executed.',
            'Box/sphere/line bodies remain pending; mixed bodies do not replace visual collision.',
            'Source triangle order, surface bytes and flags retained; Chaos cooked face order and native surface semantics unvalidated.',
            'Unreferenced source vertices and zero-area faces retained in GLB; no geometry repair.',
            'Only the explicitly selected world catalog is exported; known source-frame anomalies remain excluded.']}
    with report.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=WORLD)
    parser.add_argument('--output', type=Path, default=ROOT / 'local/converted/world-collisions-v06')
    parser.add_argument('--report', type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    print(json.dumps(export(args.catalog, args.output, args.report)['summary']))

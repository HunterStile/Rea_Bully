"""Read source collision identities and COL3 primitives without enabling physics.

Layout follows retained Steam loader 0x42c8e0 -> 0x42bcb0. COL2/COLL payloads
remain explicitly pending. Quantized vertices and surface IDs stay raw: this
catalog does not assume their world scale, placement or audio interpretation.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Reader:
    def __init__(self, data, base):
        self.data, self.base, self.position = data, base, 0

    def take(self, size):
        if size < 0 or self.position+size > len(self.data):
            raise ValueError('COL primitive exceeds its declared model extent')
        value = self.data[self.position:self.position+size]
        self.position += size
        return value

    def integer(self, maximum):
        value, = struct.unpack('<I', self.take(4))
        if value > maximum:
            raise ValueError('COL count exceeds supported native signed field')
        return value

    def section(self, count, stride):
        offset = self.base+self.position
        data = self.take(count*stride)
        return data, {'offset': offset, 'bytes': len(data), 'count': count,
                      'stride': stride, 'sha256': sha(data)}


def finite(values):
    if not all(math.isfinite(value) for value in values):
        raise ValueError('COL has nonfinite primitive coordinates')


def col3_payload(data, base, version, flags):
    if version not in (3, 4) or flags not in (1, 2, 3):
        raise ValueError('Unverified COL3 version/flags')
    reader = Reader(data, base)
    bounds = struct.unpack('<12f', reader.take(48))
    finite((*bounds[:7], *bounds[8:11]))
    sections, primitive_surfaces = {}, Counter()
    count = reader.integer(127)
    spheres, sections['spheres'] = reader.section(count, 20)
    for x, y, z, radius, surface, piece, unknown in struct.iter_unpack('<4fBBH', spheres):
        finite((x, y, z, radius))
        if radius < 0:
            raise ValueError('COL has a negative sphere radius')
        primitive_surfaces[surface] += 1
    count = reader.integer(127)
    _, sections['lines_uninterpreted'] = reader.section(count, 32)
    count = reader.integer(127)
    boxes, sections['boxes'] = reader.section(count, 36)
    for offset in range(0, len(boxes), 36):
        values = struct.unpack_from('<3f4x3f4xBBH', boxes, offset)
        finite(values[:6])
        primitive_surfaces[values[6]] += 1
    count = reader.integer(2**31-1)
    vertices, sections['vertices_quantized'] = reader.section(count, 6)
    # The native loader rounds the byte size of the vertex array to a dword.
    padding = reader.take((-len(vertices)) % 4)
    vertex_values = list(struct.iter_unpack('<3h', vertices))
    vertex_bounds = {'min': [min(row[i] for row in vertex_values) for i in range(3)],
                     'max': [max(row[i] for row in vertex_values) for i in range(3)]} if vertex_values else None
    vertex_count = count
    count = reader.integer(32767)
    stride = 12 if (version, flags) == (3, 3) else 8
    faces, sections['triangles'] = reader.section(count, stride)
    face_surfaces, face_flags, degenerate = Counter(), Counter(), 0
    for offset in range(0, len(faces), stride):
        a, b, c, surface, flag = struct.unpack_from('<3HBB', faces, offset)
        if max(a, b, c) >= vertex_count:
            raise ValueError('COL triangle references an absent vertex')
        face_surfaces[surface] += 1
        face_flags[flag] += 1
        degenerate += len({a, b, c}) < 3
    if reader.position < len(data) and data[reader.position:reader.position+4] == bytes.fromhex('33cb5def'):
        offset = base+reader.position
        header = reader.take(40)
        nodes, = struct.unpack_from('<I', header, 36)
        node_data = reader.take(nodes*12)
        sections['partition_tree_uninterpreted'] = {'offset': offset, 'bytes': 40+len(node_data),
            'node_count': nodes, 'sha256': sha(header+node_data)}
    if reader.position < len(data) and data[reader.position:reader.position+4] == b'LIMK':
        offset = base+reader.position
        header = reader.take(8)
        links = reader.take(count*12)
        sections['limk_uninterpreted'] = {'offset': offset, 'bytes': 8+len(links),
            'header_word_raw': struct.unpack_from('<I', header, 4)[0],
            'triangle_records': count, 'sha256': sha(header+links)}
    if reader.position != len(data):
        raise ValueError('Unconsumed COL3 model bytes')
    return {'status': 'col3_metadata_verified_pending_coordinate_and_runtime_validation',
        'bounds_raw': {'sphere_center': list(bounds[:3]), 'sphere_radius': bounds[3],
            'minimum': list(bounds[4:7]), 'maximum': list(bounds[8:11]),
            'ordered': all(bounds[4+i] <= bounds[8+i] for i in range(3))},
        'sections': sections, 'quantized_vertex_bounds_raw': vertex_bounds,
        'vertex_alignment_padding_hex': padding.hex(), 'triangle_surface_ids_raw': dict(face_surfaces),
        'primitive_surface_ids_raw': dict(primitive_surfaces), 'triangle_flags_raw': dict(face_flags),
        'degenerate_index_triangles': degenerate}


def parse_models(data):
    offset, models, ignored_paths = 0, [], []
    while offset < len(data):
        if not any(data[offset:]):
            break
        if offset+8 > len(data):
            raise ValueError('Truncated COL container header')
        tag, size = struct.unpack_from('<4sI', data, offset)
        end = offset+8+size
        if end > len(data):
            raise ValueError('COL model extends beyond archive extent')
        if tag == b'PATH':
            ignored_paths.append({'offset': offset, 'bytes': 8+size, 'sha256': sha(data[offset:end])})
            offset = end
            continue
        if tag not in (b'COLL', b'COL2', b'COL3'):
            raise ValueError('Unknown nonzero COL container tag')
        prefix = 4 if tag != b'COLL' else 0
        if size < prefix+24:
            raise ValueError('Truncated COL model identity')
        if tag == b'COL3':
            version, flags = struct.unpack_from('<2H', data, offset+8)
        elif tag == b'COL2':
            version, = struct.unpack_from('<I', data, offset+8)
            flags = 1
        else:
            version, flags = 0, 1
        name_offset = offset+8+prefix
        name_data = data[name_offset:name_offset+20]
        if b'\0' not in name_data:
            raise ValueError('Unterminated COL model name')
        name = name_data.split(b'\0', 1)[0].decode('ascii')
        model_id, = struct.unpack_from('<i', data, name_offset+20)
        body_offset = name_offset+24
        result = {'offset': offset, 'bytes': end-offset, 'sha256': sha(data[offset:end]),
            'tag': tag.decode('ascii'), 'version': version, 'flags': flags,
            'name': name, 'model_id_raw': model_id,
            'native_lookup': {'kind': 'name', 'value': name} if name else {'kind': 'id', 'value': model_id},
            'body_offset': body_offset, 'body_bytes': end-body_offset, 'runtime_integrated': False}
        if tag == b'COL3':
            result.update(col3_payload(data[body_offset:end], body_offset, version, flags))
        else:
            result['status'] = 'identity_verified_payload_decoder_pending'
        models.append(result)
        offset = end
    return {'models': models, 'path_chunks_ignored_by_native_loader': ignored_paths,
            'logical_bytes': offset, 'zero_padding_bytes': len(data)-offset}


def source_record(path):
    return {'path': str(path), 'sha256': sha(path.read_bytes())}


def catalog(world, coverage_path):
    manifest_path = world/'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    coverage = json.loads(coverage_path.read_text())
    by_id, by_name = defaultdict(list), defaultdict(list)
    for model in coverage['models']:
        by_id[model['model_id']].append(model['model_key'])
        by_name[model['name'].casefold()].append(model['model_key'])
    records, totals, formats, surfaces = [], Counter(), Counter(), Counter()
    image_path = Path(manifest['source']['image']['path'])
    with image_path.open('rb') as archive:
        for entry in manifest['entries']:
            if not entry['name'].casefold().endswith('.col'):
                continue
            path = (world/entry['path']).resolve()
            if world.resolve() not in path.parents:
                raise ValueError('COL source escapes indexed extraction')
            data = path.read_bytes()
            if not entry['verified'] or sha(data) != entry['extracted_sha256'] or len(data) != entry['allocated_bytes']:
                raise ValueError('COL differs from extraction manifest')
            archive.seek(entry['offset_bytes'])
            if archive.read(entry['allocated_bytes']) != data:
                raise ValueError('COL differs from its original IMG extent')
            record = parse_models(data)
            record['source'] = {'path': str(path), 'sha256': sha(data), 'archive_index': entry['index'],
                'archive_offset': entry['offset_bytes'], 'allocated_bytes': entry['allocated_bytes'],
                'original_extent_byte_match': True}
            for model in record['models']:
                lookup = model['native_lookup']
                candidates = by_name.get(lookup['value'].casefold(), []) if lookup['kind']=='name' else by_id.get(lookup['value'], [])
                model['world_model_candidates'] = sorted(set(candidates))
                totals['models'] += 1
                formats[f"{model['tag']}/v{model['version']}/flags{model['flags']}"] += 1
                if 'sections' in model:
                    totals['col3_models_parsed'] += 1
                    for name, section in model['sections'].items():
                        totals[name] += section.get('count', 1)
                    surfaces.update(model['triangle_surface_ids_raw'])
                else:
                    totals['payloads_pending'] += 1
            records.append(record)
    native = ROOT/'local/native/decompiled/full-20261020/functions'
    return {'schema_version': 1, 'status': 'source_collision_metadata_pending_engine_integration',
        'summary': {'files': len(records), **dict(totals), 'formats': dict(formats),
            'triangle_surface_ids_raw': dict(surfaces)},
        'sources': {'archive_manifest': source_record(manifest_path), 'world_coverage': source_record(coverage_path),
            'original_archive': {**manifest['source']['image'], 'fresh_full_archive_hash': False,
                'verification': 'Every selected COL extent compared byte-for-byte with this IMG.'},
            'native_loader': [source_record(native/(address+'.c')) for address in ('0x42c8e0','0x42c6c0','0x42bcb0','0x42aac0')]},
        'limitations': ['No original collision actor or physics shape has been enabled.',
            'COL2/COLL bodies, line semantics and optional partition/LIMK behavior remain pending.',
            'Raw int16 vertices have no assumed scale or placement conversion in this catalog.',
            'Surface bytes are not mapped to audio, friction or gameplay by matching labels.',
            'World model matches are source identity candidates; placement and shape equivalence require validation.'],
        'files': records, 'ready_for_unreal': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--world', type=Path, default=ROOT/'local/extracted/Stream/World')
    parser.add_argument('--coverage', type=Path, default=ROOT/'evidence/world-coverage.json')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        raise ValueError('A new collision report is required')
    result = catalog(args.world, args.coverage)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result['summary']))


if __name__ == '__main__':
    main()

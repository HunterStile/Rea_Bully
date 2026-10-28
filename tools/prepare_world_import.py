"""Create stable UE names while preserving every GLB binary geometry/texture byte."""
import hashlib
import json
from pathlib import Path
import struct


def prepare(source, destination, consolidate=True):
    source, destination = Path(source), Path(destination)
    raw = source.read_bytes()
    magic, version, total = struct.unpack_from('<III', raw)
    if (magic, version, total) != (0x46546C67, 2, len(raw)):
        raise ValueError('Invalid source GLB')
    size, kind = struct.unpack_from('<II', raw, 12)
    if kind != 0x4E4F534A:
        raise ValueError('Expected JSON chunk')
    doc = json.loads(raw[20:20+size])
    source_shape_count = len(doc.get('meshes', []))
    original_names = {'meshes': [], 'materials': []}
    for category, prefix in [('meshes', 'REA_Shape'), ('materials', 'REA_Mat')]:
        for index, entry in enumerate(doc.get(category, [])):
            original_names[category].append(entry.get('name'))
            entry['name'] = f'{prefix}_{index:04d}'
    groups = []
    if consolidate:
        # Entity-space exports already bake every shape into the same frame.
        # Join primitive references (never geometry bytes), keeping effect-only
        # quads separate so they cannot introduce invisible collision barriers.
        nodes = doc.get('nodes', [])
        if len(nodes) != source_shape_count or any(set(node)-{'name', 'mesh'} for node in nodes):
            raise ValueError('Consolidation requires flat, untransformed entity-space mesh nodes')
        if sorted(n['mesh'] for n in nodes) != list(range(source_shape_count)):
            raise ValueError('Each source shape must be referenced exactly once')
        grouped = {}
        for index, mesh in enumerate(doc['meshes']):
            for primitive_index, primitive in enumerate(mesh['primitives']):
                material = doc['materials'][primitive['material']]
                state = material.get('extras', {}).get('REA_originalAlpha', {})
                visual = bool(state.get('flags', 0) & 1 and
                    (state.get('sourceBlend'), state.get('destinationBlend')) == (0, 0))
                grouped.setdefault(visual, []).append((index, primitive_index, primitive))
        meshes = []
        for index, (visual, primitives) in enumerate(sorted(grouped.items())):
            mapping = [{'source_mesh': m, 'source_primitive': p} for m, p, _ in primitives]
            meshes.append({'name': f'REA_Shape_{index:04d}', 'primitives': [v for _, _, v in primitives],
                'extras': {'REA_source_primitives': mapping, 'REA_visual_only': visual}})
            groups.append({'visual_only': visual, 'source_primitives': mapping})
        doc['meshes'] = meshes
        doc['nodes'] = [{'name': mesh['name'], 'mesh': i} for i, mesh in enumerate(meshes)]
        doc['scenes'] = [{'nodes': list(range(len(meshes)))}]
        doc['scene'] = 0
    doc.setdefault('asset', {}).setdefault('extras', {})['REA_source_sha256'] = hashlib.sha256(raw).hexdigest()
    encoded = json.dumps(doc, separators=(',', ':'), allow_nan=False).encode()
    encoded += b' ' * ((-len(encoded)) % 4)
    tail = raw[20+size:]
    prepared = struct.pack('<III', magic, version, 20+len(encoded)+len(tail))
    prepared += struct.pack('<II', len(encoded), kind) + encoded + tail
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(prepared)
    return {'original_names': original_names, 'source_sha256': hashlib.sha256(raw).hexdigest(),
            'prepared_sha256': hashlib.sha256(prepared).hexdigest(), 'binary_chunks_unchanged': True,
            'materials': doc.get('materials', []), 'shape_count': len(doc.get('meshes', [])),
            'source_shape_count': source_shape_count, 'primitive_groups': groups}

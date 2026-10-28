"""Create an isolated UE/Chaos winding experiment without changing source GLBs.

Positions and all non-index BIN bytes are retained verbatim. Every original face
(a,b,c) becomes (a,c,b), preserving face sequence and surface/flag metadata.
The default is the single failed spawn floor, not the whole world.
"""
import argparse
import copy
import json
from pathlib import Path
import struct

from export_world_collisions import ROOT, DEFAULT_REPORT, bounded_new, checked, reference, require, sha
from unreal_import_world_collisions import read_source
from validate_audio_waves import lower_priority

POLICY = 'ue58_chaos_swap_second_third_v06'
ENGINE = Path('C:/Program Files/Epic Games/UE_5.8/Engine/Source/Runtime')


def split_glb(raw):
    require(struct.unpack_from('<III', raw) == (0x46546c67, 2, len(raw)), 'Invalid source GLB envelope')
    size, kind = struct.unpack_from('<II', raw, 12)
    require(kind == 0x4e4f534a, 'GLB JSON missing')
    document = json.loads(raw[20:20+size])
    binary_size, kind = struct.unpack_from('<II', raw, 20+size)
    require(kind == 0x004e4942 and 28+size+binary_size == len(raw), 'GLB BIN extent differs')
    return document, raw[28+size:]


def swap_glb_winding(raw, metadata):
    document, original_blob = split_glb(raw)
    primitive, = document['meshes'][0]['primitives']
    item = document['accessors'][primitive['indices']]
    view = document['bufferViews'][item['bufferView']]
    require(item['componentType'] == 5123 and item['type'] == 'SCALAR' and item['count'] % 3 == 0
            and not item.get('sparse') and not view.get('byteStride'), 'Expected packed uint16 triangle indices')
    start = view.get('byteOffset', 0)+item.get('byteOffset', 0)
    size = item['count']*2
    require(0 <= start and start+size <= len(original_blob), 'Invalid triangle byte extent')
    blob = bytearray(original_blob)
    for offset in range(start, start+size, 6):
        a, b, c = struct.unpack_from('<3H', original_blob, offset)
        struct.pack_into('<3H', blob, offset, a, c, b)
    require(blob[:start] == original_blob[:start] and blob[start+size:] == original_blob[start+size:],
            'Winding adapter changed non-index BIN bytes')
    document['extras']['engine_winding_adapter'] = metadata
    document['asset']['generator'] = 'Rea_Bully explicit UE58 Chaos winding adapter; source unchanged'
    encoded = json.dumps(document, separators=(',', ':')).encode()
    encoded += b' '*((-len(encoded)) % 4)
    result = (struct.pack('<III', 0x46546c67, 2, 28+len(encoded)+len(blob))
              +struct.pack('<II', len(encoded), 0x4e4f534a)+encoded
              +struct.pack('<II', len(blob), 0x004e4942)+blob)
    return result, {'index_byte_offset_in_bin': start, 'index_bytes': size,
                    'original_bin_sha256': sha(original_blob), 'adapted_bin_sha256': sha(blob),
                    'all_non_index_bin_bytes_identical': True}


def engine_proof():
    files = [
        ('Engine/Private/StaticMesh.cpp', 'CollisionData->bFlipNormals = true;',
         'UStaticMesh marks the physics triangle descriptor for winding inversion.'),
        ('Engine/Private/PhysicsEngine/Experimental/ChaosCooking.cpp',
         'FinalIndices.Add(InParams.TriangleMeshDesc.bFlipNormals ? Tri.v1 : Tri.v0);',
         'Chaos cook swaps v0/v1 before cleaning and building the triangle mesh.'),
        ('Engine/Private/Collision/CollisionConversions.cpp',
         'OutResult.ImpactNormal = FindGeomOpposingNormal(SweptGeometryType, Hit, TraceStartToEnd, Normal);',
         'Capsule/sphere impact normals use the geometry face normal.'),
        ('Experimental/Chaos/Private/Chaos/TriangleMeshImplicitObject.cpp',
         'return GetFaceNormal(FaceIndex);',
         'Triangle mesh FindGeometryOpposingNormal returns the stored face normal.'),
        ('Engine/Private/Components/CharacterMovementComponent.cpp',
         'if (ImpactNormalZ < UE_KINDA_SMALL_NUMBER)',
         'Character walkability rejects non-upward impact normals.')]
    rows = []
    for relative, anchor, meaning in files:
        path = ENGINE/relative
        data = path.read_bytes()
        lines = data.decode('utf-8-sig').splitlines()
        hits = [i+1 for i, line in enumerate(lines) if anchor in line]
        require(bool(hits), 'Local UE source anchor missing: '+relative)
        rows.append({**reference(path, data), 'anchor_lines': hits, 'anchor': anchor, 'meaning': meaning})
    return rows


def adapt(catalog_path, keys, output, report, reuse_path=None):
    lower_priority()
    output = bounded_new(output, ROOT/'local/converted')
    report = bounded_new(report, ROOT/'evidence')
    catalog = json.loads(Path(catalog_path).read_text())
    require(catalog['status'] == 'source_triangles_exported_engine_pending', 'Expected immutable source export')
    models = [m for m in catalog['models'] if m['model_key'] in keys]
    require({m['model_key'] for m in models} == set(keys), 'Requested model missing from source export')
    reused = {}
    if reuse_path:
        prior = json.loads(Path(reuse_path).read_text())
        require(prior['status'] == 'engine_winding_adapter_exported_runtime_pending'
                and prior['engine_target'] == 'ue_winding_adapter_v06', 'Expected a prior explicit adapter report')
        for previous in prior['models']:
            require(previous['model_key'] not in reused, 'Duplicate prior adapted model')
            reused[previous['model_key']] = previous
        require(set(reused).issubset(set(keys)), 'Reuse report contains unselected models')
    proofs = engine_proof()
    raw_sources = {m['model_key']: read_source(m) for m in models}
    output.mkdir(parents=True)
    results = []
    for original in models:
        if original['model_key'] in reused:
            previous = reused[original['model_key']]
            require(previous['winding_adapter']['original_model'] == original
                    and previous['winding_adapter']['source_export'] == reference(catalog_path),
                    'Prior adapter references a different original source model')
            read_source(previous)
            results.append(copy.deepcopy(previous))
            continue
        metadata = {'policy': POLICY, 'index_mapping': [0, 2, 1],
                    'source_glb': reference(original['output']), 'source_export': reference(catalog_path),
                    'source_face_sequence_preserved': True, 'source_surface_flags_preserved': True,
                    'engine_source_proofs': proofs, 'original_model': copy.deepcopy(original),
                    'runtime_validation': 'pending_isolated_ab_test'}
        raw, byte_proof = swap_glb_winding(raw_sources[original['model_key']], metadata)
        target = output/(original['model_key']+'.glb')
        target.write_bytes(raw)
        model = {**copy.deepcopy(original), 'output': str(target), 'output_sha256': sha(raw),
                 'status': 'engine_winding_adapter_exported_runtime_pending',
                 'winding_adapter': metadata, 'winding_byte_proof': byte_proof}
        read_source(model)
        results.append(model)
    result = {'schema': 'rea-bully-world-collision-ue-winding-adapter-v1',
              'status': 'engine_winding_adapter_exported_runtime_pending',
              'engine_target': 'ue_winding_adapter_v06',
              'sources': {**catalog['sources'], 'original_export': reference(catalog_path)},
              'models': results, 'pending': [],
              'summary': {'models': len(results), 'reused_models': len(reused),
                          'triangles': sum(m['triangles'] for m in results)},
              'limitations': ['Isolated winding experiment; no UE import or runtime validation executed.',
                  'Source positions, face sequence and surface metadata preserved; vertex order within each face deliberately reversed.',
                  'Mixed primitive bodies retain replace_visual_collision=false; this adapter does not supply missing boxes/spheres.',
                  'Original source GLBs, original export report and original asset namespace remain unchanged.']}
    if reuse_path:
        result['sources']['reused_adapter_report'] = reference(reuse_path)
    with report.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=DEFAULT_REPORT)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--model', action='append', default=None)
    selection.add_argument('--all-models', action='store_true', help='Adapt all catalog models; mixed bodies remain ineligible for replacement')
    parser.add_argument('--output', type=Path, default=ROOT/'local/converted/world-collisions-v06-ue-probe')
    parser.add_argument('--report', type=Path, default=ROOT/'evidence/world-collisions-ue-winding-v06-probe.json')
    parser.add_argument('--reuse-adapter-report', type=Path, help='Preserve prior adapted GLB paths and import provenance when expanding selection')
    args = parser.parse_args()
    keys = ([m['model_key'] for m in json.loads(args.catalog.read_text())['models']]
            if args.all_models else args.model or ['05140_fgrd_sc1b11'])
    result = adapt(args.catalog, keys, args.output, args.report, args.reuse_adapter_report)
    print(json.dumps(result['summary']))

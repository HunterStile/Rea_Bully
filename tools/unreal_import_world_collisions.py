"""Import exact COL3 triangle GLBs as isolated collision-only StaticMeshes.

UE Python entry point; BULLY_UE_REPORT must be new. Optional
REA_WORLD_COLLISIONS_KEYS selects exact model keys. No actors or gameplay start.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from export_world_collisions import DEFAULT_REPORT, checked, reference, triangle_geometry, section_bytes, require, sha
from collision_import_geometry import prove_coincident_vertex_pruning
from unreal_import_npcs import verify_import_origin


def import_target(catalog, requested=None):
    adapted = catalog['status'] == 'engine_winding_adapter_exported_runtime_pending'
    target = 'ue_winding_adapter_v06' if adapted else 'source'
    if requested is not None and requested != target:
        raise ValueError('Collision import target does not match catalog provenance')
    if adapted:
        if catalog.get('engine_target') != target or not all(m.get('winding_adapter') for m in catalog['models']):
            raise ValueError('Unverified engine winding adapter target')
        return '/Game/Original/WorldCollisionUE', ROOT/'local/world-import/collisions-v06-ue', '/Game/World/Import/OriginalCollisionUEPipeline'
    if catalog['status'] != 'source_triangles_exported_engine_pending' or any(m.get('winding_adapter') for m in catalog['models']):
        raise ValueError('Unverified original collision export')
    return '/Game/Original/WorldCollision', ROOT/'local/world-import/collisions', '/Game/World/Import/OriginalCollisionPipeline'


def configure_collision_pipeline(pipeline, collision_none):
    """Resolve every UE property before changing this project-local pipeline."""
    mesh = pipeline.get_editor_property('mesh_pipeline')
    common = pipeline.get_editor_property('common_meshes_properties')
    material = pipeline.get_editor_property('material_pipeline')
    # UE 5.8 InterchangeGenericMeshPipeline.h declares bGenerateLightmapUVs.
    # PyGenUtil + CamelCaseBreakIterator split it as Generate|Lightmap|U|Vs.
    # Keep bCollision enabled: disabling it can suppress cooked complex data.
    # The enum Collision has ScriptName="FallbackCollisionType"; None=0xFF.
    settings = [(mesh, 'mesh_pipeline', {
        'build_nanite': False, 'generate_lightmap_u_vs': False, 'collision': True,
        'import_collision_according_to_mesh_name': False,
        'fallback_collision_type': collision_none}),
        (common, 'common_meshes_properties', {
            'remove_degenerates': False, 'use_mikk_t_space': False}),
        (material, 'material_pipeline', {'import_materials': False})]
    for owner, label, values in settings:
        if owner is None:
            raise RuntimeError('Original collision pipeline is missing ' + label)
        for name in values:
            try:
                owner.get_editor_property(name)
            except Exception as exc:
                raise RuntimeError('Unsupported original collision property: ' + label + '.' + name) from exc
    for owner, label, values in settings:
        for name, value in values.items():
            owner.set_editor_property(name, value)
            if owner.get_editor_property(name) != value:
                raise RuntimeError('Original collision setting was not retained: ' + label + '.' + name)


def read_source(model):
    raw = checked({'path': model['output'], 'sha256': model['output_sha256']})
    if len(raw) < 28 or struct.unpack_from('<III', raw) != (0x46546c67, 2, len(raw)):
        raise ValueError('Invalid original collision GLB')
    length, kind = struct.unpack_from('<II', raw, 12)
    if kind != 0x4e4f534a:
        raise ValueError('Collision GLB has no JSON chunk')
    doc = json.loads(raw[20:20 + length])
    offset = 20 + length
    size, kind = struct.unpack_from('<II', raw, offset)
    if kind != 0x004e4942 or offset + 8 + size != len(raw):
        raise ValueError('Collision GLB has invalid BIN extent')
    blob = raw[offset + 8:]
    if len(doc['meshes']) != 1 or len(doc['nodes']) != 1 or doc['nodes'][0] != {'name': model['model_key'], 'mesh': 0}:
        raise ValueError('Collision GLB must contain one untransformed source mesh')
    primitive, = doc['meshes'][0]['primitives']
    if set(primitive['attributes']) != {'POSITION'} or primitive.get('material') is not None:
        raise ValueError('Collision GLB unexpectedly contains visual material data')
    def accessor(index, component, kind, width, fmt):
        item = doc['accessors'][index]
        view = doc['bufferViews'][item['bufferView']]
        start = view.get('byteOffset', 0) + item.get('byteOffset', 0)
        count = item['count'] * width
        if (item['componentType'] != component or item['type'] != kind or item.get('sparse')
                or view.get('byteStride') or view.get('buffer') != 0 or start < 0
                or start + count * struct.calcsize(fmt) > view.get('byteOffset', 0) + view['byteLength']
                or view.get('byteOffset', 0) + view['byteLength'] > len(blob)):
            raise ValueError('Collision accessor layout changed')
        return struct.unpack_from('<' + fmt * count, blob, start)
    source = checked(model['source_col'])
    collision = model['source_collision']
    start = collision['offset']
    if sha(source[start:start + collision['bytes']]) != collision['sha256']:
        raise ValueError('Original collision model hash changed')
    geometry = triangle_geometry(source, collision)
    positions = accessor(primitive['attributes']['POSITION'], 5126, 'VEC3', 3, 'f')
    indices = accessor(primitive['indices'], 5123, 'SCALAR', 1, 'H')
    expected_faces = geometry['triangles']
    adapter = model.get('winding_adapter')
    if adapter:
        if adapter['policy'] != 'ue58_chaos_swap_second_third_v06' or adapter['index_mapping'] != [0, 2, 1]:
            raise ValueError('Unapproved collision winding adaptation')
        original = adapter['original_model']
        if original.get('winding_adapter'):
            raise ValueError('Recursive collision winding adaptation')
        original_raw = read_source(original)
        if adapter['source_glb'] != reference(original['output'], original_raw):
            raise ValueError('Winding adapter source identity differs')
        original_export = json.loads(checked(adapter['source_export']))
        if original not in original_export['models']:
            raise ValueError('Winding adapter original model absent from pinned export')
        for proof in adapter['engine_source_proofs']:
            checked(proof)
        for field, value in original.items():
            if field not in ('output', 'output_sha256', 'status') and model.get(field) != value:
                raise ValueError('Winding adapter changed original model metadata: '+field)
        original_json_size = struct.unpack_from('<I', original_raw, 12)[0]
        original_doc = json.loads(original_raw[20:20+original_json_size])
        original_blob = bytearray(original_raw[28+original_json_size:])
        original_primitive, = original_doc['meshes'][0]['primitives']
        original_item = original_doc['accessors'][original_primitive['indices']]
        original_view = original_doc['bufferViews'][original_item['bufferView']]
        original_start = original_view.get('byteOffset', 0)+original_item.get('byteOffset', 0)
        for position in range(original_start, original_start+original_item['count']*2, 6):
            a, b, c = struct.unpack_from('<3H', original_blob, position)
            struct.pack_into('<3H', original_blob, position, a, c, b)
        if blob != original_blob:
            raise ValueError('Winding adapter altered bytes outside the exact index swap')
        original_doc['extras']['engine_winding_adapter'] = adapter
        original_doc['asset']['generator'] = 'Rea_Bully explicit UE58 Chaos winding adapter; source unchanged'
        if doc != original_doc:
            raise ValueError('Winding adapter changed unrelated GLB structure')
        expected_faces = [(a, c, b) for a, b, c in expected_faces]
    elif doc.get('extras', {}).get('engine_winding_adapter'):
        raise ValueError('Undeclared engine winding adapter')
    if positions != tuple(v for row in geometry['positions'] for v in row) or indices != tuple(v for row in expected_faces for v in row):
        raise ValueError('Collision GLB does not preserve original quantized vertices/face indices')
    extras = doc['extras']['source_collision']
    for field in ('model_key', 'source_col', 'source_collision', 'replace_visual_collision', 'surface_face_indices', 'triangle_flags'):
        if extras[field] != model[field]:
            raise ValueError('Collision GLB original metadata differs: ' + field)
    return raw


def _adaptation_source(model):
    """Re-read original integer vertices and faces, never trust the zero-area list."""
    data = checked(model['source_col'])
    collision = model['source_collision']
    start, size = collision['offset'], collision['bytes']
    require(0 <= start and size > 0 and start + size <= len(data)
            and sha(data[start:start + size]) == collision['sha256'], 'Original collision model extent changed')
    geometry = triangle_geometry(data, collision)
    require(model['triangles'] == len(geometry['triangles'])
            and model['surface_face_indices'] == geometry['surface_face_indices']
            and model['triangle_flags'] == geometry['triangle_flags'], 'Original collision face metadata changed')
    if 'zero_area_source_faces' in model:
        require(model['zero_area_source_faces'] == geometry['zero_area_source_faces'],
                'Original collision zero-area metadata changed')
    vertices = list(struct.iter_unpack('<3h', section_bytes(data, collision['sections']['vertices_quantized'])))
    return vertices, geometry


def collision_pruning_engine_proofs():
    """Pin the actual local editor-builder branch and its exact equality test."""
    engine = Path('C:/Program Files/Epic Games/UE_5.8/Engine/Source')
    definitions = [
        ('Developer/MeshBuilder/Private/StaticMeshBuilder.cpp', [
            'float VertexComparisonThreshold = BuildSettings.bRemoveDegenerates ? THRESH_POINTS_ARE_SAME : 0.0f;',
            'OverlappingThresholds.ThresholdPosition = VertexComparisonThreshold;',
            'if (PointsEqual(CornerPositions[0], CornerPositions[1], OverlappingThresholds)',
            '|| PointsEqual(CornerPositions[0], CornerPositions[2], OverlappingThresholds)',
            '|| PointsEqual(CornerPositions[1], CornerPositions[2], OverlappingThresholds))']),
        ('Runtime/Engine/Public/MeshBuild.h', [
            'inline bool PointsEqual(const FVector3f& V1, const FVector3f& V2, const FOverlappingThresholds& OverlappingThreshold)',
            'const float Epsilon = OverlappingThreshold.ThresholdPosition;',
            'return FMath::Abs(V1.X - V2.X) <= Epsilon && FMath::Abs(V1.Y - V2.Y) <= Epsilon && FMath::Abs(V1.Z - V2.Z) <= Epsilon;'])]
    records = []
    for relative, anchors in definitions:
        path = engine / relative
        data = path.read_bytes()
        lines = data.decode('utf-8-sig').splitlines()
        located = [{'anchor': anchor, 'lines': [i + 1 for i, line in enumerate(lines) if anchor in line]}
                   for anchor in anchors]
        require(all(row['lines'] for row in located), 'Local engine coincident-position filter differs: ' + relative)
        records.append({**reference(path, data), 'anchors': located})
    return records


def prove_engine_triangle_adaptation(model, geometry, mesh, observed_triangles):
    """Bind the complete engine geometry proof to immutable COL/GLB and engine sources."""
    read_source(model)  # Includes exact source GLB and declared winding-adapter byte checks.
    vertices, source = _adaptation_source(model)
    adapter = model.get('winding_adapter')
    mapping = adapter['index_mapping'] if adapter else [0, 1, 2]
    proof = prove_coincident_vertex_pruning(vertices, source['triangles'], geometry, mesh=mesh,
        index_mapping=mapping, surface_face_indices=source['surface_face_indices'],
        triangle_flags=source['triangle_flags'])
    require(type(observed_triangles) is int and observed_triangles == proof['observed_triangle_count'],
            'Render and collision-input triangle counts differ')
    proof.update(source_glb_sha256=model['output_sha256'], source_col=model['source_col'],
                 source_model_sha256=model['source_collision']['sha256'],
                 engine_source_proofs=collision_pruning_engine_proofs())
    return proof


def verify_engine_triangle_adaptation(model, record):
    """Planning replays the full proof from original bytes, not a saved success flag."""
    proof = record.get('engine_triangle_adaptation')
    if proof is None:
        if model.get('zero_area_source_faces'):
            vertices, source = _adaptation_source(model)
            require(not any(len({vertices[v] for v in face}) < 3 for face in source['triangles']),
                    'Missing engine triangle adaptation for original coincident-position faces')
        return
    require(isinstance(proof, dict) and record.get('triangles') == model['triangles'],
            'Invalid engine triangle adaptation or changed original triangle count')
    expected = prove_engine_triangle_adaptation(model, proof.get('geometry'), record['mesh'],
                                                proof.get('observed_triangle_count'))
    require(proof == expected, 'Engine triangle adaptation proof differs from original source reconstruction')


def triangle_adaptation_summary(proof):
    """Compact declaration for asset metadata and placement plans; full proof stays in checkpoint."""
    return {**{key: proof[key] for key in ('policy', 'source_triangle_count', 'observed_triangle_count',
                                         'omitted_source_face_indices')},
            'proof_sha256': sha(json.dumps(proof, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())}


def main():
    import unreal
    report_path = Path(os.environ['BULLY_UE_REPORT'])
    if report_path.exists():
        raise ValueError('New collision import report required')
    catalog_path = Path(os.environ.get('REA_WORLD_COLLISIONS_CATALOG', DEFAULT_REPORT))
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    asset_root, checkpoints, pipeline_path = import_target(catalog, os.environ.get('REA_WORLD_COLLISIONS_TARGET'))
    for field in ('world_catalog', 'collision_catalog', 'native_legacy_analysis', 'archive_manifest', 'inventory'):
        checked(catalog['sources'][field])
    selected = set(filter(None, os.environ.get('REA_WORLD_COLLISIONS_KEYS', '').split(',')))
    models = [m for m in catalog['models'] if not selected or m['model_key'] in selected]
    if not models or (selected and selected != {m['model_key'] for m in models}):
        raise ValueError('Requested original collision model is absent')
    # Check all selected source geometry before creating any UE asset.
    sources = {m['model_key']: read_source(m) for m in models}
    pipeline = (unreal.load_asset(pipeline_path) if unreal.EditorAssetLibrary.does_asset_exist(pipeline_path)
        else unreal.EditorAssetLibrary.duplicate_loaded_asset(
            unreal.load_asset('/Interchange/Pipelines/DefaultGLTFAssetsPipeline'), pipeline_path))
    if not pipeline:
        raise RuntimeError('Cannot create project-local original collision pipeline')
    configure_collision_pipeline(pipeline, unreal.InterchangeMeshCollision.NONE)
    unreal.EditorAssetLibrary.save_loaded_asset(pipeline, only_if_is_dirty=False)
    options = unreal.InterchangePipelineStackOverride()
    options.set_editor_property('override_pipelines', [unreal.SoftObjectPath(pipeline.get_path_name()),
        unreal.SoftObjectPath('/Interchange/Pipelines/DefaultGLTFPipeline.DefaultGLTFPipeline')])
    checkpoints.mkdir(parents=True, exist_ok=True)
    results = []
    for model in models:
        key = model['model_key']
        folder = asset_root + '/' + key
        source = Path(model['output'])
        raw = sources[key]
        existing = list(unreal.EditorAssetLibrary.list_assets(folder, recursive=True, include_folder=False))
        if not existing:
            task = unreal.AssetImportTask()
            task.filename = str(source)
            task.destination_path = folder
            task.automated = True
            task.replace_existing = False
            task.save = True
            task.options = options
            unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
        unreal.AutomationLibrary.finish_loading_before_screenshot()
        assets = [unreal.load_asset(p) for p in unreal.EditorAssetLibrary.list_assets(folder, recursive=True, include_folder=False)]
        if len(assets) != 1 or not isinstance(assets[0], unreal.StaticMesh):
            raise RuntimeError('Expected only one isolated collision StaticMesh: ' + key)
        mesh = assets[0]
        if not mesh.get_path_name().startswith(folder + '/' + source.stem + '/'):
            raise RuntimeError('Unrelated mesh in collision asset folder')
        provenance = verify_import_origin(unreal, mesh, source, hashlib.md5(raw).hexdigest())
        old = unreal.EditorAssetLibrary.get_metadata_tag(mesh, 'REAOriginalCollisionGLBSHA256')
        if old and old != model['output_sha256']:
            raise RuntimeError('Preserve foreign collision asset')
        observed_triangles = mesh.get_num_triangles(0)
        triangle_adaptation = None
        if observed_triangles != model['triangles']:
            geometry = json.loads(unreal.ReaBullyWorldPropTools.read_collision_triangle_geometry(mesh))
            triangle_adaptation = prove_engine_triangle_adaptation(model, geometry, mesh.get_path_name(), observed_triangles)
        old_adaptation = unreal.EditorAssetLibrary.get_metadata_tag(mesh, 'REAOriginalCollisionTriangleAdaptation')
        if old_adaptation and (triangle_adaptation is None
                or json.loads(old_adaptation) != triangle_adaptation_summary(triangle_adaptation)):
            raise RuntimeError('Prior collision triangle adaptation changed: ' + key)
        if mesh.get_editor_property('nanite_settings').get_editor_property('enabled'):
            raise RuntimeError('Original collision mesh unexpectedly uses Nanite')
        setup = mesh.get_editor_property('body_setup')
        if setup is None:
            raise RuntimeError('Original collision mesh has no BodySetup')
        setup.set_editor_property('collision_trace_flag', unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE)
        setup.set_editor_property('double_sided_geometry', True)
        bounds = mesh.get_bounds()
        origin = [bounds.origin.x, bounds.origin.y, bounds.origin.z]
        extent = [bounds.box_extent.x, bounds.box_extent.y, bounds.box_extent.z]
        actual = {'min': [a-b for a, b in zip(origin, extent)], 'max': [a+b for a, b in zip(origin, extent)]}
        error = max(abs(actual[side][axis] - model['bounds_unreal_cm'][side][axis]) for side in ('min', 'max') for axis in range(3))
        if not all(math.isfinite(v) for row in actual.values() for v in row) or error > .05:
            raise RuntimeError('Imported original collision frame/bounds mismatch: ' + key)
        for name, value in {'REAOriginalCollisionGLBSHA256': model['output_sha256'],
                'REAOriginalCOLModelSHA256': model['source_collision']['sha256'],
                'REAOriginalCOLSurfaceMetadata': json.dumps({'surface_face_indices': model['surface_face_indices'],
                    'triangle_flags': model['triangle_flags'], 'source': model['source_col']}),
                'REAOriginalCollisionPolicy': 'complex_as_simple_double_sided_no_surface_semantics'}.items():
            unreal.EditorAssetLibrary.set_metadata_tag(mesh, name, value)
        if model.get('winding_adapter'):
            unreal.EditorAssetLibrary.set_metadata_tag(mesh, 'REAOriginalCollisionSourceGLBSHA256',
                model['winding_adapter']['source_glb']['sha256'])
            unreal.EditorAssetLibrary.set_metadata_tag(mesh, 'REAOriginalCollisionWindingAdapter',
                json.dumps({'policy': model['winding_adapter']['policy'], 'index_mapping': [0, 2, 1],
                    'source_export': model['winding_adapter']['source_export'], 'runtime_validation': 'pending_isolated_ab_test'}))
        if triangle_adaptation:
            unreal.EditorAssetLibrary.set_metadata_tag(mesh, 'REAOriginalCollisionTriangleAdaptation',
                json.dumps(triangle_adaptation_summary(triangle_adaptation), sort_keys=True))
        unreal.EditorAssetLibrary.save_loaded_asset(mesh, only_if_is_dirty=False)
        record = {'status': 'collision_assets_verified', 'model_key': key,
            'source_glb_sha256': model['output_sha256'], 'mesh': mesh.get_path_name(),
            'source_col': model['source_col'], 'source_collision': model['source_collision'],
            'triangles': model['triangles'], 'source_bounds_max_error_cm': error,
            'body_policy': 'complex_as_simple_double_sided', 'nanite': False,
            'replace_visual_collision': model['replace_visual_collision'],
            'missing_primitive_counts': model['missing_primitive_counts'], 'import_provenance': provenance,
            'runtime_physics_validation': 'pending'}
        if model.get('winding_adapter'):
            record['winding_adapter'] = model['winding_adapter']
        if triangle_adaptation:
            record['engine_triangle_adaptation'] = triangle_adaptation
        checkpoint = checkpoints / (key + '.json')
        if checkpoint.exists():
            if json.loads(checkpoint.read_text()) != record:
                raise RuntimeError('Prior verified collision checkpoint changed: ' + key)
        else:
            with checkpoint.open('x', encoding='utf-8') as stream:
                json.dump(record, stream, indent=2)
        results.append(record)
        unreal.log('REA_WORLD_COLLISION_VERIFIED ' + key)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open('x', encoding='utf-8') as stream:
        json.dump({'status': 'collision_assets_verified_runtime_pending', 'catalog': reference(catalog_path),
            'models': results, 'engine': unreal.SystemLibrary.get_engine_version(),
            'actor_or_physics_executed': False}, stream, indent=2)


if __name__ == '__main__':
    main()

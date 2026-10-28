"""Extend door geometry recovery using exact IDE/NIF/NFT/AGR sources only.

The six DAT models are separate from the verified world/prop exports. Exporting
their source skins and curves does not validate their poses or door behavior.
"""
from __future__ import annotations

import argparse
from collections import Counter
import ctypes
import gzip
import hashlib
import json
import math
from pathlib import Path
import struct

from bully_agr import decode_animation, read_hxd_bytes
from catalog_world import model_key
from export_character import CharacterNif, export as export_skin, skeleton
from export_skinned_props import curve_metrics, export_all
from export_world import Resolver
from export_zone import required_base_names
from merge_player_animations import merge
from validate_player_actions import glb, values

ROOT = Path(__file__).resolve().parents[1]
DOORS = ROOT/'evidence/door-objects-v05.json'
LIBRARY = ROOT/'evidence/animation-library-v04.json'
WORLD = ROOT/'local/extracted/Stream/World'
NAMES = ('FDoor', 'TSGate', 'scObsDr', 'TadGates', 'RideGate', 'ExtWind')


def below_normal():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.GetPriorityClass.argtypes = [ctypes.c_void_p]
    handle = kernel.GetCurrentProcess()
    if not kernel.SetPriorityClass(handle, 0x4000) or kernel.GetPriorityClass(handle) != 0x4000:
        raise OSError('Could not set BelowNormal process priority')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def reference(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': digest(path.read_bytes())}


def checked(reference):
    data = Path(reference['path']).read_bytes()
    if digest(data) != reference['sha256']:
        raise ValueError('Changed source: ' + reference['path'])
    return data


class ArchiveProof:
    """Recheck small original extents without rereading the whole 1.9 GB image."""
    def __init__(self, world, manifest, game, inventory):
        self.world, self.manifest = world.resolve(), manifest
        self.by_path = {(world/e['path']).resolve(): e for e in manifest['entries']}
        self.proof = {}
        for kind in ('directory', 'image'):
            src = manifest['source'][kind]
            path = Path(src['path']).resolve()
            relative = path.relative_to(game).as_posix()
            item = inventory[relative]
            if src['sha256'] != item['sha256'] or src['size_bytes'] != item['bytes'] or path.stat().st_size != item['bytes']:
                raise ValueError('Archive differs from restored Steam inventory')
            self.proof[kind] = {**src, 'sha256_verified_against_inventory': True,
                                'whole_file_sha256_freshly_recomputed': kind == 'directory'}
        self.directory = checked(manifest['source']['directory'])
        self.image = Path(manifest['source']['image']['path'])

    def entry(self, path):
        path = Path(path).resolve()
        entry = self.by_path.get(path)
        if not path.is_relative_to(self.world) or not entry or not entry['verified']:
            raise ValueError('Source is not a verified World archive entry')
        raw = path.read_bytes()
        if (len(raw) != entry['allocated_bytes'] or digest(raw) != entry['extracted_sha256']
                or digest(raw) != entry['source_extent_sha256']):
            raise ValueError('Extracted source differs from archived extent hash')
        offset, sectors, name = struct.unpack_from('<II24s', self.directory, entry['index']*32)
        if (offset*2048 != entry['offset_bytes'] or sectors*2048 != len(raw)
                or name.split(b'\0', 1)[0].decode('ascii') != entry['name']):
            raise ValueError('Original archive directory disagrees with extracted entry')
        with self.image.open('rb') as stream:
            stream.seek(entry['offset_bytes'])
            archived = stream.read(entry['allocated_bytes'])
        if archived != raw:
            raise ValueError('Fresh original archive extent differs from extracted source')
        return {'path': str(path), 'sha256': digest(raw), 'index': entry['index'],
                'name': entry['name'], 'offset_bytes': entry['offset_bytes'], 'bytes': len(raw),
                'fresh_archive_extent_matches': True,
                'source_archive_sha256': self.manifest['source']['image']['sha256']}


def source_kind(nif):
    shapes = []
    for index, kind in enumerate(nif.types):
        if kind in ('NiTriShape', 'NiTriStrips'):
            shape = nif.block(index)
            shapes.append({'block': index, 'name': shape['name'], 'skin_block': shape['skin'],
                           'flags_raw': shape['flags'], 'hidden_bit_raw': bool(shape['flags'] & 1)})
    skinned = [shape for shape in shapes if shape['skin_block'] >= 0]
    kind = ('skinned' if skinned else 'unskinned_with_transform_controllers'
            if 'NiTransformController' in nif.types else 'static')
    return {'kind': kind, 'blocks_by_type': dict(Counter(nif.types)),
            'skinned_shapes': skinned, 'unskinned_shapes': [s for s in shapes if s not in skinned]}


def group_proof(group, definition, bones, archives, game, inventory):
    curves = json.loads(gzip.decompress(checked(group)))
    info = curves['group']
    if info['group_name'].casefold() != definition['animation_group'].casefold():
        raise ValueError('AGR group differs from exact IDE animation group')
    agr = checked(info['source'])
    archive = archives.entry(info['source']['path'])
    extent = info['expected_agr_bytes']
    if (len(agr) != info['source']['bytes'] or not 0 < extent <= len(agr)
            or len(agr)-extent != info['source']['padding_bytes'] or any(agr[extent:])
            or digest(agr[:extent]) != info['source']['payload_sha256']):
        raise ValueError('AGR extent or zero padding differs from source proof')
    hxd_source = curves['dictionary']['source']
    hxd_raw = checked(hxd_source)
    for field, hash_field in (('path', 'sha256'), ('name_list_path', 'name_list_sha256')):
        path = Path(hxd_source[field]).resolve()
        item = inventory[path.relative_to(game).as_posix()]
        if digest(path.read_bytes()) != item['sha256'] or item['sha256'] != hxd_source[hash_field]:
            raise ValueError('HXD source differs from restored Steam inventory')
    start, size = hxd_source['embedded_offset'], hxd_source['embedded_size']
    hxd = hxd_raw[start:start+size]
    if start < 0 or len(hxd) != size or digest(hxd) != hxd_source['embedded_sha256']:
        raise ValueError('Embedded HXD extent differs')
    parsed = read_hxd_bytes(hxd)
    hxd_group = [g for g in parsed['groups'] if g['name'].casefold() == definition['animation_group'].casefold()]
    if len(hxd_group) != 1 or hxd_group[0] != info['hxd_group']:
        raise ValueError('Original HXD group identity differs')
    records = [a for a in parsed['animations'] if a['group'] == hxd_group[0]['index']]
    if [c['hxd_record'] for c in curves['clips']] != records:
        raise ValueError('Original HXD clip identity/order differs from library')
    for clip in curves['clips']:
        start, size = clip['agr_offset'], clip['agr_size']
        payload = agr[start:start+size]
        if start < 0 or start+size > extent or len(payload) != size or digest(payload) != clip['agr_sha256']:
            raise ValueError('Clip exceeds original AGR extent or has changed bytes')
        fresh = json.loads(json.dumps(decode_animation(payload, clip['duration'], strict_times=False)))
        if any(clip[key] != value for key, value in fresh.items()):
            raise ValueError('Original AGR decode differs from saved curves')
        if clip['duplicate_instants'] or len(clip['tracks']) != len(bones):
            raise ValueError('AGR tracks cannot map to the original Root hierarchy')
    return {'source_group': group, 'source_agr': info['source'], 'source_hxd': hxd_source,
            'agr_archive': archive, 'clips': [{k: c[k] for k in
                ('name', 'ordinal', 'duration', 'agr_offset', 'agr_size', 'agr_sha256')} for c in curves['clips']],
            'fresh_agr_decode_matches_library': True,
            'hierarchy_mapping_status': 'source_Root_depth_first_track_count_matches_pending_independent_pose_validation'}


def plan(names=NAMES):
    below_normal()
    if not names or len(names) != len(set(names)) or not set(names) <= set(NAMES):
        raise ValueError('Only the six original pending door models are in scope')
    doors = json.loads(DOORS.read_text())
    inventory_path = ROOT/'evidence/local_inventory-restored.json'
    inventory = json.loads(inventory_path.read_text())
    game = Path(inventory['game_root']).resolve()
    original = {f['path']: f for f in inventory['files']}
    resolver = Resolver(WORLD)
    if digest((WORLD/'manifest.json').read_bytes()) != doors['world_archive_manifest']['sha256']:
        raise ValueError('World extraction manifest changed since door catalog')
    archives = ArchiveProof(WORLD, resolver.manifest, game, original)
    library = json.loads(LIBRARY.read_text())
    checked(library['catalog'])
    models = []
    for name in names:
        objects = [o for o in doors['objects'] if o['placement_name'] == name]
        definitions = [o['definition'] for o in objects]
        if not objects or any(d != definitions[0] for d in definitions) or any(o['ambiguous_definition_candidates'] for o in objects):
            raise ValueError('Missing or ambiguous DAT model definition: ' + name)
        definition = definitions[0]
        ide = Path(definition['source'])
        ide_raw = checked({'path': str(ide), 'sha256': definition['source_sha256']})
        if digest(ide_raw) != original[ide.resolve().relative_to(game).as_posix()]['sha256']:
            raise ValueError('IDE differs from restored Steam inventory')
        if ide_raw.decode('utf-8-sig').splitlines()[definition['line']-1] != definition['raw_line']:
            raise ValueError('Original IDE line changed')
        for obj in objects:
            checked(obj['source'])
        path, resolution = resolver.asset(name+'.nif', model_name=name)
        nif = CharacterNif(path)
        classification = source_kind(nif)
        record = {'model_key': model_key(definition['model_id'], name), 'model_id': definition['model_id'],
            'name': name, 'placement_count': len(objects), 'zones': [], 'definition': definition,
            'door_object_ids': [o['id'] for o in objects], 'model_resolution': resolution,
            'source_classification': classification, 'source_nif_archive': archives.entry(path),
            'status': 'pending'}
        models.append(record)
        try:
            if classification['kind'] != 'skinned':
                raise ValueError('This extension requires the classified original skin; no static-to-skin substitution')
            if any(not shape['hidden_bit_raw'] for shape in classification['unskinned_shapes']):
                raise ValueError('Visible unskinned geometry would be omitted by the skin exporter')
            bones = skeleton(nif, 'Root')
            required = required_base_names(nif)
            texture, resolution = resolver.asset(definition['texture_dictionary']+'.nft', required_textures=required)
            if required-resolver.texture_names(texture):
                raise ValueError('Exact IDE texture dictionary lacks required original texture names')
            record.update(source_texture_archive=archives.entry(texture), texture_resolution=resolution,
                          required_base_textures=sorted(required), source_bones=bones)
            groups = [g for g in library['groups'] if g['name'].casefold() == definition['animation_group'].casefold()]
            if len(groups) != 1:
                raise ValueError('Exact IDE animation group is missing or ambiguous')
            record['animation_provenance'] = group_proof(groups[0], definition, bones, archives, game, original)
            record['status'] = 'source_compatible_pending_export'
        except (ValueError, OSError, KeyError, IndexError, struct.error) as exc:
            record['diagnostic'] = str(exc)
    return {'schema': 'rea-bully-door-model-extension-plan-v1', 'door_catalog': reference(DOORS),
            'inventory': reference(inventory_path), 'animation_library': reference(LIBRARY),
            'world_manifest': reference(WORLD/'manifest.json'), 'archive_sources': archives.proof,
            'archive_verification_scope': 'Restored inventory archive SHA256, fresh directory SHA256 and selected image extent equality; whole image was not rehashed.',
            'models': models, 'ready_for_unreal': False}


def write_new(path, data):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2)


def geometry_diagnostics(model):
    checked(model['source_nif_archive'])
    nif = CharacterNif(Path(model['source_nif_archive']['path']))
    result = []
    for shape_record in model['source_classification']['skinned_shapes']:
        shape = nif.block(shape_record['block'])
        mesh = nif.block(shape['data'])
        base_maps = [nif.block(i)['slots'][0] for i in shape['properties']
                     if nif.types[i] == 'NiTexturingProperty' and nif.block(i)['slots'] and nif.block(i)['slots'][0]]
        if len(base_maps) != 1:
            raise ValueError('Expected one explicit source base texture map')
        selected = base_maps[0]['flags'] & 255
        uv_sets = []
        for index, coordinates in enumerate(mesh['uv_sets']):
            invalid = {i for i, uv in enumerate(coordinates) if not all(math.isfinite(v) for v in uv)}
            uv_sets.append({'index': index, 'selected_by_source_base_map': index == selected,
                            'nonfinite_vertex_indices': sorted(invalid),
                            'affected_triangle_indices': [i for i, tri in enumerate(mesh['triangles']) if invalid.intersection(tri)]})
        colors = mesh['colors']
        invalid = [i for i, row in enumerate(colors) if not all(math.isfinite(v) and 0 <= v <= 1 for v in row[:3])]
        result.append({'shape_block': shape_record['block'], 'data_block': shape['data'], 'name': shape['name'],
                       'vertices': len(mesh['vertices']), 'triangles': len(mesh['triangles']),
                       'base_texture_map_flags_raw': base_maps[0]['flags'], 'selected_uv_set': selected,
                       'uv_sets': uv_sets, 'rgb_rows': len(colors), 'invalid_rgb_indices': invalid,
                       'all_rgb_exactly_100': bool(colors) and all(tuple(row[:3]) == (100., 100., 100.) for row in colors)})
    return result


def validate_conversion(conversion, expected_clips, *, colors_omitted=False):
    checked({'path': conversion['output'], 'sha256': conversion['output_sha256']})
    skin_report = json.loads(checked(conversion['skin_report']))
    doc, blob = glb(conversion['output'])
    original, original_blob = glb(skin_report['output'])
    for field in ('nodes', 'meshes', 'skins', 'materials', 'images', 'textures'):
        if doc.get(field) != original.get(field):
            raise ValueError('Animation merge changed original skin ' + field)
    if blob[:len(original_blob)] != original_blob or any(doc[f][:len(original[f])] != original[f] for f in ('accessors', 'bufferViews')):
        raise ValueError('Animation merge changed original skin binary')
    if len(doc['animations']) != len(expected_clips):
        raise ValueError('Exported animation count differs from source')
    for animation, clip in zip(doc['animations'], expected_clips):
        if (animation['name'] != clip['name'].replace('\\', '_')
                or animation['extras']['source_agr_sha256'] != clip['agr_sha256']
                or animation['extras']['source_duration_seconds'] != clip['duration']):
            raise ValueError('Exported clip identity/provenance changed')
        for sampler in animation['samplers']:
            times, samples = values(doc, blob, sampler['input']), values(doc, blob, sampler['output'])
            if (not times or not all(math.isfinite(v) for v in (*times, *samples))
                    or not all(a < b for a, b in zip(times, times[1:]))
                    or min(times) < 0 or max(times) > clip['duration']+1e-6):
                raise ValueError('Exported clip contains invalid sample data')
    if colors_omitted and any('COLOR_0' in primitive['attributes'] for mesh in doc['meshes'] for primitive in mesh['primitives']):
        raise ValueError('Base-material variant unexpectedly contains vertex RGB')
    return {'source_skin_nodes_and_binary_preserved': True, 'original_clip_identity_and_hashes': True,
            'finite_strictly_ordered_animation_data': True, 'vertex_rgb_omitted': colors_omitted,
            'independent_deformed_pose_validation': 'not_run'}


def audit_existing(export_report, output):
    below_normal()
    export_report, output = Path(export_report), Path(output)
    if output.exists():
        raise FileExistsError('New diagnostics report required')
    report = json.loads(export_report.read_text())
    models = []
    for model in report['models']:
        item = {'name': model['name'], 'model_key': model['model_key'], 'source_nif': model['source_nif_archive'],
                'geometry': geometry_diagnostics(model), 'status': model['status'], 'diagnostic': model.get('diagnostic')}
        if model['status'] == 'exported_pending_independent_pose_validation':
            item['structural_validation'] = validate_conversion(model['conversion'], model['animation_provenance']['clips'])
        models.append(item)
    result = {'schema': 'rea-bully-door-model-diagnostics-v1', 'export_report': reference(export_report),
              'models': models, 'ready_for_unreal': False,
              'limitations': ['No alternate UV set or normalized color values are substituted.',
                              'Structural validation is not an independent deformed-pose or visual check.']}
    write_new(output, result)
    return result


def export_tadgates_base_material(output, report):
    output, report = Path(output).resolve(), Path(report).resolve()
    if output.exists() or report.exists():
        raise FileExistsError('New variant output and report required')
    if not output.is_relative_to(ROOT/'local/converted') or not report.is_relative_to(ROOT/'evidence'):
        raise ValueError('Variant must remain inside project outputs')
    source_plan = plan(('TadGates',))
    model = source_plan['models'][0]
    if model['status'] != 'source_compatible_pending_export':
        raise ValueError('Original TadGates sources are incompatible')
    diagnostic = geometry_diagnostics(model)
    if (not all(shape['all_rgb_exactly_100'] for shape in diagnostic)
            or any(shape['selected_uv_set'] != 0 or shape['uv_sets'][0]['nonfinite_vertex_indices'] for shape in diagnostic)):
        raise ValueError('Variant is limited to original RGB100 and verified finite source UV0')
    output.mkdir(parents=True)
    skin_path, skin_report = output/'TadGates_skin.glb', output/'skin-report.json'
    skin_result = export_skin(Path(model['source_nif_archive']['path']), [], skin_path, skin_report,
                base_texture_paths=[Path(model['source_texture_archive']['path'])], asset_name='TadGates',
                skeleton_root_name='Root', allow_transformed_skin_shape=True,
                preserve_global_skin_transform=True, preserve_vertex_colors=False)
    curves = json.loads(gzip.decompress(checked(model['animation_provenance']['source_group'])))
    clips = []
    for original in curves['clips']:
        metrics = curve_metrics(original)
        clips.append({**{k: v for k, v in original.items() if k != 'tracks'},
            'channels': [dict(bone=i, name=bone['name'], rotation=track['rotation'], translation=track['translation'])
                         for i, (bone, track) in enumerate(zip(model['source_bones'], original['tracks']))],
            'root_motion': {'name': model['source_bones'][0]['name'],
                            'displacement': metrics['root_displacement_metres'],
                            'speed_m_per_second': metrics['root_endpoint_drift_metres']/original['duration']}})
    payload = output/'animations.json'
    write_new(payload, {'root_motion_policy': 'Original object local animation; no synthesized loop or motion extraction', 'clips': clips})
    target = output/'10102_tadgates_base_material_only.glb'
    merge(skin_path, payload, target, output/'merge-report.json', in_place=False, omit_translation_bones=())
    converted = {'output': str(target), 'output_sha256': digest(target.read_bytes()), 'skin_report': reference(skin_report)}
    validation = validate_conversion(converted, model['animation_provenance']['clips'], colors_omitted=True)
    result = {'schema': 'rea-bully-door-model-degraded-variant-v1',
        'status': 'exported_base_material_only_shading_and_independent_pose_pending',
        'model_key': model['model_key'], 'name': model['name'], 'door_candidates': model['placement_count'],
        'source': model, 'source_context': {k: v for k, v in source_plan.items() if k != 'models'},
        'geometry_diagnostics': diagnostic, 'conversion': converted, 'structural_validation': validation,
        'material_policy': 'base_material_only', 'omitted_vertex_rgb_rows': sum(s['rgb_rows'] for s in diagnostic),
        'source_rgb_value': [100.0, 100.0, 100.0], 'normalized_or_replaced_rgb': False,
        'skinned_geometry_and_uv_omissions': [], 'excluded_unskinned_helpers': skin_result['excluded_helpers'],
        'clips': model['animation_provenance']['clips'],
        'shading_pending': True, 'ready_for_unreal': False, 'runtime_integrated': False,
        'limitations': ['Source RGB100 rows are omitted, never normalized; appearance is not a complete reconstruction.',
                        'No Unreal, Blender, visual/deformed-pose validation or interaction integration was performed.',
                        'The strict original export remains rejected and this variant does not supersede it.']}
    write_new(report, result)
    return result


def run(output, report, plan_path):
    output, report, plan_path = map(lambda p: Path(p).resolve(), (output, report, plan_path))
    if any(path.exists() for path in (output, report, plan_path)):
        raise FileExistsError('New output, report and plan paths required')
    if (not output.is_relative_to(ROOT/'local/converted')
            or any(not path.is_relative_to(ROOT/'evidence') for path in (report, plan_path))):
        raise ValueError('Door extension outputs must remain in project local/converted and evidence')
    source_plan = plan()
    write_new(plan_path, source_plan)
    eligible = [m for m in source_plan['models'] if m['status'] == 'source_compatible_pending_export']
    pipeline_input = ROOT/'local'/('door-model-pipeline-input-'+output.name+'.json')
    write_new(pipeline_input, {'models': eligible})
    pipeline_path = output/'pipeline-report.json'
    conversion = export_all(pipeline_input, LIBRARY, WORLD, output, pipeline_path)
    converted = {m['model_key']: m for m in conversion['models']}
    models = []
    for source in source_plan['models']:
        item = {**source, 'status': 'pending', 'conversion': converted.get(source['model_key']),
                'runtime_integrated': False, 'independent_pose_validation': 'not_run'}
        if item['conversion']:
            if item['conversion']['status'] == 'exported':
                item['status'] = 'exported_pending_independent_pose_validation'
            else:
                item['diagnostic'] = item['conversion']['error']
        models.append(item)
    result = {'schema': 'rea-bully-door-model-extension-v1',
        'status': 'export_complete_pending_independent_pose_validation' if all(m['status'].startswith('exported_') for m in models) else 'partially_exported_with_explicit_pending',
        'source_plan': reference(plan_path), 'source_door_catalog': source_plan['door_catalog'],
        'pipeline_report': reference(pipeline_path), 'archive_sources': source_plan['archive_sources'],
        'archive_verification_scope': source_plan['archive_verification_scope'], 'models': models,
        'summary': {'models': len(models), 'door_candidates': sum(m['placement_count'] for m in models),
                    'statuses': dict(Counter(m['status'] for m in models)),
                    'clips': sum(len(m['conversion'].get('clips', [])) for m in models if m['conversion'])},
        'ready_for_unreal': False, 'limitations': [
            'No Unreal, Blender, rendered/deformed-pose validation, placement or runtime interaction was performed.',
            'Source hidden unskinned helpers remain explicitly omitted; no replacement mesh or texture alias was synthesized.',
            'AGR track count and original Root traversal match, but this is not independent proof of every deformed pose.',
            'DAT candidate count does not prove current activation, lock state, collision, opening policy or paired-door synchronization.',
            'Existing world-export and skinned-props-v04 reports and assets are unchanged.']}
    write_new(report, result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--plan', type=Path, default=ROOT/'evidence/door-models-source-v05.json')
    parser.add_argument('--tadgates-base-material-only', action='store_true')
    args = parser.parse_args()
    if args.tadgates_base_material_only:
        result = export_tadgates_base_material(args.output or ROOT/'local/converted/door-models-v05-base-material/10102_tadgates',
                    args.report or ROOT/'evidence/door-tadgates-base-material-v05.json')
        print(json.dumps({k: result[k] for k in ('status', 'model_key', 'omitted_vertex_rgb_rows')}))
    else:
        print(json.dumps(run(args.output or ROOT/'local/converted/door-models-v05',
              args.report or ROOT/'evidence/door-models-export-v05.json', args.plan)['summary']))

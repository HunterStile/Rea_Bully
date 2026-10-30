"""Recover six retained static meshes while declaring unresolved BaseMap pixels.

Uses original NiMaterial values and existing exporter behavior on affected shapes.
No texture aliases, candidate pixel choices, geometry edits or baseline rewrites.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct

from audit_world_scope import ROOT, read, reference, verify
from catalog_dat_door_objects import below_normal
from export_nif import Nif, export, texture_basename
from export_world import Resolver, finalize_zones
from export_zone import required_base_names, ResolutionError

KEYS = {'03398_sb1_rocks', '03400_sb1_bgtrees01', '09812_dpi_garbcana',
        '09818_dpi_terriumss', '11745_from_bu169', '11746_from_bu170'}


def chunks(raw):
    magic, version, total, size, kind = struct.unpack_from('<IIIII', raw)
    if (magic, version, total, kind) != (0x46546c67, 2, len(raw), 0x4e4f534a):
        raise ValueError('Invalid source GLB')
    return json.loads(raw[20:20+size]), raw[20+size:]


def encode(doc, tail):
    data = json.dumps(doc, separators=(',', ':'), allow_nan=False).encode()
    data += b' '*((-len(data)) % 4)
    return struct.pack('<IIIII', 0x46546c67, 2, 20+len(data)+len(tail), len(data), 0x4e4f534a)+data+tail


def affected_shapes(nif, missing):
    affected = []
    for index, kind in enumerate(nif.types):
        if kind not in ('NiTriShape', 'NiTriStrips'):
            continue
        shape = nif.block(index)
        material, unresolved = [], []
        for prop_index in shape['properties']:
            prop = nif.block(prop_index)
            if prop['type'] == 'NiMaterialProperty':
                material.append({'block': prop_index, 'original': prop})
            if prop['type'] == 'NiTexturingProperty' and prop['slots'] and prop['slots'][0]:
                source_index = prop['slots'][0]['source']
                source = nif.block(source_index)
                name = texture_basename(source['filename'])
                if name in missing:
                    unresolved.append({'texturing_property_block': prop_index, 'texture_block': source_index,
                        'original_filename': source['filename'], 'texture_basename': name})
        if unresolved:
            if len(material) != 1:
                raise ValueError('One original NiMaterialProperty required for affected shape')
            mesh = nif.block(shape['data'])
            affected.append({'shape_block': index, 'shape_name': shape['name'], 'data_block': shape['data'],
                'vertices': len(mesh['vertices']), 'triangles': len(mesh['triangles']),
                'unresolved_base_maps': unresolved, 'original_material': material[0],
                'policy': 'original_NiMaterial_diffuse_alpha_without_unresolved_BaseMap', 'shading_pending': True})
    if {m['texture_basename'] for s in affected for m in s['unresolved_base_maps']} != set(missing):
        raise ValueError('Unresolved texture is not accounted for by an actual shape')
    return affected


def annotate(raw, shapes):
    original, tail = chunks(raw)
    doc = copy.deepcopy(original)
    by_name = {s['shape_name']: s for s in shapes}
    if len(by_name) != len(shapes):
        raise ValueError('Ambiguous material/shape names')
    changed = []
    for index, material in enumerate(doc['materials']):
        shape = by_name.get(material['name'])
        if shape is None:
            continue
        pbr = material['pbrMetallicRoughness']
        original_material = shape['original_material']['original']
        if 'baseColorTexture' in pbr or pbr['baseColorFactor'] != [*original_material['diffuse'], original_material['alpha']]:
            raise ValueError('Affected shape must retain original diffuse/alpha and no invented texture')
        material.setdefault('extras', {})['REA_unresolvedBaseMap'] = shape
        changed.append(index)
    if len(changed) != len(shapes):
        raise ValueError('Affected source shape did not produce exactly one material')
    result = encode(doc, tail)
    verification, actual_tail = chunks(result)
    for index in changed:
        verification['materials'][index]['extras'].pop('REA_unresolvedBaseMap')
        if not verification['materials'][index]['extras']:
            del verification['materials'][index]['extras']
    if verification != original or actual_tail != tail:
        raise ValueError('Material recovery changed resolved materials or mesh/texture data')
    return result, {'binary_payload_sha256': hashlib.sha256(tail).hexdigest(),
        'binary_payload_unchanged': True, 'resolved_materials_unchanged': True,
        'affected_material_indices': changed,
        'verification': 'GLB JSON identical after removing only added unresolved-material metadata; BIN chunk byte-identical.'}


def run(catalog_path, output, overlay_path, report_path):
    for path in (output, overlay_path, report_path):
        if path.exists():
            raise ValueError('Fresh recovery paths required: ' + str(path))
    if not output.resolve().is_relative_to(ROOT/'local') or not overlay_path.resolve().is_relative_to(ROOT/'local'):
        raise ValueError('Derived game geometry/catalog must remain local')
    baseline_bytes = catalog_path.read_bytes()
    baseline = json.loads(baseline_bytes)
    selected = [m for m in baseline['models'] if m['model_key'] in KEYS]
    if {m['model_key'] for m in selected} != KEYS or any(m['status'] != 'ambiguous' for m in selected):
        raise ValueError('Expected exactly the six retained ambiguous models')
    resolver = Resolver(ROOT/'local/extracted/Stream/World')
    output.mkdir(parents=True)
    recovered = []
    for original in selected:
        record = copy.deepcopy(original)
        source = Path(next(c['path'] for c in record['model_resolution']['candidates']
                           if c['index'] == record['model_resolution']['selected_index']))
        verify({'path': str(source), 'sha256': record['model_resolution']['selected_sha256']})
        nif = Nif(source)
        required = required_base_names(nif)
        primary, resolution = resolver.asset(record['definition']['texture_dictionary']+'.nft', required_textures=required)
        textures = [primary]
        unresolved, resolved = [], []
        for name in sorted(required-resolver.texture_names(primary)):
            try:
                more, details = resolver.supplemental({name})
                textures.extend(p for p in more if p not in textures)
                resolved.extend(details)
            except ResolutionError as error:
                # Integrity failures are not material-resolution choices.
                if not str(error).startswith('Exact supplemental texture absent or has differing original pixels:'):
                    raise
                unresolved.append({'name': name, 'reason': str(error), 'candidates': error.candidates})
        if not unresolved:
            raise ValueError('No unresolved BaseMap; use normal strict pipeline for this model')
        missing = {m['name'] for m in unresolved}
        shapes = affected_shapes(nif, missing)
        raw_path = output/(record['model_key']+'.raw.glb')
        stats = export(source, textures, raw_path, True, loose_textures=False, require_textures=False,
            invalid_geometry_policy='reject', invalid_color_policy='base_material_only')
        if set(stats['missing_textures']) != missing:
            raise ValueError('Exporter unresolved BaseMap set changed')
        result, verification = annotate(raw_path.read_bytes(), shapes)
        target = output/(record['model_key']+'.glb')
        target.write_bytes(result)
        stats = dict(stats, output=str(target.resolve()), material_recovery=shapes)
        target.with_suffix('.report.json').write_text(json.dumps(stats, indent=2), encoding='utf-8')
        low, high = stats['bounds_min_gltf'], stats['bounds_max_gltf']
        record.update(status='exported', source_status_before_recovery='ambiguous', output=str(target.resolve()),
            output_sha256=hashlib.sha256(result).hexdigest(), output_relative=target.name, export=stats,
            bounds_entity_unreal_cm={'min': [100*low[i] for i in (0, 2, 1)], 'max': [100*high[i] for i in (0, 2, 1)]},
            shading_pending=True, material_recovery_policy='original_NiMaterial_only_for_unresolved_BaseMap_shapes',
            unresolved_base_textures=unresolved, affected_shapes=shapes, texture_resolution=resolution,
            supplemental_texture_resolution=resolved, preservation_verification=verification)
        recovered.append(record)
    overlay = copy.deepcopy(baseline)
    replacements = {m['model_key']: m for m in recovered}
    overlay['models'] = [replacements.get(m['model_key'], m) for m in overlay['models']]
    overlay['material_recovery_extension'] = {'source_catalog': reference(catalog_path),
        'models': sorted(KEYS), 'policy': 'retain_original_material_and_geometry_with_explicit_unresolved_BaseMap',
        'original_appearance_verified': False}
    overlay['generated_at_utc'] = datetime.now(timezone.utc).isoformat()
    finalize_zones(overlay, output)
    unchanged = [m for m in overlay['models'] if m['model_key'] not in KEYS]
    if unchanged != [m for m in baseline['models'] if m['model_key'] not in KEYS] or catalog_path.read_bytes() != baseline_bytes:
        raise ValueError('Recovery modified unrelated model records or original catalog')
    with overlay_path.open('x', encoding='utf-8') as stream:
        json.dump(overlay, stream, indent=2)
    report = {'schema': 'rea-bully-world-material-recovery-v1', 'status': 'geometry_recovered_shading_pending_engine_pending',
        'source_catalog': reference(catalog_path), 'overlay_catalog': reference(overlay_path), 'models': recovered,
        'summary': {'models': len(recovered), 'placements': sum(m['placement_count'] for m in recovered),
            'shapes': sum(m['export']['shapes'] for m in recovered),
            'affected_shapes': sum(len(m['affected_shapes']) for m in recovered),
            'affected_source_triangles': sum(s['triangles'] for m in recovered for s in m['affected_shapes'])},
        'baseline_bytes_unchanged': True, 'unrelated_model_records_unchanged': True,
        'limitations': ['Unresolved BaseMap images are absent, not replaced by guessed aliases or selected seasonal pixels.',
            'Original diffuse/alpha are retained; untextured appearance is not claimed to match the original game.',
            'NiMaterial lighting and original special shaders are not fully reproduced by glTF PBR.',
            'No source geometry repaired/removed; retained source vertex RGB omission policy remains explicit.',
            'Import, rendering and physics remain to be verified in Unreal.']}
    with report_path.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
    return report


if __name__ == '__main__':
    below_normal()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=ROOT/'local/world-export-with-collectables-v04.json')
    parser.add_argument('--output', type=Path, default=ROOT/'local/converted/world-material-recovery-v07')
    parser.add_argument('--overlay', type=Path, default=ROOT/'local/world-export-v07-material-recovery.json')
    parser.add_argument('--report', type=Path, default=ROOT/'evidence/world-material-recovery-v07.json')
    args = parser.parse_args()
    print(json.dumps(run(args.catalog, args.output, args.overlay, args.report)['summary']))

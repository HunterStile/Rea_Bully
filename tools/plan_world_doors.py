"""Source-bound DAT doors for exploration: native pose, original skins/clips, declared policy.

No mission trigger, saved lock, chapter schedule or inferred L/R pair is enabled.
The UE import and visual checks remain separate from this mathematical derivation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import struct

from catalog_dat_door_objects import below_normal
from export_character import quaternion

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT/'local/native/decompiled/full-20261020/functions'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def reference(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def verify(ref):
    path = ROOT/ref['path']
    if reference(path)['sha256'] != ref['sha256']:
        raise ValueError('Original door source changed: ' + str(path))
    return path


def matrix_product(a, b):
    return [sum(a[r*3+k]*b[k*3+c] for k in range(3)) for r in range(3) for c in range(3)]


def native_door_matrix(yaw_pitch_roll, yaw):
    """Row-major active matrix, derived from 4effb0 -> 415eb0/415e30/415e00.

    768820 stores transposed Rodrigues and receives the NEGATED angle, canceling
    its sign. Mode 2 calls 406830 with ECX=new and argument=current, hence
    premultiplication (768920). 768a70 proves matrix times column-vector.
    These are DAT Euler fields; do not apply the IPB quaternion conjugation.
    """
    if len(yaw_pitch_roll) != 3 or not all(math.isfinite(x) for x in [*yaw_pitch_roll, yaw]):
        raise ValueError('Nonfinite DAT rotation')
    z, y, x = map(math.radians, (yaw_pitch_roll[0]-yaw, yaw_pitch_roll[1], yaw_pitch_roll[2]))
    cz, sz, cy, sy, cx, sx = math.cos(z), math.sin(z), math.cos(y), math.sin(y), math.cos(x), math.sin(x)
    rz = [cz, -sz, 0, sz, cz, 0, 0, 0, 1]
    ry = [cy, 0, sy, 0, 1, 0, -sy, 0, cy]
    rx = [1, 0, 0, 0, cx, -sx, 0, sx, cx]
    return matrix_product(rx, matrix_product(ry, rz))


def door_transform(pose):
    position = pose['position_source_game_m']
    if len(position) != 3 or not all(math.isfinite(v) for v in position):
        raise ValueError('Nonfinite DAT position')
    original = native_door_matrix(pose['yaw_pitch_roll_degrees_raw'], pose['yaw_degrees_raw'])
    # Imported skinned model basis is (x,-y,z), cm. Reflection is conjugation.
    signs = [1, -1, 1]
    ue = [original[r*3+c]*signs[r]*signs[c] for r in range(3) for c in range(3)]
    return {'translation_cm': [100*position[0], -100*position[1], 100*position[2]],
            'rotation_quaternion_xyzw': quaternion(ue), 'scale_xyz': [1, 1, 1]}


def rotation_evidence():
    addresses = ('4effb0', '415eb0', '415e30', '415e00', '406830', '768820', '768920', '768a70',
                 '416f80', '416440', '475730', '412c20', '4154a0')
    return {'classification': 'native_matrix_derivation_not_render_validation',
        'functions': [reference(NATIVE/('0x'+address+'.c')) for address in addresses],
        'native_matrix_row_major': 'Rx(YPR.z) * Ry(YPR.y) * Rz(YPR.x - YAW)',
        'ue_matrix_row_major': 'diag(1,-1,1) * native_matrix * diag(1,-1,1)',
        'translation': 'TRIGGER POSITION: (100*x,-100*y,100*z), no arrival-point substitution',
        'travel_heading': '416f80: h=radians(POINTYAW-90); 4154a0: forward=(-sin(h),cos(h),0); UE forward=(-sin(h),-cos(h),0); pawn yaw=-degrees(h)-90.',
        'asm_anchors': [
            {'va': '0x415e88', 'bytes_hex': '8b5424448b742440525656e868ffffff',
             'meaning': 'Mode2 pushes new,current,out to 415e00.'},
            {'va': '0x415e00', 'bytes_hex': '8b44240883ec345657508d4c240c518b4c2450e8180affff',
             'meaning': '415e00 passes current as rhs, new in ECX to 406830.'},
            {'va': '0x85aded', 'bytes_hex': 'd9ff', 'meaning': '85ad80 fallback evaluates FCOS.'},
            {'va': '0x85af5d', 'bytes_hex': 'd9fe', 'meaning': '85aef0 fallback evaluates FSIN.'},
            {'va': '0x4154c5', 'bytes_hex': 'd9471056e8225a4400d95c2408d94424088b742410d9e0d91e',
             'meaning': 'Forward.x = -sin(stored heading); next call to 85ad80 supplies Forward.y = cos.'}],
        'precision': 'Analytic double evaluation of source degree fields; native f32/radian roundoff not bit-reproduced.',
        'runtime_visual_validation': 'pending'}


def validate_pose_report(doors):
    """Check actual original binary anchors and all DAT poses without loading UE."""
    import pefile
    executable = ROOT/'local/native/Bully.steam-original.exe.unpacked.exe'
    binary = reference(executable)
    if binary['sha256'] != '457b595ae70e92031b04954de36525e52dd9ab5eb5f7364480ad2542e8a7d67b':
        raise ValueError('Native executable differs from retained decompilation')
    derivation = rotation_evidence()
    pe = pefile.PE(str(executable), fast_load=True)
    with executable.open('rb') as stream:
        for anchor in derivation['asm_anchors']:
            expected = bytes.fromhex(anchor['bytes_hex'])
            offset = pe.get_offset_from_rva(int(anchor['va'], 16)-pe.OPTIONAL_HEADER.ImageBase)
            stream.seek(offset)
            if stream.read(len(expected)) != expected:
                raise ValueError('Native rotation ABI anchor differs')
            anchor['file_offset'] = offset
            anchor['original_bytes_verified'] = True
    pe.close()
    def cross(a, b):
        return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
    records = []
    for door in doors['objects']:
        pose = door['pose']
        matrix = native_door_matrix(pose['yaw_pitch_roll_degrees_raw'], pose['yaw_degrees_raw'])
        transform = door_transform(pose)
        q = transform['rotation_quaternion_xyzw']
        worst = 0.0
        # Compare the independently expressed quaternion cross-product action
        # with original matrix action at four entity points, including origin.
        for point in ([0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]):
            game = [sum(matrix[r*3+c]*point[c] for c in range(3))+pose['position_source_game_m'][r] for r in range(3)]
            expected = [100*game[0], -100*game[1], 100*game[2]]
            ue = [100*point[0], -100*point[1], 100*point[2]]
            a = cross(q[:3], ue)
            b = cross(q[:3], a)
            actual = [ue[i]+2*(q[3]*a[i]+b[i])+transform['translation_cm'][i] for i in range(3)]
            worst = max(worst, max(abs(a-b) for a, b in zip(actual, expected)))
        if worst > 1e-7 or abs(sum(v*v for v in q)-1) > 1e-12:
            raise ValueError('DAT pose basis conversion failed')
        records.append({'id': door['id'], 'source': door['source'], 'trigger_sha256': door['trigger']['raw_sha256'],
            'pose_raw': pose, 'native_matrix_row_major': matrix, 'unreal_transform': transform,
            'basis_points_sampled': 4, 'max_coordinate_error_cm': worst})
    return {'schema': 'rea-bully-door-pose-validation-v1', 'status': 'native_math_verified_engine_pending',
        'native_executable': binary, 'source_catalog': reference(ROOT/'evidence/door-objects-v05.json'),
        'rotation_derivation': derivation, 'doors': records,
        'summary': {'doors': len(records), 'basis_points_sampled': 4*len(records),
                    'max_coordinate_error_cm': max(r['max_coordinate_error_cm'] for r in records)},
        'tests': 'tests/test_world_doors.py: native ABI bytes, noncommuting basis and original school doors',
        'ready_for_unreal_visual_claim': False, 'runtime_integrated_by_this_report': False,
        'limitations': ['Mathematical placement proof only; no rendered alignment or collision test.',
            'Native f32 conversion of parsed degrees to radians is approximated analytically, not bit-reproduced.']}


def _gltf(path):
    data = Path(path).read_bytes()
    magic, version, size, count, kind = struct.unpack_from('<IIIII', data)
    if (magic, version, size, kind) != (0x46546c67, 2, len(data), 0x4e4f534a):
        raise ValueError('Invalid door GLB')
    return json.loads(data[20:20+count])


def load_models():
    """Normalize existing eleven props, four strict exports and explicit TadGates variant.

    This authorizes importing candidates for engine validation; it does not
    relabel the five new exports as independently deformed-pose validated.
    """
    paths = [ROOT/'evidence/door-objects-v05.json', ROOT/'evidence/skinned-props-export-v04-rgb.json',
             ROOT/'evidence/skinned-props-validation-v04-rgb-final.json', ROOT/'evidence/door-models-export-v05.json',
             ROOT/'evidence/door-tadgates-base-material-v05.json']
    doors, props, validation, additional, variant = map(read, paths)
    if validation.get('status') != 'passed' or validation.get('failures'):
        raise ValueError('Baseline prop validation did not pass')
    if (ROOT/validation['source_report']).resolve() != paths[1].resolve():
        raise ValueError('Prop validation belongs to a different report')
    verify(doors['conversion_report'])
    baseline_keys = {m['model_key'] for m in doors['models']}
    definitions = {m['model_key']: m['definition'] for m in doors['models']}
    validated = {m['model_key'] for m in validation['models']}
    models = []
    for model in props['models']:
        if model['model_key'] not in baseline_keys:
            continue
        if model['status'] != 'exported' or model['model_key'] not in validated:
            raise ValueError('Unvalidated baseline door model')
        models.append(dict(model, door_definition=definitions[model['model_key']],
                           door_validation='baseline_structural_and_pose_validation'))
    if {m['model_key'] for m in models} != baseline_keys:
        raise ValueError('Missing baseline door source')
    for row in additional['models']:
        if row['status'] == 'exported_pending_independent_pose_validation':
            verify(row['source_nif_archive'])
            verify(row['source_texture_archive'])
            verify(row['animation_provenance']['source_agr'])
            models.append(dict(row['conversion'], door_definition=row['definition'],
                               door_validation='strict_source_export_engine_pose_review_pending'))
    if variant['material_policy'] != 'base_material_only' or variant['normalized_or_replaced_rgb']:
        raise ValueError('Unapproved TadGates material policy')
    original = variant['source']
    verify(original['source_nif_archive'])
    verify(original['source_texture_archive'])
    verify(original['animation_provenance']['source_agr'])
    conversion = variant['conversion']
    source_path = verify({'path': conversion['output'], 'sha256': conversion['output_sha256']})
    document = _gltf(source_path)
    joints = document['skins'][0]['joints']
    names = [document['nodes'][joint]['name'] for joint in joints]
    source_names = [bone['name'] for bone in original['source_bones']]
    if names != source_names:
        raise ValueError('TadGates hierarchy differs from original')
    clips = [dict(c, glb_name=c['name'].replace('\\', '_')) for c in variant['clips']]
    if [a['name'] for a in document.get('animations', [])] != [c['glb_name'] for c in clips]:
        raise ValueError('TadGates clip inventory differs')
    skin = read(verify(conversion['skin_report']))
    models.append({'model_key': variant['model_key'], 'status': 'exported',
        'door_definition': original['definition'],
        'output': conversion['output'], 'output_sha256': conversion['output_sha256'],
        'bone_names': names, 'bones': len(names), 'clips': clips,
        'preserved_global_skin_transform': skin.get('preserved_geometry_skin_transform_wrapper'),
        'material_policy': 'base_material_only', 'shading_pending': True,
        'door_validation': 'explicit_material_variant_engine_pose_review_pending'})
    if len({m['model_key'] for m in models}) != len(models):
        raise ValueError('Duplicate door model identity')
    for model in models:
        verify({'path': model['output'], 'sha256': model['output_sha256']})
    return {'schema': 'rea-bully-door-import-candidates-v1', 'models': models,
            'source_catalogs': [reference(p) for p in paths],
            'validation_status': 'source_identity_checked_engine_validation_required'}


def action_clips(model):
    result = {}
    for suffix in ('IDLE', 'OPEN', 'CLOSE'):
        matches = [c for c in model['clips'] if c['name'].endswith('\\'+suffix)]
        if len(matches) != 1:
            raise ValueError('Door has ambiguous own clip: ' + model['model_key'] + '/' + suffix)
        result[suffix] = matches[0]['name']
    return result


def travel_yaw(native_heading_radians):
    # The explorer actor's X axis is forward; native ped heading zero uses +Y.
    if not math.isfinite(native_heading_radians):
        raise ValueError('Nonfinite original arrival heading')
    return -math.degrees(native_heading_radians)-90.0


def assign_zone(obj, world_zones, area_name):
    candidates = [z for z in world_zones if z['area'] == area_name and z.get('bounds')]
    if area_name != 'MainMap':
        if len(candidates) <= 1:
            return candidates[0]['name'] if candidates else None
        # Several original IPBs can belong to the same area (Midway). This
        # chooses only a streaming owner; it never changes the source DAT pose.
        position = obj['pose']['position_unreal_cm']
        contained = [z for z in candidates if all(
            z['bounds']['min'][i] <= position[i] <= z['bounds']['max'][i] for i in range(3))]
        if not contained:
            return None
        return min(contained, key=lambda z: (
            math.prod(z['bounds']['max'][i]-z['bounds']['min'][i] for i in range(3)), z['name']))['name']
    position = obj['pose']['position_unreal_cm']
    # Ownership only controls streaming. This is explicitly a reconstructed
    # partition; the DAT area/pose/model identity does not depend on this choice.
    candidates = [z for z in candidates if z.get('runtime_enabled') and not z['name'].startswith('zone_')
                  and z['name'] not in ('tGlobal', 'ftest')]
    contained = [z for z in candidates if all(z['bounds']['min'][i] <= position[i] <= z['bounds']['max'][i] for i in (0, 1))]
    if not contained:
        return None
    return min(contained, key=lambda z: ((z['bounds']['max'][0]-z['bounds']['min'][0]) *
        (z['bounds']['max'][1]-z['bounds']['min'][1]), z['name']))['name']


def plan(doors, model_catalog, imports, world_plan, links, area_catalog):
    models = {m['model_key']: m for m in model_catalog['models']}
    areas = {a['area_code']: a['area_name'] for a in area_catalog['area_enum']['entries']}
    link_by_id = {link['id']: link for link in links['links']}
    available_areas = {z['area'] for z in world_plan['zones']}
    zones = {z['name']: {'name': z['name'], 'area': z['area'], 'placements': []} for z in world_plan['zones']}
    pending, excluded = [], []
    for obj in doors['objects']:
        definition = obj['definition']
        candidates = [m for m in models.values() if definition and all(
            m['door_definition'][field] == definition[field]
            for field in ('model_id', 'name', 'source_sha256', 'line'))]
        if len(candidates) > 1:
            raise ValueError('Ambiguous door model definition')
        model_key = candidates[0]['model_key'] if candidates else obj['model_key']
        if obj['model_key'] and model_key != obj['model_key']:
            raise ValueError('Door model definition identity changed')
        base = {'id': obj['id'], 'model_key': model_key, 'area_code': obj['area_code_raw']}
        if obj['is_mission_specific_raw'] is not False or obj['source_isdoor_raw'] is not True:
            excluded.append(dict(base, reason='not_an_explicit_nonmission_door'))
            continue
        unresolved_issues = [issue for issue in obj['issues'] if issue !=
            'Original model is not present in the existing skinned conversion report']
        if obj['ambiguous_definition_candidates'] or unresolved_issues:
            pending.append(dict(base, reason='source_definition_or_pose_ambiguous'))
            continue
        area = areas[obj['area_code_raw']]
        zone = assign_zone(obj, world_plan['zones'], area)
        if not zone:
            pending.append(dict(base, reason='area_or_streaming_zone_not_in_selected_world'))
            continue
        model = models.get(model_key)
        imported = imports.get(model_key)
        if not model or not imported:
            pending.append(dict(base, reason='door_model_export_pending' if not model else 'unreal_door_import_pending', zone=zone))
            continue
        clips = action_clips(model)
        if (imported.get('status') != 'door_assets_verified' or imported.get('source_sha256') != model['output_sha256']
                or imported.get('model_key') != model_key or imported.get('original_clips') != clips):
            raise ValueError('Imported door source identity changed: ' + model_key)
        transitions = []
        for ref in obj['existing_transition_links']:
            link = link_by_id[ref['id']]
            trigger = link['source_trigger']
            if (link['kind'] != 'DT' or not link['destination_coordinate_proven'] or
                    trigger['is_mission_specific_raw'] is not False):
                continue
            if (link['original_name_hash'] != obj['name_hash'] or trigger['source']['sha256'] != obj['source']['sha256']
                    or trigger['line'] != obj['trigger']['line']):
                raise ValueError('Door transition is not bound to this exact original trigger')
            destination = link['destination_position']
            transitions.append({'id': link['id'], 'area': link['destination_area_name'],
                'position_cm': destination['ue_position_cm'],
                'yaw_degrees': travel_yaw(destination['native_heading_radians']),
                'native_heading_radians': destination['native_heading_radians'],
                'area_in_world_plan': link['destination_area_name'] in available_areas,
                'source': link['destination_point_list']['source'],
                'source_line': destination['line']})
        if len(transitions) > 1:
            raise ValueError('Ambiguous original transition destination')
        placement = dict(base, name=obj['name'], area=area, data=imported['data'],
            source_sha256=model['output_sha256'], transform=door_transform(obj['pose']),
            source={'dat': obj['source'], 'trigger_line': obj['trigger']['line'],
                    'trigger_sha256': obj['trigger']['raw_sha256'], 'perimeter_line': obj['perimeter']['line']},
            original_pose=obj['pose'], source_locked_raw=obj['source_islocked_raw'],
            exploration_unlocked=True, original_clips=clips,
            material_policy=imported.get('material_policy', 'exported_original_materials'),
            shading_pending=imported.get('shading_pending', False),
            transition=transitions[0] if transitions else None,
            reconstruction_policy={'interaction': 'nearest_door_E_original_OPEN_CLOSE',
                'collision': 'closed_imported_mesh_bounds_box_disabled_while_moving_or_occupied',
                'lock': 'exploration_unlocked_source_raw_retained_saved_and_script_state_not_recovered',
                'activation': 'nonmission_DAT_area_gating_chapter_and_daily_schedule_not_recovered',
                'streaming_owner': 'smallest_containing_mainmap_zone_XY_AABB_or_exact_area_then_smallest_containing_XYZ_AABB'},
            visual_validation='pending_engine_pose_review')
        zones[zone]['placements'].append(placement)
    used = [z for z in zones.values() if z['placements']]
    return {'schema': 'rea-bully-world-door-plan-v1', 'zones': used, 'pending': pending, 'excluded': excluded,
        'summary': {'source_doors': len(doors['objects']), 'placed': sum(len(z['placements']) for z in used),
                    'pending': len(pending), 'excluded': len(excluded)},
        'rotation_derivation': rotation_evidence(),
        'limitations': ['Native DAT placement and original clips are retained; engine pose validation remains separate.',
            'Locks, E interaction, closed collision proxy and streaming ownership are exploration reconstruction.',
            'Only exact original DT destinations are offered; unavailable areas cannot be entered.',
            'No mission-specific doors, inferred paired leaves or ACT chapter/saved-state scheduling is enabled.']}


def main():
    below_normal()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--world-plan', type=Path)
    parser.add_argument('--validate-pose-report', action='store_true')
    parser.add_argument('--imports', type=Path, default=ROOT/'local/world-import/doors')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    paths = [ROOT/'evidence/door-objects-v05.json', ROOT/'evidence/world-door-links.json', ROOT/'evidence/world-zone-links.json']
    doors, links, areas = map(read, paths)
    if args.validate_pose_report:
        result = validate_pose_report(doors)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2)
        print(json.dumps(result['summary']))
        return
    if not args.world_plan:
        parser.error('--world-plan is required for the import placement plan')
    models = load_models()
    imports = {p.stem: read(p) for p in args.imports.glob('*.json')}
    result = plan(doors, models, imports, read(args.world_plan), links, areas)
    result['source_catalogs'] = models['source_catalogs'] + [reference(p) for p in [*paths, args.world_plan]]
    result['import_checkpoints'] = [reference(args.imports/(key+'.json')) for key in sorted(imports)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result['summary']))


if __name__ == '__main__':
    main()

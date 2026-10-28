"""Promote six CPU pose-validated NPC candidates to a separate UE import manifest.

Existing Bucky/Seth records remain byte-equivalent JSON values. Promotion allows
engine import; it does not claim original-game visual or collision equivalence.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT/'evidence/npc-import-manifest-v04.json'
BASELINE_SHA = 'bd5374ef4474006b6a73496eaa8499622faad57142f41e20583e2606e869b3fc'
CANDIDATES = ROOT/'evidence/npc-candidate-manifest-v05.json'
CANDIDATES_SHA = '5ef807865792c730e8e1d064a055f969a65dfc051f99b3e09c4e40938a41f6f7'
NUMERIC = ROOT/'evidence/npc-candidate-math-validation-v05.json'
NUMERIC_SHA = '18c1eaf4a331dfd71e21a0a4269d85d478847c622be705283341be04f69c14cc'
EXPECTED_IDS = {9, 11, 72, 73, 85, 99}
ROLES = {'idle', 'walk', 'run', 'sprint', 'ambient_idle'}


def reference(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def checked(ref):
    path = Path(ref['path'])
    path = path if path.is_absolute() else ROOT/path
    if reference(path)['sha256'] != ref['sha256']:
        raise ValueError('NPC evidence/source SHA256 mismatch: ' + str(path))
    return path


def pinned(path, sha):
    return json.loads(checked({'path': str(path), 'sha256': sha}).read_text())


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def verify_npc(npc):
    checked(npc['source_glb'])
    if npc['bone_count'] != 36 or npc['skin_own_bind'] is not True or npc['reuse_jimmy_skeleton'] is not False:
        raise ValueError('NPC does not retain its own 36-bone skin')
    if len(npc['clips']) != 5 or {c['role'] for c in npc['clips']} != ROLES:
        raise ValueError('NPC is missing exact locomotion/idle roles')
    for clip in npc['clips']:
        if (clip['glb_animation_name'] != clip['original_name'].replace('\\', '_')
                or not numeric(clip['duration']) or clip['duration'] <= 0
                or not numeric(clip['source_speed_cm_per_second']) or clip['source_speed_cm_per_second'] < 0):
            raise ValueError('NPC clip identity or source duration/speed is invalid')


def bounds_union(samples):
    if not samples:
        raise ValueError('NPC pose samples are missing')
    for sample in samples:
        lo, hi = sample['bounds_min'], sample['bounds_max']
        if (len(lo) != 3 or len(hi) != 3 or not all(numeric(v) for v in (*lo, *hi))
                or any(a >= b for a, b in zip(lo, hi)) or not 1.2 < hi[2]-lo[2] < 2.4
                or max(hi[i]-lo[i] for i in (0, 1)) > 2.0):
            raise ValueError('NPC pose bounds are nonfinite or outside verified human scale')
    return [[min(s['bounds_min'][i] for s in samples) for i in range(3)],
            [max(s['bounds_max'][i] for s in samples) for i in range(3)]]


def capsule_measurements(npc, posed):
    if (posed['ped_id'] != npc['ped_id'] or posed['model'] != npc['model']
            or posed['source_glb']['sha256'] != npc['source_glb']['sha256']
            or checked(posed['source_glb']).resolve() != checked(npc['source_glb']).resolve()
            or posed['export_report'] != npc['export_report'] or posed['bones'] != 36
            or posed['own_skin'] is not True or not numeric(posed['weight_sum_error'])
            or not 0 <= posed['weight_sum_error'] <= 1e-5):
        raise ValueError('Blender pose validation belongs to a different or invalid NPC skin')
    clips = {c['role']: c for c in posed['clips']}
    if len(posed['clips']) != 5 or set(clips) != ROLES:
        raise ValueError('Blender did not validate all original NPC roles')
    all_samples = []
    for original in npc['clips']:
        clip = clips[original['role']]
        if clip['name'] != original['glb_animation_name'] or clip['duration'] != original['duration']:
            raise ValueError('Blender validated a different clip or duration')
        samples = clip['samples']
        times = [s['time_seconds'] for s in samples]
        if (len(samples) < 9 or not all(numeric(t) for t in times)
                or not all(a < b for a, b in zip(times, times[1:]))
                or abs(times[0]) > 1e-7 or abs(times[-1]-clip['duration']) > 1e-6):
            raise ValueError('Blender pose validation has incomplete time coverage')
        bounds_union(samples)
        all_samples.extend(samples)
    idle = bounds_union(clips['idle']['samples'])
    envelope = bounds_union(all_samples)
    if abs(idle[0][2]) > .15:
        raise ValueError('Measured idle floor offset needs explicit runtime correction')
    # Runtime mesh origin sits at the capsule bottom. Include the full sampled
    # vertical extent and four cm padding per half-height; round upward to cm.
    half_height = math.ceil(50*max(envelope[1][2], envelope[1][2]-envelope[0][2])+4)
    if not 65 <= half_height <= 130:
        raise ValueError('Measured NPC capsule height is outside the supported range')
    return {'idle_bounds_metres_original_z_up': idle,
            'idle_height_cm': 100*(idle[1][2]-idle[0][2]), 'idle_floor_min_z_cm': 100*idle[0][2],
            'capsule_half_height_cm': half_height,
            'capsule_measurement': {'sampled_animation_bounds_metres_original_z_up': envelope,
                'pose_samples': len(all_samples), 'padding_per_half_height_cm': 4,
                'rounding': 'ceil to whole centimetre', 'runtime_capsule_radius_cm': 33,
                'policy': 'Conservative sampled vertical envelope; existing runtime mesh origin and capsule radius retained',
                'ground_and_collision_validation': 'pending_unreal'}}


def baseline():
    manifest = pinned(BASELINE, BASELINE_SHA)
    if {n['ped_id'] for n in manifest['npcs']} != {8, 50} or len(manifest['npcs']) != 2:
        raise ValueError('Verified baseline NPC identities changed')
    for npc in manifest['npcs']:
        verify_npc(npc)
    return manifest


def build(cpu_validation, *, include_baseline=True):
    old = baseline()
    candidate = pinned(CANDIDATES, CANDIDATES_SHA)
    math_report = pinned(NUMERIC, NUMERIC_SHA)
    cpu_validation = Path(cpu_validation).resolve()
    posed = json.loads(cpu_validation.read_text())
    if (posed.get('schema') != 'rea-bully-npc-blender-cpu-validation-v1'
            or posed.get('passed') is not True or posed.get('status') != 'passed_cpu_pose_validation_pending_unreal'
            or posed.get('background') is not True or posed.get('render_performed') is not False
            or posed['candidate_manifest'] != reference(CANDIDATES)):
        raise ValueError('A passing source-bound CPU Blender report is required for NPC promotion')
    checked(posed['validator'])
    if checked(posed['validator']).resolve() != (ROOT/'tools/validate_npc_blender.py').resolve():
        raise ValueError('Unexpected NPC pose validator')
    if math_report.get('passed') is not True or checked(math_report['candidate_manifest']).resolve() != CANDIDATES.resolve():
        raise ValueError('Original NPC numeric/source validation is missing')
    if ({n['ped_id'] for n in candidate['npcs']} != EXPECTED_IDS or len(candidate['npcs']) != 6
            or {n['ped_id'] for n in posed['models']} != EXPECTED_IDS or len(posed['models']) != 6
            or {n['ped_id'] for n in math_report['models']} != EXPECTED_IDS or len(math_report['models']) != 6):
        raise ValueError('Promotion requires all six exact original candidate identities')
    models = copy.deepcopy(old['npcs']) if include_baseline else []
    for npc in candidate['npcs']:
        verify_npc(npc)
        for key in ('export_report', 'original_model', 'original_texture'):
            checked(npc[key])
        pose = next(m for m in posed['models'] if m['ped_id'] == npc['ped_id'])
        source_math = next(m for m in math_report['models'] if m['ped_id'] == npc['ped_id'])
        if source_math['sources']['glb'] != npc['source_glb']:
            raise ValueError('Numeric proof belongs to different candidate GLB bytes')
        measurements = capsule_measurements(npc, pose)
        row = {k: copy.deepcopy(npc[k]) for k in ('ped_id', 'model', 'ped_type', 'female', 'source_glb',
               'export_report', 'original_model', 'original_texture', 'ide_line', 'action_root', 'action_file',
               'profile', 'bone_count', 'skin_own_bind', 'reuse_jimmy_skeleton', 'clips')}
        row.update(status='cpu_pose_verified_pending_unreal', unreal_import_name=npc['model'],
                   default_mesh_yaw_degrees=90, coordinate_system=old['npcs'][0]['coordinate_system'],
                   **measurements)
        row['validation_limitations'] = {
            'unanimated_rest_max_vertex_difference_m': source_math['skin']['rest_pose']['max_vertex_distance_m'],
            'rest_matrix_loss_repaired': False,
            'weight_source': 'Original NiSkinData normalized per vertex; no substitution with partition weights',
            'skin_partition_comparisons': [s['skin_partition_comparison'] for s in source_math['skin']['shapes']],
            'original_runtime_weight_selection': 'unresolved', 'visual_parity': 'not_verified',
            'unreal_import_and_collision': 'pending'}
        models.append(row)
    return {'schema': 'rea-bully-npc-import-manifest-v2', 'status': 'cpu_pose_verified_ready_for_unreal_import',
            'ready_for_unreal_import': True, 'runtime_validated': False,
            'selection': 'baseline_plus_six' if include_baseline else 'six_candidates_only',
            'baseline_manifest': reference(BASELINE), 'candidate_manifest': reference(CANDIDATES),
            'numeric_validation': reference(NUMERIC), 'cpu_pose_validation': reference(cpu_validation),
            'generator': reference(__file__), 'npcs': models,
            'limitations': list(math_report['limitations']) + [
                'Actual Blender CPU pose checks add sampled animation bounds; previous rest-matrix and partition-weight caveats remain.',
                'Conservative capsule heights are import candidates; Unreal ground contact, collisions and visual checks remain required.',
                'Bucky and Seth records are preserved from the verified v04 manifest.']}


def load_runtime_manifest(path):
    path = Path(path).resolve()
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() == BASELINE_SHA:
        return baseline()
    manifest = json.loads(raw)
    if manifest.get('schema') != 'rea-bully-npc-import-manifest-v2':
        raise ValueError('NPC candidates require a promoted runtime manifest')
    for key in ('baseline_manifest', 'candidate_manifest', 'numeric_validation', 'cpu_pose_validation', 'generator'):
        checked(manifest[key])
    if manifest['selection'] not in ('baseline_plus_six', 'six_candidates_only'):
        raise ValueError('Unexpected NPC runtime selection')
    expected = build(checked(manifest['cpu_pose_validation']), include_baseline=manifest['selection'] == 'baseline_plus_six')
    if manifest != expected:
        raise ValueError('NPC runtime manifest differs from its verified promotion evidence')
    return manifest


def half_height(npc):
    value = npc.get('capsule_half_height_cm', npc['idle_height_cm']/2+4)
    if not numeric(value) or not 40 <= value <= 130:
        raise ValueError('Invalid NPC capsule half height')
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpu-validation', type=Path, default=ROOT/'evidence/npc-blender-validation-v06.json')
    parser.add_argument('--output', type=Path, default=ROOT/'evidence/npc-import-manifest-v06.json')
    parser.add_argument('--new-only', action='store_true')
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('New NPC runtime manifest path required')
    result = build(args.cpu_validation, include_baseline=not args.new_only)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({'manifest': str(args.output), 'ped_ids': [n['ped_id'] for n in result['npcs']],
                      'half_heights_cm': {n['ped_id']: half_height(n) for n in result['npcs']}}))

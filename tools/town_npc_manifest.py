"""Promote measured outdoor NPCs while preserving the 23 verified school/recovered identities."""
import argparse
import copy
import json
from pathlib import Path

from prepare_npc_runtime_manifest import (ROOT, reference, checked, verify_npc,
    capsule_measurements)
from world_npc_manifest import load_runtime_manifest as load_previous
from plan_ambient_cast import inspect_candidate
from town_npc_profiles import TOWN_IDS, register

SCHEMA = 'rea-bully-town-npc-import-manifest-v1'
BASELINE = ROOT/'evidence/npc-import-manifest-v07-r4.json'


def build(candidate_path, numeric_path, cpu_path):
    candidate_path, numeric_path, cpu_path = map(Path, (candidate_path, numeric_path, cpu_path))
    previous = load_previous(BASELINE)
    candidate = json.loads(candidate_path.read_text())
    numeric, posed = (json.loads(p.read_text()) for p in (numeric_path, cpu_path))
    population = json.loads(checked(candidate['population_catalog']).read_text())
    checked(candidate['generator']); checked(candidate['profile_adapter']); checked(candidate['cat_catalog'])
    peds = register(population['ped_definitions'])
    ids = [npc['ped_id'] for npc in candidate['npcs']]
    pending = [npc['ped_id'] for npc in candidate.get('pending', [])]
    if not ids or sorted(ids+pending) != sorted(TOWN_IDS):
        raise ValueError('Outdoor identity scope lost or duplicated candidates')
    for proof in (numeric, posed):
        if (proof.get('passed') is not True or proof['candidate_manifest'] != reference(candidate_path)
                or sorted(row['ped_id'] for row in proof['models']) != sorted(ids)):
            raise ValueError('Independent validation does not cover this exact candidate cast')
    if (posed['background'] is not True or posed['render_performed'] is not False
            or posed['status'] != 'passed_cpu_pose_validation_pending_unreal'):
        raise ValueError('Actual background CPU pose proof required')
    checked(posed['validator']); checked(posed['pose_validator'])
    for ref in numeric['validator_sources']:
        checked(ref)
    models = copy.deepcopy(previous['npcs'])
    for npc in candidate['npcs']:
        verify_npc(npc)
        checked(npc['original_model'])
        checked(npc['original_texture'])
        if npc != inspect_candidate(checked(npc['export_report']), peds[npc['ped_id']]):
            raise ValueError('Candidate manifest differs from original source/clip proof')
        pose = next(row for row in posed['models'] if row['ped_id'] == npc['ped_id'])
        math = next(row for row in numeric['models'] if row['ped_id'] == npc['ped_id'])
        if math['sources']['glb'] != npc['source_glb']:
            raise ValueError('Numeric comparison targets a different skin')
        row = copy.deepcopy(npc)
        row.update(status='cpu_pose_verified_pending_unreal', ready_for_unreal=True,
                   unreal_import_name=npc['model'], default_mesh_yaw_degrees=90,
                   coordinate_system=previous['npcs'][0]['coordinate_system'],
                   **capsule_measurements(npc, pose))
        row['validation_limitations'] = {
            'rest_pose_max_vertex_difference_m': math['skin']['rest_pose']['max_vertex_distance_m'],
            'skin_partition_comparisons': [s['skin_partition_comparison'] for s in math['skin']['shapes']],
            'source_weight_selection': 'NiSkinData normalized; original CPU/GPU selection remains unresolved',
            'visual_parity': 'not_verified', 'unreal_import_and_collision': 'pending'}
        models.append(row)
    return {'schema': SCHEMA, 'status': 'cpu_pose_verified_ready_for_unreal_import',
            'ready_for_unreal_import': True, 'runtime_validated': False,
            'baseline_manifest': reference(BASELINE), 'candidate_manifest': reference(candidate_path),
            'numeric_validation': reference(numeric_path), 'cpu_pose_validation': reference(cpu_path),
            'generator': reference(__file__), 'npcs': models, 'pending_candidates': candidate.get('pending', []),
            'limitations': copy.deepcopy(numeric['limitations'])+[
                'The 23 school/recovered identities are preserved as JSON values, with no mesh replacement or skeleton reuse.',
                'Named selection and population scheduling are reconstructed; original AI remains incomplete.',
                'Outdoor cast has passed CPU deformation checks; engine import and per-area floor/behavior checks remain required.']}


def load_runtime_manifest(path):
    manifest = json.loads(Path(path).read_text())
    if manifest.get('schema') != SCHEMA:
        return load_previous(path)
    for field in ('baseline_manifest', 'candidate_manifest', 'numeric_validation', 'cpu_pose_validation', 'generator'):
        checked(manifest[field])
    expected = build(checked(manifest['candidate_manifest']), checked(manifest['numeric_validation']),
                     checked(manifest['cpu_pose_validation']))
    if manifest != expected:
        raise ValueError('World NPC manifest differs from its actual independent evidence')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--numeric', type=Path, required=True)
    parser.add_argument('--cpu', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('New runtime manifest required')
    result = build(args.candidate, args.numeric, args.cpu)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({'npcs': len(result['npcs']), 'pending': len(result['pending_candidates'])}))

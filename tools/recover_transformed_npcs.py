"""Recover three original NPC skins through explicitly checked source transforms.

The historical character/NPC exporters are unchanged. Max and Pedro have
nonidentity shape transforms cancelled exactly by their overall skin matrix.
Earnest has a common pure translation, retained as an original skeleton wrapper.
"""
import argparse
import copy
import json
from pathlib import Path

import export_npc
from catalog_npc_locomotion import lower_priority
from export_character import CharacterNif
from plan_ambient_cast import inspect_candidate, source, verify_record
from validate_npc_candidates import nif_matrix, np, require
from world_npc_profiles import register

ROOT = Path(__file__).resolve().parents[1]
RECOVERED_IDS = (10, 49, 69)


def source_transforms(nif):
    parents = {}
    for i, kind in enumerate(nif.types):
        if kind == 'NiNode':
            for child in nif.block(i)['children']:
                require(child not in parents, 'Source geometry has multiple parents')
                parents[child] = i

    def world(index, seen=()):
        require(index not in seen, 'Source geometry hierarchy cycle')
        before = world(parents[index], (*seen, index)) if index in parents else np.eye(4)
        return before @ nif_matrix(nif.block(index))

    rows = []
    for i, kind in enumerate(nif.types):
        if kind not in ('NiTriShape', 'NiTriStrips') or nif.block(i)['skin'] < 0:
            continue
        shape = nif.block(i)
        data_id = nif.block(shape['skin'])['data']
        geometry = world(i)
        overall = nif_matrix(nif.block(data_id)['transform'])
        rows.append({'shape_block': i, 'skin_data_block': data_id,
                     'geometry_world': geometry.tolist(), 'overall_skin': overall.tolist(),
                     'product': (geometry @ overall).tolist(),
                     'local_shape_nonidentity': float(np.max(np.abs(nif_matrix(shape)-np.eye(4))))})
    require(bool(rows), 'No original skinned geometry')
    return rows


def recovery_options(ped_id, nif):
    require(ped_id in RECOVERED_IDS, 'Pedestrian is outside the explicit recovery scope')
    rows = source_transforms(nif)
    if ped_id in (49, 69):
        require(all(np.array_equal(row['product'], np.eye(4)) for row in rows),
                'Transformed NPC geometry/skin does not cancel exactly')
        require(any(row['local_shape_nonidentity'] > 1e-5 for row in rows),
                'Expected transformed original NPC shape')
        return {'allow_transformed_skin_shape': True}, rows
    product = np.asarray(rows[0]['product'])
    require(all(np.array_equal(row['product'], product) for row in rows),
            'Original shapes require different global transforms')
    require(np.array_equal(product[:3, :3], np.eye(3)) and
            np.array_equal(product[3], [0., 0., 0., 1.]),
            'Only an original common translation is supported')
    require(np.array_equal(product[:3, 3], [0., -0.013427734375, 0.]),
            'Earnest original geometry/skin translation changed')
    return {'preserve_global_skin_transform': True}, rows


def export_recovered(ped_id, population_path, output_dir, report_path):
    population = json.loads(population_path.read_text())
    peds = register(population['ped_definitions'])
    ped = peds[ped_id]
    model = verify_record(ped['model_candidates'][0])
    options, transforms = recovery_options(ped_id, CharacterNif(model))
    original = export_npc.export_skin

    def checked_skin(*args, **kwargs):
        require(Path(args[0]).resolve() == model.resolve(), 'Recovery changed pedestrian skin')
        return original(*args, **kwargs, **options)

    # This CLI is single-threaded. Restore the process-local adapter even when
    # export fails; no historical source file or existing output is rewritten.
    export_npc.export_skin = checked_skin
    try:
        result = export_npc.export(ped_id, population_path, ROOT/'evidence/animation-library-v04.json',
                                  ROOT/'evidence/npc-locomotion-cat-v05.json', output_dir, report_path)
    finally:
        export_npc.export_skin = original
    return {'ped_id': ped_id, 'export_report': source(report_path),
            'source_transform_audit': transforms, 'explicit_options': options,
            'output': result['output']}


def build(previous_path, output, recovery_report):
    require(not output.exists() and not recovery_report.exists(), 'New output paths required')
    previous = json.loads(previous_path.read_text())
    population_path = verify_record(previous['population_catalog'])
    population = json.loads(population_path.read_text())
    peds = register(population['ped_definitions'])
    require(sorted(row['ped_id'] for row in previous['pending']) == list(RECOVERED_IDS),
            'Unexpected pending recovery set')
    candidates = copy.deepcopy(previous['npcs'])
    for row in candidates:
        require(row == inspect_candidate(verify_record(row['export_report']), peds[row['ped_id']]),
                'Baseline candidate no longer matches its original sources')
    recovered = []
    for ped_id in RECOVERED_IDS:
        report = ROOT/f'evidence/npc-{ped_id:03d}-export-v07-recovered.json'
        directory = ROOT/f'local/converted/npcs-v07-recovered/{ped_id:03d}'
        recovered.append(export_recovered(ped_id, population_path, directory, report))
        candidates.append(inspect_candidate(report, peds[ped_id]))
        print(json.dumps({'ped_id': ped_id, 'status': 'exported_pending_pose_validation'}), flush=True)
    result = {**previous, 'generator': source(__file__),
              'previous_manifest': source(previous_path),
              'npcs': sorted(candidates, key=lambda row: row['ped_id']), 'pending': [],
              'recovery_scope': 'Two exact cancellation skins and one preserved original common translation; no bind edits'}
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    with recovery_report.open('x', encoding='utf-8') as stream:
        json.dump({'status': 'exported_pending_independent_validation', 'generator': source(__file__),
                   'candidate_manifest': source(output), 'recovered': recovered}, stream, indent=2)
    return result


if __name__ == '__main__':
    lower_priority()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous', type=Path, default=ROOT/'evidence/npc-candidate-manifest-v07-r2.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({'candidates': len(build(args.previous, args.output, args.report)['npcs'])}))

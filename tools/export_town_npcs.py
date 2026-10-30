"""Export a bounded, distinct outdoor cast with original skins and CAT references."""
import argparse
import json
from pathlib import Path

import export_npc
from catalog_npc_locomotion import catalog, lower_priority
from plan_ambient_cast import source, verify_record, inspect_candidate
from recover_transformed_npcs import ROOT, source_transforms
from validate_npc_candidates import CharacterNif, np, require
from town_npc_profiles import TOWN_IDS, register


def build(output, cat_path):
    require(not output.exists(), 'New town candidate manifest required')
    population_path = ROOT/'evidence/world-population.json'
    population = json.loads(population_path.read_text())
    verify_record(population['ped_definitions_source'])
    peds = register(population['ped_definitions'])
    cat = catalog()
    if cat_path.exists():
        require(json.loads(cat_path.read_text()) == cat, 'Stored town CAT proof changed')
    else:
        with cat_path.open('x', encoding='utf-8') as stream:
            json.dump(cat, stream, indent=2)
    candidates, pending = [], []
    for ped_id in TOWN_IDS:
        ped = peds[ped_id]
        report = ROOT/f'evidence/npc-{ped_id:03d}-town-export-v07.json'
        directory = ROOT/f'local/converted/town-npcs-v07/{ped_id:03d}'
        try:
            if not report.exists():
                nif = CharacterNif(verify_record(ped['model_candidates'][0]))
                products = [np.asarray(row['product']) for row in source_transforms(nif)]
                require(all(np.array_equal(product, products[0]) and
                            np.array_equal(product[:3, :3], np.eye(3)) and
                            np.array_equal(product[3], [0., 0., 0., 1.]) for product in products),
                        'Town NPC requires separately verified nontranslation/per-shape skin recovery')
                original = export_npc.export_skin

                def checked_skin(*args, **kwargs):
                    return original(*args, **kwargs, allow_transformed_skin_shape=True,
                                    preserve_global_skin_transform=True)

                export_npc.export_skin = checked_skin
                try:
                    export_npc.export(ped_id, population_path, ROOT/'evidence/animation-library-v04.json',
                                      cat_path, directory, report)
                finally:
                    export_npc.export_skin = original
            candidates.append(inspect_candidate(report, ped))
            print(json.dumps({'ped_id': ped_id, 'status': 'export_verified_pose_pending'}), flush=True)
        except (ValueError, RuntimeError) as exc:
            pending.append({'ped_id': ped_id, 'model': ped['model'], 'reason': str(exc), 'runtime_ready': False})
            print(json.dumps(pending[-1]), flush=True)
    result = {'schema': 'rea-bully-town-npc-candidates-v1',
              'status': 'exported_pending_independent_pose_validation', 'ready_for_unreal': False,
              'population_catalog': source(population_path), 'generator': source(__file__),
              'profile_adapter': source(ROOT/'tools/town_npc_profiles.py'), 'cat_catalog': source(cat_path),
              'npcs': candidates, 'pending': pending,
              'limitations': ['Named outdoor cast and scheduling are reconstructed, not recovered AI.',
                             'Exact original CAT links identify clips, not native action conditions.',
                             'Civilian idle bank is the first default.ide animation group by project policy; two source banks contain this hash and native precedence is unresolved.',
                             'Independent numeric, Blender and Unreal validation remain required.']}
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    return result


if __name__ == '__main__':
    lower_priority()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cat', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({'exported': len(build(args.output, args.cat)['npcs'])}))

"""Export an additional distinct exploration cast with original skins and CAT clips."""
import argparse
import json
from pathlib import Path

from catalog_npc_locomotion import lower_priority
from export_npc import export
from plan_ambient_cast import inspect_candidate, source, verify_record
from world_npc_profiles import ADDITIONAL, register

ROOT = Path(__file__).resolve().parents[1]


def build(output):
    if output.exists():
        raise FileExistsError('A new candidate manifest path is required')
    population_path = ROOT/'evidence/world-population.json'
    population = json.loads(population_path.read_text())
    verify_record(population['ped_definitions_source'])
    peds = register(population['ped_definitions'])
    candidates, pending = [], []
    for ped_id in ADDITIONAL:
        report = ROOT/f'evidence/npc-{ped_id:03d}-export-v07.json'
        directory = ROOT/f'local/converted/npcs-v07/{ped_id:03d}'
        try:
            if not report.exists():
                # A prior interrupted export has no verified checkpoint. Keep
                # its bytes for diagnosis and use an exclusive new directory.
                attempt = 1
                while directory.exists():
                    attempt += 1
                    directory = ROOT/f'local/converted/npcs-v07/{ped_id:03d}-attempt{attempt}'
                export(ped_id, population_path, ROOT/'evidence/animation-library-v04.json',
                       ROOT/'evidence/npc-locomotion-cat-v05.json', directory, report)
        except (ValueError, RuntimeError) as exc:
            pending.append({'ped_id': ped_id, 'model': peds[ped_id]['model'], 'reason': str(exc),
                            'directory': str(directory), 'runtime_ready': False})
            print(json.dumps({'ped_id': ped_id, 'status': 'source_conversion_pending', 'reason': str(exc)}), flush=True)
            continue
        # A resumed export is accepted only after checking its actual bytes,
        # original own skin, IDE identity and exact original CAT references.
        candidates.append(inspect_candidate(report, peds[ped_id]))
        print(json.dumps({'ped_id': ped_id, 'model': peds[ped_id]['model'], 'status': 'export_verified_pose_pending'}), flush=True)
    result = {'schema': 'rea-bully-world-npc-candidates-v1',
              'status': 'exported_pending_independent_pose_validation', 'ready_for_unreal': False,
              'population_catalog': source(population_path), 'generator': source(__file__),
              'profile_adapter': source(ROOT/'tools/world_npc_profiles.py'), 'npcs': candidates, 'pending': pending,
              'limitations': ['Named cast selection is reconstructed; no original population randomizer is claimed.',
                             'Original skinning, independent poses and runtime placement require separate validation.']}
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    return result


if __name__ == '__main__':
    lower_priority()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'evidence/npc-candidate-manifest-v07.json')
    args = parser.parse_args()
    print(json.dumps({'exported': len(build(args.output)['npcs'])}))

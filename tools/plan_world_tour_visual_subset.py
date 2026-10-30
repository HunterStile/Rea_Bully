"""Select nine unchanged source checkpoints for a small real-render capture tour.

This is an offline subset plan, not screenshots, a full 85-point tour, or a
runtime navigation promotion. Geometry risks remain attached to the selection.
"""
import argparse
import copy
import json
from pathlib import Path

from area_navigation_contracts import reference, verify_references

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT/'evidence/world-tour-plan-v07-r6.json'
DIAGNOSTICS = ROOT/'evidence/world-tour-source-refinement-v07-r3.json'
GROUPS = (
    ('campus', ('tschool', 'zone_school3', 'zone_school5', 'zone_school1')),
    ('downtown', ('zone_busines1', 'zone_busines3', 'zone_busines4')),
    ('poor_area', ('zone_busines6', 'zone_busines7', 'zone_busines8')),
    ('rich_area', ('trich', 'zone_rich3', 'zone_rich2')),
    ('industrial_area', ('tindust', 'zone_indust2', 'zone_indust3')),
    ('carnival', ('tcarni', 'zone_carn1')),
    ('school_hallways', ('ischool',)),
    ('library', ('isc_lib',)),
    ('midway_original_collision_helper', ('iballtos',)))


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def build(parent_path=PARENT, diagnostic_path=DIAGNOSTICS):
    parent_path, diagnostic_path = Path(parent_path), Path(diagnostic_path)
    parent, report = read(parent_path), read(diagnostic_path)
    verify_references(parent)
    verify_references(report)
    if len(parent['checkpoints']) != 85 or len({c['area'] for c in parent['checkpoints']}) != 47:
        raise ValueError('Expected the complete frozen 85-checkpoint/47-area source parent')
    by_index = {}
    for index, checkpoint in enumerate(parent['checkpoints']):
        matches = [row['selected_diagnostic'] for row in report['diagnostics']
                   if row['selected_diagnostic']['source_checkpoint'] == checkpoint]
        if len(matches) != 1:
            raise ValueError('Geometry selection proof does not match an exact parent checkpoint')
        by_index[index] = matches[0]
    selected, mapping = [], []
    used = set()
    for label, zones in GROUPS:
        candidates = [index for index, checkpoint in enumerate(parent['checkpoints'])
                      if checkpoint.get('zone') in zones and index not in used]
        if not candidates:
            raise ValueError('Required visual subject is absent from parent: '+label)
        index = min(candidates, key=lambda i: (len(by_index[i]['risks']),
                                              zones.index(parent['checkpoints'][i]['zone']), i))
        used.add(index)
        checkpoint, proof = parent['checkpoints'][index], by_index[index]
        selected.append(copy.deepcopy(checkpoint))
        mapping.append({'subject': label, 'parent_index_zero_based': index, 'checkpoint_name': checkpoint['name'],
                        'geometry_report': reference(diagnostic_path),
                        'geometry_risks': copy.deepcopy(proof['risks']),
                        'runtime_verified': False})
    sources = copy.deepcopy(parent.get('sources', []))
    for source in (reference(parent_path), reference(diagnostic_path), reference(__file__)):
        if source not in sources:
            sources.append(source)
    return {'schema': parent['schema'], 'status': 'offline_visual_subset_runtime_capture_pending',
            'parent_tour': reference(parent_path), 'geometry_selection_report': reference(diagnostic_path),
            'checkpoints': selected, 'subset_selection': mapping, 'pending': [], 'sources': sources,
            'full_parent_tour_validated': False, 'runtime_validated': False, 'screenshots_captured': False,
            'summary': {'checkpoints': len(selected), 'areas': len({c['area'] for c in selected}),
                        'parent_checkpoints': len(parent['checkpoints']), 'parent_areas': 47,
                        'omitted_parent_checkpoints': len(parent['checkpoints'])-len(selected),
                        'without_reported_geometry_risk': sum(not row['geometry_risks'] for row in mapping)},
            'selection_policy': 'Within each requested subject prefer fewer reported geometric risks, then declared zone preference and parent order; copy the entire parent checkpoint unchanged',
            'limitations': ['This nine-checkpoint visual subset cannot stand in for the full 85-point/47-area validation.',
                'No screenshot or runtime result exists merely because this plan is generated.',
                'Source coordinates, heading and every checkpoint field remain identical to the frozen parent.',
                'Midway is retained to inspect the original collision helper; its straight-walk geometry risk remains visible.',
                'Geometry diagnostics do not prove real rendering, collision, streaming or animation fidelity.']}


def validate(plan):
    verify_references(plan)
    if plan != build(plan['parent_tour']['path'], plan['geometry_selection_report']['path']):
        raise ValueError('Visual subset differs from its exact source parent/selection proof')
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent', type=Path, default=PARENT)
    parser.add_argument('--diagnostics', type=Path, default=DIAGNOSTICS)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('New visual subset path required')
    result = build(args.parent, args.diagnostics)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result['summary']))

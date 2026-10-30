"""Compare old/new NPC grounding rays and individual source-geometry capsules.

This is double-sided geometry diagnostics, not Chaos or runtime validation.
Original placement coordinates are read unchanged and never rewritten.
"""
import argparse
from collections import Counter
import json
from pathlib import Path

import numpy as np
from preflight_world_tour_geometry import (WorldGeometry, read, vertical_hits, reference, ROOT,
    point_triangle_distance, segment_distance)
from catalog_npc_locomotion import lower_priority
from prepare_npc_runtime_manifest import half_height
from town_npc_manifest import load_runtime_manifest
from plan_town_population import validate

RADIUS = 33.


def capsule_distance(triangle, feet, half):
    a = np.asarray(feet, dtype=float)+[0, 0, 2+RADIUS]
    b = a+[0, 0, 2*(half-RADIUS)]
    p, q, r = triangle
    direction, e1, e2 = b-a, q-p, r-p
    h = np.cross(direction, e2)
    determinant = np.dot(e1, h)
    if abs(determinant) > 1e-10:
        s = a-p
        u = np.dot(s, h)/determinant
        cross = np.cross(s, e1)
        v, t = np.dot(direction, cross)/determinant, np.dot(e2, cross)/determinant
        if 0 <= u <= 1 and 0 <= v and u+v <= 1 and 0 <= t <= 1:
            return 0.
    return min(point_triangle_distance(a, triangle), point_triangle_distance(b, triangle),
               *(segment_distance(a, b, p, q) for p, q in ((p, q), (q, r), (r, p))))


def geometry_for_both_policies(world, feet, area, half):
    # Two overlapping bounds queries cover new feet-400 and the old highest
    # ray hit plus the complete capsule above it, including tall NPC skins.
    triangles, metadata, seen = [], [], set()
    for offset in (-200., 3*half-280.):
        geometry, sources, _ = world.around({'name': 'npc_grounding_comparison', 'area': area,
                                            'position_cm': [feet[0], feet[1], feet[2]+offset]})
        for face, source in zip(geometry['intended'], sources['intended']):
            key = (source['kind'], source['model_key'], source['zone'], source['instance_index'],
                   tuple(source['mesh_primitive_triangle']))
            if key not in seen:
                seen.add(key)
                triangles.append(face)
                metadata.append(source)
    return np.asarray(triangles).reshape(-1, 3, 3), metadata


def policy_result(triangles, metadata, feet, half, low, high):
    hits = vertical_hits(triangles, feet[0], feet[1], low, high)
    if not hits:
        return {'ray_z_cm': [high, low], 'first_surface': None, 'capsule_overlaps': [],
                'risks': ['no_source_surface_in_ray']}
    index, z, normal = max(hits, key=lambda hit: hit[1])
    grounded = [feet[0], feet[1], z]
    low_bounds = np.asarray(grounded)-[RADIUS, RADIUS, 0]
    high_bounds = np.asarray(grounded)+[RADIUS, RADIUS, 2*half+2]
    possible = np.flatnonzero(np.all(triangles.max(axis=1) >= low_bounds, axis=1) &
                              np.all(triangles.min(axis=1) <= high_bounds, axis=1))
    overlapping = []
    for i in possible:
        distance = capsule_distance(triangles[i], grounded, half)
        if distance < RADIUS-.5:
            overlapping.append({'axis_distance_cm': distance, 'source_triangle': metadata[i]})
    overlapping.sort(key=lambda item: item['axis_distance_cm'])
    risks = []
    if abs(normal) < .7:
        risks.append('first_surface_exceeds_walkable_slope')
    if z-feet[2] > 40:
        risks.append('first_surface_above_source_feet_over_40cm')
    if feet[2]-z > 40:
        risks.append('vertical_drop_over_40cm_requires_runtime_review')
    if overlapping:
        risks.append('projected_capsule_intersects_source_geometry')
    return {'ray_z_cm': [high, low],
            'first_surface': {'height_cm': z, 'source_feet_delta_cm': z-feet[2],
                              'geometric_normal_z': normal, 'source_triangle': metadata[index]},
            'capsule_center_cm': [feet[0], feet[1], z+half+2],
            'capsule_overlap_count': len(overlapping), 'capsule_overlaps': overlapping[:12],
            'risks': risks}


def compare(world, placement, npc, area):
    feet, half = placement['position_cm'], half_height(npc)
    triangles, metadata = geometry_for_both_policies(world, feet, area, half)
    old = policy_result(triangles, metadata, feet, half, feet[2]+half-398, feet[2]+half+302)
    new = policy_result(triangles, metadata, feet, half, feet[2]-400, feet[2]+40)
    old_first, new_first = old['first_surface'], new['first_surface']
    return {'ped_id': npc['ped_id'], 'model': npc['model'], 'area': area, 'zone': placement['zone'],
            'original_position_cm': feet, 'source_reference': placement['source_reference'],
            'capsule_radius_cm': RADIUS, 'capsule_half_height_cm': half, 'old': old, 'new': new,
            'first_surface_height_changed': bool(old_first and new_first and abs(old_first['height_cm']-new_first['height_cm']) > .01),
            'runtime_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--population', type=Path, default=ROOT/'evidence/ambient-runtime-plan-v07-r5.json')
    parser.add_argument('--manifest', type=Path, default=ROOT/'evidence/npc-import-manifest-v07-r5.json')
    parser.add_argument('--catalog', type=Path, default=ROOT/'local/world-export-v07-material-recovery.json')
    parser.add_argument('--collisions', type=Path, default=ROOT/'evidence/world-collisions-export-v07-r2.json')
    parser.add_argument('--scope', type=Path, default=ROOT/'evidence/world-scope-v07.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('New comparison report required')
    lower_priority()
    plan, scope = read(args.population), read(args.scope)
    validate(plan)
    manifest = load_runtime_manifest(args.manifest)
    if plan['sources']['npc_manifest'] != reference(args.manifest):
        raise ValueError('Population and NPC manifest differ')
    catalog, collisions = read(args.catalog), read(args.collisions)
    if collisions['sources']['world_catalog'] != reference(args.catalog):
        raise ValueError('Collision and static catalog provenance differ')
    world = WorldGeometry(catalog, collisions)
    npcs = {n['ped_id']: n for n in manifest['npcs']}
    zones = {z['name']: z for z in scope['zones']}
    records = []
    for placement in plan['placements']:
        result = compare(world, placement, npcs[placement['ped_id']], zones[placement['zone']]['area'])
        records.append(result)
        print(json.dumps({'ped_id': result['ped_id'], 'height_changed': result['first_surface_height_changed'],
                          'old_risks': result['old']['risks'], 'new_risks': result['new']['risks']}), flush=True)
    report = {'schema': 'rea-bully-npc-grounding-source-comparison-v1', 'status': 'offline_diagnostic_runtime_pending',
              'sources': {name: reference(path) for name, path in vars(args).items() if name != 'output'},
              'generator': reference(__file__), 'geometry_tool': reference(ROOT/'tools/preflight_world_tour_geometry.py'),
              'runtime_source': reference(ROOT/'unreal/Source/ReaBullyRuntime/ReaBullyAmbient.cpp'),
              'geometry_sources_rehashed': world.sources, 'npcs': records,
              'policy': {'old': 'actor center +300 to -400 cm', 'new': 'source feet +40 to -400 cm',
                         'source_feet': 'actor location minus own capsule half-height and 2cm clearance',
                         'xy_and_original_positions_modified': False,
                         'projection_is_reconstructed': True, 'capsule_overlap_margin_cm': .5},
              'summary': {'npcs': len(records), 'changed_floor_candidates': sum(n['first_surface_height_changed'] for n in records),
                          'old_risk_counts': dict(Counter(r for n in records for r in n['old']['risks'])),
                          'new_risk_counts': dict(Counter(r for n in records for r in n['new']['risks']))},
              'limitations': ['Double-sided source triangles and completed import policy; no Chaos, engine face-winding, visibility channel or real streaming execution.',
                             'Capsule-to-triangle distances are geometric risk indicators, not engine sweeps or penetration resolution.',
                             'Animated props, doors and other NPC capsules are excluded.',
                             'No source placement, manifest or scene asset is rewritten. Grounding is an explicit reconstructed vertical projection.',
                             'Drops, steep surfaces and capsule intersections remain for the real runtime tour to assess.']}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(report['summary']), flush=True)


if __name__ == '__main__':
    main()

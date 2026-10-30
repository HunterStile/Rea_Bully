"""Compare NPC spawn-floor rays against original triangles without running Unreal."""
import argparse
from collections import Counter
import json
from pathlib import Path

from preflight_world_tour_geometry import WorldGeometry, read, vertical_hits, reference, ROOT
from catalog_npc_locomotion import lower_priority
from town_npc_manifest import load_runtime_manifest
from plan_town_population import validate
from prepare_npc_runtime_manifest import half_height


def diagnose(world, placement, npc, area):
    feet = placement['position_cm']
    half = half_height(npc)
    # Broaden WorldGeometry's query vertically so it contains the entire
    # actual NPC ray (center+300 to center-400), not only the player ray.
    query = {'name': 'NPC_'+str(npc['ped_id']), 'area': area, 'zone': placement['zone'],
             'position_cm': [feet[0], feet[1], feet[2]+half-198]}
    geometry, metadata, placements = world.around(query)
    low, high = feet[2]+half-398, feet[2]+half+302
    rows = {}
    for kind, triangles in geometry.items():
        hits = vertical_hits(triangles, feet[0], feet[1], low, high)
        def record(hit):
            index, height, normal = hit
            return {'height_cm': height, 'source_feet_delta_cm': height-feet[2],
                    'geometric_normal_z': normal, 'absolute_slope_walkable': abs(normal) >= .7,
                    'source_triangle': metadata[kind][index]}
        nearest = sorted((h for h in hits if abs(h[1]-feet[2]) <= 40 and abs(h[2]) >= .7),
                         key=lambda h: abs(h[1]-feet[2]))
        # Retain one witness per distinct height so ceilings and stacked floors
        # remain visible without duplicating the two triangles of each quad.
        levels = {}
        for hit in sorted(hits, key=lambda h: -h[1]):
            levels.setdefault(round(hit[1], 3), record(hit))
        rows[kind] = {'first_from_above': record(max(hits, key=lambda h: h[1])) if hits else None,
                      'nearest_source_feet_support': record(nearest[0]) if nearest else None,
                      'intersection_count': len(hits), 'distinct_surface_levels': list(levels.values())}
    active = rows['intended']
    first, support = active['first_from_above'], active['nearest_source_feet_support']
    risks = []
    if first is None:
        risks.append('no_source_geometry_in_current_npc_ray')
    elif not first['absolute_slope_walkable']:
        risks.append('first_surface_exceeds_walkable_slope')
    if first and abs(first['source_feet_delta_cm']) > 40:
        risks.append('current_npc_ray_first_surface_over_40cm_from_original_feet')
        if support:
            risks.append('nearby_original_floor_exists_but_current_ray_hits_another_surface')
    if not support:
        risks.append('no_walkable_source_support_within_40cm')
    return {'ped_id': npc['ped_id'], 'model': npc['model'], 'area': area, 'zone': placement['zone'],
            'original_position_cm': feet, 'capsule_half_height_cm': half,
            'current_ray_z_cm': [high, low], 'nearby_model_placements': placements,
            'source_surfaces': rows, 'risks': risks, 'runtime_verified': False}


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
        raise FileExistsError('New diagnostic report required')
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
        result = diagnose(world, placement, npcs[placement['ped_id']], zones[placement['zone']]['area'])
        records.append(result)
        print(json.dumps({'ped_id': result['ped_id'], 'area': result['area'], 'risks': result['risks']}), flush=True)
    report = {'schema': 'rea-bully-npc-source-floor-diagnostics-v1', 'status': 'offline_geometry_diagnostic_only',
              'sources': {name: reference(path) for name, path in vars(args).items() if name != 'output'},
              'generator': reference(__file__), 'geometry_tool': reference(ROOT/'tools/preflight_world_tour_geometry.py'),
              'runtime_source': reference(ROOT/'unreal/Source/ReaBullyRuntime/ReaBullyAmbient.cpp'),
              'geometry_sources_rehashed': world.sources, 'npcs': records,
              'summary': {'npcs': len(records), 'risk_counts': dict(Counter(r for n in records for r in n['risks']))},
              'limitations': ['Double-sided source triangles and completed import policy; no Chaos, face-winding, collision-channel or real streaming execution.',
                             'Animated props, NPC capsules and doors are excluded; actual Visibility ray can hit them.',
                             'No original point, runtime source, scene or asset was changed.',
                             'Nearest original-coordinate support is a diagnostic candidate, not a runtime floor promotion.']}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(report['summary']), flush=True)


if __name__ == '__main__':
    main()

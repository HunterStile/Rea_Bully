"""Offline source-triangle diagnostics for original tour coordinates, never physics promotion.

Uses the source GLB geometry and exact IPB TRS. The intended completed collision
policy is pure COL3 replacement, otherwise non-additive static visual triangles.
No Unreal assets are modified or imported; the original tour is never rewritten.
"""
from __future__ import annotations

import argparse
from collections import Counter
from functools import lru_cache
import hashlib
from itertools import product
import json
import math
from pathlib import Path
import struct

import numpy as np

from area_navigation_contracts import reference, verify_references
from plan_unreal_world import placement_identity, zone_area
from unreal_import_world_collisions import read_source

ROOT = Path(__file__).resolve().parents[1]
CAPSULE_RADIUS, CAPSULE_HALF_HEIGHT = 34.0, 88.0
WALK_CM, STEP_CM = 361.375, 40.0


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def transform_matrix(transform):
    x, y, z, w = transform['rotation_quaternion_xyzw']
    rotation = np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                         [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                         [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])
    return rotation @ np.diag(transform['scale_xyz']), np.asarray(transform['translation_cm'])


def transform_bounds(bounds, linear, translation):
    corners = np.asarray(list(product(*zip(bounds['min'], bounds['max']))))
    placed = corners @ linear.T + translation
    return placed.min(axis=0), placed.max(axis=0)


def glb_triangles(raw, *, visual):
    require(struct.unpack_from('<III', raw) == (0x46546c67, 2, len(raw)), 'Invalid GLB envelope')
    size, kind = struct.unpack_from('<II', raw, 12)
    require(kind == 0x4e4f534a, 'GLB JSON chunk required')
    doc = json.loads(raw[20:20+size])
    blob_size, kind = struct.unpack_from('<II', raw, 20+size)
    require(kind == 0x004e4942 and 28+size+blob_size == len(raw), 'GLB BIN extent differs')
    blob = raw[28+size:]
    require(len(doc['nodes']) == len(doc['meshes']) and
            all(not set(n)-{'name', 'mesh'} for n in doc['nodes']) and
            sorted(n['mesh'] for n in doc['nodes']) == list(range(len(doc['meshes']))),
            'Expected flat, already baked entity-space meshes')

    def accessor(index):
        item = doc['accessors'][index]
        view = doc['bufferViews'][item['bufferView']]
        types = {5126: '<f4', 5123: '<u2', 5125: '<u4'}
        widths = {'SCALAR': 1, 'VEC3': 3}
        require(item['componentType'] in types and item['type'] in widths and
                not item.get('sparse') and not view.get('byteStride') and view.get('buffer') == 0,
                'Unsupported source accessor layout')
        dtype = np.dtype(types[item['componentType']])
        width, count = widths[item['type']], item['count']
        start = view.get('byteOffset', 0) + item.get('byteOffset', 0)
        end = start + count*width*dtype.itemsize
        require(0 <= start <= end <= view.get('byteOffset', 0)+view['byteLength'] <= len(blob), 'Accessor extent differs')
        return np.frombuffer(blob, dtype=dtype, count=count*width, offset=start).reshape(count, width)

    triangles, identities = [], []
    for mesh_index, mesh in enumerate(doc['meshes']):
        for primitive_index, primitive in enumerate(mesh['primitives']):
            require(primitive.get('mode', 4) == 4, 'Source primitive is not triangles')
            if visual:
                state = doc['materials'][primitive['material']].get('extras', {}).get('REA_originalAlpha', {})
                if state.get('flags', 0) & 1 and (state.get('sourceBlend'), state.get('destinationBlend')) == (0, 0):
                    continue  # Matches the importer's deliberately non-colliding additive grouping.
            vertices = accessor(primitive['attributes']['POSITION']).astype(np.float64)
            # Independently established glTF importer basis: metres (x,y,z) -> UE cm (x,z,y).
            vertices = vertices[:, (0, 2, 1)] * 100
            indices = accessor(primitive['indices']).reshape(-1)
            require(len(indices) % 3 == 0 and (not len(indices) or indices.max() < len(vertices)), 'Invalid source triangle indices')
            faces = vertices[indices.reshape(-1, 3)]
            require(np.isfinite(faces).all(), 'Nonfinite source positions')
            triangles.append(faces)
            identities.extend((mesh_index, primitive_index, index) for index in range(len(faces)))
    return (np.concatenate(triangles) if triangles else np.empty((0, 3, 3))), identities


def vertical_hits(triangles, x, y, low, high):
    """Double-sided geometric ray intersections; deliberately independent of Chaos face winding."""
    if not len(triangles):
        return []
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    u, v = b-a, c-a
    den = u[:, 0]*v[:, 1] - u[:, 1]*v[:, 0]
    valid = np.abs(den) > 1e-10
    alpha, beta = np.zeros(len(a)), np.zeros(len(a))
    alpha[valid] = ((x-a[valid, 0])*v[valid, 1] - (y-a[valid, 1])*v[valid, 0]) / den[valid]
    beta[valid] = (u[valid, 0]*(y-a[valid, 1]) - u[valid, 1]*(x-a[valid, 0])) / den[valid]
    height = a[:, 2] + alpha*u[:, 2] + beta*v[:, 2]
    mask = valid & (alpha >= -1e-8) & (beta >= -1e-8) & (alpha+beta <= 1+1e-8) & (height >= low) & (height <= high)
    normal = np.cross(u, v)
    length = np.linalg.norm(normal, axis=1)
    return [(int(index), float(height[index]), float(normal[index, 2]/length[index])) for index in np.flatnonzero(mask)]


def point_triangle_distance(point, triangle):
    """Euclidean point/triangle distance, including the vertex and edge regions."""
    a, b, c = triangle
    ab, ac, ap = b-a, c-a, point-a
    if np.linalg.norm(np.cross(ab, ac)) < 1e-12:
        return min(segment_distance(point, point, p, q) for p, q in ((a, b), (b, c), (c, a)))
    d1, d2 = np.dot(ab, ap), np.dot(ac, ap)
    if d1 <= 0 and d2 <= 0:
        return float(np.linalg.norm(ap))
    bp = point-b
    d3, d4 = np.dot(ab, bp), np.dot(ac, bp)
    if d3 >= 0 and d4 <= d3:
        return float(np.linalg.norm(bp))
    vc = d1*d4 - d3*d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        return float(np.linalg.norm(point-(a+d1/(d1-d3)*ab)))
    cp = point-c
    d5, d6 = np.dot(ab, cp), np.dot(ac, cp)
    if d6 >= 0 and d5 <= d6:
        return float(np.linalg.norm(cp))
    vb = d5*d2-d1*d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        return float(np.linalg.norm(point-(a+d2/(d2-d6)*ac)))
    va = d3*d6-d5*d4
    if va <= 0 and d4-d3 >= 0 and d5-d6 >= 0:
        return float(np.linalg.norm(point-(b+(d4-d3)/((d4-d3)+(d5-d6))*(c-b))))
    normal = np.cross(ab, ac)
    length = np.linalg.norm(normal)
    if length < 1e-12:
        return min(np.linalg.norm(point-p) for p in triangle)
    return float(abs(np.dot(ap, normal))/length)


def segment_distance(p, q, a, b):
    d1, d2, r = q-p, b-a, p-a
    aa, ee, ff = np.dot(d1, d1), np.dot(d2, d2), np.dot(d2, r)
    if aa < 1e-12 and ee < 1e-12:
        return float(np.linalg.norm(r))
    if aa < 1e-12:
        s, t = 0.0, np.clip(ff/ee, 0, 1)
    else:
        cc = np.dot(d1, r)
        if ee < 1e-12:
            t, s = 0.0, np.clip(-cc/aa, 0, 1)
        else:
            bb = np.dot(d1, d2)
            denom = aa*ee-bb*bb
            s = np.clip((bb*ff-cc*ee)/denom, 0, 1) if abs(denom) > 1e-12 else 0.0
            t = (bb*s+ff)/ee
            if t < 0:
                t, s = 0.0, np.clip(-cc/aa, 0, 1)
            elif t > 1:
                t, s = 1.0, np.clip((bb-cc)/aa, 0, 1)
    return float(np.linalg.norm(p+s*d1-a-t*d2))


def capsule_distance(triangle, feet):
    a = np.array([feet[0], feet[1], feet[2]+2+CAPSULE_RADIUS])
    b = a + np.array([0, 0, 2*(CAPSULE_HALF_HEIGHT-CAPSULE_RADIUS)])
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
            return 0.0
    return min(point_triangle_distance(a, triangle), point_triangle_distance(b, triangle),
               *(segment_distance(a, b, p, q) for p, q in ((p, q), (q, r), (r, p))))


class WorldGeometry:
    def __init__(self, catalog, collisions, helpers=None):
        self.models = {m['model_key']: m for m in catalog['models']}
        self.collisions = {m['model_key']: m for m in collisions['models']}
        self.rows, self.sources = [], {}
        seen = set()
        for zone in sorted(catalog['zones'], key=lambda z: (z['classification'] != 'outdoor', z['source_archive_index'])):
            require(reference(zone['source_ipb'])['sha256'] == zone['source_ipb_sha256'], 'Source IPB changed')
            area, _ = zone_area(zone)
            for placement in zone['placements']:
                identity = (area, *placement_identity(placement))
                if identity in seen:
                    continue
                seen.add(identity)
                key = placement['model_key']
                model, col = self.models[key], self.collisions.get(key)
                if model['status'] != 'exported' or model.get('definition', {}).get('section') == 'item':
                    continue
                linear, translation = transform_matrix(placement['unreal_transform'])
                low, high = transform_bounds(model['bounds_entity_unreal_cm'], linear, translation)
                # Cross-check the independently composed TRS against the exporter's saved placement AABB.
                bounds = placement.get('world_bounds_unreal_cm')
                if bounds:
                    require(np.max(np.abs(low-bounds['min'])) < .01 and np.max(np.abs(high-bounds['max'])) < .01,
                            'TRS and source placement AABB differ: ' + key)
                if col:
                    col_low, col_high = transform_bounds(col['bounds_unreal_cm'], linear, translation)
                    low, high = np.minimum(low, col_low), np.maximum(high, col_high)
                self.rows.append({'key': key, 'zone': zone['name'], 'area': area,
                    'index': placement['instance_index'], 'linear': linear, 'translation': translation,
                    'low': low, 'high': high, 'source_ipb_sha256': zone['source_ipb_sha256']})
        if helpers:
            from plan_collision_helpers import validate
            validate(helpers)
            for zone in helpers['zones']:
                for helper in zone['placements']:
                    model = self.collisions[helper['model_key']]
                    require(model['source_collision'] == helper['source_collision'], 'Diagnostic helper COL identity differs')
                    linear, translation = transform_matrix(helper['transform'])
                    low, high = transform_bounds(model['bounds_unreal_cm'], linear, translation)
                    self.rows.append({'key': helper['model_key'], 'zone': zone['name'], 'area': zone['area'],
                        'index': helper['source']['instance_index'], 'linear': linear, 'translation': translation,
                        'low': low, 'high': high, 'source_ipb_sha256': zone['source_ipb_sha256'],
                        'collision_only_helper': True})

    @lru_cache(maxsize=128)
    def triangles(self, key, kind):
        model = (self.collisions if kind == 'COL3' else self.models)[key]
        if kind == 'COL3':
            raw = read_source(model)  # Verifies quantized COL3 body and GLB triangle identity.
        else:
            raw = Path(model['output']).read_bytes()
            require(hashlib.sha256(raw).hexdigest() == model['output_sha256'], 'Static GLB changed: ' + key)
            require(reference(model['export']['source'])['sha256'] == model['export']['source_sha256'], 'Original NIF changed')
        self.sources[key+'/'+kind] = {'path': model['output'], 'sha256': model['output_sha256']}
        return glb_triangles(raw, visual=kind == 'visual')

    def around(self, checkpoint):
        feet = np.asarray(checkpoint['position_cm'])
        angle = math.radians(checkpoint.get('yaw_degrees', 0))
        forward = np.array([math.cos(angle), math.sin(angle), 0])
        end = feet + forward*WALK_CM
        low = np.minimum(feet, end) - [CAPSULE_RADIUS, CAPSULE_RADIUS, 210]
        high = np.maximum(feet, end) + [CAPSULE_RADIUS, CAPSULE_RADIUS, 600]
        groups = {'visual': [], 'COL3': [], 'intended': []}
        metadata = {'visual': [], 'COL3': [], 'intended': []}
        placements = 0
        for row in self.rows:
            if row['area'] != checkpoint['area'] or np.any(row['high'] < low) or np.any(row['low'] > high):
                continue
            placements += 1
            col = self.collisions.get(row['key'])
            helper = row.get('collision_only_helper', False)
            visual_triangles = self.triangles(row['key'], 'visual')[0] if not helper else []
            # The real collision planner never attaches an invisible source COL body to an effect-only model.
            has_colliding_visual = bool(len(visual_triangles))
            pure = bool((helper or has_colliding_visual) and col and col['replace_visual_collision'] and
                        not any(col['missing_primitive_counts'].values()))
            for kind in (('COL3',) if helper else ('visual', 'COL3') if col else ('visual',)):
                triangles, identities = self.triangles(row['key'], kind)
                placed = triangles @ row['linear'].T + row['translation']
                keep = np.all(placed.max(axis=1) >= low, axis=1) & np.all(placed.min(axis=1) <= high, axis=1)
                selected = placed[keep]
                source = [{'model_key': row['key'], 'zone': row['zone'], 'instance_index': row['index'],
                           'source_ipb_sha256': row['source_ipb_sha256'], 'kind': kind,
                           'mesh_primitive_triangle': identities[index], 'collision_only_helper': helper}
                          for index in np.flatnonzero(keep)]
                groups[kind].append(selected)
                metadata[kind].extend(source)
                if (kind == 'COL3' and pure) or (kind == 'visual' and not pure):
                    groups['intended'].append(selected)
                    metadata['intended'].extend(source)
        return {k: np.concatenate(v) if v else np.empty((0, 3, 3)) for k, v in groups.items()}, metadata, placements


def hit_summary(triangles, metadata, feet, *, expanded=False):
    low, high = feet[2]-210, feet[2]+240
    hits = vertical_hits(triangles, feet[0], feet[1], low, high if not expanded else feet[2]+600)
    if not hits:
        return None
    index, height, nz = max(hits, key=lambda hit: hit[1])
    return {'height_cm': height, 'offset_from_source_feet_cm': height-float(feet[2]),
            'geometric_normal_z': nz, 'absolute_slope_is_walkable_candidate': abs(nz) > .7,
            'triangle': metadata[index], 'intersections': len(hits)}


def overlaps(triangles, metadata, feet):
    low = np.asarray(feet) - [CAPSULE_RADIUS, CAPSULE_RADIUS, 0]
    high = np.asarray(feet) + [CAPSULE_RADIUS, CAPSULE_RADIUS, CAPSULE_HALF_HEIGHT*2+2]
    candidates = np.flatnonzero(np.all(triangles.max(axis=1) >= low, axis=1) & np.all(triangles.min(axis=1) <= high, axis=1))
    found = []
    for index in candidates:
        distance = capsule_distance(triangles[index], feet)
        if distance < CAPSULE_RADIUS-.5:
            found.append({'capsule_axis_distance_cm': distance, 'triangle': metadata[index]})
    return sorted(found, key=lambda item: item['capsule_axis_distance_cm'])[:12]


def diagnose(world, checkpoint):
    geometry, metadata, placements = world.around(checkpoint)
    feet = np.asarray(checkpoint['position_cm'], dtype=float)
    surfaces = {kind: hit_summary(geometry[kind], metadata[kind], feet) for kind in geometry}
    active = surfaces['intended']
    issues, walk = [], []
    if not active:
        issues.append('no_source_triangle_in_director_ray')
        spawn_overlaps = []
    else:
        if not active['absolute_slope_is_walkable_candidate']:
            issues.append('first_ray_hit_exceeds_walkable_slope')
        if abs(active['offset_from_source_feet_cm']) > STEP_CM:
            issues.append('arrival_floor_differs_from_original_feet_by_over_40_cm')
        start = np.array([feet[0], feet[1], active['height_cm']])
        spawn_overlaps = overlaps(geometry['intended'], metadata['intended'], start)
        if spawn_overlaps:
            issues.append('capsule_intersects_source_triangle_at_arrival')
        angle = math.radians(checkpoint.get('yaw_degrees', 0))
        forward = np.array([math.cos(angle), math.sin(angle), 0])
        previous = start[2]
        for distance in (30, 60, 120, 180, 240, 300, WALK_CM):
            point = start + forward*distance
            point[2] = previous
            floor = hit_summary(geometry['intended'], metadata['intended'], point)
            reasons, blockers = [], []
            if floor is None:
                reasons.append('no_floor_in_ray')
            else:
                if not floor['absolute_slope_is_walkable_candidate']:
                    reasons.append('steep_first_hit')
                if abs(floor['height_cm']-previous) > STEP_CM:
                    reasons.append('height_change_exceeds_40_cm')
                point[2] = floor['height_cm']
                blockers = overlaps(geometry['intended'], metadata['intended'], point)
                if blockers:
                    reasons.append('capsule_overlap')
                previous = point[2]
            walk.append({'distance_forward_cm': distance, 'floor': floor, 'risks': reasons, 'overlaps': blockers})
        if any(row['risks'] for row in walk):
            issues.append('straight_walk_has_geometric_risk')
    return {'name': checkpoint['name'], 'area': checkpoint['area'], 'zone': checkpoint.get('zone'),
            'source_checkpoint': checkpoint, 'ray_z_cm': [float(feet[2]+240), float(feet[2]-210)],
            'nearby_placements': placements, 'triangles_considered': {k: len(v) for k, v in geometry.items()},
            'first_surfaces': surfaces, 'spawn_capsule_overlaps': spawn_overlaps,
            'straight_walk_samples': walk, 'risks': issues, 'runtime_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=ROOT/'local/world-export-v07-material-recovery.json')
    parser.add_argument('--collisions', type=Path, default=ROOT/'evidence/world-collisions-export-v07.json')
    parser.add_argument('--tour', type=Path, default=ROOT/'evidence/world-tour-plan-v07-r3.json')
    parser.add_argument('--scope', type=Path, default=ROOT/'evidence/world-scope-v07.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--collision-helpers', type=Path, help='Separate explicitly verified additive helper scenario, still runtime pending')
    parser.add_argument('--alternatives', action='store_true', help='Check existing original area candidates when the selected spawn has risks')
    args = parser.parse_args()
    require(not args.output.exists(), 'A new diagnostic output is required')
    from validate_audio_waves import lower_priority
    lower_priority()
    catalog, collisions, tour, scope = map(read, (args.catalog, args.collisions, args.tour, args.scope))
    verify_references(tour)
    verify_references(scope)
    base = collisions['sources']['world_catalog']
    require(reference(args.catalog) == base or catalog.get('material_recovery_extension', {}).get('source_catalog') == base,
            'Static and collision catalogs have unrelated original placements')
    verify_references(base)
    helpers = read(args.collision_helpers) if args.collision_helpers else None
    world = WorldGeometry(catalog, collisions, helpers)
    records = []
    for index, checkpoint in enumerate(tour['checkpoints']):
        result = diagnose(world, checkpoint)
        records.append(result)
        print(json.dumps({'checkpoint': index+1, 'total': len(tour['checkpoints']), 'name': checkpoint['name'], 'risks': result['risks']}), flush=True)
    alternatives = []
    if args.alternatives:
        risky_areas = {r['area'] for r in records if r['area'] != 'MainMap' and
                       (r['first_surfaces']['intended'] is None or r['spawn_capsule_overlaps'] or
                        not r['first_surfaces']['intended']['absolute_slope_is_walkable_candidate'])}
        existing = {(r['area'], tuple(r['source_checkpoint']['position_cm'])) for r in records}
        for area in scope['areas']:
            if area['name'] not in risky_areas:
                continue
            for index, candidate in enumerate(area['entry_candidates']):
                identity = area['name'], tuple(candidate['position_cm'])
                if identity in existing:
                    continue
                existing.add(identity)
                point = {'name': area['name']+'/existing_candidate_'+str(index), 'area': area['name'],
                         'position_cm': candidate['position_cm'], 'source': candidate, 'runtime_ground_verified': False}
                alternatives.append(diagnose(world, point))
    result = {'schema': 'rea-bully-tour-source-geometry-preflight-v1', 'status': 'offline_diagnostic_runtime_pending',
        'sources': {name: reference(path) for name, path in (
            ('static_catalog', args.catalog), ('collision_catalog', args.collisions), ('tour', args.tour), ('scope', args.scope))},
        'geometry_sources_rehashed': world.sources,
        'collision_helpers_scenario': reference(args.collision_helpers) if args.collision_helpers else None,
        'policy': {'capsule_radius_cm': CAPSULE_RADIUS, 'capsule_half_height_cm': CAPSULE_HALF_HEIGHT,
                   'director_ray_from_source_feet_cm': [240, -210], 'straight_walk_cm': WALK_CM,
                   'step_height_cm': STEP_CM, 'pure_COL3_replaces_visual_fallback': True,
                   'static_import_completion_assumed': True, 'COL3_import_completion_assumed': True,
                   'triangle_normal_winding_is_not_engine_walkability_proof': True},
        'summary': {'checkpoints': len(records), 'areas': len({r['area'] for r in records}),
                    'with_floor_ray_hit': sum(r['first_surfaces']['intended'] is not None for r in records),
                    'without_reported_geometric_risk': sum(not r['risks'] for r in records),
                    'risk_counts': dict(Counter(issue for r in records for issue in r['risks'])),
                    'existing_alternative_coordinates_checked': len(alternatives)},
        'checkpoints': records, 'existing_original_alternatives': alternatives,
        'limitations': ['Source-triangle mathematics does not execute Chaos physics, streaming, animation or capsule sweeps.',
            'The intended completed import policy is evaluated; current UE import checkpoint completeness is not claimed.',
            'Triangle surfaces are double-sided for this diagnostic; absolute slope does not prove engine face winding.',
            'Capsule overlap and spaced straight-line samples are risk indicators, not a predicted full movement trajectory.',
            'Animated/skinned props, doors, NPCs and unavailable geometry are excluded from this static diagnostic.',
            'No tour coordinates or original files were modified and no destination was promoted.']}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
    print(json.dumps(result['summary']), flush=True)


if __name__ == '__main__':
    main()

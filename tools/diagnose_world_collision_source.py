"""Read-only source-triangle vertical intersections near a failed world spawn."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path

from export_world_collisions import ROOT, checked, reference, triangle_geometry
from validate_audio_waves import lower_priority


def cross(a, b):
    return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]


def transform(p, t):
    # Exported glTF (x,z,-y) metres -> UE (x,-y,z) centimetres.
    v = [p[0]*100, p[2]*100, p[1]*100]
    v = [a*b for a, b in zip(v, t['scale_xyz'])]
    q = t['rotation_quaternion_xyzw']
    uv = cross(q[:3], v)
    uuv = cross(q[:3], uv)
    return [v[i] + 2*(q[3]*uv[i]+uuv[i]) + t['translation_cm'][i] for i in range(3)]


def vertical_hit(point, vertices):
    a, b, c = vertices
    ab, ac = [[p[i]-a[i] for i in range(3)] for p in (b, c)]
    n = cross(ab, ac)
    if abs(n[2]) < 1e-8:
        return None
    dx, dy = point[0]-a[0], point[1]-a[1]
    u, v = (dx*ac[1]-dy*ac[0])/n[2], (ab[0]*dy-ab[1]*dx)/n[2]
    if u < -1e-7 or v < -1e-7 or u+v > 1+1e-7:
        return None
    height = a[2]+u*ab[2]+v*ac[2]
    return {'height_cm': height, 'delta_from_start_cm': height-point[2],
            'normal': [x/math.sqrt(sum(y*y for y in n)) for x in n],
            'barycentric': [1-u-v, u, v]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, default=ROOT/'evidence/world-collisions-plan-v06.json')
    parser.add_argument('--export', type=Path, default=ROOT/'evidence/world-collisions-export-v06.json')
    parser.add_argument('--point', type=float, nargs=3, default=[17973.3002, 11888.5002, 763.364])
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    lower_priority()
    if args.report.exists():
        raise ValueError('New diagnostic report required')
    plan = json.loads(args.plan.read_text())
    catalog = json.loads(args.export.read_text())
    models = {m['model_key']: m for m in catalog['models']}
    samples = [{'offset_xy_cm': [x, y], 'hits': []} for x, y in
               [(0, 0), (34, 0), (-34, 0), (0, 34), (0, -34), (100, 0), (250, 0)]]
    candidates = []
    for zone in plan['zones']:
        if zone['name'] not in ('tGlobal', 'tschool'):
            continue
        for p in zone['placements']:
            m = models[p['model_key']]
            if p['source_glb_sha256'] != m['output_sha256']:
                raise ValueError('Plan/export source mismatch')
            data = checked(m['source_col'])
            g = triangle_geometry(data, m['source_collision'])
            vertices = [transform(v, p['transform']) for v in g['positions']]
            lo, hi = [[fn(v[i] for v in vertices) for i in range(3)] for fn in (min, max)]
            if any(args.point[i] < lo[i]-300 or args.point[i] > hi[i]+300 for i in (0, 1)):
                continue
            edge_counts = Counter(tuple(sorted((face[i], face[(i+1)%3]))) for face in g['triangles'] for i in range(3))
            candidates.append({'model_key': m['model_key'], 'source': p['source'], 'mesh': p['mesh'],
                'bounds_world_cm': [lo, hi], 'triangles': len(g['triangles']),
                'boundary_edges': sum(n == 1 for n in edge_counts.values()),
                'nonmanifold_edges': sum(n > 2 for n in edge_counts.values()),
                'source_col': m['source_col'], 'source_model_sha256': m['source_collision']['sha256']})
            for i, face in enumerate(g['triangles']):
                for sample in samples:
                    point = [args.point[j]+sample['offset_xy_cm'][j] for j in (0, 1)]+[args.point[2]]
                    hit = vertical_hit(point, [vertices[k] for k in face])
                    if hit is not None and abs(hit['delta_from_start_cm']) < 600:
                        sample['hits'].append({**hit, 'model_key': m['model_key'], 'source_face': i,
                                               'source': p['source']})
    for sample in samples:
        sample['hits'].sort(key=lambda h: h['height_cm'], reverse=True)
    result = {'status': 'source_geometry_only_no_engine_query', 'point_cm': args.point,
              'source_reports': {'plan': reference(args.plan), 'export': reference(args.export)},
              'nearby_original_colliders': candidates, 'vertical_samples': samples,
              'limitations': ['Only planned original COL3 triangle bodies; visual fallback and dynamic actors excluded.',
                  'Open/nonmanifold edge counts do not establish a Chaos collision failure.',
                  'Vertical intersection is not a capsule sweep or overlap test.']}
    with args.report.open('x', encoding='utf-8') as out:
        json.dump(result, out, indent=2)
    print(json.dumps({'nearby': [(p['model_key'], p['nonmanifold_edges']) for p in candidates], 'samples': samples}))


if __name__ == '__main__':
    main()

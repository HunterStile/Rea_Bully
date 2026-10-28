"""Measure each door's IDLE(0) skin, independently of imported reference bounds.

This is CPU source-data math, not a rendered Unreal pose or collision test.
The eleven baseline doors are compared with their previous original-NIF/AGR
evaluation. The five newer exports retain their pending independent review.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import struct

from catalog_dat_door_objects import below_normal
from export_nif import matmul
from plan_world_doors import ROOT, action_clips, load_models, read, reference, verify
from validate_player_actions import glb


def _accessor(document, blob, index):
    item = document['accessors'][index]
    view = document['bufferViews'][item['bufferView']]
    width = {'SCALAR': 1, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}[item['type']]
    code = {5126: 'f', 5123: 'H'}[item['componentType']]
    size = struct.calcsize('<'+code*width)
    offset = item.get('byteOffset', 0)
    stride = view.get('byteStride', size)
    if (item.get('sparse') or item.get('normalized') or item['count'] <= 0 or
            stride < size or offset < 0 or offset+(item['count']-1)*stride+size > view['byteLength']):
        raise ValueError('Unsupported or out-of-bounds door accessor')
    return [struct.unpack_from('<'+code*width, blob, view.get('byteOffset', 0)+offset+i*stride)
            for i in range(item['count'])]


def _matrix(node):
    if 'matrix' in node:
        return tuple(node['matrix'][c*4+r] for r in range(4) for c in range(4))
    x, y, z, w = node.get('rotation', (0., 0., 0., 1.))
    length = math.sqrt(x*x+y*y+z*z+w*w)
    if not math.isfinite(length) or length == 0:
        raise ValueError('Invalid door quaternion')
    x, y, z, w = (v/length for v in (x, y, z, w))
    rotation = (1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w),
                2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w),
                2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y))
    scale = node.get('scale', (1., 1., 1.))
    translation = node.get('translation', (0., 0., 0.))
    return tuple(rotation[r*3+c]*scale[c] if r < 3 and c < 3 else
                 translation[r] if r < 3 and c == 3 else float(r == c)
                 for r in range(4) for c in range(4))


def _bounds(points):
    if not points or not all(math.isfinite(v) for point in points for v in point):
        raise ValueError('Missing or nonfinite door vertices')
    low = [min(p[axis] for p in points) for axis in range(3)]
    high = [max(p[axis] for p in points) for axis in range(3)]
    return {'min': low, 'max': high, 'center': [(a+b)*.5 for a, b in zip(low, high)],
            'extent': [(b-a)*.5 for a, b in zip(low, high)]}


def closed_bounds(model):
    """Return actual GLB IDLE(0) world skin bounds in the imported UE local basis."""
    source = verify({'path': model['output'], 'sha256': model['output_sha256']})
    document, blob = glb(source)
    original = action_clips(model)['IDLE']
    clips = [a for a in document['animations'] if a['name'] == original.replace('\\', '_')]
    if len(clips) != 1:
        raise ValueError('No unique original IDLE animation')
    cache = {}
    def accessor(index):
        if index not in cache:
            cache[index] = _accessor(document, blob, index)
        return cache[index]
    keyed = {}
    for channel in clips[0]['channels']:
        sampler = clips[0]['samplers'][channel['sampler']]
        times, values = accessor(sampler['input']), accessor(sampler['output'])
        target, field = channel['target']['node'], channel['target']['path']
        if (sampler.get('interpolation', 'LINEAR') not in ('LINEAR', 'STEP') or times[0] != (0.,) or
                len(times) != len(values) or field not in ('rotation', 'translation', 'scale') or
                field in keyed.get(target, {})):
            raise ValueError('IDLE requires unsupported or ambiguous time-zero sampling')
        keyed.setdefault(target, {})[field] = values[0]
    parents = {}
    for parent, node in enumerate(document['nodes']):
        for child in node.get('children', []):
            if child in parents:
                raise ValueError('Multiple GLB node parents')
            parents[child] = parent
    worlds, visiting = {}, set()
    def world(index):
        if index in visiting:
            raise ValueError('Cyclic GLB hierarchy')
        if index not in worlds:
            visiting.add(index)
            node = document['nodes'][index]
            if 'matrix' in node and index in keyed:
                raise ValueError('Animated matrix node')
            local = _matrix({**node, **keyed.get(index, {})})
            worlds[index] = matmul(world(parents[index]), local) if index in parents else local
            visiting.remove(index)
        return worlds[index]
    points, raw_points = [], []
    primitives = 0
    for node in document['nodes']:
        if 'mesh' not in node:
            continue
        if 'skin' not in node:
            raise ValueError('Door bounds include an unskinned mesh')
        skin = document['skins'][node['skin']]
        binds = accessor(skin['inverseBindMatrices'])
        if len(binds) != len(skin['joints']):
            raise ValueError('Incomplete door inverse binds')
        transforms = [matmul(world(joint), tuple(bind[c*4+r] for r in range(4) for c in range(4)))
                      for joint, bind in zip(skin['joints'], binds)]
        for primitive in document['meshes'][node['mesh']]['primitives']:
            attributes = primitive['attributes']
            vertices = accessor(attributes['POSITION'])
            indices, weights = accessor(attributes['JOINTS_0']), accessor(attributes['WEIGHTS_0'])
            if len(vertices) != len(indices) or len(vertices) != len(weights):
                raise ValueError('Mismatched door skin arrays')
            primitives += 1
            for vertex, joints, influence in zip(vertices, indices, weights):
                if abs(sum(influence)-1.) > 2e-6 or min(influence) < 0:
                    raise ValueError('Invalid door skin weights')
                posed = [0., 0., 0.]
                for joint, weight in zip(joints, influence):
                    if not weight:
                        continue
                    transform = transforms[joint]
                    for axis in range(3):
                        posed[axis] += weight*(sum(transform[axis*4+c]*vertex[c] for c in range(3))+transform[axis*4+3])
                # GLTF (x,source_z,-source_y) -> UE (source_x,-source_y,source_z).
                points.append([100*posed[0], 100*posed[2], 100*posed[1]])
                raw_points.append([100*vertex[0], 100*vertex[2], 100*vertex[1]])
    return {'model_key': model['model_key'], 'source': reference(source), 'original_clip': original,
            'sample_time_seconds': 0., 'vertices': len(points), 'primitives': primitives,
            'basis': 'UE local centimetres: 100 * (gltf_x,gltf_z,gltf_y)',
            'closed_bounds_cm': _bounds(points), 'raw_reference_bounds_cm': _bounds(raw_points)}


def make_report():
    catalog = load_models()
    baseline_path = ROOT/'evidence/skinned-props-validation-v04-rgb-final.json'
    baseline = {row['model_key']: row for row in read(baseline_path)['models']}
    records = []
    for model in catalog['models']:
        result = closed_bounds(model)
        result['independent_original_comparison'] = 'pending'
        if model['model_key'] in baseline:
            clips = [c for c in baseline[model['model_key']]['clips'] if c['name'] == result['original_clip']]
            sample = next(s for s in clips[0]['samples'] if s['ratio'] == 0.)
            low, high = sample['original_cpu_bounds']
            expected = [[100*low[0], -100*high[1], 100*low[2]],
                        [100*high[0], -100*low[1], 100*high[2]]]
            actual = [result['closed_bounds_cm']['min'], result['closed_bounds_cm']['max']]
            error = max(abs(a-b) for ar, br in zip(actual, expected) for a, b in zip(ar, br))
            if error > .002:
                raise ValueError('Door IDLE bounds differ from original NIF/AGR evaluation')
            result['independent_original_comparison'] = {'reference': reference(baseline_path),
                'method': 'Previous original NIF sparse-weight skinning with original AGR local curves, sample ratio 0.',
                'max_bound_coordinate_error_cm': error}
        records.append(result)
    return {'schema': 'rea-bully-door-closed-bounds-v1', 'status': 'source_math_only_runtime_pending',
        'models': records, 'source_catalogs': catalog['source_catalogs'],
        'summary': {'models': len(records), 'vertices': sum(r['vertices'] for r in records),
            'original_baseline_compared': sum(isinstance(r['independent_original_comparison'], dict) for r in records)},
        'collision_policy': 'An AABB fitted to closed source skin is an exploration reconstruction, not recovered original collision.',
        'limitations': ['IDLE at time zero only; no claim about whole-clip bounds.',
            'This calculation neither executes Unreal skinning nor tests the actual player interaction.',
            'Five new door exports retain pending independent original deformed-pose review.']}


def main():
    below_normal()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = make_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result['summary']))


if __name__ == '__main__':
    main()

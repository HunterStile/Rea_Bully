"""Evaluate candidate NPC skins in background Blender on CPU, without rendering.

Run Blender --background --factory-startup --threads 2 --python-exit-code 1
--python tools/validate_npc_blender.py -- --manifest ... --report NEW.json.
"""
import argparse
import ctypes
import hashlib
import json
import math
from pathlib import Path
import struct
import sys

import bpy

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_IDS = {9, 11, 72, 73, 85, 99}


def reference(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def checked(ref):
    path = Path(ref['path'])
    path = path if path.is_absolute() else ROOT/path
    if reference(path)['sha256'] != ref['sha256']:
        raise RuntimeError('Changed source: ' + str(path))
    return path


def source_times(path, clips):
    raw = path.read_bytes()
    if struct.unpack_from('<III', raw) != (0x46546c67, 2, len(raw)):
        raise RuntimeError('Invalid original candidate GLB')
    size, tag = struct.unpack_from('<II', raw, 12)
    if tag != 0x4e4f534a:
        raise RuntimeError('GLB JSON is missing')
    doc = json.loads(raw[20:20+size])
    binary_size, binary_tag = struct.unpack_from('<II', raw, 20+size)
    blob = raw[28+size:]
    if binary_tag != 0x004e4942 or len(blob) != binary_size:
        raise RuntimeError('GLB binary is missing')
    by_name = {c['glb_animation_name']: c for c in clips}
    result = {}
    for animation in doc['animations']:
        name = animation['name']
        if name not in by_name or name in result:
            raise RuntimeError('Candidate animation identity is ambiguous')
        duration = by_name[name]['duration']
        times = {duration*i/8 for i in range(9)}
        for sampler in animation['samplers']:
            accessor = doc['accessors'][sampler['input']]
            view = doc['bufferViews'][accessor['bufferView']]
            if accessor['componentType'] != 5126 or accessor['type'] != 'SCALAR' or view.get('byteStride', 4) != 4:
                raise RuntimeError('Unsupported candidate time accessor')
            offset = view.get('byteOffset', 0)+accessor.get('byteOffset', 0)
            keys = struct.unpack_from('<'+'f'*accessor['count'], blob, offset)
            if not keys or not all(math.isfinite(t) and -1e-7 <= t <= duration+1e-6 for t in keys):
                raise RuntimeError('Original clip time is outside its duration')
            times.update(max(0., min(duration, t)) for t in keys)
        result[name] = sorted(times)
    if set(result) != set(by_name):
        raise RuntimeError('Candidate GLB lost original clips')
    return result


def measured_bounds(mesh):
    evaluated = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
    data = evaluated.to_mesh()
    try:
        points = [evaluated.matrix_world@v.co for v in data.vertices]
        if not points or not all(math.isfinite(v) for point in points for v in point):
            raise RuntimeError('NPC deformation has empty or nonfinite geometry')
        lo = [min(point[axis] for point in points) for axis in range(3)]
        hi = [max(point[axis] for point in points) for axis in range(3)]
        if not 1.2 < hi[2]-lo[2] < 2.4 or max(hi[i]-lo[i] for i in (0, 1)) > 2.0:
            raise RuntimeError('NPC deformation bounds outside verified human scale')
        return {'bounds_min': lo, 'bounds_max': hi}
    finally:
        evaluated.to_mesh_clear()


def validate(npc):
    source = checked(npc['source_glb'])
    checked(npc['export_report'])
    checked(npc['original_model'])
    checked(npc['original_texture'])
    samples = source_times(source, npc['clips'])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.fps = 60
    scene.render.fps_base = 1.0
    bpy.ops.import_scene.gltf(filepath=str(source))
    arms = [o for o in scene.objects if o.type == 'ARMATURE']
    meshes = [o for o in scene.objects if o.type == 'MESH' and any(m.type == 'ARMATURE' for m in o.modifiers)]
    if len(arms) != 1 or len(meshes) != 1:
        raise RuntimeError('Expected one own skeleton and one original skinned mesh')
    arm, mesh = arms[0], meshes[0]
    if len(arm.data.bones) != 36 or not any(m.type == 'ARMATURE' and m.object == arm for m in mesh.modifiers):
        raise RuntimeError('Original NPC skeleton or binding changed')
    sums = [sum(g.weight for g in v.groups) for v in mesh.data.vertices]
    weight_error = max(abs(value-1.) for value in sums)
    if weight_error > 1e-5:
        raise RuntimeError('Imported NPC weight sums changed')
    tracks = list(arm.animation_data.nla_tracks)
    if len(tracks) != 5 or {t.name for t in tracks} != set(samples):
        raise RuntimeError('Blender did not import the exact five original clips')
    arm.animation_data.action = None
    for track in tracks:
        track.mute = True
    arm.data.pose_position = 'REST'
    scene.frame_set(1)
    rest = measured_bounds(mesh)
    arm.data.pose_position = 'POSE'
    report = {'ped_id': npc['ped_id'], 'model': npc['model'], 'source_glb': reference(source),
              'export_report': npc['export_report'], 'bones': 36, 'own_skin': True,
              'vertices': len(mesh.data.vertices), 'weight_sum_error': weight_error,
              'unanimated_rest_bounds_metres': rest, 'clips': []}
    for original in npc['clips']:
        track = next(t for t in tracks if t.name == original['glb_animation_name'])
        if len(track.strips) != 1:
            raise RuntimeError('Unexpected imported NLA strip count')
        strip = track.strips[0]
        duration = (strip.frame_end-strip.frame_start)/scene.render.fps
        if abs(duration-original['duration']) > 1e-5:
            raise RuntimeError('Blender clip duration differs from original')
        track.mute = False
        clip = {'name': track.name, 'role': original['role'], 'duration': original['duration'], 'samples': []}
        for seconds in samples[track.name]:
            frame = strip.frame_start+seconds*scene.render.fps
            scene.frame_set(math.floor(frame), subframe=frame-math.floor(frame))
            clip['samples'].append({'time_seconds': seconds, 'ratio': seconds/original['duration'], **measured_bounds(mesh)})
        report['clips'].append(clip)
        track.mute = True
    print('REA_NPC_CPU_POSES', json.dumps({'ped_id': npc['ped_id'], 'clips': len(report['clips']),
              'samples': sum(len(c['samples']) for c in report['clips'])}), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    if args.report.exists():
        raise RuntimeError('New validation report path required')
    if not bpy.app.background:
        raise RuntimeError('NPC pose validation requires background Blender')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    if not kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000):
        raise RuntimeError('Could not set BelowNormal validation priority')
    candidate = json.loads(args.manifest.read_text())
    if (candidate['status'] != 'exported_pending_independent_pose_validation'
            or {n['ped_id'] for n in candidate['npcs']} != EXPECTED_IDS or len(candidate['npcs']) != 6):
        raise RuntimeError('Expected the six original v05 candidates')
    result = {'schema': 'rea-bully-npc-blender-cpu-validation-v1', 'status': 'running', 'passed': False,
              'candidate_manifest': reference(args.manifest), 'validator': reference(__file__),
              'host': bpy.app.version_string, 'blender_build_hash': bpy.app.build_hash.decode(),
              'background': True, 'render_performed': False, 'process_priority': 'BelowNormal',
              'sampling': 'All exported animation key instants plus nine evenly spaced instants per clip',
              'coordinate_system': 'Blender world metres, Z up after glTF importer coordinate conversion',
              'models': [], 'limitations': [
                  'This validates actual Blender skin deformation at sampled times; no rendered visual or Unreal/runtime check was performed.',
                  'Blender quaternion curves and original game interpolation are not claimed identical.',
                  'Documented nonorthogonal rest-matrix conversion loss and NiSkinData/NiSkinPartition differences remain unresolved.',
                  'Capsule dimensions require a separate measured manifest and runtime collision checks.']}
    try:
        for npc in candidate['npcs']:
            result['models'].append(validate(npc))
        result.update(status='passed_cpu_pose_validation_pending_unreal', passed=True)
    except Exception as exc:
        result.update(status='failed', error=str(exc))
        raise
    finally:
        with args.report.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2)
    print('REA_NPC_VALIDATED', json.dumps({'report': str(args.report), 'models': len(result['models']),
          'clips': sum(len(model['clips']) for model in result['models'])}), flush=True)


if __name__ == '__main__':
    main()

"""Evaluate all original locomotion clips and render actual skinned animation poses."""
import bpy
from pathlib import Path
from mathutils import Vector
import json
import math

root=Path(__file__).resolve().parents[1]
folder=root/'local/converted/jimmy'
bpy.ops.wm.open_mainfile(filepath=str(folder/'jimmy-final.blend'))
for o in list(bpy.context.scene.objects):
    if o.type in ('ARMATURE','MESH','EMPTY'):bpy.data.objects.remove(o,do_unlink=True)
bpy.ops.import_scene.gltf(filepath=str(folder/'jimmy_final_animated.glb'))
arms=[o for o in bpy.context.scene.objects if o.type=='ARMATURE']
meshes=[o for o in bpy.context.scene.objects if o.type=='MESH' and any(m.type=='ARMATURE' for m in o.modifiers)]
if len(arms)!=1 or len(meshes)!=1:raise RuntimeError('Expected one original mesh/skeleton')
arm=arms[0]
for o in bpy.context.scene.objects:
    if o.type=='MESH' and o not in meshes:o.hide_render=True
tracks=list(arm.animation_data.nla_tracks)
print('ORIGINAL_TRACKS',[(t.name,[(s.name,s.frame_start,s.frame_end) for s in t.strips]) for t in tracks])
if len(tracks)!=4:raise RuntimeError(f'Expected four original clips, got {len(tracks)}')
for t in tracks:t.mute=True
arm.animation_data.action=None
scene=bpy.context.scene
scene.render.resolution_x=720;scene.render.resolution_y=800
scene.cycles.samples=24
report={'host':bpy.app.version_string,'source':str(folder/'jimmy_final_animated.glb'),'bones':len(arm.data.bones),'clips':[]}
for track in tracks:
    track.mute=False
    strip=track.strips[0]
    clip={'name':track.name,'samples':[]}
    for ratio in (0,.25,.5,.75,1):
        frame=strip.frame_start+(strip.frame_end-strip.frame_start)*ratio
        scene.frame_set(int(frame),subframe=frame-int(frame))
        deps=bpy.context.evaluated_depsgraph_get()
        evaluated=meshes[0].evaluated_get(deps)
        data=evaluated.to_mesh()
        points=[evaluated.matrix_world@v.co for v in data.vertices]
        if not all(math.isfinite(v) for p in points for v in p):raise RuntimeError('Nonfinite deformed original vertex')
        lo=[min(p[i] for p in points) for i in range(3)];hi=[max(p[i] for p in points) for i in range(3)]
        clip['samples'].append({'ratio':ratio,'bounds_min':lo,'bounds_max':hi,'vertices':len(points)})
        evaluated.to_mesh_clear()
    frame=(strip.frame_start+strip.frame_end)/2
    scene.frame_set(int(frame),subframe=frame-int(frame))
    scene.render.filepath=str(folder/(track.name.lower()+'-pose.png'))
    bpy.ops.render.render(write_still=True)
    clip['render']=scene.render.filepath
    report['clips'].append(clip)
    track.mute=True
(root/'evidence/jimmy-animation-blender-validation.json').write_text(json.dumps(report,indent=2)+'\n')
bpy.ops.wm.save_as_mainfile(filepath=str(folder/'jimmy-animations.blend'))
print('REA_ANIMATION_VALIDATED',json.dumps(report))

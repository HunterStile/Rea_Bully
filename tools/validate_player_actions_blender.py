"""Evaluate actual original jump/fall deformation and render selected source poses."""
import bpy
import json
import math
from pathlib import Path

root=Path(__file__).resolve().parents[1]
folder=root/'local/converted/jimmy'
output=root/'evidence/player-actions-blender-v03-review.json'
if output.exists():
    raise FileExistsError(output)
bpy.ops.wm.open_mainfile(filepath=str(folder/'jimmy-final.blend'))
for obj in list(bpy.context.scene.objects):
    if obj.type in ('ARMATURE','MESH','EMPTY'):
        bpy.data.objects.remove(obj,do_unlink=True)
bpy.ops.import_scene.gltf(filepath=str(folder/'jimmy_actions_v03_final.glb'))
arms=[o for o in bpy.context.scene.objects if o.type=='ARMATURE']
meshes=[o for o in bpy.context.scene.objects if o.type=='MESH' and any(m.type=='ARMATURE' for m in o.modifiers)]
assert len(arms)==len(meshes)==1
arm=arms[0]
tracks=list(arm.animation_data.nla_tracks)
assert len(tracks)==10
for track in tracks:
    track.mute=True
arm.animation_data.action=None
scene=bpy.context.scene
scene.render.resolution_x=600
scene.render.resolution_y=720
scene.cycles.samples=16
scene.camera.data.ortho_scale=3.2
payload=json.loads((root/'local/converted/player/player-original-actions-v03-final.json').read_text())
byname={c['name'].replace('\\','_'):c for c in payload['clips'][4:]}
render_times={'C_PLAYER_IDLEJUMP':(.10,.3652968285,.6666667),
              'C_PLAYER_JOGJUMP_L':(.2984344565,), 'C_PLAYER_FALL_LAND':(.1663405,)}
report={'status':'original_actions_deformation_validated','host':bpy.app.version_string,
        'source':str(folder/'jimmy_actions_v03_final.glb'),'bones':len(arm.data.bones),'clips':[]}
for track in tracks:
    if track.name not in byname:
        continue
    clip=byname[track.name]
    track.mute=False
    strip=track.strips[0]
    # Evaluate all keyed source Root and ARROW times plus 21 uniform samples.
    times={clip['duration']*i/20 for i in range(21)}
    times.update(t for t,_ in clip['source_channels'][1]['translation'])
    times.update(t for t,_ in clip['root_motion']['translation'])
    result={'name':track.name,'samples':[],'renders':[]}
    def frame_at(time):
        frame=strip.frame_start+(strip.frame_end-strip.frame_start)*time/clip['duration']
        scene.frame_set(int(frame),subframe=frame-int(frame))
    for time in sorted(times):
        frame_at(time)
        deps=bpy.context.evaluated_depsgraph_get()
        evaluated=meshes[0].evaluated_get(deps)
        data=evaluated.to_mesh()
        points=[evaluated.matrix_world@v.co for v in data.vertices]
        assert all(math.isfinite(v) for p in points for v in p)
        lo=[min(p[i] for p in points) for i in range(3)]
        hi=[max(p[i] for p in points) for i in range(3)]
        assert all(-3 < v < 3 for v in lo+hi), (track.name,time,lo,hi)
        result['samples'].append(dict(time_seconds=time,bounds_min=lo,bounds_max=hi,vertices=len(points)))
        evaluated.to_mesh_clear()
    for index,time in enumerate(render_times.get(track.name,())):
        frame_at(time)
        scene.render.filepath=str(folder/(track.name.lower()+f'-v03-review-{index}.png'))
        bpy.ops.render.render(write_still=True)
        result['renders'].append(dict(time_seconds=time,path=scene.render.filepath))
    report['clips'].append(result)
    track.mute=True
assert len(report['clips'])==6
output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print('REA_ACTIONS_VALIDATED',json.dumps(dict(report=str(output),clips=len(report['clips']))))

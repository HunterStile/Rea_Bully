"""Load derived original Jimmy GLB in Blender, audit skin, render a neutral view."""
import bpy
from mathutils import Vector
import json
import os
from pathlib import Path

root=Path(__file__).resolve().parents[1]
folder=root/'local/converted/jimmy'
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=str(folder/os.environ.get('JIMMY_GLB','jimmy.glb')))
meshes=[o for o in bpy.context.scene.objects if o.type=='MESH' and any(m.type=='ARMATURE' for m in o.modifiers)]
for o in bpy.context.scene.objects:
    if o.type=='MESH' and o not in meshes:o.hide_render=True
arms=[o for o in bpy.context.scene.objects if o.type=='ARMATURE']
if len(arms)!=1:raise RuntimeError(f'Expected one skeleton, got {len(arms)}')
arm=arms[0]
report={'host':bpy.app.version_string,'meshes':len(meshes),'bones':len(arm.data.bones),'mesh_stats':[],
        'bone_names':[b.name for b in arm.data.bones]}
allpoints=[]
for o in meshes:
    vertices=len(o.data.vertices)
    mods=[m for m in o.modifiers if m.type=='ARMATURE']
    if len(mods)!=1 or mods[0].object!=arm:raise RuntimeError('Missing or incorrect original armature binding')
    sums=[sum(g.weight for g in v.groups) for v in o.data.vertices]
    if any(abs(x-1)>1e-5 for x in sums):raise RuntimeError('Invalid imported weight sums')
    points=[o.matrix_world@v.co for v in o.data.vertices];allpoints+=points
    report['mesh_stats'].append({'name':o.name,'vertices':vertices,'triangles':sum(len(p.vertices)-2 for p in o.data.polygons),
                                  'weight_sum_min':min(sums),'weight_sum_max':max(sums),
                                  'bounds_min':[min(p[i] for p in points) for i in range(3)],
                                  'bounds_max':[max(p[i] for p in points) for i in range(3)]})
lo=Vector([min(p[i] for p in allpoints) for i in range(3)]);hi=Vector([max(p[i] for p in allpoints) for i in range(3)])
report['bounds_min_blender']=list(lo);report['bounds_max_blender']=list(hi)
target=(lo+hi)*.5
scene=bpy.context.scene
world=bpy.data.worlds.new('Neutral studio');world.use_nodes=True
world.node_tree.nodes['Background'].inputs[0].default_value=(.19,.21,.25,1)
world.node_tree.nodes['Background'].inputs[1].default_value=.6
scene.world=world
def area(name,position,energy,size):
    d=bpy.data.lights.new(name,'AREA');d.energy=energy;d.shape='DISK';d.size=size
    o=bpy.data.objects.new(name,d);scene.collection.objects.link(o);o.location=position
    o.rotation_euler=(target-o.location).to_track_quat('-Z','Y').to_euler()
area('Key',(3,-4,4),450,4);area('Fill',(-3,-1,2),200,3);area('Rim',(1,3,3),350,3)
camera=bpy.data.cameras.new('Original Jimmy');obj=bpy.data.objects.new('Original Jimmy',camera)
scene.collection.objects.link(obj);obj.location=target+Vector((2.5,-4.5,1.2))
obj.rotation_euler=(target-obj.location).to_track_quat('-Z','Y').to_euler()
camera.type='ORTHO';camera.ortho_scale=2.25;scene.camera=obj
scene.render.engine='CYCLES';scene.cycles.samples=32
scene.render.resolution_x=900;scene.render.resolution_y=1000;scene.render.resolution_percentage=100
scene.view_settings.view_transform='AgX'
stem=os.environ.get('JIMMY_QA_STEM','jimmy-preview')
scene.render.image_settings.file_format='PNG';scene.render.filepath=str(folder/(stem+'.png'))
scene.render.film_transparent=False
bpy.ops.wm.save_as_mainfile(filepath=str(folder/(stem+'.blend')))
bpy.ops.render.render(write_still=True)
(root/'evidence'/(stem+'-blender-validation.json')).write_text(json.dumps(report,indent=2)+'\n')
print('REA_CHARACTER_VALIDATED',json.dumps(report))

"""Compare every recovered PANM animation to independent original-axis skinning.

Run with Blender --background --factory-startup --python this_file.py.
Source evaluation uses original NIF bind matrices, hierarchy and AGR curves;
Blender independently imports/evaluates the GLB. No source file is modified.
"""
import bpy
import json
import math
import os
from mathutils import Quaternion,Vector
from pathlib import Path
import sys
import struct

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'tools'))
from export_character import CharacterNif,geometry_worlds,influences,matrix,matmul,skeleton
from bully_agr import interpolate_linear,sha256


def q_at(keys,time,method='slerp'):
    if time<=keys[0][0]:return keys[0][1]
    if time>=keys[-1][0]:return keys[-1][1]
    for (ta,a),(tb,b) in zip(keys,keys[1:]):
        if time<=tb:
            if method=='nlerp':
                blend=(time-ta)/(tb-ta)
                values=[x+(y-x)*blend for x,y in zip(a,b)]
                norm=math.sqrt(sum(v*v for v in values))
                return [v/norm for v in values]
            q=Quaternion((a[3],a[0],a[1],a[2])).slerp(Quaternion((b[3],b[0],b[1],b[2])),(time-ta)/(tb-ta))
            return [q.x,q.y,q.z,q.w]
    raise RuntimeError('Source curve interpolation failed')


def original_bounds(bones,shapes,clip,time,method='slerp'):
    blockmap={b['source_block']:i for i,b in enumerate(bones)}
    worlds=[]
    for i,(bone,track) in enumerate(zip(bones,clip['channels'])):
        local=dict(bone)
        if track['rotation']:
            q=q_at(track['rotation'],time,method);m=Quaternion((q[3],q[0],q[1],q[2])).to_matrix()
            local['rotation']=[m[r][c] for r in range(3) for c in range(3)]
        if track['translation']:local['translation']=interpolate_linear(track['translation'],time)
        transform=matrix(local)
        worlds.append(matmul(worlds[blockmap[bone['parent_block']]],transform) if bone['parent_block'] is not None else transform)
    points=[]
    for shape in shapes:
        transforms=[matmul(shape['global_skin'],matmul(worlds[blockmap[block]],bind)) for block,bind in shape['binds']]
        for vertex,weights in zip(shape['vertices'],shape['influences']):
            p=[0.,0.,0.];total=sum(w for _,w in weights)
            for bone,weight in weights:
                m=transforms[bone]
                for r in range(3):p[r]+=(weight/total)*(sum(m[r*4+c]*vertex[c] for c in range(3))+m[r*4+3])
            points.append(p)
    return [[min(p[i] for p in points) for i in range(3)],[max(p[i] for p in points) for i in range(3)]]


class GlbEvaluator:
    """Conformant glTF SLERP skin evaluation directly from exported accessors."""
    def __init__(self,path):
        raw=path.read_bytes();size=struct.unpack_from('<I',raw,12)[0]
        self.doc=json.loads(raw[20:20+size]);self.blob=raw[28+size:]
        self.parents={child:i for i,n in enumerate(self.doc['nodes']) for child in n.get('children',[])}
        self.cache={}
        self.primitives=[]
        for node in self.doc['nodes']:
            if 'skin' not in node:continue
            skin=self.doc['skins'][node['skin']]
            ibms=[tuple(value[c*4+r] for r in range(4) for c in range(4)) for value in self.accessor(skin['inverseBindMatrices'])]
            for p in self.doc['meshes'][node['mesh']]['primitives']:
                self.primitives.append(dict(vertices=self.accessor(p['attributes']['POSITION']),
                    indices=self.accessor(p['attributes']['JOINTS_0']),weights=self.accessor(p['attributes']['WEIGHTS_0']),
                    joints=skin['joints'],ibms=ibms))
    def accessor(self,index):
        if index in self.cache:return self.cache[index]
        a=self.doc['accessors'][index];v=self.doc['bufferViews'][a['bufferView']]
        width={'SCALAR':1,'VEC3':3,'VEC4':4,'MAT4':16}[a['type']]
        fmt={5126:'f',5123:'H'}[a['componentType']]
        stride=v.get('byteStride',struct.calcsize('<'+fmt)*width)
        offset=v.get('byteOffset',0)+a.get('byteOffset',0)
        values=[struct.unpack_from('<'+fmt*width,self.blob,offset+i*stride) for i in range(a['count'])]
        self.cache[index]=values
        return values
    def bounds(self,name,time):
        animation=next(a for a in self.doc['animations'] if a['name']==name)
        keyed={}
        for channel in animation['channels']:
            sampler=animation['samplers'][channel['sampler']]
            times=self.accessor(sampler['input']);values=self.accessor(sampler['output'])
            keys=[[t[0],list(v)] for t,v in zip(times,values)]
            field=channel['target']['path']
            value=q_at(keys,time) if field=='rotation' else interpolate_linear(keys,time)
            keyed.setdefault(channel['target']['node'],{})[field]=value
        worlds={}
        def world(index):
            if index in worlds:return worlds[index]
            node=self.doc['nodes'][index];pose={**node,**keyed.get(index,{})}
            if 'matrix' in pose:
                m=pose['matrix'];local=tuple(m[c*4+r] for r in range(4) for c in range(4))
            else:
                x,y,z,w=pose.get('rotation',[0.,0.,0.,1.]);q=Quaternion((w,x,y,z)).to_matrix()
                scale=pose.get('scale',[1.,1.,1.]);translation=pose.get('translation',[0.,0.,0.])
                local=tuple(q[r][c]*scale[c] if c<3 and r<3 else translation[r] if c==3 and r<3 else float(r==c) for r in range(4) for c in range(4))
            worlds[index]=matmul(world(self.parents[index]),local) if index in self.parents else local
            return worlds[index]
        points=[]
        for primitive in self.primitives:
            transforms=[matmul(world(j),b) for j,b in zip(primitive['joints'],primitive['ibms'])]
            for vertex,indices,weights in zip(primitive['vertices'],primitive['indices'],primitive['weights']):
                p=[0.,0.,0.]
                for index,weight in zip(indices,weights):
                    if not weight:continue
                    m=transforms[index]
                    for r in range(3):p[r]+=weight*(sum(m[r*4+c]*vertex[c] for c in range(3))+m[r*4+3])
                points.append([p[0],-p[2],p[1]])
        return [[min(p[i] for p in points) for i in range(3)],[max(p[i] for p in points) for i in range(3)]]


source_report=os.environ.get('BULLY_PROP_SOURCE_REPORT','evidence/skinned-props-export-v04.json')
catalog=json.loads((root/source_report).read_text())
result=dict(schema='rea-bully-skinned-props-blender-validation-v1',host=bpy.app.version_string,
            source_report=source_report,models=[],failures=[])
selected_names=os.environ.get('BULLY_PROP_QA_MODELS')
result['scope']=selected_names.split(',') if selected_names else 'all exported models'
result['expected_model_count']=sum(m['status']=='exported' and (not selected_names or m['name'] in selected_names.split(',')) for m in catalog['models'])
result['interpolation_note']='Source and direct glTF use SLERP; Blender preview uses normalized linear quaternion FCurves. Both references are checked independently; preview differences are preserved.'
renders={'TGKFlag':'TGKFLAG_IDLE','PortaPoo':'PORTAPOO_DAMAGE01','CARNI01':'CARNI01_CARNY_IDLE',
         'BikeGar':'BIKEGAR_OPEN','SCDoor':'SCDOOR_OPEN','Armor':'ARMOR_IDLE'}
for model in catalog['models']:
    if model['status']!='exported':continue
    if selected_names and model['name'] not in selected_names.split(','):continue
    try:
        path=root/model['output']
        if sha256(path)!=model['output_sha256']:raise RuntimeError('GLB changed after export')
        nif=CharacterNif(Path(model['source_nif']['path']))
        bones=skeleton(nif,model['skeleton_root_name']);source_worlds=geometry_worlds(nif);shapes=[]
        for i,t in enumerate(nif.types):
            if t not in ('NiTriShape','NiTriStrips'):continue
            shape=nif.block(i)
            if shape['skin']<0:continue
            mesh=nif.block(shape['data']);sk=nif.block(shape['skin']);data=nif.block(sk['data'])
            inf,*_=influences(data,len(mesh['vertices']))
            shapes.append(dict(vertices=mesh['vertices'],influences=inf,
                colors=mesh.get('colors') or [],
                binds=[(block,matrix(bind)) for block,bind in zip(sk['bones'],data['bones'])],
                global_skin=matmul(source_worlds[i],matrix(data['transform']))))
        payload=json.loads((path.parent/'animations.json').read_text())
        glb=GlbEvaluator(path)
        if model.get('source_vertex_rgb_preserved'):
            primitives=glb.doc['meshes'][0]['primitives']
            if len(primitives)!=len(shapes):raise RuntimeError('Source geometry/color primitive correspondence lost')
            for shape,primitive in zip(shapes,primitives):
                if not shape['colors']:continue
                colors=glb.accessor(primitive['attributes']['COLOR_0'])
                if colors!=[tuple(c[:3]) for c in shape['colors']]:raise RuntimeError('Original source vertex RGB values changed')
        by_name={c['name'].replace('\\','_'):c for c in payload['clips']}
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=str(path))
        arms=[o for o in bpy.context.scene.objects if o.type=='ARMATURE']
        meshes=[o for o in bpy.context.scene.objects if o.type=='MESH' and any(m.type=='ARMATURE' for m in o.modifiers)]
        if len(arms)!=1 or len(meshes)!=1:raise RuntimeError('Original skin mesh or armature missing')
        arm,mesh=arms[0],meshes[0]
        for o in bpy.context.scene.objects:
            if o.type=='MESH' and o not in meshes:o.hide_render=True
        tracks=list(arm.animation_data.nla_tracks)
        if len(tracks)!=len(model['clips']):raise RuntimeError('Imported animation count differs from original group')
        arm.animation_data.action=None
        for track in tracks:track.mute=True
        scene=bpy.context.scene
        checked=dict(model_key=model['model_key'],name=model['name'],original_bones=len(bones),
                     imported_bones=len(arm.data.bones),clips=[],maximum_source_bounds_difference_metres=0.,
                     original_vertex_rgb_copied_exactly=bool(model.get('source_vertex_rgb_preserved')),
                     maximum_blender_nlerp_difference_metres=0.,maximum_gltf_source_slerp_difference_metres=0.)
        for track in tracks:
            clip=by_name[track.name];strip=track.strips[0];track.mute=False
            checked_clip=dict(name=clip['name'],samples=[],nla_frame_start=strip.frame_start,nla_frame_end=strip.frame_end,
                action_frame_start=strip.action_frame_start,action_frame_end=strip.action_frame_end,
                source_duration=clip['duration'],scene_fps=scene.render.fps,
                source_last_key_time=max(t for tr in clip['channels'] for field in ('rotation','translation') for t,_ in tr[field]))
            for ratio in (0.,.5,1.):
                frame=strip.frame_start+(strip.frame_end-strip.frame_start)*ratio
                scene.frame_set(int(frame),subframe=frame-int(frame))
                deps=bpy.context.evaluated_depsgraph_get();evaluated=mesh.evaluated_get(deps);data=evaluated.to_mesh()
                points=[evaluated.matrix_world@v.co for v in data.vertices]
                if not all(math.isfinite(v) for p in points for v in p):raise RuntimeError('Nonfinite GLB deformed vertex')
                bounds=[[fn(p[i] for p in points) for i in range(3)] for fn in (min,max)]
                expected=original_bounds(bones,shapes,clip,ratio*clip['duration'])
                nlerp=original_bounds(bones,shapes,clip,ratio*clip['duration'],'nlerp')
                exported=glb.bounds(track.name,ratio*clip['duration'])
                difference=max(abs(a-b) for x,y in zip(bounds,expected) for a,b in zip(x,y))
                nlerp_difference=max(abs(a-b) for x,y in zip(bounds,nlerp) for a,b in zip(x,y))
                gltf_difference=max(abs(a-b) for x,y in zip(exported,expected) for a,b in zip(x,y))
                checked['maximum_source_bounds_difference_metres']=max(checked['maximum_source_bounds_difference_metres'],difference)
                checked['maximum_blender_nlerp_difference_metres']=max(checked['maximum_blender_nlerp_difference_metres'],nlerp_difference)
                checked['maximum_gltf_source_slerp_difference_metres']=max(checked['maximum_gltf_source_slerp_difference_metres'],gltf_difference)
                checked_clip['samples'].append(dict(ratio=ratio,bounds=bounds,original_cpu_bounds=expected,difference_metres=difference,
                    blender_nlerp_reference_difference_metres=nlerp_difference,gltf_source_slerp_difference_metres=gltf_difference))
                evaluated.to_mesh_clear()
            checked['clips'].append(checked_clip)
            track.mute=True
        if checked['maximum_blender_nlerp_difference_metres']>.0001 or checked['maximum_gltf_source_slerp_difference_metres']>.0001:
            result.setdefault('failed_model_checks',[]).append(checked)
            raise RuntimeError('Source/GLB/Blender interpolation-reference disagreement: '+str(checked['maximum_gltf_source_slerp_difference_metres'])+'/'+str(checked['maximum_blender_nlerp_difference_metres']))
        if os.environ.get('BULLY_PROP_QA_RENDER')=='1' and model['name'] in renders:
            track=next(t for t in tracks if t.name==renders[model['name']]);track.mute=False;strip=track.strips[0]
            frame=(strip.frame_start+strip.frame_end)/2.;scene.frame_set(int(frame),subframe=frame-int(frame))
            deps=bpy.context.evaluated_depsgraph_get();evaluated=mesh.evaluated_get(deps);data=evaluated.to_mesh()
            points=[evaluated.matrix_world@v.co for v in data.vertices]
            lo=Vector([min(p[i] for p in points) for i in range(3)]);hi=Vector([max(p[i] for p in points) for i in range(3)])
            evaluated.to_mesh_clear();target=(lo+hi)/2;extent=max(hi[i]-lo[i] for i in range(3))
            world=bpy.data.worlds.new('Neutral studio');world.use_nodes=True
            world.node_tree.nodes['Background'].inputs[0].default_value=(.2,.22,.25,1.)
            world.node_tree.nodes['Background'].inputs[1].default_value=.65;scene.world=world
            for name,direction,energy in [('Key',(3.,4.,4.),350.),('Fill',(-3.,1.,2.),180.),('Rim',(1.,-3.,3.),250.)]:
                light=bpy.data.lights.new(name,'AREA');light.energy=energy*extent*extent;light.size=extent*2
                obj=bpy.data.objects.new(name,light);scene.collection.objects.link(obj);obj.location=target+Vector(direction)*extent/2
                obj.rotation_euler=(target-obj.location).to_track_quat('-Z','Y').to_euler()
            camera=bpy.data.cameras.new('Original skinned object');obj=bpy.data.objects.new('Original skinned object',camera)
            scene.collection.objects.link(obj);obj.location=target+Vector((2.5,4.5,2.))*extent
            obj.rotation_euler=(target-obj.location).to_track_quat('-Z','Y').to_euler()
            camera.type='ORTHO';camera.ortho_scale=extent*1.4;scene.camera=obj
            scene.render.engine='CYCLES';scene.cycles.samples=16
            scene.render.resolution_x=800;scene.render.resolution_y=800;scene.render.resolution_percentage=100
            scene.render.image_settings.file_format='PNG';scene.render.filepath=str(path.parent/(renders[model['name']].lower()+'-preview-v04.png'))
            bpy.ops.render.render(write_still=True);checked['actual_pose_render']=scene.render.filepath
        result['models'].append(checked)
        print('REA_PROP_VALIDATED',model['name'],checked['maximum_source_bounds_difference_metres'],flush=True)
    except Exception as exc:
        result['failures'].append(dict(name=model['name'],model_key=model['model_key'],error=str(exc)))
        print('REA_PROP_REJECTED',model['name'],str(exc),flush=True)
    result['status']='passed' if not result['failures'] and len(result['models'])==result['expected_model_count'] else 'incomplete'
    (root/os.environ.get('BULLY_PROP_QA_REPORT','evidence/skinned-props-blender-validation-v04.json')).write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print('REA_PROPS_RESULT',json.dumps(dict(status=result['status'],models=len(result['models']),failures=result['failures'])))

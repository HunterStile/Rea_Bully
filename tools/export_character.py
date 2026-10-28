"""Independent original Bully skinned NIF -> glTF 2.0 exporter.

Format facts: https://github.com/LiquidSnake0/bully-re/blob/main/docs/nif.md
No source implementation from that GPL project is included here.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import struct

from export_nif import Nif, Glb, FormatError, IDENTITY, matrix, matmul, point, load_textures, texture_basename

C = (1,0,0,0, 0,0,1,0, 0,-1,0,0, 0,0,0,1)
CI = (1,0,0,0, 0,0,-1,0, 0,1,0,0, 0,0,0,1)


def converted(m):
    return matmul(matmul(C, m), CI)


def column_major(m):
    return tuple(m[r*4+c] for c in range(4) for r in range(4))


def inverse(m):
    rows=[[float(m[r*4+c]) for c in range(4)]+[float(r==c) for c in range(4)] for r in range(4)]
    for c in range(4):
        p=max(range(c,4),key=lambda r:abs(rows[r][c]))
        if abs(rows[p][c])<1e-12:raise FormatError('Singular rest transform')
        rows[c],rows[p]=rows[p],rows[c]
        v=rows[c][c];rows[c]=[x/v for x in rows[c]]
        for r in range(4):
            if r!=c:
                v=rows[r][c];rows[r]=[x-v*y for x,y in zip(rows[r],rows[c])]
    return tuple(rows[r][c+4] for r in range(4) for c in range(4))


def unify_mesh(glb,bones,asset_name='JimmyOriginal'):
    """One mesh with original material primitives, one skin, original bind matrices.

    Four unweighted hierarchy helpers have no stored NiSkinData bind matrix;
    their inverse rest matrix is generated solely to make glTF joints complete.
    All weighted-joint inverse binds must agree bit-for-bit across source parts.
    """
    bind_by_joint={}
    for skin in glb.json['skins']:
        a=glb.json['accessors'][skin['inverseBindMatrices']];v=glb.json['bufferViews'][a['bufferView']]
        values=struct.unpack_from('<'+'f'*16*a['count'],glb.data,v.get('byteOffset',0))
        for i,joint in enumerate(skin['joints']):
            m=values[i*16:(i+1)*16]
            if joint in bind_by_joint and bind_by_joint[joint]!=m:
                raise FormatError('Cannot unify differing original bind matrices')
            bind_by_joint[joint]=m
    generated=[];ibms=[]
    for i,b in enumerate(bones):
        if i not in bind_by_joint:
            bind_by_joint[i]=column_major(converted(inverse(b['world_matrix'])))
            generated.append(b['name'])
        ibms.append(bind_by_joint[i])
    primitives=[]
    for n in glb.json['nodes'][len(bones):]:
        skin=glb.json['skins'][n['skin']]
        for primitive in glb.json['meshes'][n['mesh']]['primitives']:
            acc=glb.json['accessors'][primitive['attributes']['JOINTS_0']]
            view=glb.json['bufferViews'][acc['bufferView']]
            old=struct.unpack_from('<'+'H'*acc['count']*4,glb.data,view.get('byteOffset',0))
            new=[skin['joints'][j] for j in old]
            primitive['attributes']['JOINTS_0']=glb.accessor([tuple(new[i:i+4]) for i in range(0,len(new),4)],'H','VEC4',5123)
            primitives.append(primitive)
    glb.json['skins']=[{'name':'OriginalJimmySkeleton' if asset_name=='JimmyOriginal' else asset_name+'_OriginalSkeleton','joints':list(range(len(bones))),'skeleton':0,
                        'inverseBindMatrices':glb.accessor(ibms,'f','MAT4',5126)}]
    glb.json['meshes']=[{'name':asset_name,'primitives':primitives}]
    glb.json['nodes']=glb.json['nodes'][:len(bones)]+[{'name':asset_name,'mesh':0,'skin':0}]
    glb.json['scenes'][0]['nodes']=[0,len(bones)]
    return generated


def quaternion(r):
    # Orthogonal rotation matrix to glTF xyzw quaternion.
    trace = r[0]+r[4]+r[8]
    if trace > 0:
        s = math.sqrt(trace+1)*2
        q = ((r[7]-r[5])/s, (r[2]-r[6])/s, (r[3]-r[1])/s, s/4)
    else:
        i = max(range(3), key=lambda i: r[i*3+i])
        j, k = (i+1)%3, (i+2)%3
        s = math.sqrt(1+r[i*3+i]-r[j*3+j]-r[k*3+k])*2
        v = [0.,0.,0.]
        v[i] = s/4
        v[j] = (r[i*3+j]+r[j*3+i])/s
        v[k] = (r[i*3+k]+r[k*3+i])/s
        q = (*v, (r[k*3+j]-r[j*3+k])/s)
    length = math.sqrt(sum(x*x for x in q))
    return [x/length for x in q]


class CharacterNif(Nif):
    def block(self, index):
        if index < 0 or self.types[index] not in ('NiSkinInstance','NiSkinData','NiSkinPartition'):
            return super().block(index)
        if index in self.decoded:
            return self.decoded[index]
        r = self.blocks[index]
        r.pos = 0
        kind = self.types[index]
        out = {'type':kind}
        if kind == 'NiSkinInstance':
            out['data'],out['partition'],out['root'] = r.read('iii')
            out['bones'] = [x[0] for x in r.array('i',r.read('I'))]
        elif kind == 'NiSkinData':
            def transform():
                return {'rotation':r.read('9f'),'translation':r.read('3f'),'scale':r.read('f')}
            out['transform'] = transform()
            count, has_weights = r.read('IB')
            out['bones'] = []
            for _ in range(count):
                b = transform()
                b['sphere'] = r.read('4f')
                count = r.read('H')
                b['weights'] = r.array('Hf',count) if has_weights else []
                out['bones'].append(b)
            out['has_weights'] = bool(has_weights)
        else:
            out['partitions'] = []
            for _ in range(r.read('I')):
                nv,nt,nb,ns,nw = r.read('5H')
                p = dict(vertex_count=nv,triangle_count=nt,influences=nw)
                p['bones'] = [x[0] for x in r.array('H',nb)]
                p['vertices'] = [x[0] for x in r.array('H',nv)] if r.read('B') else list(range(nv))
                p['weights'] = r.array('f'*nw,nv) if r.read('B') else []
                lengths = [x[0] for x in r.array('H',ns)]
                has_faces = r.read('B')
                p['faces'] = r.array('3H',nt) if not ns and has_faces else []
                p['strips'] = [[x[0] for x in r.array('H',size)] for size in lengths] if ns and has_faces else []
                p['indices'] = r.array('B'*nw,nv) if r.read('B') else []
                out['partitions'].append(p)
        if r.pos != len(r.data):
            raise FormatError(f'{kind} #{index} consumed {r.pos}/{len(r.data)}')
        self.decoded[index] = out
        return out


def source(path):
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def skeleton(nif,root_name='Dummy'):
    dummy = [i for i,k in enumerate(nif.types) if k == 'NiNode' and nif.block(i)['name'] == root_name]
    if len(dummy) != 1:
        raise FormatError('Expected exactly one original '+root_name+' skeleton')
    bones = []
    def visit(i,parent,world):
        n = nif.block(i)
        if n['type'] != 'NiNode':
            return
        w = matmul(world,matrix(n))
        bones.append({'source_block':i,'parent_block':parent,'name':n['name'],
                      'translation':n['translation'],'rotation':n['rotation'],'scale':n['scale'],
                      'world_matrix':w})
        for ch in n['children']:
            visit(ch,i,w)
    visit(dummy[0],None,IDENTITY)
    return bones


def influences(skin_data, count):
    result = [[] for _ in range(count)]
    for b,bone in enumerate(skin_data['bones']):
        for v,w in bone['weights']:
            if v >= count or not math.isfinite(w) or w < 0:
                raise FormatError('Invalid skin vertex or weight')
            if w:
                result[v].append((b,w))
    totals = [sum(w for _,w in a) for a in result]
    if any(not a or len(a)>4 for a in result):
        raise FormatError('Unweighted vertex or more than four influences')
    if any(abs(t-1)>0.001 for t in totals):
        raise FormatError('Skin weights do not sum to one')
    joints,weights = [],[]
    for a,t in zip(result,totals):
        joints.append(tuple(b for b,_ in a)+(0,)*(4-len(a)))
        weights.append(tuple(w/t for _,w in a)+(0.,)*(4-len(a)))
    return result,joints,weights,totals


def geometry_worlds(nif):
    """Original ancestor transforms, used to prove overall skin cancellation."""
    worlds={}
    def visit(index,parent,seen):
        if index in seen:raise FormatError('Cyclic original node hierarchy')
        node=nif.block(index)
        if node['type'] not in ('NiNode','NiTriShape','NiTriStrips'):return
        world=matmul(parent,matrix(node))
        if index in worlds and worlds[index]!=world:raise FormatError('Ambiguous original node parent')
        worlds[index]=world
        for child in node.get('children',[]):visit(child,world,seen|{index})
    for root in nif.roots:visit(root,IDENTITY,set())
    return worlds


def export(base, parts, output, report_path, *, base_texture_paths=None, base_texture_name_map=None, asset_name='JimmyOriginal', skeleton_root_name='Dummy', allow_transformed_skin_shape=False, preserve_global_skin_transform=False, preserve_vertex_colors=False):
    nif = CharacterNif(base)
    bones = skeleton(nif,skeleton_root_name)
    names = {b['name']:i for i,b in enumerate(bones)}
    if len(names) != len(bones):
        raise FormatError('Ambiguous duplicate skeleton names')
    blockmap = {b['source_block']:i for i,b in enumerate(bones)}
    glb = Glb()
    glb.json['asset']['generator'] = 'Rea_Bully independent original skin exporter'
    for b in bones:
        local = converted(matrix(b))
        scale = b['scale']
        rot = tuple(local[r*4+c]/scale for r in range(3) for c in range(3))
        n = {'name':b['name'],'translation':[local[3],local[7],local[11]],
             'rotation':quaternion(rot),'scale':[scale]*3,
             'extras':{'originalNifBlock':b['source_block'],'originalBoneIndex':len(glb.json['nodes'])}}
        glb.json['nodes'].append(n)
    for i,b in enumerate(bones):
        if b['parent_block'] is None:
            glb.json['scenes'][0]['nodes'].append(i)
        else:
            glb.json['nodes'][blockmap[b['parent_block']]].setdefault('children',[]).append(i)
    glb.json['skins'] = []
    report = {'schema_version':1,'base':source(base),'output':str(output),'bone_count':len(bones),
              'skeleton_bones':bones,'coordinate_conversion':'Original Z up -> glTF Y up: (x,z,-y); metres retained',
              'parts':[],'shape_count':0,'vertices':0,'triangles':0,'textures':0,'excluded_helpers':[],
              'raw_bounds_min_gltf':None,'raw_bounds_max_gltf':None,
              'uv_policy':'Original UV coordinates retained; embedded decoded image origin matches glTF. No vertical inversion.',
              'limitations':['Original material shaders/special slots are not recreated',
                             'No gameplay, collision, or animation is generated by this exporter',
                             'Default PLAYER already includes uniform; replacement garment visibility masks are not implemented']}
    image_cache = {}
    preserved_global_skin = None
    for path,texture_paths in [(base, [base.with_suffix('.nft')])]+parts:
        # Main NFT is in another indexed directory; callers supply paths for all parts.
        if path == base:
            texture_paths = ([Path(p) for p in base_texture_paths] if base_texture_paths is not None
                             else [p for p in base.parents[1].rglob(base.stem+'.nft')])
        src = CharacterNif(path)
        source_worlds = geometry_worlds(src)
        textures = load_textures(texture_paths)
        if path==base and base_texture_name_map:
            for requested,actual in base_texture_name_map.items():
                if requested in textures or actual not in textures:
                    raise FormatError('Ambiguous/missing explicit texture variant mapping')
                textures[requested]=textures[actual]
        part = {'source':source(path),'texture_sources':[source(p) for p in texture_paths],
                'explicit_texture_name_map':base_texture_name_map if path==base else None,
                'shapes':[],'block_types':dict(Counter(src.types))}
        for i,k in enumerate(src.types):
            if k not in ('NiTriShape','NiTriStrips'):
                continue
            shape = src.block(i)
            if shape['skin'] < 0:
                report['excluded_helpers'].append({'source':str(path),'block':i,'name':shape['name']})
                continue
            mesh = src.block(shape['data'])
            if max(abs(x-y) for x,y in zip(matrix(shape),IDENTITY))>1e-5 and not allow_transformed_skin_shape:
                raise FormatError('Nonidentity skinned shape transform requires explicit recovery')
            skin = src.block(shape['skin'])
            sd = src.block(skin['data'])
            skin_overall=matrix(sd['transform'])
            skin_overall_nonidentity=max(abs(x-y) for x,y in zip(skin_overall,IDENTITY))>1e-5
            cancellation=matmul(source_worlds[i],skin_overall)
            cancellation_error=max(abs(x-y) for x,y in zip(cancellation,IDENTITY))
            # NIF evaluates geometryWorld * overallSkin * boneWorld * bind.
            # Installed NPC geometry ancestors are precisely inverse(overallSkin):
            # their cancellation leaves the existing glTF boneWorld * bind path.
            # Keep both original matrices as evidence; reject uncancelled cases.
            if cancellation_error>1e-5 and preserve_global_skin_transform:
                if preserved_global_skin is not None and max(abs(a-b) for a,b in zip(preserved_global_skin,cancellation))>1e-5:
                    raise FormatError('Different per-shape global skin matrices require a multi-skeleton export')
                preserved_global_skin=cancellation
            elif skin_overall_nonidentity and cancellation_error>1e-5:
                raise FormatError('Nonidentity overall skin transform does not cancel original geometry world transform')
            partition = src.block(skin['partition']) if skin['partition'] >= 0 else None
            if len(sd['bones']) != len(skin['bones']):
                raise FormatError('Skin bone count mismatch')
            source_names = [src.block(b)['name'] for b in skin['bones']]
            if any(n not in names for n in source_names):
                raise FormatError('Garment references bone outside original base skeleton')
            joint_nodes = [names[n] for n in source_names]
            inf,joints,weights,totals = influences(sd,len(mesh['vertices']))
            # Independently verify partition-local weights against authoritative NiSkinData.
            errors = []
            if partition:
                for p in partition['partitions']:
                    if not p['weights'] or not p['indices']:
                        raise FormatError('Missing partition weights/index map')
                    for row,v in enumerate(p['vertices']):
                        actual = {}
                        for idx,w in zip(p['indices'][row],p['weights'][row]):
                            if w:
                                if idx>=len(p['bones']):raise FormatError('Partition bone index exceeds palette')
                                b = p['bones'][idx];actual[b] = actual.get(b,0)+w
                        expected = dict(inf[v])
                        errors.extend(abs(actual.get(b,0)-expected.get(b,0)) for b in actual.keys()|expected.keys())
                # GPU partitions may prune a bone to fit the per-batch palette
                # and renormalize the remaining influences. Preserve the full
                # original NiSkinData weights and report this measured delta.
            ibms = [column_major(converted(matrix(b))) for b in sd['bones']]
            glb.json['skins'].append({'name':path.stem+'_'+str(i),'joints':joint_nodes,'skeleton':0,
                                     'inverseBindMatrices':glb.accessor(ibms,'f','MAT4',5126)})
            attrs = {'POSITION':glb.accessor([point(IDENTITY,p) for p in mesh['vertices']],'f','VEC3',5126),
                     'JOINTS_0':glb.accessor(joints,'H','VEC4',5123),
                     'WEIGHTS_0':glb.accessor(weights,'f','VEC4',5126)}
            if preserve_vertex_colors and mesh.get('colors'):
                if not all(math.isfinite(v) and 0<=v<=1 for row in mesh['colors'] for v in row[:3]):
                    raise FormatError('Original vertex RGB is not finite normalized data')
                attrs['COLOR_0']=glb.accessor([tuple(row[:3]) for row in mesh['colors']],'f','VEC3',5126)
            posacc=glb.json['accessors'][attrs['POSITION']]
            for key,acc_key,fn in [('raw_bounds_min_gltf','min',min),('raw_bounds_max_gltf','max',max)]:
                report[key]=[fn(a,b) for a,b in zip(report[key],posacc[acc_key])] if report[key] else posacc[acc_key]
            if mesh['normals']:
                attrs['NORMAL']=glb.accessor([point(IDENTITY,p,True) for p in mesh['normals']],'f','VEC3',5126)
            if mesh['uv_sets']:
                attrs['TEXCOORD_0']=glb.accessor(mesh['uv_sets'][0],'f','VEC2',5126)
            material={'name':path.stem+'_'+shape['name'], 'pbrMetallicRoughness':{'metallicFactor':0,'roughnessFactor':0.85}}
            for propid in shape['properties']:
                prop=src.block(propid)
                if prop['type']=='NiMaterialProperty':
                    material['pbrMetallicRoughness']['baseColorFactor']=[*prop['diffuse'],prop['alpha']]
                if prop['type']=='NiTexturingProperty' and prop['slots'] and prop['slots'][0]:
                    texname=texture_basename(src.block(prop['slots'][0]['source'])['filename'])
                    if texname not in textures:raise FormatError('Missing original texture '+texname)
                    key=hashlib.sha256(textures[texname]['png']).hexdigest()
                    if key not in image_cache:image_cache[key]=glb.image(texname,textures[texname]['png'])
                    material['pbrMetallicRoughness']['baseColorTexture']={'index':image_cache[key]}
                if prop['type']=='NiAlphaProperty':
                    if prop['flags']&1:material['alphaMode']='BLEND'
                    elif prop['flags']&0x200:
                        material['alphaMode']='MASK';material['alphaCutoff']=prop['threshold']/255
                if prop['type']=='NiStencilProperty':material['doubleSided']=((prop['flags']>>10)&3)==3
            glb.json['materials'].append(material)
            primitive={'attributes':attrs,'indices':glb.accessor([(v,) for t in mesh['triangles'] for v in t],'H','SCALAR',5123),'material':len(glb.json['materials'])-1}
            glb.json['meshes'].append({'name':path.stem+'_'+shape['name'],'primitives':[primitive]})
            nodeidx=len(glb.json['nodes'])
            glb.json['nodes'].append({'name':path.stem+'_'+shape['name'],'mesh':len(glb.json['meshes'])-1,'skin':len(glb.json['skins'])-1,
                                      'extras':{'sourceNif':str(path),'sourceShapeBlock':i,'sourceDataBlock':shape['data']}})
            glb.json['scenes'][0]['nodes'].append(nodeidx)
            # Static bind consistency using ORIGINAL stored transforms and ORIGINAL source vertices.
            bind_errors=[]
            for v,a in zip(mesh['vertices'],inf):
                projected=[0.,0.,0.]
                for b,w in a:
                    m=matmul(bones[joint_nodes[b]]['world_matrix'],matrix(sd['bones'][b]))
                    q=[sum(m[r*4+c]*v[c] for c in range(3))+m[r*4+3] for r in range(3)]
                    projected=[x+w*y for x,y in zip(projected,q)]
                bind_errors.append(math.dist(v,projected))
            sh={'name':shape['name'],'shape_block':i,'data_block':shape['data'],'skin_block':shape['skin'],
                'original_geometry_world_matrix':source_worlds[i],
                'original_overall_skin_matrix':skin_overall,
                'overall_skin_geometry_cancellation_max_error':cancellation_error,
                'original_vertex_rgb_preserved':bool(preserve_vertex_colors and mesh.get('colors')),
                'original_vertex_alpha_range_not_mapped_to_material_opacity':([min(c[3] for c in mesh['colors']),max(c[3] for c in mesh['colors'])] if mesh.get('colors') else None),
                'vertices':len(mesh['vertices']),'triangles':len(mesh['triangles']),'joint_names':source_names,
                'weight_sum_min':min(totals),'weight_sum_max':max(totals),'max_influences':max(map(len,inf)),
                'partition_count':len(partition['partitions']) if partition else 0,
                'partition_weight_max_difference':max(errors,default=0),
                'bind_pose_error_max_metres':max(bind_errors),'bind_pose_error_mean_metres':sum(bind_errors)/len(bind_errors)}
            part['shapes'].append(sh)
            report['shape_count']+=1;report['vertices']+=len(mesh['vertices']);report['triangles']+=len(mesh['triangles'])
        report['parts'].append(part)
    report['unweighted_helpers_with_generated_inverse_rest_bind']=unify_mesh(glb,bones,asset_name)
    if preserved_global_skin is not None:
        transform=converted(preserved_global_skin)
        wrapper=len(glb.json['nodes'])
        glb.json['nodes'].append(dict(name=asset_name+'_OriginalGeometrySkinTransform',matrix=column_major(transform),children=[0]))
        glb.json['scenes'][0]['nodes']=[wrapper,len(bones)]
        report['preserved_geometry_skin_transform_wrapper']=preserved_global_skin
    report['gltf_mesh_count']=1
    report['gltf_skin_joint_count']=len(bones)
    glb.write(output)
    report['textures']=len(image_cache)
    report['output_sha256']=hashlib.sha256(output.read_bytes()).hexdigest()
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=Path('local/converted/jimmy/jimmy_final.glb'))
    p.add_argument('--report',type=Path,default=Path('evidence/jimmy-final-export.json'))
    p.add_argument('--garment-overlays',action='store_true',help='Audit original replacement garments; default PLAYER already includes uniform')
    a=p.parse_args()
    local=Path(__file__).resolve().parents[1]/'local'
    if local.resolve() not in a.output.resolve().parents or a.output.suffix.lower()!='.glb':
        p.error('Derived GLB output must remain inside workspace local/')
    root=Path('local/extracted/Stream/World')
    def find(name):
        paths=list(root.rglob(name))
        if len(paths)!=1:raise FormatError(f'Expected one {name}, got {len(paths)}')
        return paths[0]
    parts=[(find(name+'.nif'),[find(name+'.nft')]) for name in ('S_Sweater5','S_Pants1','S_Sneakers1')] if a.garment_overlays else []
    r=export(find('PLAYER.nif'),parts,a.output,a.report)
    print(json.dumps({k:r[k] for k in ('output','bone_count','shape_count','vertices','triangles','textures')}))


if __name__=='__main__':main()

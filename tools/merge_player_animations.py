"""Append independently decoded original AGR curves to Jimmy's skinned GLB.

Original NIF XYZ -> glTF XYZ is the proper rotation (x,z,-y), applied to
translation and quaternion vector parts. Local node rotation is replaced by
AGR rotation. ARROW displacement remains metadata; output clips are in-place.
"""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import struct


def merge(source='local/converted/jimmy/jimmy.glb', payload='local/converted/player/player-original-animations.json', output='local/converted/jimmy/jimmy_animated.glb', report='evidence/jimmy-animation-glb.json', *, in_place=True, omit_translation_bones=('ARROW',)):
    source,payload,output,report=map(Path,(source,payload,output,report))
    if output.exists() or report.exists():raise FileExistsError('Output/report must be new')
    raw=source.read_bytes();magic,version,total=struct.unpack_from('<III',raw)
    if magic!=0x46546C67 or version!=2 or total!=len(raw):raise ValueError('Invalid GLB')
    size,kind=struct.unpack_from('<II',raw,12)
    if kind!=0x4E4F534A:raise ValueError('GLB JSON chunk missing')
    doc=json.loads(raw[20:20+size]);pos=20+size
    bsize,bkind=struct.unpack_from('<II',raw,pos)
    if bkind!=0x004E4942 or pos+8+bsize!=len(raw):raise ValueError('Expected exactly one BIN chunk')
    blob=bytearray(raw[pos+8:])
    if len(doc['buffers'])!=1 or 'uri' in doc['buffers'][0]:raise ValueError('Expected GLB embedded single buffer')
    p=json.loads(payload.read_text());original_nodes=copy.deepcopy(doc['nodes'])
    if doc.get('animations'):raise ValueError('Refuse existing animations')
    def accessor(values,width):
        while len(blob)%4:blob.append(0)
        flat=[v for value in values for v in (value if isinstance(value,(list,tuple)) else [value])]
        offset=len(blob);packed=struct.pack('<'+'f'*len(flat),*flat);blob.extend(packed)
        view=len(doc['bufferViews']);doc['bufferViews'].append({'buffer':0,'byteOffset':offset,'byteLength':len(packed)})
        item={'bufferView':view,'componentType':5126,'count':len(values),'type':{1:'SCALAR',3:'VEC3',4:'VEC4'}[width]}
        if width==1:item.update(min=[min(values)],max=[max(values)])
        index=len(doc['accessors']);doc['accessors'].append(item);return index
    stats=[];doc['animations']=[]
    for clip in p['clips']:
        animation={'name':clip['name'].replace('\\','_'),'samplers':[],'channels':[],
                   'extras':{'original_name':clip['name'],'source_agr_sha256':clip['agr_sha256'],
                             'source_duration_seconds':clip['duration'],'in_place':in_place,
                             'root_motion_policy':clip.get('root_motion_policy',p['root_motion_policy']),
                             'source_motion_displacement_metres':clip['root_motion']['displacement'],
                             'source_motion_bone':clip['root_motion'].get('name'),
                             'source_locomotion_speed_metres_per_second':clip['root_motion']['speed_m_per_second']}}
        for track in clip['channels']:
            index=track['bone']
            if doc['nodes'][index]['name']!=track['name']:raise ValueError('Original skeleton order/name mismatch')
            for field,width in [('rotation',4),('translation',3)]:
                keys=track[field]
                if not keys or (field=='translation' and track['name'] in omit_translation_bones):continue
                values=[]
                for _,v in keys:
                    result=[v[0],v[2],-v[1]]
                    if width==4:result.append(v[3])
                    values.append(result)
                time_accessor=accessor([key[0] for key in keys],1);value_accessor=accessor(values,width)
                sampler=len(animation['samplers'])
                animation['samplers'].append({'input':time_accessor,'output':value_accessor,'interpolation':'LINEAR'})
                animation['channels'].append({'sampler':sampler,'target':{'node':index,'path':field}})
        doc['animations'].append(animation)
        stats.append({'name':animation['name'],'channels':len(animation['channels']),'duration':clip['duration']})
    assert doc['nodes']==original_nodes
    doc['buffers'][0]['byteLength']=len(blob)
    doc.setdefault('asset',{})['generator']='Rea_Bully independent NIF + AGR converter'
    encoded=json.dumps(doc,separators=(',',':')).encode('utf-8');encoded+=b' '*((-len(encoded))%4)
    blob.extend(b'\0'*((-len(blob))%4))
    result=struct.pack('<III',magic,2,12+8+len(encoded)+8+len(blob))+struct.pack('<II',len(encoded),0x4E4F534A)+encoded+struct.pack('<II',len(blob),0x004E4942)+blob
    output.write_bytes(result)
    evidence={'status':'original_curves_merged_numeric_validation_pending_visual',
              'source_glb':str(source),'source_glb_sha256':hashlib.sha256(raw).hexdigest(),
              'animation_payload':str(payload),'animation_payload_sha256':hashlib.sha256(payload.read_bytes()).hexdigest(),
              'output':str(output),'output_sha256':hashlib.sha256(result).hexdigest(),
              'original_nodes_unchanged':True,'original_binary_prefix_unchanged':True,
              'clips':stats,'root_motion_policy':p['root_motion_policy'],
              'in_place':in_place,'omitted_translation_bone_names':list(omit_translation_bones),
              'limitations':[f'{len(p["clips"])} decoded original curves. Original gameplay state-machine behavior is not implemented.',
                             'NIF and animation coordinate conversion still requires visual validation.']}
    report.write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(output),'clips':stats,'bytes':len(result)}))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--source',default='local/converted/jimmy/jimmy.glb');ap.add_argument('--payload',default='local/converted/player/player-original-animations.json');ap.add_argument('--output',default='local/converted/jimmy/jimmy_animated.glb');ap.add_argument('--report',default='evidence/jimmy-animation-glb.json');a=ap.parse_args();merge(a.source,a.payload,a.output,a.report)

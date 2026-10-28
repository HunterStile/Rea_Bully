"""Numerical heading evidence from original skeleton and AGR rotations.

The bind-facing vector (-Y in NIF) is the visual finding from the skin QA;
we transport that vector by the exact bind-to-animated head rotation. This
checks whether the observed reversal is already in source rotations.
"""
from pathlib import Path
import json
import math
from bully_agr import sha256


def quaternion_matrix(q):
    x,y,z,w=q
    return [1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w),
            2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w),
            2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]


def multiply(a,b):
    return [sum(a[i*3+k]*b[k*3+j] for k in range(3)) for i in range(3) for j in range(3)]


def vector(a,v):
    return [sum(a[i*3+j]*v[j] for j in range(3)) for i in range(3)]


def inverse(a):
    A,B,C,D,E,F,G,H,I=a
    det=A*(E*I-F*H)-B*(D*I-F*G)+C*(D*H-E*G)
    if abs(det)<1e-12:raise ValueError('Singular matrix')
    return [v/det for v in [E*I-F*H,C*H-B*I,B*F-C*E,F*G-D*I,A*I-C*G,C*D-A*F,D*H-E*G,B*G-A*H,A*E-B*D]]


def rotation_at(keys,time):
    if time<=keys[0][0]:return keys[0][1]
    for a,b in zip(keys,keys[1:]):
        if time<=b[0]:
            t=(time-a[0])/(b[0]-a[0]);qa,qb=a[1],b[1]
            dot=sum(x*y for x,y in zip(qa,qb))
            if dot<0:qb=[-v for v in qb];dot=-dot
            if dot>.9995:
                q=[x+t*(y-x) for x,y in zip(qa,qb)];norm=math.sqrt(sum(v*v for v in q));return [v/norm for v in q]
            angle=math.acos(min(1,dot));s=math.sin(angle)
            return [(math.sin((1-t)*angle)*x+math.sin(t*angle)*y)/s for x,y in zip(qa,qb)]
    return keys[-1][1]


def run(output='evidence/player-animation-heading.json'):
    skeleton_path=Path('evidence/player-skeleton-rest.json')
    payload_path=Path('local/converted/player/player-original-animations.json')
    bones=json.loads(skeleton_path.read_text())['bones'];payload=json.loads(payload_path.read_text())
    by_block={bone['source_block']:i for i,bone in enumerate(bones)}
    assert all(abs(b['scale']-1)<1e-6 for b in bones)
    def world_rotations(clip=None,time=0):
        result=[]
        for i,bone in enumerate(bones):
            local=bone['rotation'] if clip is None else quaternion_matrix(rotation_at(clip['channels'][i]['rotation'],time))
            if bone['parent_block'] is not None:local=multiply(result[by_block[bone['parent_block']]],local)
            result.append(local)
        return result
    rest_world=world_rotations();head=next(i for i,b in enumerate(bones) if b['name']=='Root Head')
    bind_forward=[0,-1,0]
    local_head_forward=vector(inverse(rest_world[head]),bind_forward)
    root_rest_yaw=math.degrees(math.atan2(bones[1]['rotation'][3],bones[1]['rotation'][0]))
    clips=[]
    for clip in payload['clips']:
        samples=[]
        for ratio in (0,.25,.5,.75,1):
            world=world_rotations(clip,clip['duration']*ratio)
            facing=vector(world[head],local_head_forward)
            assert facing[1]>0, (clip['name'],ratio,facing)
            samples.append({'ratio':ratio,'transported_head_forward_original_axes':facing,
                            'heading_xy_degrees':math.degrees(math.atan2(facing[1],facing[0]))})
        initial=quaternion_matrix(clip['channels'][1]['rotation'][0][1])
        root_yaw=math.degrees(math.atan2(initial[3],initial[0]))
        arrow=clip['root_motion']['displacement']
        if clip['name']!='C_PLAYER\\IDLE':assert arrow[0]==0 and arrow[1]>0 and arrow[2]==0
        clips.append({'name':clip['name'],'source_root_initial_yaw_degrees':root_yaw,
                      'root_initial_yaw_difference_from_bind_degrees':(root_yaw-root_rest_yaw+180)%360-180,
                      'head_facing_samples':samples,'source_arrow_displacement_metres':arrow})
    result={'status':'source_rotation_reversal_confirmed',
            'inputs':[{'path':str(path),'sha256':sha256(path)} for path in (skeleton_path,payload_path)],
            'bind_forward_original_axes':bind_forward,'bind_forward_basis':'Visual skin QA; static model faces NIF -Y.',
            'source_root_bind_yaw_degrees':root_rest_yaw,
            'method':'Build all 36 world rotations from original local matrices/quaternions; transport visual bind-forward by animated Head * inverse(bind Head).',
            'clips':clips,
            'conclusion':'All four original clips face approximately NIF +Y (glTF -Z); three locomotion clips carry exact positive-Y ARROW displacement. Preserve source curves and apply a 180-degree component-facing adjustment relative to bind-only preview.',
            'unreal_adjustment':'If the bind-only mesh used relative yaw -90 degrees, the animated mesh requires +90 degrees under the same importer/component conventions.',
            'limitations':['Head direction includes original pose/head movement, so it is not exactly +Y at every sample.',
                           'Numerical orientation evidence does not determine Unreal importer axes by itself.']}
    output=Path(output)
    if output.exists():raise FileExistsError(output)
    output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'status':result['status'],'clips':len(clips),'report':str(output)}))


if __name__=='__main__':run()

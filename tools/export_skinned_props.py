"""Recover original PANM object skins and their exact IDE-linked AGR groups.

Animations replace local original nodes, without flattening or extracting
contextual object motion. Source helpers without skin remain explicit omissions.
"""
from __future__ import annotations
import argparse
from collections import Counter
import gzip
import json
import math
from pathlib import Path
import struct

from bully_agr import FormatError, sha256
from export_character import CharacterNif, export as export_skin, skeleton
from export_zone import required_base_names
from export_world import Resolver
from merge_player_animations import merge


def curve_metrics(clip):
    angles,endangles,translations=[],[],[]
    for track in clip['tracks']:
        rotations=track['rotation']
        if rotations:
            q0=rotations[0][1]
            difference=lambda q:math.degrees(2*math.acos(min(1.,abs(sum(a*b for a,b in zip(q0,q))))))
            angles.extend(difference(q) for _,q in rotations)
            endangles.append(difference(rotations[-1][1]))
        positions=track['translation']
        if positions:
            translations.extend(math.dist(positions[0][1],p) for _,p in positions)
    root=clip['tracks'][0]['translation']
    displacement=[b-a for a,b in zip(root[0][1],root[-1][1])] if root else [0.,0.,0.]
    return dict(max_rotation_change_degrees=max(angles,default=0.),
                max_endpoint_rotation_degrees=max(endangles,default=0.),
                max_translation_change_metres=max(translations,default=0.),
                root_displacement_metres=displacement,
                root_endpoint_drift_metres=math.sqrt(sum(v*v for v in displacement)))


def export_all(pending_path,library_path,world,output_dir,report_path,limit=None):
    pending_path,library_path,world,output_dir,report_path=map(Path,(pending_path,library_path,world,output_dir,report_path))
    if output_dir.exists() or report_path.exists():raise FileExistsError('New output/report required')
    pending=json.loads(pending_path.read_text());library=json.loads(library_path.read_text())
    resolver=Resolver(world)
    output_dir.mkdir(parents=True)
    report=dict(schema='rea-bully-skinned-props-v1',status='running',source=dict(path=str(pending_path),sha256=sha256(pending_path)),
        animation_library=dict(path=str(library_path),sha256=sha256(library_path)),models=[],
        limitations=['Original skinned models and curves are separate from collision-aware object interactions.',
                     'Animations retain absolute source local translation, including event motions; do not loop every exported clip.',
                     'Unskinned helper geometry remains listed as omitted, never converted to replacement skin.',
                     'Source vertex/material custom effects and original ACT scheduling are not reconstructed here.'])
    selected=pending['models'][:limit] if limit else pending['models']
    for model in selected:
        item=dict(model_key=model['model_key'],model_id=model['model_id'],name=model['name'],
                  placement_count=model['placement_count'],zones=model['zones'],status='pending')
        try:
            definition=model['definition'];ide_path=Path(definition['source'])
            line=ide_path.read_text(encoding='utf-8-sig').splitlines()[definition['line']-1]
            cells=[v.strip() for v in line.split(',')]
            if int(cells[0])!=model['model_id'] or cells[1].casefold()!=model['name'].casefold():
                raise FormatError('Original IDE identity no longer matches catalog')
            group_name=cells[3]
            item['original_ide']=dict(path=str(ide_path),sha256=sha256(ide_path),line=definition['line'],
                raw=line,animation_group=group_name,ped_group=cells[4],texture_dictionary=cells[2])
            groups=[g for g in library['groups'] if g['name'].casefold()==group_name.casefold()]
            if len(groups)!=1:raise FormatError('Ambiguous/missing IDE animation group')
            group=groups[0];group_path=Path(group['path'])
            if sha256(group_path)!=group['sha256']:raise FormatError('Animation library changed')
            curves=json.loads(gzip.decompress(group_path.read_bytes()))
            source=next(c for c in model['model_resolution']['candidates'] if c['index']==model['model_resolution']['selected_index'])
            path=Path(source['path'])
            if sha256(path)!=source['sha256']:raise FormatError('Original model changed')
            nif=CharacterNif(path)
            root_name='Dummy' if any(t=='NiNode' and nif.block(i)['name']=='Dummy' for i,t in enumerate(nif.types)) else 'Root'
            bones=skeleton(nif,root_name)
            if any(len(c['tracks'])!=len(bones) for c in curves['clips']):
                raise FormatError('AGR track count differs from original hierarchy '+root_name)
            textures=[];required=required_base_names(nif)
            if required:
                texture,resolution=resolver.asset(cells[2]+'.nft',required_textures=required)
                item['texture_resolution']=resolution;textures.append(texture)
                missing=required-resolver.texture_names(texture)
                if missing:
                    extras,resolutions=resolver.supplemental(missing)
                    textures+=extras;item['supplemental_texture_resolution']=resolutions
            folder=output_dir/model['model_key'];folder.mkdir()
            skin_path=folder/(model['name']+'_skin.glb');skin_report=folder/'skin-report.json'
            skin=export_skin(path,[],skin_path,skin_report,base_texture_paths=textures,
                             asset_name=model['name'],skeleton_root_name=root_name,
                             allow_transformed_skin_shape=True,preserve_global_skin_transform=True,preserve_vertex_colors=True)
            clips=[];stats=[]
            for original in curves['clips']:
                if original['duplicate_instants']:raise FormatError('Equal-time source keys require explicit engine curve policy')
                clip={k:v for k,v in original.items() if k!='tracks'}
                metrics=curve_metrics(original)
                clip.update(channels=[dict(bone=i,name=b['name'],rotation=t['rotation'],translation=t['translation'])
                                     for i,(b,t) in enumerate(zip(bones,original['tracks']))],
                    root_motion_policy='Original local rotations and absolute translations retained on original object hierarchy; no humanoid root extraction',
                    root_motion=dict(name=bones[0]['name'],displacement=metrics['root_displacement_metres'],
                        speed_m_per_second=metrics['root_endpoint_drift_metres']/original['duration']))
                clips.append(clip)
                stats.append(dict(name=original['name'],glb_name=original['name'].replace('\\','_'),duration=original['duration'],**metrics))
            payload=folder/'animations.json'
            payload.write_text(json.dumps(dict(root_motion_policy='Original object local animation, no motion extraction or synthesized loop',clips=clips),separators=(',',':'))+'\n',encoding='utf-8')
            target=folder/(model['model_key']+'.glb')
            merge(skin_path,payload,target,folder/'merge-report.json',in_place=False,omit_translation_bones=())
            ambient_group=group_name.casefold() in ('carni01','tgkflag','portapoo')
            ambient=[c for c in stats if c['root_endpoint_drift_metres']<.5 and c['max_endpoint_rotation_degrees']<8 and
                     (c['max_rotation_change_degrees']>1 or c['max_translation_change_metres']>.005)] if ambient_group else []
            item.update(status='exported',output=str(target),output_sha256=sha256(target),
                source_nif=dict(path=str(path),sha256=sha256(path)),source_group=group,
                skin_report=dict(path=str(skin_report),sha256=sha256(skin_report)),
                bones=len(bones),bone_names=[b['name'] for b in bones],skeleton_root_name=root_name,
                shapes=skin['shape_count'],vertices=skin['vertices'],triangles=skin['triangles'],textures=skin['textures'],
                original_bind_matrices_preserved=True,clips=stats,
                source_vertex_rgb_preserved=True,
                shared_dummy_group=group_name.casefold()=='fxtestg',
                ambient_loop_candidates=[c['name'] for c in ambient],
                context_required=not ambient_group,
                original_bounds_gltf=[skin['raw_bounds_min_gltf'],skin['raw_bounds_max_gltf']],
                preserved_global_skin_transform=skin.get('preserved_geometry_skin_transform_wrapper'),
                excluded_unskinned_helpers=skin['excluded_helpers'])
        except (ValueError,OSError,KeyError,IndexError,struct.error) as exc:
            item.update(status='rejected',error=str(exc))
        report['models'].append(item)
        report['summary']=dict(models=len(report['models']),statuses=dict(Counter(m['status'] for m in report['models'])),
            placements_by_status=dict((s,sum(m['placement_count'] for m in report['models'] if m['status']==s)) for s in set(m['status'] for m in report['models'])))
        report_path.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(dict(model=model['name'],status=item['status'],error=item.get('error'))),flush=True)
    report['status']='export_complete_pending_visual_validation' if all(m['status']=='exported' for m in report['models']) and len(selected)==len(pending['models']) else 'incomplete'
    report_path.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pending',default='evidence/world-skinned-objects-pending.json')
    parser.add_argument('--library',default='evidence/animation-library-v04.json')
    parser.add_argument('--world',default='local/extracted/Stream/World')
    parser.add_argument('--output',default='local/converted/skinned-props-v04')
    parser.add_argument('--report',default='evidence/skinned-props-export-v04.json')
    parser.add_argument('--limit',type=int)
    args=parser.parse_args()
    result=export_all(args.pending,args.library,args.world,args.output,args.report,args.limit)
    print(json.dumps(result['summary']))

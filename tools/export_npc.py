"""Export own original NPC skins and CAT-linked locomotion, without retargeting.

Uses default.ide identities, exact winter texture dictionaries, original NIF
binds, and animation-hash references from recovered original CAT trees.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path

from bully_agr import FormatError, interpolate_linear, sha256
from export_character import CharacterNif, export as export_skin
from export_nif import load_textures, texture_basename
from merge_player_animations import merge
from catalog_npc_locomotion import PED_PROFILES, lower_priority, selections, verify_ped_selection


SELECTION = {ped_id: selections(ped_id) for ped_id in PED_PROFILES}
WINTER_TEXTURE_ALIASES = {
    ped_id: {f'bully{number}_md_{slot}.tga': f'bully{number}_md_w_{slot}.tga' for slot in ('d','s','n')}
    for ped_id, number in ((85,'03'),(99,'02'))
}


def texture_variant(requested, available, ped_id):
    if requested in available:
        return requested
    # These exact NIF/NFT pairs were read from the named ped's declared _W
    # dictionary; unlike nerds/students, bully costumes put W after MD.
    candidate=WINTER_TEXTURE_ALIASES.get(ped_id,{}).get(requested,requested.replace('_md_','_w_md_'))
    if candidate not in available:
        raise FormatError('Exact original winter texture slot mapping absent')
    return candidate


def channels(tracks, bones, reference_root, moving):
    if len(tracks)!=36 or len(bones)!=36 or bones[1]['name']!='Root' or bones[35]['name']!='ARROW':
        raise FormatError('Exact original pedestrian hierarchy is required')
    root,arrow=tracks[1]['translation'],tracks[35]['translation']
    if not root:
        raise FormatError('Selected original NPC clip lacks Root translation')
    if arrow:
        travel=arrow
        policy='Original ARROW translation extracted; Root relative to own idle Root reference'
        source_bone=35
    elif moving:
        travel=[[t,[p[0],p[1],0.]] for t,p in root]
        policy='No ARROW source: extract original Root planar curve verbatim; preserve vertical Root bob relative to own idle reference'
        source_bone=1
    else:
        travel=[]
        policy='Stationary clip: retain all original Root deltas relative to own idle Root reference'
        source_bone=None
    times=sorted({t for t,_ in root+travel})
    initial=travel[0][1] if travel else [0.,0.,0.]
    output=[]
    for time in times:
        pose=interpolate_linear(root,time)
        displacement=interpolate_linear(travel,time) if travel else [0.,0.,0.]
        output.append([time,[bones[1]['translation'][i]+pose[i]-reference_root[i]-displacement[i]+initial[i] for i in range(3)]])
    result=[dict(bone=i,name=bone['name'],rotation=track['rotation'],
                 translation=output if i==1 else track['translation'],rest_translation=bone['translation'])
            for i,(bone,track) in enumerate(zip(bones,tracks))]
    displacement=[b-a for a,b in zip(travel[0][1],travel[-1][1])] if travel else [0.,0.,0.]
    return result,dict(bone=source_bone,name=bones[source_bone]['name'] if source_bone is not None else None,
        source_translation_present=bool(travel),translation=travel,displacement=displacement,policy=policy)


def export(ped_id,population_path,library_path,cat_path,output_dir,report_path):
    population_path,library_path,cat_path,output_dir,report_path=map(Path,(population_path,library_path,cat_path,output_dir,report_path))
    if output_dir.exists() or report_path.exists():raise FileExistsError('NPC outputs must be new')
    population=json.loads(population_path.read_text());library=json.loads(library_path.read_text())
    ped=next(p for p in population['ped_definitions'] if p['id']==ped_id)
    cat_proof=verify_ped_selection(ped,cat_path)
    if len(ped['model_candidates'])!=1 or len(ped['texture_candidates'])!=1:
        raise FormatError('Ambiguous original NPC model or texture dictionary')
    model=Path(ped['model_candidates'][0]['path']);texture=Path(ped['texture_candidates'][0]['path'])
    if sha256(model)!=ped['model_candidates'][0]['sha256'] or sha256(texture)!=ped['texture_candidates'][0]['sha256']:
        raise FormatError('Original NPC source changed')
    source_glb=output_dir/(ped['model']+'_skin.glb');skin_report=output_dir/'skin-report.json'
    nif=CharacterNif(model)
    texture_keys=load_textures([texture])
    variant_map={}
    for i,t in enumerate(nif.types):
        if t!='NiSourceTexture':continue
        requested=texture_basename(nif.block(i)['filename'])
        if requested not in texture_keys:
            variant_map[requested]=texture_variant(requested,texture_keys,ped_id)
    output_dir.mkdir(parents=True)
    skin=export_skin(model,[],source_glb,skin_report,base_texture_paths=[texture],base_texture_name_map=variant_map,asset_name=ped['model'])
    bones=skin['skeleton_bones']
    original_order=population['ped_definitions'][0]['model_candidates'][0]['skeleton']['bone_names']
    if [b['name'] for b in bones]!=original_order:
        raise FormatError('NPC original bone order mismatch')
    decoded=[]
    group_cache={}
    for group_name,clip_name,role in SELECTION[ped_id]:
        matches=[g for g in library['groups'] if g['dictionary']==9 and g['name']==group_name]
        if len(matches)!=1:raise FormatError('Ambiguous original group')
        group=matches[0];path=Path(group['path'])
        if group_name not in group_cache:
            if sha256(path)!=group['sha256']:raise FormatError('Original animation library changed')
            group_data=json.loads(gzip.decompress(path.read_bytes()))
            if group_data['dictionary']['source']['sha256']!=cat_proof['hxd']['sha256']:
                raise FormatError('Animation library uses a different MAINPED dictionary')
            wanted={name for name_group,name,_ in SELECTION[ped_id] if name_group==group_name}
            # Keep only this ped's five clips; don't repeatedly decode the large
            # C_Player bank for walk/run/sprint or retain unrelated action curves.
            group_cache[group_name]={key:group_data[key] for key in ('group','dictionary')}
            group_cache[group_name]['clips']=[clip for clip in group_data['clips'] if clip['name'] in wanted]
            del group_data
        group_data=group_cache[group_name]
        matching=[c for c in group_data['clips'] if c['name']==clip_name]
        if len(matching)!=1:raise FormatError('Missing/ambiguous original NPC animation')
        proof=next(item for item in cat_proof['clips'] if item['role']==role)
        if matching[0]['hxd_record']['name_hash']!=int(proof['animation_hash'],16) or matching[0]['duration']!=proof['duration']:
            raise FormatError('Decoded animation differs from the exact CAT/HXD selection')
        decoded.append((matching[0],role,dict(path=str(path),sha256=sha256(path),
            agr=group_data['group']['source'],dictionary=group_data['dictionary']['source'],group=group_name)))
    idle=decoded[0][0]
    reference=idle['tracks'][1]['translation'][0][1]
    clips=[]
    for source,role,provenance in decoded:
        if source['duplicate_instants']:raise FormatError('Playback cannot accept duplicate source times')
        # Ambient takes were authored at different absolute pelvis heights.
        # Their own initial Root removes the take's authoring origin, while
        # locomotion retains the idle reference to preserve gait crouching.
        clip_reference=source['tracks'][1]['translation'][0][1] if role=='ambient_idle' else reference
        normalized,motion=channels(source['tracks'],bones,clip_reference,role in ('walk','run','sprint'))
        motion['speed_m_per_second']=math.sqrt(sum(v*v for v in motion['displacement']))/source['duration']
        clip={k:v for k,v in source.items() if k!='tracks'}
        clip.update(channels=normalized,root_motion=motion,root_motion_policy=motion['policy'],
                    root_reference=dict(clip=source['name'] if role=='ambient_idle' else idle['name'],translation=clip_reference),
                    role=role,provenance=provenance,source_channels=source['tracks'])
        clips.append(clip)
    payload=output_dir/'animations.json'
    payload.write_text(json.dumps(dict(schema='rea-bully-own-skeleton-npc-animation-v1',
        root_motion_policy='Own NIF skeleton/bind; source idle Root reference; source locomotion trajectory extracted separately',
        ped=ped,clips=clips),separators=(',',':'))+'\n',encoding='utf-8')
    target=output_dir/(f'{ped_id:03d}_'+ped['model']+'.glb')
    merge(source_glb,payload,target,output_dir/'merge-report.json')
    report=dict(status='exported_pending_independent_pose_validation',ped_id=ped_id,model=ped['model'],
        source_metadata=dict(path=str(population_path),sha256=sha256(population_path)),
        original_cat_evidence=dict(path=str(cat_path),sha256=sha256(cat_path)),
        exact_cat_selection=cat_proof,
        texture_dictionary=ped['texture_dictionary'],texture=dict(path=str(texture),sha256=sha256(texture)),
        explicit_winter_texture_slot_name_map=variant_map,
        original_skin=dict(path=str(skin_report),sha256=sha256(skin_report)),
        output=dict(path=str(target),sha256=sha256(target)),vertices=skin['vertices'],triangles=skin['triangles'],
        textures=skin['textures'],bones=len(bones),own_skeleton_and_bind=True,retargeted_from_jimmy=False,
        original_bounds_gltf=[skin['raw_bounds_min_gltf'],skin['raw_bounds_max_gltf']],
        clips=[dict(name=c['name'],role=c['role'],duration=c['duration'],root_motion=c['root_motion']) for c in clips],
        limitations=['Original CAT references establish clip choice; full original AI and action conditions are not executed.',
                     'Winter _W texture dictionary follows installed default.ide. No costume is invented.',
                     'Original own bind transforms are retained; nonzero source bind residuals are reported in the skin report.',
                     'Average extracted source speed does not by itself reproduce original foot IK or collision-aware navigation.'])
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


if __name__=='__main__':
    priority=lower_priority()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ped-id',type=int,choices=sorted(SELECTION),required=True)
    parser.add_argument('--population',default='evidence/world-population.json')
    parser.add_argument('--library',default='evidence/animation-library-v04.json')
    parser.add_argument('--cat',help='Exact v05 CAT evidence; historical v04 evidence remains valid for Bucky/Seth')
    parser.add_argument('--output',required=True)
    parser.add_argument('--report',required=True)
    args=parser.parse_args()
    cat=args.cat or ('evidence/npc-locomotion-cat-v04.json' if args.ped_id in (8,50) else 'evidence/npc-locomotion-cat-v05.json')
    result=export(args.ped_id,args.population,args.library,cat,args.output,args.report)
    print(json.dumps({**{k:result[k] for k in ('ped_id','model','output','vertices','triangles','textures','bones')},
                      'process_priority':priority}))

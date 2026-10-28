"""Describe pending original NPC exports and regional quotas without spawning actors.

This produces a candidate manifest, deliberately distinct from the verified v04
Unreal import manifest. Structural checks do not validate deformed poses.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path

from bully_agr import FormatError, sha256
from catalog_npc_locomotion import NEW_PED_IDS, lower_priority, verify_ped_selection
from validate_player_actions import glb, values

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('cornelius','donald','ivan','trevor','trent','davis')
PERIODS = {'POPULATION_DAY':'DAY_OUTOFCLASS', 'POPULATION_CLASS':'DAY_INCLASS',
           'POPULATION_NIGHT':'NIGHT_EVENING', 'POPULATION_CURFEW':'NIGHT_CURFEW'}


def source(path):
    path=Path(path)
    return {'path':str(path),'sha256':sha256(path)}


def verify_record(record):
    path=ROOT/record['path']
    if sha256(path)!=record['sha256']:
        raise FormatError('Input provenance changed: '+str(path))
    return path


def inspect_candidate(report_path, ped):
    report_path=Path(report_path)
    report=json.loads(report_path.read_text())
    if report['status']!='exported_pending_independent_pose_validation' or report['ped_id']!=ped['id'] or report['model']!=ped['model']:
        raise FormatError('Unexpected candidate export status/identity')
    output=verify_record(report['output'])
    cat_path=verify_record(report['original_cat_evidence'])
    proof=verify_ped_selection(ped,cat_path)
    if proof!=report['exact_cat_selection']:
        raise FormatError('Candidate export does not retain its exact original CAT evidence')
    skin_report=json.loads(verify_record(report['original_skin']).read_text())
    if verify_record(skin_report['base'])!=Path(ped['model_candidates'][0]['path']):
        raise FormatError('Candidate skin belongs to a different original model')
    doc,blob=glb(output)
    skin_doc,skin_blob=glb(ROOT/skin_report['output'])
    if len(doc.get('skins',[]))!=1 or len(doc['skins'][0]['joints'])!=36 or len(doc['meshes'])!=1:
        raise FormatError('Candidate own-skin shape changed')
    for field in ('nodes','meshes','skins','materials','images','textures'):
        if doc.get(field)!=skin_doc.get(field):
            raise FormatError('Animation merge changed original own skin: '+field)
    if blob[:len(skin_blob)]!=skin_blob or any(doc[field][:len(skin_doc[field])]!=skin_doc[field] for field in ('accessors','bufferViews')):
        raise FormatError('Animation merge changed own skin binary data')
    if len(doc.get('animations',[]))!=len(report['clips']):
        raise FormatError('Candidate animation count changed')
    clips=[]
    for animation,clip in zip(doc['animations'],report['clips']):
        if animation['name']!=clip['name'].replace('\\','_') or animation['extras']['source_duration_seconds']!=clip['duration']:
            raise FormatError('Candidate animation identity/duration changed')
        for sampler in animation['samplers']:
            times=values(doc,blob,sampler['input'])
            samples=values(doc,blob,sampler['output'])
            if not times or not all(math.isfinite(v) for v in (*times,*samples)) or not all(a<b for a,b in zip(times,times[1:])):
                raise FormatError('Candidate animation has nonfinite or unordered data')
            if min(times)<0 or max(times)>clip['duration']+1e-6:
                raise FormatError('Candidate animation is outside its original duration')
        clips.append({'original_name':clip['name'],'glb_animation_name':animation['name'],'role':clip['role'],
                      'duration':clip['duration'],'source_speed_cm_per_second':100*clip['root_motion']['speed_m_per_second']})
    return {'ped_id':ped['id'],'model':ped['model'],'ped_type':ped['ped_type'],'female':ped['female'],
        'status':'exported_pending_independent_pose_validation','ready_for_unreal':False,
        'source_glb':source(output),'export_report':source(report_path),
        'original_model':{key:ped['model_candidates'][0][key] for key in ('path','sha256')},
        'original_texture':{key:ped['texture_candidates'][0][key] for key in ('path','sha256')},
        'ide_line':ped['line'],'action_root':ped['action_root'],'action_file':ped['action_file'],
        'profile':proof['profile'],'bone_count':36,'skin_own_bind':True,'reuse_jimmy_skeleton':False,
        'structural_checks':{'source_hashes':True,'own_skin_nodes_and_binary_unchanged':True,
                             'five_original_clips':True,'finite_strictly_ordered_animation_data':True},
        'independent_pose_validation':{'status':'pending','idle_height_cm':None,'capsule_half_height_cm':None},
        'clips':clips}


def quota_plan(regions,pools):
    ids=[ped_id for group in pools.values() for ped_id in group]
    if len(ids)!=len(set(ids)):
        raise FormatError('Candidate pools must not duplicate a named character')
    result=[]
    for region in regions:
        if region['name'] not in ('SchoolArea','OutsideSchool1'):
            continue
        periods=[]
        for original,period in PERIODS.items():
            quotas=region['population'][original]
            categories=[]
            for category,ped_ids in pools.items():
                quota=quotas.get(category,0)
                categories.append({'ped_type':category,'original_quota':quota,'candidate_ped_ids':list(ped_ids),
                                   'max_distinct_candidates_within_quota':min(quota,len(ped_ids)),
                                   'unfilled_slots_with_this_pool':max(0,quota-len(ped_ids))})
            periods.append({'period':period,'original_field':original,'original_quotas':quotas,
                'categories':categories,'max_distinct_candidates_within_quota':sum(c['max_distinct_candidates_within_quota'] for c in categories),
                'actors_to_spawn':None})
        result.append({'name':region['name'],'source':region['source'],'line':region['line'],
                       'area_code':region['area_code'],'periods':periods})
    return result


def build():
    population_path=ROOT/'evidence/world-population.json'
    population=json.loads(population_path.read_text())
    verify_record(population['ped_definitions_source'])
    by_id={ped['id']:ped for ped in population['ped_definitions']}
    candidates=[]
    for ped_id,name in zip(NEW_PED_IDS,NAMES):
        ped=by_id[ped_id]
        if len(ped['model_candidates'])!=1 or len(ped['texture_candidates'])!=1:
            raise FormatError('Candidate does not have a unique original source')
        verify_record(ped['model_candidates'][0]);verify_record(ped['texture_candidates'][0])
        candidates.append(inspect_candidate(ROOT/f'evidence/npc-{name}-export-v05.json',ped))
    old_path=ROOT/'evidence/npc-import-manifest-v04.json'
    old=json.loads(old_path.read_text())
    if [ped['ped_id'] for ped in old['npcs']]!=[8,50]:
        raise FormatError('Verified v04 manifest identity changed')
    for ped in old['npcs']:
        verify_record(ped['source_glb'])
    manifest={'schema':'rea-bully-npc-candidate-manifest-v1','status':'exported_pending_independent_pose_validation',
        'ready_for_unreal':False,'supersedes_verified_manifest':False,'verified_v04_manifest_preserved':source(old_path),
        'population_catalog':source(population_path),'npcs':candidates,
        'limitations':['No Blender, Unreal, rendered or independent deformed-pose validation has run on these six exports.',
                       'Capsule and idle-pose bounds must be measured before promoting candidates to an Unreal import manifest.']}
    region_path=ROOT/'evidence/ambient-population-regions.json'
    regions=json.loads(region_path.read_text())['regions']
    school_path=ROOT/'evidence/school-ambient-source.json'
    school=json.loads(school_path.read_text())
    pools={'NERD':[8,9,11],'STUDENT':[72,73],'BULLY':[85,99]}
    for category,ids in pools.items():
        if any(by_id[ped_id]['ped_type']!=category for ped_id in ids):
            raise FormatError('Candidate is assigned to a different original ped category')
    for region in regions:
        if region['name'] in ('SchoolArea','OutsideSchool1'):
            verify_record(region['source'])
    pool_ids={ped_id for values in pools.values() for ped_id in values}
    poi=[]
    for point in school['poi_points']:
        eligible=sorted(pool_ids.intersection(point['candidate_ped_ids_same_hierarchy_only']))
        if eligible:
            verify_record(point['source'])
            poi.append({'id':point['id'],'group_name':point['group_name'],'source':point['source'],'line':point['line'],
                        'ped_type':point['ped_type'],'gender':point['gender'],'maximum_raw':point['maximum_raw'],
                        'candidate_ped_ids':eligible,'assigned_ped_id':None})
    plan={'schema':'rea-bully-ambient-cast-plan-v1','status':'source_quotas_and_pending_candidate_pool_only',
        'runtime_ready':False,'original_selection_algorithm_recovered':False,'spawn_assignments':[],
        'sources':{'population':source(population_path),'regions':source(region_path),'school_ambient':source(school_path)},
        'candidate_pools':pools,'pending_pose_validation_ids':list(NEW_PED_IDS),'already_verified_ped_ids':[8,50],
        'regional_quotas':quota_plan(regions,pools),'poi_candidate_links_not_assignments':poi,
        'separate_patrol':{'ped_id':50,'source_path_names':[p['name'] for p in school['patrol_paths']],
                           'counted_in_regional_population_quota':False,'new_assignment':None},
        'selection_policy':'No selection or cloning performed. Preserve named character identity; use quotas as source data only.',
        'limitations':['Regional priority, overlap and exact time boundaries remain unimplemented.',
                       'IDE category membership is not proof of the original randomized spawn selection.',
                       'Available candidates cannot fill every original quota without additional distinct characters.',
                       'POI metadata matches gender/type candidates; it does not prove original AI scheduling or a collision-free spawn.']}
    return manifest,plan


if __name__=='__main__':
    priority=lower_priority()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,default=ROOT/'evidence/npc-candidate-manifest-v05.json')
    parser.add_argument('--plan',type=Path,default=ROOT/'evidence/ambient-cast-plan-v05.json')
    args=parser.parse_args()
    if args.manifest.exists() or args.plan.exists():
        parser.error('New candidate manifest and plan paths required')
    manifest,plan=build()
    for path,record in ((args.manifest,manifest),(args.plan,plan)):
        record['process_priority']=priority
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('x',encoding='utf-8') as stream:
            json.dump(record,stream,indent=2);stream.write('\n')
    print(json.dumps({'candidates':len(manifest['npcs']),'clips':sum(len(ped['clips']) for ped in manifest['npcs']),
                      'manifest':str(args.manifest),'plan':str(args.plan),'process_priority':priority}))

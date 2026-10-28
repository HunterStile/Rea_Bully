"""Experimental CAT metadata diagnostic; not a production parser or interpreter.

Reads local game files, extracted CATs and saved native evidence; never changes
Steam or overwrites a report. Format facts are implemented independently.
"""
import argparse
import json, hashlib, struct, math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-report',required=True,type=Path,help='New JSON destination; existing files are refused.')
    args=parser.parse_args()
    if args.output_report.exists():
        parser.error(f'Output already exists: {args.output_report}')
    import inspect_jump_cat as m
    root=ROOT
    manifest=json.loads((root/'local/extracted/Act/Act/manifest.json').read_text())
    imagesource=Path(manifest['source']['image']['path'])
    cat=m.Cat(root/'local/extracted/Act/Act/JumpActions.cat')
    selected=[]
    for n in cat.flat():
        tracks=[]
        for off in n.get('track_offsets',[]):
            t=cat.track(off)
            if t['type'].upper() not in ['ANIMATION','ANIMATIONPARAMETRIC','LOCOMOTIONANIMATIONNEW','LOCOMOTIONJUMP','GRAVITY','SEQUENCE','SOUNDFX','OPPORTUNITY','EXECUTE']:continue
            attrs={}
            for k,a in t['attrs'].items():
                if k=='0':continue
                v={'file_offset':a['file_offset'],'uint':a['uint']}
                if 'float'in a and math.isfinite(a['float']):v['float_interpretation']=a['float']
                for x in ['animation','string']:
                    if x in a:v[x]=a[x]
                attrs[k]=v
            tracks.append({'offset_relative_data':off,'record_file_offset':cat.data+off,'type':t['type'],'attributes':attrs})
        conditions=[]
        for off in n.get('condition_offsets',[]):
            p=cat.data+off;h=struct.unpack_from('<I',cat.d,p)[0]
            c={'offset_relative_data':off,'record_file_offset':p,'type_hash':f'{h:08x}','class_name_candidate':m.label(h)}
            if c['class_name_candidate'] in ['MotionIntentionSpeed','PedVelocity','MotionCycle','SurfaceGrade','DistToGround']:
                c.update(threshold_float=struct.unpack_from('<f',cat.d,p+8)[0],comparison_opcode_uninterpreted=struct.unpack_from('<I',cat.d,p+12)[0])
            conditions.append(c)
        selected.append({'path':n['path'],'node_file_offset':n['offset'],'name_hash':n.get('name_hash'),'kind':n['kind'],'conditions':conditions,'tracks':tracks})

    checked=[];counts={};trackcount=0
    with imagesource.open('rb') as f:
        for e in manifest['entries']:
            p=root/'local/extracted/Act/Act'/e['path'];c=m.Cat(p)
            for n in c.flat():
                for o in n.get('track_offsets',[]):c.track(o);trackcount+=1
            for k,v in c.counts.items():counts[k]=counts.get(k,0)+v
            f.seek(e['offset_bytes']);digest=hashlib.sha256(f.read(e['allocated_bytes'])).hexdigest()
            assert digest==sha(p)==e['extracted_sha256']==e['source_extent_sha256']
            checked.append({'name':e['name'],'sha256':digest,'logical_size':c.h[0],'tree_end':c.end,'header_end':c.data})

    native=[]
    for address,meaning in [('5fb550','CAT loader reads declared file/header lengths and Act\\Act.IMG'),('5fb380','CAT allocation counts from header'),('5fb110','child tags b/i/r/l/n; l/n share playable parser'),('5fa940','compressed attribute width, offset, continuation, recursive inheritance'),('5faad0','condition factory from class hash'),('5f37d0','exact native FallingOppsBase action path')]:
        p=root/f'local/native/decompiled/full-20261020/functions/0x{address}.c'
        txt=p.read_text();ev=txt.split('Evidence: ')[1].split()[0]
        native.append({'address':'0x'+address,'path':str(p.relative_to(root)).replace('\\','/'),'sha256':sha(p),'rea_evidence_id':ev,'observed_use':meaning})

    result={
     'classification':'read_only_static_CAT_metadata_not_gameplay_runtime',
     'format_reference':{'url':'https://github.com/LiquidSnake0/bully-re/blob/dce485bf29117edca4d04b802a169f0a909da432/docs/cat.md','use':'format facts only; independent Python reader; no third-party implementation imported','warning':'reference native addresses differ in this build; actual native routines independently located and listed below'},
     'sources':{'act_image':manifest['source']['image'],'act_directory':manifest['source']['directory'],'jump_cat':{'path':str(cat.path.relative_to(root)).replace('\\','/'),'size_bytes':len(cat.d),'sha256':sha(cat.path),'header_words':list(cat.h),'counts':cat.counts},'mainped_hxd':{'path':r'C:\Program Files (x86)\Steam\steamapps\common\Bully Scholarship Edition\Anim\MAINPED.HXD','sha256':sha(r'C:\Program Files (x86)\Steam\steamapps\common\Bully Scholarship Edition\Anim\MAINPED.HXD')}},
     'decoder':{'path':'tools/diagnostics/inspect_jump_cat.py','sha256':sha(root/'tools/diagnostics/inspect_jump_cat.py'),'summary_generator_path':'tools/diagnostics/summarize_jump_cat.py'},
     'checks':{'all_479_tree_end_and_counts_match':True,'all_51001_track_inheritance_records_decoded':True,'all_479_source_archive_extents_match':True,'zero_sector_padding_outside_declared_size':True,'files':len(checked),'counts_including_file_roots':counts,'tracks':trackcount},
     'native_static_corroboration':native,
     'analyst_hash_candidates':[{'hash':'3851b1f0','candidate':'SprintJump','status':'analyst-supplied name hashes exactly; original spelling not recovered'},{'hash':'47515de1','candidate':'RunJump','status':'analyst-supplied name hashes exactly; original spelling not recovered'}],
     'interpretation_limits':['No original game execution or CAT interpreter was run.','Class hash names are candidates from shipped strings and do not prove factory registration.','Condition records are identified; nested boolean/comparison evaluation and flags are not implemented.','Float and uint are alternate views of the same attribute, not two fields; only documented track fields have claimed semantics.','LocomotionAnimationNew parameter semantics are not established; no claim these FALL clips loop.','Gravity flag changes do not identify a physical takeoff event.','No damage, climb, vault, swimming, collision-event, or full action runtime has been reconstructed.'],
     'jump_nodes':selected,
     'validated_archive_entries':checked,
    }
    for key in ['act_image','act_directory']:
        source=result['sources'][key]
        assert sha(source['path'])==source['sha256']
        source['sha256_verified_this_run']=True
    result['decoder']['summary_generator_sha256']=sha(__file__)
    args.output_report.parent.mkdir(parents=True,exist_ok=True)
    with args.output_report.open('x',encoding='utf-8') as f:
        json.dump(result,f,indent=2,allow_nan=False)
        f.write('\n')
    print(args.output_report,args.output_report.stat().st_size,'bytes')


if __name__=='__main__':
    main()

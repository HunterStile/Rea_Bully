"""Verify original player animation extraction and fail-closed controls."""
from pathlib import Path
import json
import math
import struct
from bully_agr import read_hxd, decode_animation, sha256, FormatError


def run(payload_path='local/converted/player/player-original-animations.json', report='evidence/player-original-animation-validation.json'):
    payload_path=Path(payload_path)
    p=json.loads(payload_path.read_text())
    agr_path=Path(p['sources'][0]['path'])
    hxd_path=Path(p['sources'][1]['path'])
    source_checks=[{'path':s['path'],'sha256':sha256(s['path']), 'matches':sha256(s['path'])==s['sha256']} for s in p['sources']]
    assert all(s['matches'] for s in source_checks)
    d=agr_path.read_bytes();h=read_hxd(hxd_path)
    records=[a for a in h['animations'] if a['group']==0]
    offset=0; decoded=0; failures=[]
    for a in records:
        size=a['size']-4
        try:
            decode_animation(d[offset:offset+size],a['duration']);decoded+=1
        except FormatError as e:
            failures.append({'name':a['name'],'offset':offset,'size':size,'error':str(e)})
        offset+=size
    assert offset==len(d)
    clips=[]
    for c in p['clips']:
        times_count=0;max_norm=0;max_endpoint_angle=0
        for ch in c['channels']:
            for kind in ('rotation','translation'):
                track=ch[kind]
                assert all(math.isfinite(v) for t,values in track for v in [t,*values])
                assert all(0<=t<=c['duration'] for t,_ in track)
                assert all(b[0]>a[0] for a,b in zip(track,track[1:]))
                times_count+=len(track)
            for _,q in ch['rotation']:
                error=abs(math.sqrt(sum(v*v for v in q))-1)
                max_norm=max(max_norm,error)
                assert error<1e-12
            if len(ch['rotation'])>1:
                qa,qb=ch['rotation'][0][1],ch['rotation'][-1][1]
                angle=2*math.acos(min(1,abs(sum(a*b for a,b in zip(qa,qb)))))
                max_endpoint_angle=max(max_endpoint_angle,math.degrees(angle))
        root=c['channels'][1]['translation']
        root_error=math.sqrt(sum((a-b)**2 for a,b in zip(root[0][1],root[-1][1])))
        clips.append({'name':c['name'],'duration_seconds':c['duration'],'encoding':c['encoding'],
                      'source_keys':c['key_count'],'bone_tracks':len(c['channels']),
                      'exported_samples':times_count,'max_normalized_quaternion_error':max_norm,
                      'max_source_quantization_norm_error':c['max_source_quaternion_norm_error'],
                      'max_endpoint_rotation_degrees':max_endpoint_angle,
                      'in_place_root_endpoint_distance_metres':root_error,
                      'root_motion':c['root_motion']})
    # Controls corrupt real original spans in memory only; originals stay read-only.
    sample=p['clips'][0]
    raw=d[sample['agr_offset']:sample['agr_offset']+sample['agr_size']]
    controls=[]
    corrupted=bytearray(raw);struct.pack_into('<I',corrupted,8,65536)
    variants=[('excessive_key_count',bytes(corrupted)),('truncated_key_payload',raw[:23])]
    corrupted=bytearray(raw);word=struct.unpack_from('<I',corrupted,20)[0]
    struct.pack_into('<I',corrupted,20,word|1|(1<<11))
    variants.append(('forward_or_self_previous_link',bytes(corrupted)))
    for name,value in variants:
        try:decode_animation(value,sample['duration'])
        except FormatError as error:controls.append({'name':name,'rejected':True,'error':str(error)})
        else:raise AssertionError(f'Corruption control accepted: {name}')
    result={'status':'selected_original_clips_validated','payload':str(payload_path),'payload_sha256':sha256(payload_path),
            'source_checks':source_checks,'hxd_full_file_consumed':True,'hxd_records':len(h['animations']),
            'hxd_groups':len(h['groups']),'c_player_file_bytes_consumed':offset,
            'c_player_group_records':len(records),'c_player_all_curves_decoded':decoded,
            'unrequested_curve_failures':failures,'selected_clips':clips,'negative_controls':controls,
            'limitations':['Selected 4 original player clips are valid; 2 other clips fail closed and are not exported.',
                           'Numeric checks do not prove visual playback matches original game.',
                           'Endpoint differences are measured, never silently corrected into synthetic motion.']}
    report=Path(report)
    if report.exists():raise FileExistsError(report)
    report.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'status':result['status'],'selected_clips':len(clips),'decoded_c_player':decoded,'unrequested_failures':len(failures),'report':str(report)}))


if __name__=='__main__':run()

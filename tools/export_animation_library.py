"""Archive all verified original curves, plus all original player tracks.

Each compressed group retains its own HXD timing, events, original coordinates,
and exact AGR offsets. This is a data recovery library, not gameplay coverage.
"""
from __future__ import annotations
import argparse
import copy
import gzip
import hashlib
import json
import math
from pathlib import Path

from bully_agr import FormatError, decode_animation, interpolate_linear, sha256


PLAYER_POLICY = ('For locomotion IDLE/PLAYERWALK/PLAYERRUN/RUNFAST use original per-clip Root origin; '
                 'all other actions use original IDLE Root origin, preserving crouches and action offsets. '
                 'Root = rest + source Root - reference Root - (ARROW - initial ARROW). '
                 'ARROW original track retained as metadata and omitted from in-place GLB translation. '
                 'No missing source key is synthesized; absent Root translation keeps bind translation.')


def player_channels(tracks, skeleton, reference_root, duration):
    if len(tracks) != 36 or len(skeleton) != 36 or skeleton[1]['name'] != 'Root' or skeleton[35]['name'] != 'ARROW':
        raise FormatError('Exact independently verified 36-bone player order required')
    root, arrow = tracks[1]['translation'], tracks[35]['translation']
    extracted = []
    if root:
        initial_arrow = arrow[0][1] if arrow else [0.,0.,0.]
        times = sorted({k[0] for k in root+arrow})
        for time in times:
            rp = interpolate_linear(root,time)
            ap = interpolate_linear(arrow,time) if arrow else [0.,0.,0.]
            extracted.append([time,[skeleton[1]['translation'][i]+rp[i]-reference_root[i]-ap[i]+initial_arrow[i]
                                     for i in range(3)]])
    return [dict(bone=i, name=rest['name'], rotation=track['rotation'],
                 translation=extracted if i==1 else track['translation'],
                 rest_translation=rest['translation'])
            for i,(rest,track) in enumerate(zip(skeleton,tracks))]


def export(catalog_path, skeleton_path, output_dir, report_path):
    catalog_path,skeleton_path,output_dir,report_path = map(Path,(catalog_path,skeleton_path,output_dir,report_path))
    if output_dir.exists() or report_path.exists():
        raise FileExistsError('Library output and report must be new')
    catalog = json.loads(catalog_path.read_text())
    if catalog['status'] != 'all_addressed_curves_decoded':
        raise FormatError('Full addressed curve coverage must be validated first')
    skeleton = json.loads(skeleton_path.read_text())['bones']
    mainped_index = next(d['index'] for d in catalog['dictionaries'] if d['source']['logical_path'].upper()=='ANIM/MAINPED.HXD')
    player_group = next(g for g in catalog['groups'] if g['dictionary']==mainped_index and g['group_name'].upper()=='C_PLAYER')
    player_catalog = [c for c in catalog['clips'] if c['dictionary']==mainped_index and c['group']==player_group['group']]
    player_bytes = Path(player_group['source']['path']).read_bytes()
    idle = next(c for c in player_catalog if c['hxd_record']['name']=='C_PLAYER\\IDLE')
    idle_curve = decode_animation(player_bytes[idle['agr_offset']:idle['agr_offset']+idle['agr_size']],idle['duration'])
    reference_root = idle_curve['tracks'][1]['translation'][0][1]
    output_dir.mkdir(parents=True)
    index, player_clips = [], []
    locomotion = {'C_PLAYER\\'+n for n in ('IDLE','PLAYERWALK','PLAYERRUN','RUNFAST')}
    for group in catalog['groups']:
        if not group['records']:
            continue
        source = group['source']
        if not source or sha256(source['path']) != source['sha256']:
            raise FormatError('Catalog source missing/changed')
        dictionary = catalog['dictionaries'][group['dictionary']]
        data = Path(source['path']).read_bytes()
        clips = []
        for c in catalog['clips']:
            if c['dictionary'] != group['dictionary'] or c['group'] != group['group']:
                continue
            raw = data[c['agr_offset']:c['agr_offset']+c['agr_size']]
            if hashlib.sha256(raw).hexdigest() != c['agr_sha256']:
                raise FormatError('Catalog slice hash changed')
            curve = decode_animation(raw,c['duration'],strict_times=False)
            clip = dict(name=c['hxd_record']['name'], ordinal=c['ordinal'],
                hxd_record=c['hxd_record'], agr_offset=c['agr_offset'], agr_size=c['agr_size'],
                agr_sha256=c['agr_sha256'], **curve)
            clips.append(clip)
            if group is player_group or (group['dictionary']==mainped_index and group['group']==player_group['group']):
                if curve['duplicate_instants']:
                    raise FormatError('Player normalization requires strictly increasing source time')
                tracks = curve['tracks']
                root_reference = tracks[1]['translation'][0][1] if clip['name'] in locomotion else reference_root
                channels = player_channels(tracks,skeleton,root_reference,curve['duration'])
                arrow = tracks[35]['translation']
                displacement = [b-a for a,b in zip(arrow[0][1],arrow[-1][1])] if arrow else [0.,0.,0.]
                normalized = {k:v for k,v in clip.items() if k!='tracks'}
                normalized.update(channels=channels, source_channels=tracks,
                    root_motion_policy=PLAYER_POLICY,
                    root_reference=dict(clip=clip['name'] if clip['name'] in locomotion else 'C_PLAYER\\IDLE',
                                        translation=root_reference),
                    root_motion=dict(bone=35,name='ARROW',source_translation_present=bool(arrow),translation=arrow,
                        displacement=displacement,speed_m_per_second=math.sqrt(sum(v*v for v in displacement))/curve['duration']))
                player_clips.append(normalized)
        payload = dict(schema='rea-bully-original-group-curves-v1',
            coordinate_system='Original NIF axes in metres; rotation xyzw replaces local node rotation',
            dictionary=dictionary, group=group, clips=clips,
            skeleton_order=('Original pedestrian depth-first Dummy hierarchy, independently verified against Jimmy'
                            if all(len(c['tracks'])==36 for c in clips) and group['dictionary']==mainped_index
                            else 'Unmapped: requires original NIF Root subtree depth-first hierarchy verification'),
            duplicate_instant_policy='All source keys retained in stable order, including equal instants; no resampling or last-wins assumption')
        name=f'{group["dictionary"]:03d}-{group["group"]:03d}-{group["group_name"]}.json.gz'
        # HXD group names are logical basenames, never paths.
        if Path(name).name != name or any(v in name for v in ('/','\\',':')):
            raise FormatError('Unsafe group name')
        target=output_dir/name
        encoded=json.dumps(payload,separators=(',',':'),allow_nan=False).encode('utf-8')
        target.write_bytes(gzip.compress(encoded,compresslevel=6,mtime=0))
        index.append(dict(dictionary=group['dictionary'],group=group['group'],name=group['group_name'],
            path=str(target),sha256=sha256(target),clips=len(clips),bytes=target.stat().st_size,
            keys=sum(c['key_count'] for c in clips),
            duplicate_time_clips=[c['name'] for c in clips if c['duplicate_instants']]))
    player_path=output_dir/'player-all-original-v04.json'
    player_payload=dict(schema='rea-bully-original-animation-v3',
        coordinate_system='Original NIF axes in metres; rotation xyzw replaces local node rotation',
        sources=[dict(path=str(p),sha256=sha256(p)) for p in (catalog_path,skeleton_path)],
        root_motion_policy=PLAYER_POLICY, action_reference_root=reference_root,
        clips=player_clips,
        limitations=['All 439 player AGR records recovered, including combat and contextual actions; this is not 439 implemented gameplay states.',
                     'Action categorization, attachment events, collisions, context matching and state transitions remain runtime work.',
                     'No ending pose, loop key, mirrored motion or missing motion is synthesized.'])
    player_path.write_text(json.dumps(player_payload,separators=(',',':'),allow_nan=False)+'\n',encoding='utf-8')
    result=dict(schema='rea-bully-animation-library-v1',status='original_curves_exported',
        catalog=dict(path=str(catalog_path),sha256=sha256(catalog_path)),
        groups=index,group_count=len(index),clips=sum(g['clips'] for g in index),
        source_keys=sum(g['keys'] for g in index),bytes=sum(g['bytes'] for g in index),
        player=dict(path=str(player_path),sha256=sha256(player_path),clips=len(player_clips),
                    original_idle_reference=reference_root,root_motion_policy=PLAYER_POLICY),
        limitations=['Recovered animation data is distinct from engine integration and original world behavior.',
                     'Four source clips contain equal-time keys; preserved verbatim, not made silently glTF-compatible.'])
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',default='evidence/animation-coverage-v04-complete.json')
    parser.add_argument('--skeleton',default='evidence/player-skeleton-rest.json')
    parser.add_argument('--output',default='local/converted/animation-library-v04')
    parser.add_argument('--report',default='evidence/animation-library-v04.json')
    args=parser.parse_args()
    result=export(args.catalog,args.skeleton,args.output,args.report)
    print(json.dumps({k:v for k,v in result.items() if k!='groups'}))

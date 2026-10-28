"""Append original jump/fall AGR clips without changing the v0.2 locomotion export.

Root pose uses the IDLE reference, not each action's first pose: the original
landing crouch and jump preparation must survive motion extraction. ARROW is
kept verbatim as evidence; its displacement is removed from the visible root
because Unreal CharacterMovement owns the capsule's motion.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path

from bully_agr import FormatError, decode_animation, interpolate_linear, read_hxd, sha256

ACTION_NAMES = ('IDLEJUMP', 'JOGJUMP_L', 'JOGJUMP_R', 'FALL', 'FALL_FLAIL', 'FALL_LAND')


def action_channels(tracks, skeleton, reference_root, duration):
    if len(tracks) != 36 or len(skeleton) != 36 or skeleton[1]['name'] != 'Root' or skeleton[35]['name'] != 'ARROW':
        raise FormatError('Exact original 36-bone order required')
    root, arrow = tracks[1]['translation'], tracks[35]['translation']
    if not root:
        raise FormatError('Action has no source Root position')
    if not arrow:
        arrow = [[0., [0., 0., 0.]], [duration, [0., 0., 0.]]]
    times = sorted({k[0] for k in root+arrow})
    extracted = []
    for time in times:
        rp, ap = interpolate_linear(root, time), interpolate_linear(arrow, time)
        extracted.append([time, [skeleton[1]['translation'][i]+rp[i]-reference_root[i]-ap[i]+arrow[0][1][i]
                                for i in range(3)]])
    channels = []
    for bone, (rest, track) in enumerate(zip(skeleton, tracks)):
        translation = extracted if bone == 1 else track['translation']
        channels.append(dict(bone=bone, name=rest['name'], rotation=track['rotation'],
                             translation=translation, rest_translation=rest['translation']))
    return channels


def export(game, skeleton_path, base_path, output):
    game, skeleton_path, base_path, output = map(Path, (game, skeleton_path, base_path, output))
    if output.exists():
        raise FileExistsError(output)
    base = json.loads(base_path.read_text())
    for source in base['sources']:
        if sha256(source['path']) != source['sha256']:
            raise FormatError(f'v0.2 source hash changed: {source["path"]}')
    skeleton = json.loads(skeleton_path.read_text())['bones']
    hxd_path, agr_path = game/'Anim/MAINPED.HXD', game/'Anim/C_Player.agr'
    hxd, data = read_hxd(hxd_path), agr_path.read_bytes()
    group = next(g for g in hxd['groups'] if g['name'].lower() == 'c_player')
    records = [a for a in hxd['animations'] if a['group'] == group['index']]
    spans, offset = {}, 0
    for ordinal, record in enumerate(records):
        size = record['size']-4
        raw = data[offset:offset+size]
        if len(raw) != size or raw[:4] != b'\x00\x01\x00\x00' or record['name'] in spans:
            raise FormatError('AGR/HXD exact boundary or duplicate-name mismatch')
        spans[record['name']] = (ordinal, offset, raw, record)
        offset += size
    if offset != len(data):
        raise FormatError('AGR source file not consumed exactly')
    idle = spans['C_PLAYER\\IDLE']
    reference_root = decode_animation(idle[2], idle[3]['duration'])['tracks'][1]['translation'][0][1]
    result = copy.deepcopy(base)
    result.update(schema='rea-bully-original-animation-v2', milestone='v0.3 original jump actions',
                  base_payload=dict(path=str(base_path), sha256=sha256(base_path)),
                  action_reference_root=dict(clip='C_PLAYER\\IDLE', translation=reference_root,
                                             agr_offset=idle[1]),
                  action_root_motion_policy='rest Root + source Root - source IDLE initial Root - (ARROW - initial ARROW); ARROW translation omitted from GLB; capsule physics owns travel')
    for short_name in ACTION_NAMES:
        name = 'C_PLAYER\\'+short_name
        ordinal, offset, raw, record = spans[name]
        decoded = decode_animation(raw, record['duration'])
        tracks = decoded.pop('tracks')
        arrow = tracks[35]['translation']
        displacement = [b-a for a,b in zip(arrow[0][1],arrow[-1][1])] if arrow else [0.,0.,0.]
        channels = action_channels(tracks, skeleton, reference_root, record['duration'])
        result['clips'].append(dict(name=name, ordinal=ordinal, agr_offset=offset, agr_size=len(raw),
            agr_sha256=hashlib.sha256(raw).hexdigest(), hxd_record=record, **decoded,
            channels=channels, source_channels=tracks,
            root_motion_policy=result['action_root_motion_policy'],
            root_motion=dict(bone=35,name='ARROW',source_translation_present=bool(arrow),
                translation=arrow, displacement=displacement,
                speed_m_per_second=math.dist(displacement,[0,0,0])/record['duration'],
                translation_bounds=([min(p[i] for _,p in arrow) for i in range(3)],
                                    [max(p[i] for _,p in arrow) for i in range(3)]) if arrow else None)))
    if result['clips'][:len(base['clips'])] != base['clips']:
        raise AssertionError('Existing locomotion clips changed')
    result['limitations'] = ['Original action curves and HXD times; no reconstructed ACT state machine implied.',
                             'Physics capsule and original ARROW trajectory are distinct; motion policy must be checked in-engine.',
                             'No new keyframes or loops are synthesized. HXD events retained, not executed.']
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', default=r'C:\Program Files (x86)\Steam\steamapps\common\Bully Scholarship Edition')
    parser.add_argument('--skeleton', default='evidence/player-skeleton-rest.json')
    parser.add_argument('--base', default='local/converted/player/player-original-animations.json')
    parser.add_argument('--output', default='local/converted/player/player-original-actions-v03-final.json')
    args = parser.parse_args()
    result = export(args.game, args.skeleton, args.base, args.output)
    print(json.dumps(dict(output=args.output, clips=len(result['clips']),
                          actions=[c['name'] for c in result['clips'][4:]])))

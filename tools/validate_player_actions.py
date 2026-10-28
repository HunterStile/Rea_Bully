"""Validate v0.3 original curves, boundaries, extraction and corruption controls."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct

from bully_agr import FormatError, decode_animation, interpolate_linear, sha256
from export_player_actions import action_channels


def glb(path):
    raw = Path(path).read_bytes()
    magic, version, total = struct.unpack_from('<III', raw)
    assert (magic, version, total) == (0x46546c67, 2, len(raw))
    size, kind = struct.unpack_from('<II', raw, 12)
    assert kind == 0x4e4f534a
    doc = json.loads(raw[20:20+size])
    n, kind = struct.unpack_from('<II', raw, 20+size)
    assert kind == 0x004e4942 and 28+size+n == len(raw)
    blob = raw[28+size:]
    for view in doc['bufferViews']:
        assert 0 <= view.get('byteOffset', 0) <= len(blob)-view['byteLength']
    return doc, blob


def values(doc, blob, index):
    accessor = doc['accessors'][index]
    width = {'SCALAR':1,'VEC3':3,'VEC4':4}[accessor['type']]
    assert accessor['componentType'] == 5126
    view = doc['bufferViews'][accessor['bufferView']]
    offset = view.get('byteOffset', 0)+accessor.get('byteOffset', 0)
    return struct.unpack_from('<'+'f'*(accessor['count']*width), blob, offset)


def validate(payload_path, glb_path, report_path):
    payload_path, glb_path, report_path = map(Path, (payload_path, glb_path, report_path))
    if report_path.exists():
        raise FileExistsError(report_path)
    p = json.loads(payload_path.read_text())
    base = json.loads(Path(p['base_payload']['path']).read_text())
    assert sha256(p['base_payload']['path']) == p['base_payload']['sha256']
    assert p['clips'][:4] == base['clips']
    assert all(sha256(s['path']) == s['sha256'] for s in p['sources'])
    skeleton = json.loads(Path(p['sources'][2]['path']).read_text())['bones']
    data = Path(p['sources'][0]['path']).read_bytes()
    clips, controls = [], []
    reference = p['action_reference_root']['translation']
    for c in p['clips'][4:]:
        raw = data[c['agr_offset']:c['agr_offset']+c['agr_size']]
        assert hashlib.sha256(raw).hexdigest() == c['agr_sha256']
        source = decode_animation(raw, c['duration'])
        assert source['tracks'] == c['source_channels']
        assert c['channels'] == action_channels(source['tracks'], skeleton, reference, c['duration'])
        assert len(c['channels']) == 36
        for ch in c['channels']:
            for kind in ('rotation','translation'):
                keys = ch[kind]
                assert all(math.isfinite(v) for t,p in keys for v in [t,*p])
                assert all(0 <= t <= c['duration'] for t,_ in keys)
                assert all(a[0] < b[0] for a,b in zip(keys,keys[1:]))
            assert all(abs(math.dist(q,[0,0,0,0])-1) < 1e-12 for _,q in ch['rotation'])
        arrow = c['root_motion']['translation']
        positive = [(t,p[2]) for t,p in arrow if p[2] > 0]
        pelvis = c['channels'][1]['translation']
        clips.append(dict(name=c['name'], duration=c['duration'], encoding=c['encoding'],
            agr_offset=c['agr_offset'], agr_size=c['agr_size'], agr_sha256=c['agr_sha256'],
            arrow_keyframes=arrow, first_positive_z=positive[0] if positive else None,
            apex_z=max(positive,key=lambda k:k[1]) if positive else None,
            raw_root_translation=c['source_channels'][1]['translation'],
            extracted_root_bounds=[[min(v[i] for _,v in pelvis) for i in range(3)],
                                   [max(v[i] for _,v in pelvis) for i in range(3)]],
            first_pose_offset_from_rest=[pelvis[0][1][i]-skeleton[1]['translation'][i] for i in range(3)],
            per_clip_normalization_difference=[c['source_channels'][1]['translation'][0][1][i]-reference[i]
                                               for i in range(3)]))
        corrupt = bytearray(raw)
        struct.pack_into('<I',corrupt,8,65536)
        variants=[('excessive_key_count',bytes(corrupt)),('truncated_key_payload',raw[:23])]
        corrupt = bytearray(raw)
        word = struct.unpack_from('<I',corrupt,20)[0]
        struct.pack_into('<I',corrupt,20,word|1|(1<<11))
        variants.append(('first_key_forward_link',bytes(corrupt)))
        for name,value in variants:
            try:
                decode_animation(value,c['duration'])
            except FormatError as e:
                controls.append(dict(clip=c['name'],control=name,rejected=True,error=str(e)))
            else:
                raise AssertionError(f'Corruption accepted: {c["name"]} {name}')
    doc, blob = glb(glb_path)
    old_doc, old_blob = glb('local/converted/jimmy/jimmy_final_animated.glb')
    source_doc, source_blob = glb('local/converted/jimmy/jimmy_final.glb')
    for field in ('nodes','meshes','skins','materials','images','textures'):
        assert doc.get(field) == source_doc.get(field)
    assert blob[:len(source_blob)] == source_blob
    assert len(doc['animations']) == len(p['clips']) == 10
    for a,b in zip(old_doc['animations'],doc['animations'][:4]):
        assert a['name'] == b['name'] and a['channels'] == b['channels']
        for sa,sb in zip(a['samplers'],b['samplers']):
            for field in ('input','output'):
                assert values(old_doc,old_blob,sa[field]) == values(doc,blob,sb[field])
    for animation in doc['animations']:
        assert len(animation['channels']) == 37
        for sampler in animation['samplers']:
            times = values(doc,blob,sampler['input'])
            assert all(a < b for a,b in zip(times,times[1:]))
            assert all(math.isfinite(v) for v in values(doc,blob,sampler['output']))
    result=dict(status='v03_original_actions_numerically_validated', payload=str(payload_path),
                payload_sha256=sha256(payload_path),glb=str(glb_path),glb_sha256=sha256(glb_path),
                sources_unchanged=True,existing_four_clips_exactly_unchanged=True,
                glb_skin_nodes_materials_binary_prefix_unchanged=True,
                idle_reference=p['action_reference_root'],clips=clips,negative_controls=controls,
                limitations=['Numeric validity does not establish original ACT sequencing or visual fidelity.',
                             'The capsule uses separately verified physics, not direct ARROW playback.'])
    report_path.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload',default='local/converted/player/player-original-actions-v03-final.json')
    parser.add_argument('--glb',default='local/converted/jimmy/jimmy_actions_v03_final.glb')
    parser.add_argument('--report',default='evidence/player-actions-validation-v03-final.json')
    args=parser.parse_args()
    result=validate(args.payload,args.glb,args.report)
    print(json.dumps(dict(status=result['status'],actions=len(result['clips']),
                          negative_controls=len(result['negative_controls']))))

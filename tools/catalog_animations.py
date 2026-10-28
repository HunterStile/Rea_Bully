"""Validate every HXD-addressed AGR curve and retain exact source provenance.

No naming-prefix approximation: the numeric HXD group and size delimit clips.
Only sector padding may follow the final clip. Curves remain original-axis.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import struct

from bully_agr import FormatError, Reader, decode_animation, read_hxd, read_hxd_bytes, sha256

GAME = r'C:\Program Files (x86)\Steam\steamapps\common\Bully Scholarship Edition'


def dictionaries(game):
    game = Path(game)
    for path in sorted((game/'Anim').glob('*.HXD')):
        yield dict(path=str(path), sha256=sha256(path), embedded_offset=None,
                   logical_path='Anim/'+path.name), read_hxd(path)
    path = game/'Anim/hxds.dat'
    data = path.read_bytes()
    names_path = game/'Config/Dat/PropHXDs.dat'
    names = names_path.read_text().splitlines()
    # The installed count is 131; it includes the count line itself, while the
    # archive contains 130 ANIM payloads and the list contains 130 paths.
    if int(names[0]) != len(names):
        raise FormatError('PropHXDs installed count convention changed')
    offset, ordinal = 4, 0
    advertised = struct.unpack_from('<I', data)[0]
    if advertised != len(names):
        raise FormatError('hxds.dat / PropHXDs count mismatch')
    while offset < len(data):
        magic, size = struct.unpack_from('<4sI', data, offset)
        if magic != b'ANIM' or offset+8+size > len(data) or ordinal >= len(names)-1:
            raise FormatError('Invalid embedded HXD boundary')
        raw = data[offset+8:offset+8+size]
        yield dict(path=str(path), sha256=sha256(path), embedded_offset=offset+8,
                   embedded_size=size, embedded_sha256=hashlib.sha256(raw).hexdigest(),
                   embedded_ordinal=ordinal, logical_path=names[ordinal+1],
                   name_list_path=str(names_path), name_list_sha256=sha256(names_path)), read_hxd_bytes(raw)
        ordinal, offset = ordinal+1, offset+8+size
    if ordinal != len(names)-1 or offset != len(data):
        raise FormatError('Embedded HXD count / exact consumption mismatch')


def source_index(game, extracted):
    paths = sorted(set(list((Path(game)/'Anim').rglob('*.agr')) +
                       list(Path(extracted).rglob('*.agr'))))
    index = defaultdict(list)
    for path in paths:
        index[path.stem.casefold()].append(path)
    return index


def resolve_group(group, records, index):
    size = sum(a['size']-4 for a in records)
    candidates = []
    for path in index[group['name'].casefold()]:
        raw = path.read_bytes()
        exact = len(raw) >= size and not any(raw[size:])
        candidates.append(dict(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                               extent_matches=exact, padding_bytes=len(raw)-size,
                               payload_sha256=hashlib.sha256(raw[:size]).hexdigest()))
    matching = [c for c in candidates if c['extent_matches']]
    if len({c['payload_sha256'] for c in matching}) > 1:
        raise FormatError('Ambiguous differing AGR contents: '+group['name'])
    return size, candidates, matching[0] if matching else None


def summarize_curve(curve):
    tracks = curve['tracks']
    rotations = sum(len(t['rotation']) for t in tracks)
    positions = sum(len(t['translation']) for t in tracks)
    result = {k:v for k,v in curve.items() if k != 'tracks'}
    result.update(bone_tracks=len(tracks), rotation_keys=rotations, translation_keys=positions,
                  empty_rotation_tracks=[i for i,t in enumerate(tracks) if not t['rotation']],
                  translation_tracks=[i for i,t in enumerate(tracks) if t['translation']])
    if len(tracks) == 36:
        arrow = tracks[35]['translation']
        displacement = [b-a for a,b in zip(arrow[0][1],arrow[-1][1])] if arrow else [0.,0.,0.]
        result['pedestrian_arrow'] = dict(present=bool(arrow), displacement_metres=displacement)
    return result


def catalog(game, extracted, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    index = source_index(game, extracted)
    dictionaries_out, groups_out, clips_out, failures = [], [], [], []
    encodings, bone_counts = Counter(), Counter()
    for dictionary_index, (source, hxd) in enumerate(dictionaries(game)):
        dictionaries_out.append(dict(index=dictionary_index, source=source,
            records=len(hxd['animations']), groups=len(hxd['groups']),
            bone_names=hxd['bones'], bone_order='HXD hashed name order; NOT AGR track order'))
        for group in hxd['groups']:
            records = [a for a in hxd['animations'] if a['group'] == group['index']]
            size, candidates, selected = resolve_group(group, records, index)
            identity = dict(dictionary=dictionary_index, group=group['index'], group_name=group['name'])
            entry = dict(**identity, records=len(records), expected_agr_bytes=size,
                         hxd_group=group, candidates=candidates, source=selected)
            entry['status'] = 'empty' if not records else 'resolved' if selected else 'missing_source'
            groups_out.append(entry)
            if not records:
                continue
            if selected is None:
                failures.append(dict(**identity, reason='Missing exact AGR source extent'))
                continue
            data, offset = Path(selected['path']).read_bytes(), 0
            for ordinal, record in enumerate(records):
                length = record['size']-4
                raw = data[offset:offset+length]
                clip = dict(**identity, ordinal=ordinal, agr_offset=offset, agr_size=length,
                            agr_sha256=hashlib.sha256(raw).hexdigest(), hxd_record=record,
                            source_path=selected['path'])
                try:
                    decoded = decode_animation(raw, record['duration'], strict_times=False)
                    clip.update(status='decoded', **summarize_curve(decoded))
                    encodings[decoded['encoding']] += 1
                    bone_counts[len(decoded['tracks'])] += 1
                except (FormatError, struct.error, IndexError, UnicodeError) as exc:
                    clip.update(status='rejected', error=str(exc))
                    failures.append(dict(**identity, name=record['name'], ordinal=ordinal,
                                         reason=str(exc), agr_offset=offset))
                clips_out.append(clip)
                offset += length
            if offset != size:
                raise AssertionError('Internal extent accounting error')
    addressed = {g['source']['path'] for g in groups_out if g['source']}
    unaddressed = [dict(path=str(p),bytes=p.stat().st_size,sha256=sha256(p))
                   for paths in index.values() for p in paths if str(p) not in addressed]
    result = dict(schema='rea-bully-animation-coverage-v1',
        status='all_addressed_curves_decoded' if not failures else 'incomplete',
        scope='All installed Anim/*.HXD plus all 130 embedded hxds.dat dictionaries; no cutscene AGR guessed boundaries',
        summary=dict(dictionaries=len(dictionaries_out), groups=len(groups_out),
                     nonempty_groups=sum(g['records'] > 0 for g in groups_out),
                     records=sum(d['records'] for d in dictionaries_out),
                     decoded=sum(c['status']=='decoded' for c in clips_out),
                     failures=len(failures), encodings=dict(encodings), bone_track_counts=dict(bone_counts),
                     source_agr_files=sum(map(len,index.values())),
                     addressed_agr_files=len({g['source']['path'] for g in groups_out if g['source']})),
        dictionaries=dictionaries_out, groups=groups_out, clips=clips_out, failures=failures,
        unaddressed_agr=unaddressed,
        limitations=['Decoding curves does not integrate their animation states, props, IK, events or missions into Unreal.',
                     '36-track pedestrian order is independently verified against Jimmy; other skeletons require exact original NIF hierarchy mapping.',
                     'HXD name lists are hash order, never assumed to be AGR track order.',
                     'Unaddressed cutscene AGR files are inventoried by source index only, not silently treated as decoded.',
                     'Namespaces containing mission names are retained as provenance, not used to claim non-mission runtime coverage.'])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game',default=GAME)
    parser.add_argument('--extracted',default='local/extracted')
    parser.add_argument('--output',default='evidence/animation-coverage-v04.json')
    args=parser.parse_args()
    result=catalog(args.game,args.extracted,args.output)
    print(json.dumps(dict(status=result['status'], **result['summary'])))

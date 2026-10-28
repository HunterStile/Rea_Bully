"""Independent, bounded reader for Bully AGR curves and MAINPED HXD names.

Format facts: LiquidSnake0/bully-re docs/agr.md and docs/hxd.md.
No third-party implementation is imported. Source game files are read only.
Exports local original-axis (x,y,z, metres), xyzw quaternion tracks; consumers
must apply the same basis conversion as their NIF skeleton exporter.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct


class FormatError(ValueError):
    pass


class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, n):
        if n < 0 or self.pos+n > len(self.data):
            raise FormatError(f"Read outside file: {self.pos}+{n}")
        value = self.data[self.pos:self.pos+n]
        self.pos += n
        return value

    def read(self, fmt):
        vals = struct.unpack('<'+fmt, self.take(struct.calcsize('<'+fmt)))
        return vals[0] if len(vals) == 1 else vals

    def count(self):
        count = self.read('I')
        if count > 100_000:
            raise FormatError(f"Unbounded count {count}")
        return count

    def text(self, size):
        return self.take(size).split(b'\0', 1)[0].decode('ascii')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_hxd(path):
    return read_hxd_bytes(Path(path).read_bytes())


def read_hxd_bytes(data):
    """Read standalone or ANIM-container HXD payload, with exact consumption."""
    r = Reader(data)
    version, header_word = r.read('fI')
    if abs(version-1.12) > 0.00001:
        raise FormatError(f"Only verified HXD version 1.12 supported: {version}")
    masks = []
    for _ in range(r.count()):
        name, n, word = r.text(32), r.count(), r.read('I')
        weights = r.read(f'{n}f') if n else ()
        masks.append({'name':name, 'count':n, 'word':word})
    bones = [r.text(32) for _ in range(r.count())]
    animations = []
    for index in range(r.count()):
        offset = r.pos
        duration, blend = r.read('ff')
        name = r.text(64)
        name_hash, flags, size, group = r.read('4I')
        events = []
        for _ in range(r.read('H')):
            a, b, kind = r.read('3H')
            extra = r.take(40).hex() if kind == 1 else None
            events.append({'fields':[a,b,kind], 'extra_hex':extra})
        shorts, word, extra_words, vector = r.read('4H'), r.read('I'), r.read('2I'), r.read('3f')
        if not math.isfinite(duration) or duration <= 0 or size < 24:
            raise FormatError(f"Invalid HXD animation {index}")
        animations.append(dict(index=index, offset=offset, name=name, duration=duration,
                               blend=blend, name_hash=name_hash, flags=flags, size=size,
                               group=group, events=events))
    groups = []
    for index in range(r.count()):
        name, size, mxd, word = r.text(32), r.read('I'), r.text(32), r.read('I')
        groups.append(dict(index=index, name=name, size=size, mxd=mxd, word=word))
    r.take(12*len(bones))
    for _ in range(r.count()):
        r.take(48)
    final_word = r.read('I')
    if r.pos != len(r.data):
        raise FormatError(f"HXD trailing bytes: {len(r.data)-r.pos}")
    for group in groups:
        selected = [a for a in animations if a['group'] == group['index']]
        if sum(a['size'] for a in selected) != group['size']:
            raise FormatError(f"HXD group size mismatch: {group['name']}")
    return dict(version=version, header_word=header_word, masks=masks, bones=bones,
                animations=animations, groups=groups, final_word=final_word)


def signed_magnitude(value, shift, bits):
    magnitude = (value >> shift) & ((1 << bits)-1)
    return -magnitude if (value >> (shift+bits)) & 1 else magnitude


def decode_animation(data, duration=None, *, strict_times=True):
    r = Reader(data)
    magic, encoding, count, zero, header_duration = r.read('4If')
    sizes = {999:32, 1000:20, 1001:12, 1002:8, 1003:20, 1004:12}
    if magic != 0x100 or encoding not in sizes or zero != 0 or count > 65535:
        raise FormatError('Unsupported AGR header')
    duration = header_duration if duration is None else duration
    if not math.isfinite(duration) or duration <= 0 or not math.isfinite(header_duration) or header_duration <= 0:
        raise FormatError('Invalid duration')
    keys, tracks, null_count, norm_error = [], [], 0, 0.0
    for index in range(count):
        raw = r.take(sizes[encoding])
        translation = None
        if encoding in (1002, 1004):
            w0, w1 = struct.unpack_from('<II', raw)
            previous, fraction = w0 & 2047, ((w0 >> 11) & 511)/511
            x = -((w0 >> 21) & 1023) if w0 & (1 << 20) else ((w0 >> 21) & 1023)
            y = -(w1 & 1023) if w0 & (1 << 31) else w1 & 1023
            z = -((w1 >> 11) & 1023) if w1 & (1 << 10) else ((w1 >> 11) & 1023)
            w = -((w1 >> 22) & 1023) if w1 & (1 << 21) else ((w1 >> 22) & 1023)
            q = [v/1023 for v in (x,y,z,w)]
            if encoding == 1004:
                packed = struct.unpack_from('<I',raw,8)[0]
                translation = [signed_magnitude(packed,s,b)*.01 for s,b in [(0,10),(11,10),(22,9)]]
        else:
            previous, instant = struct.unpack_from('<HH',raw)
            fraction = instant/65535
            if encoding in (999,1000):
                w,x,y,z = struct.unpack_from('<4f',raw,4)
                q = [x,y,z,w]
            else:
                q = [v/32767 for v in struct.unpack_from('<4h',raw,4)]
            if encoding == 999:
                translation = list(struct.unpack_from('<3f',raw,20))
            elif encoding == 1003:
                translation = [v*.001 for v in struct.unpack_from('<3h',raw,12)]
        if previous == 0 and fraction == 0:
            bone = len(tracks)
            tracks.append({'rotation':[], 'translation':[]})
        elif previous >= index:
            raise FormatError(f'AGR backward-link invalid at {index}: {previous}')
        else:
            bone = keys[previous]['bone']
        norm = math.sqrt(sum(v*v for v in q))
        if not math.isfinite(norm):
            raise FormatError('Nonfinite quaternion')
        key = {'bone':bone, 'time':fraction*duration, 'fraction':fraction}
        keys.append(key)
        if norm < 1e-12:
            null_count += 1
        else:
            norm_error = max(norm_error, abs(norm-1))
            if abs(norm-1) > .002:
                raise FormatError(f'Quaternion norm outside quantization tolerance: {norm}')
            q = [v/norm for v in q]
            out = tracks[bone]['rotation']
            if out and sum(a*b for a,b in zip(out[-1][1],q)) < 0:
                q = [-v for v in q]
            out.append([fraction*duration, q])
        if translation is not None:
            if not all(math.isfinite(v) for v in translation):
                raise FormatError('Nonfinite position')
            tracks[bone]['translation'].append([fraction*duration,translation])
    if encoding in (999,1003,1004) and r.pos != len(data):
        raise FormatError('Translation-in-key AGR has trailing bytes')
    position_count = 0
    while r.pos < len(data):
        # Float quaternion encodings retain float positions: uint32 key index,
        # three float32 metres. Quantized encodings use uint16 + 3 int16 mm.
        # MAINPED's two float-position clips disprove treating both as 8 bytes.
        if encoding == 1000:
            key_index,x,y,z = r.read('I3f')
            position = [x,y,z]
        else:
            key_index,x,y,z = r.read('H3h')
            position = [v*.001 for v in (x,y,z)]
        if key_index >= len(keys):
            raise FormatError('Position references absent key')
        if not all(math.isfinite(v) for v in position):
            raise FormatError('Nonfinite position')
        key = keys[key_index]
        tracks[key['bone']]['translation'].append([key['time'],position])
        position_count += 1
    duplicate_instants = []
    for track_index, track in enumerate(tracks):
        for kind in ('rotation','translation'):
            track[kind].sort(key=lambda k:k[0])
            duplicates = [dict(time=b[0], identical_values=a[1] == b[1])
                          for a,b in zip(track[kind],track[kind][1:]) if b[0] == a[0]]
            if duplicates:
                duplicate_instants.append(dict(bone=track_index, channel=kind, keys=duplicates))
            if strict_times and duplicates:
                raise FormatError('Duplicate or reversed track instant')
    return dict(encoding=encoding, key_count=count, header_duration=header_duration,
                duration=duration, tracks=tracks, null_quaternions_skipped=null_count,
                max_source_quaternion_norm_error=norm_error,
                separate_position_count=position_count,
                separate_position_encoding=('uint32,float32_xyz_metres' if encoding == 1000 else
                                             'uint16,int16_xyz_millimetres') if position_count else None,
                duplicate_instants=duplicate_instants)


def interpolate_linear(keys, time):
    if time <= keys[0][0]: return keys[0][1]
    for a,b in zip(keys,keys[1:]):
        if time <= b[0]:
            t = (time-a[0])/(b[0]-a[0])
            return [x+(y-x)*t for x,y in zip(a[1],b[1])]
    return keys[-1][1]


def export_player(game, skeleton_path, out):
    game, out = Path(game), Path(out)
    if out.exists(): raise FileExistsError(out)
    skeleton = json.loads(Path(skeleton_path).read_text())['bones']
    hxd_path, agr_path = game/'Anim/MAINPED.HXD', game/'Anim/C_Player.agr'
    hxd, agr = read_hxd(hxd_path), agr_path.read_bytes()
    group = next(g for g in hxd['groups'] if g['name'].lower() == 'c_player')
    records = [a for a in hxd['animations'] if a['group'] == group['index']]
    requested = {'C_PLAYER\\IDLE','C_PLAYER\\PLAYERWALK','C_PLAYER\\PLAYERRUN','C_PLAYER\\RUNFAST'}
    offset, clips, inventory = 0, [], []
    for ordinal, record in enumerate(records):
        size = record['size']-4
        raw = agr[offset:offset+size]
        if len(raw) != size or raw[:4] != b'\x00\x01\x00\x00':
            raise FormatError(f'AGR/HXD boundary mismatch: {ordinal}, {offset}')
        inventory.append({'ordinal':ordinal,'offset':offset,'size':size,'name':record['name'],
                          'duration':record['duration'],'sha256':hashlib.sha256(raw).hexdigest()})
        if record['name'] in requested:
            decoded = decode_animation(raw, record['duration'])
            if len(decoded['tracks']) != len(skeleton): raise FormatError('Skeleton track count mismatch')
            tracks = decoded.pop('tracks')
            root, arrow = tracks[1]['translation'], tracks[35]['translation']
            if not root: raise FormatError('Missing Root position track')
            arrow_present = bool(arrow)
            # No ARROW translation key means no keyed displacement, not unknown
            # motion. Preserve that distinction in the output metadata.
            if not arrow: arrow = [[0.0,[0.0,0.0,0.0]],[record['duration'],[0.0,0.0,0.0]]]
            arrow0, root0 = arrow[0][1], root[0][1]
            times = sorted(set([x[0] for x in root]+[x[0] for x in arrow]))
            root_in_place = []
            for time in times:
                rp, ap = interpolate_linear(root,time), interpolate_linear(arrow,time)
                root_in_place.append([time,[skeleton[1]['translation'][k]+rp[k]-root0[k]-ap[k]+arrow0[k] for k in range(3)]])
            channels = []
            for bone, (rest, track) in enumerate(zip(skeleton,tracks)):
                translations = track['translation']
                if bone == 1: translations = root_in_place
                elif bone == 35: translations = [[t,[v-a for v,a in zip(p,arrow0)]] for t,p in arrow]
                channels.append(dict(bone=bone, name=rest['name'], rotation=track['rotation'],
                                     translation=translations, rest_translation=rest['translation']))
            displacement = [b-a for a,b in zip(arrow[0][1],arrow[-1][1])]
            clip = dict(name=record['name'], ordinal=ordinal, agr_offset=offset, agr_size=size,
                        agr_sha256=hashlib.sha256(raw).hexdigest(), hxd_record=record,
                        **decoded, channels=channels,
                        root_motion=dict(bone=35,name=skeleton[35]['name'],source_translation_present=arrow_present,translation=arrow,
                                         displacement=displacement,
                                         speed_m_per_second=math.sqrt(sum(v*v for v in displacement))/record['duration']))
            clips.append(clip)
        offset += size
    if offset != len(agr): raise FormatError(f'AGR unconsumed bytes {len(agr)-offset}')
    if {c['name'] for c in clips} != requested: raise FormatError('Requested exact clips not unique/found')
    result = dict(schema='rea-bully-original-animation-v1', coordinate_system='Original NIF axes, metres, xyzw quaternion; quaternion matrix replaces local node rotation',
                  sources=[dict(path=str(p),sha256=sha256(p)) for p in (agr_path,hxd_path,Path(skeleton_path))],
                  format_references=['https://github.com/LiquidSnake0/bully-re/blob/main/docs/agr.md',
                                     'https://github.com/LiquidSnake0/bully-re/blob/main/docs/hxd.md'],
                  hxd_animation_count=len(hxd['animations']),hxd_group_count=len(hxd['groups']),
                  c_player_inventory=inventory, c_player_exact_bytes_consumed=offset,
                  root_motion_policy='Root uses rest + Root delta - ARROW delta. ARROW locomotion reported separately; omit ARROW translation from in-place glTF playback.',
                  clips=clips, limitations=['Original curves decoded, not original gameplay state machine.',
                                           'Root-motion policy follows documented engine interpretation, still requires visual verification on original skinned model.',
                                           'HXD event payloads preserved, not executed.'])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__ == '__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--game',default=r'C:\Program Files (x86)\Steam\steamapps\common\Bully Scholarship Edition')
    ap.add_argument('--skeleton',default='evidence/player-skeleton-rest.json')
    ap.add_argument('--output',default='local/converted/player/player-original-animations.json')
    args=ap.parse_args()
    result=export_player(args.game,args.skeleton,args.output)
    print(json.dumps({'status':'decoded','clips':[{k:c[k] for k in ('name','ordinal','duration','key_count')} for c in result['clips']], 'output':args.output}))

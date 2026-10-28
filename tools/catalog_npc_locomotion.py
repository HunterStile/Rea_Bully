"""Prove a bounded set of pedestrian locomotion choices from exact original CATs.

This reads CAT references and MAINPED hashes; it does not execute CAT conditions
or claim to reproduce the original AI. The focused reader does not scan binaries
or unrelated CATs. Source offsets are pinned to the verified Steam extraction.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import struct

from bully_agr import FormatError, read_hxd, sha256

ROOT = Path(__file__).resolve().parents[1]
CAT_ROOT = ROOT/'local/extracted/Act/Act'
HXD = Path('C:/Program Files (x86)/Steam/steamapps/common/Bully Scholarship Edition/Anim/MAINPED.HXD')
CAT_SHA256 = {
    'Authority': 'edbbb1c5e044a8be90fbdf2eb15d4aae59540417f93ea8b6f597ad4f8cada73c',
    'N_Ranged_A': '2b5be3ba92267b838c539e49f00e1b8122463f7848d3a3698a5114c0f8b51a49',
    'Walk_Default': 'e4623b2a086cf9d0efab86667bb080cce7d55d092164ac681315d10443f709aa',
    'Walk_Nerd': 'b433f6c4a00c743b57afb4d25d16f9e87aaead05e9eebfc24c23fb1ff2ed4a04',
    'GS_Male_A': 'f371013c5fd05de578ca847b5b0536b6cff4a1b235c4f068a08bba2423a66684',
    'B_Striker_A': '887f943686f59ad7450d6b05a91832c8e1870f9ad93298a612bd898e9d6fdcdc',
    'Walk_Bully': 'c1d922ea7e981d1a6ebc13594995ed4f80271c036925ac50e5ed556479fba825',
}

# group, clip, role, CAT, node offset, track offset. Offsets locate the actual
# inherited track records, whose animation field is then matched against HXD.
PROFILES = {
    'nerd': {'action': 'N_Ranged_A', 'walk': 'Walk_Nerd', 'include_node': 304, 'clips': [
        ('F_Nerds', 'F_NERDS\\IDLE', 'idle', 'N_Ranged_A', 274, 172),
        ('F_Nerds', 'F_NERDS\\WALK', 'walk', 'Walk_Nerd', 298, 821),
        ('C_Player', 'C_PLAYER\\RUN', 'run', 'Walk_Nerd', 277, 556),
        ('C_Player', 'F_NERDS\\RUN', 'sprint', 'Walk_Nerd', 256, 897),
        ('IDLE_NERD_A', 'F_NERDS\\ND_IDLE_A', 'ambient_idle', 'Walk_Nerd', 534, 1125)]},
    'prefect': {'action': 'Authority', 'walk': 'Walk_Default', 'include_node': 307, 'clips': [
        ('F_Pref', 'F_PREF\\IDLE_A', 'idle', 'Authority', 260, 552),
        ('C_Player', 'C_PLAYER\\WALK', 'walk', 'Walk_Default', 797, 2004),
        ('C_Player', 'C_PLAYER\\RUN', 'run', 'Walk_Default', 694, 1873),
        ('C_Player', 'C_PLAYER\\RUNFAST', 'sprint', 'Walk_Default', 526, 2286),
        ('IDLE_AUTH_A', 'F_PREF\\AU_IDLE_A_01', 'ambient_idle', 'Walk_Default', 2539, 1955)]},
    'student_male': {'action': 'GS_Male_A', 'walk': 'Walk_Default', 'include_node': 270, 'clips': [
        ('C_Player', 'C_PLAYER\\GEN_IDLE', 'idle', 'GS_Male_A', 236, 193),
        ('C_Player', 'C_PLAYER\\WALK', 'walk', 'Walk_Default', 797, 2004),
        ('C_Player', 'C_PLAYER\\RUN', 'run', 'Walk_Default', 694, 1873),
        ('C_Player', 'C_PLAYER\\RUNFAST', 'sprint', 'Walk_Default', 612, 2286),
        ('IDLE_GSM_A', 'GS_MALE\\GS_IDLE_A_01', 'ambient_idle', 'Walk_Default', 4088, 1787)]},
    'bully': {'action': 'B_Striker_A', 'walk': 'Walk_Bully', 'include_node': 296, 'clips': [
        ('F_BULLY', 'F_BULLY\\IDLE', 'idle', 'B_Striker_A', 266, 346),
        ('C_Player', 'C_PLAYER\\WALK', 'walk', 'Walk_Bully', 453, 945),
        ('C_Player', 'C_PLAYER\\RUN', 'run', 'Walk_Bully', 424, 874),
        ('C_Player', 'C_PLAYER\\RUNFAST', 'sprint', 'Walk_Bully', 375, 1154),
        ('IDLE_BULLY_A', 'F_BULLY\\BU_IDLE_A', 'ambient_idle', 'Walk_Bully', 680, 830)]},
}
PED_PROFILES = {8: 'nerd', 50: 'prefect', 9: 'nerd', 11: 'nerd',
                72: 'student_male', 73: 'student_male', 85: 'bully', 99: 'bully'}
NEW_PED_IDS = (9, 11, 72, 73, 85, 99)


def lower_priority():
    if os.name != 'nt':
        return 'platform_default'
    import ctypes
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.GetCurrentProcess.restype = ctypes.c_void_p
    api.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    api.GetPriorityClass.argtypes = [ctypes.c_void_p]
    handle = api.GetCurrentProcess()
    if not api.SetPriorityClass(handle, 0x4000) or api.GetPriorityClass(handle) != 0x4000:
        raise OSError('Could not set Python to BelowNormal priority')
    return 'BelowNormal'


def name_hash(value):
    result = 0
    for byte in value.upper().encode('ascii'):
        result = (result*131 + byte) & 0x7fffffff
    return result


class CatReferences:
    """Bounded independent reference reader for the existing verified CAT layout."""
    def __init__(self, path):
        self.path = Path(path)
        self.raw = self.path.read_bytes()
        if len(self.raw) < 32:
            raise FormatError('Short CAT header')
        self.header = struct.unpack_from('<8I', self.raw)
        self.length, self.data, self.strings, self.tree_end = self.header[:4]
        if not 32 <= self.tree_end <= self.data <= self.strings <= self.length <= len(self.raw) or any(self.raw[self.length:]):
            raise FormatError('CAT section bounds changed')
        self.position = 32
        self.relocations = {}
        self.nodes = {}
        self.counts = dict.fromkeys('bnlir', 0)
        for table in range(2):
            for _ in range(self.read('I')):
                value, count = self.read('IH')
                refs = [self.read('I') for _ in range(count)]
                if table == 0:
                    text = self.text(value)
                    for ref in refs:
                        if ref in self.relocations and self.relocations[ref] != text:
                            raise FormatError('Conflicting CAT relocation')
                        self.relocations[ref] = text
        self.root = self.node([])
        if self.position != self.tree_end or (self.counts['b']-1, self.counts['n']+self.counts['l'],
            self.counts['i']+self.counts['r'], self.counts['l']) != self.header[4:]:
            raise FormatError('CAT node/header counts changed')

    def read(self, layout):
        length = struct.calcsize('<'+layout)
        if self.position+length > self.tree_end:
            raise FormatError('CAT tree read outside section')
        value = struct.unpack_from('<'+layout, self.raw, self.position)
        self.position += length
        return value[0] if len(value) == 1 else value

    def text(self, relative):
        start = self.strings+relative
        if not self.strings <= start < self.length:
            raise FormatError('CAT string offset outside section')
        end = self.raw.find(b'\0', start, self.length)
        if end < 0:
            raise FormatError('CAT string has no terminator')
        return self.raw[start:end].decode('ascii')

    def node(self, ancestors):
        offset = self.position
        kind = chr(self.read('B'))
        if kind not in self.counts:
            raise FormatError('Unknown CAT node kind')
        self.counts[kind] += 1
        name = self.read('I')
        result = {'offset': offset, 'kind': kind, 'ancestors': ancestors}
        if kind in 'ir':
            result.update(name=self.text(name), target=self.text(self.read('I')))
        else:
            result['name_hash'] = name & 0x7fffffff if name & 0x80000000 else name_hash(self.text(name))
            result['conditions'] = [self.read('I') for _ in range(self.read('B'))]
            result['tracks'] = [self.read('I') for _ in range(self.read('B'))] if kind in 'nl' else []
            for _ in range(self.read('H')):
                self.node([*ancestors, offset])
        self.nodes[offset] = result
        return result

    def track(self, offset, visited=()):
        if offset in visited or not 0 <= offset < self.strings-self.data-2:
            raise FormatError('Invalid CAT inherited track reference')
        position = self.data+offset
        base = struct.unpack_from('<H', self.raw, position)[0]
        position += 2
        attributes = self.track(offset+base, (*visited, offset)).copy() if base else {}
        while True:
            if position+2 > self.strings:
                raise FormatError('CAT track header outside data')
            word = struct.unpack_from('<H', self.raw, position)[0]
            position += 2
            size, key = 1 << ((word >> 1) & 3), word >> 3
            if position+size > self.strings:
                raise FormatError('CAT track value outside data')
            data = self.raw[position:position+size]
            value = {'uint': int.from_bytes(data, 'little'), 'file_offset': position, 'hex': data.hex()}
            if position-self.data in self.relocations:
                value['string'] = self.relocations[position-self.data]
            attributes[key] = value
            position += size
            if not word & 1:
                return attributes


def selections(ped_id):
    return [tuple(clip[:3]) for clip in PROFILES[PED_PROFILES[ped_id]]['clips']]


def prove_profile(profile_name, hxd, cats):
    profile = PROFILES[profile_name]
    action = cats[profile['action']]
    if action.root['name_hash'] != name_hash(profile['action']):
        raise FormatError('Pedestrian action root does not match CAT root')
    include = action.nodes.get(profile['include_node'], {})
    if include.get('kind') not in ('i', 'r') or include.get('name') != profile['walk']+'.act':
        raise FormatError('Original locomotion subtree reference changed')
    clips = []
    for group, name, role, cat_name, node_offset, track_offset in profile['clips']:
        cat = cats[cat_name]
        node = cat.nodes.get(node_offset, {})
        if track_offset not in node.get('tracks', []):
            raise FormatError('Animation track is not owned by its declared CAT node')
        attrs = cat.track(track_offset)
        expected_type = 'ANIMATION' if role in ('idle', 'ambient_idle') else 'LocomotionAnimationNew'
        if attrs.get(0, {}).get('uint') != name_hash(expected_type) or attrs.get(24, {}).get('uint') != name_hash(name):
            raise FormatError('CAT animation type/hash differs from selected locomotion')
        if role in ('idle','ambient_idle') and 28 in attrs:
            raise FormatError('Full-body NPC idle selection unexpectedly has a body-mask attribute')
        matching = [item for item in hxd['animations'] if item['name'] == name and item['name_hash'] == attrs[24]['uint']]
        if len(matching) != 1 or hxd['groups'][matching[0]['group']]['name'] != group:
            raise FormatError('Selected CAT animation has no unique matching MAINPED group')
        animation = matching[0]
        clips.append({'role': role, 'name': name, 'group': group, 'group_index': animation['group'],
            'animation_hash': f'{animation["name_hash"]:08x}', 'duration': animation['duration'],
            'hxd_animation_index': animation['index'], 'hxd_record_offset': animation['offset'],
            'cat': cat_name, 'cat_node_offset': node_offset, 'cat_track_offset': track_offset,
            'cat_animation_field_offset': attrs[24]['file_offset'], 'cat_track_type': expected_type,
            'condition_offsets': node.get('conditions', []), 'ancestor_offsets': node['ancestors']})
    return {'action_root': '/Global/'+profile['action'], 'action_file': 'Act/Anim/'+profile['action']+'.act',
            'walk_cat': profile['walk'], 'include': include, 'clips': clips}


def catalog():
    hxd = read_hxd(HXD)
    cats = {}
    for name, expected in CAT_SHA256.items():
        path = CAT_ROOT/(name+'.cat')
        if sha256(path) != expected:
            raise FormatError('CAT does not match the verified installation: ' + name)
        cats[name] = CatReferences(path)
    return {'schema': 'rea-bully-exact-npc-locomotion-v1', 'status': 'exact_original_cat_locomotion_links_verified',
        'hxd': {'path': str(HXD), 'sha256': sha256(HXD)},
        'cats': {name: {'path': str(cat.path), 'sha256': CAT_SHA256[name]} for name, cat in cats.items()},
        'profiles': {name: prove_profile(name, hxd, cats) for name in PROFILES},
        'ped_profiles': {str(ped): family for ped, family in PED_PROFILES.items()},
        'limitations': ['References and original clips verified statically; CAT conditions and AI are not executed.',
                       'Full-body ambient take selected from the named family; original scheduling is not recreated.']}


def verify_ped_selection(ped, evidence_path):
    family = PED_PROFILES.get(ped['id'])
    if family is None:
        raise FormatError('Ped has no evidenced locomotion profile')
    expected = PROFILES[family]
    if ped['action_root'] != '/Global/'+expected['action'] or ped['action_file'].replace('\\', '/').lower() != ('Act/Anim/'+expected['action']+'.act').lower():
        raise FormatError('Ped IDE action tree differs from its evidenced locomotion profile')
    stored = json.loads(Path(evidence_path).read_text(encoding='utf-8'))
    proof = catalog()
    if stored.get('schema') == proof['schema']:
        if stored.get('hxd') != proof['hxd'] or stored.get('profiles', {}).get(family) != proof['profiles'][family]:
            raise FormatError('Stored NPC CAT selection disagrees with current exact source proof')
        for name in (expected['action'], expected['walk']):
            if stored.get('cats', {}).get(name) != proof['cats'][name]:
                raise FormatError('Stored NPC CAT provenance changed')
    else:
        # Existing v04 reports remain usable for Bucky/Seth. Re-prove their raw
        # references instead of treating a matching report filename as evidence.
        if ped['id'] not in (8, 50):
            raise FormatError('New NPC families require the exact v05 CAT catalog')
        sources = {Path(item['source']).stem: item['sha256'] for item in stored.get('files', [])}
        if any(sources.get(name) != CAT_SHA256[name] for name in (expected['action'], expected['walk'])):
            raise FormatError('Legacy NPC CAT evidence does not cover its action/locomotion sources')
    return {'profile': family, 'hxd': proof['hxd'], **proof['profiles'][family]}


if __name__ == '__main__':
    priority = lower_priority()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, default=ROOT/'evidence/npc-locomotion-cat-v05.json')
    args = parser.parse_args()
    if args.report.exists():
        parser.error('New evidence path required')
    result = catalog()
    result['process_priority'] = priority
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'report': str(args.report), 'profiles': len(result['profiles']), 'process_priority': priority}))

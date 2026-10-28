"""Read-only, source-linked catalog of contextual original prop animation facts.

Importing this module does not read game files, scan the executable or run tools.
CAT labels recovered by hashing strings are candidates, never executable rules.
This reader implements the same retained format facts as inspect_jump_cat.py;
it does not implement the CAT scheduler, conditions or prop animation runtime.
"""
from __future__ import annotations

import argparse
from collections import ChainMap, defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parents[1]


def hashstring(value):
    result = 0
    for char in value.upper().encode('ascii', errors='ignore'):
        result = (result * 131 + char) & 0x7fffffff
    return result


def ascii_strings(data):
    for match in re.finditer(rb'[ -~]{2,}', data):
        value = match.group().decode('ascii')
        yield value
        yield from re.split(r'[/\\\s]+', value)


class Cat:
    """Bounded CAT tree/attribute reader with absolute source offsets.

    ``known_names`` supplies optional hash-label candidates; it grants no runtime
    meaning. Constructor reads only the specified CAT. No implicit global input.
    """

    def __init__(self, path, known_names=()):
        self.path = Path(path)
        self.d = self.path.read_bytes()
        self.names = ChainMap({}, known_names) if isinstance(known_names, dict) else {}
        additional = (self.path.stem, *ascii_strings(self.d)) if isinstance(known_names, dict) else (*known_names, self.path.stem, *ascii_strings(self.d))
        for name in additional:
            if len(name) > 1 and name.isascii():
                value = hashstring(name)
                self.names[value] = self.names.get(value, set()) | {name}
        self.h = self.unpack('<8I', 0)
        self.data, self.strings, self.end = self.h[1:4]
        if not (32 <= self.end <= self.data <= self.strings <= self.h[0] <= len(self.d)):
            raise ValueError('CAT section bounds are invalid')
        if any(self.d[self.h[0]:]):
            raise ValueError('Nonzero bytes after declared CAT extent')
        self.p, self.relocs, self.tables = 32, {}, []
        for table_index in range(2):
            table = []
            for _ in range(self.count(self.r('I'))):
                value, count = self.r('IH')
                refs = [self.r('I') for _ in range(self.count(count))]
                table.append({'value': value, 'refs': refs})
                if table_index == 0:
                    text = self.text(value)
                    for ref in refs:
                        if ref in self.relocs and self.relocs[ref] != text:
                            raise ValueError('Conflicting CAT relocation')
                        self.relocs[ref] = text
            self.tables.append(table)
        self.counts = dict.fromkeys('bnlir', 0)
        self.root = self.node(chr(self.r('B')), '', 0)
        if (self.p != self.end or self.counts['b'] - 1 != self.h[4]
                or self.counts['n'] + self.counts['l'] != self.h[5]
                or self.counts['i'] + self.counts['r'] != self.h[6]
                or self.counts['l'] != self.h[7]):
            raise ValueError('CAT tree end/count mismatch')

    def unpack(self, fmt, offset):
        if offset < 0 or offset + struct.calcsize(fmt) > len(self.d):
            raise ValueError('CAT read outside file')
        return struct.unpack_from(fmt, self.d, offset)

    def count(self, value):
        if value > len(self.d):
            raise ValueError('Unbounded CAT count')
        return value

    def r(self, fmt):
        if self.p + struct.calcsize('<' + fmt) > self.end:
            raise ValueError('CAT tree read outside declared extent')
        values = self.unpack('<' + fmt, self.p)
        self.p += struct.calcsize('<' + fmt)
        return values[0] if len(values) == 1 else values

    def text(self, offset):
        absolute = self.strings + offset
        if not self.strings <= absolute < self.h[0]:
            raise ValueError('CAT string outside declared extent')
        return self.d[absolute:self.d.index(0, absolute, self.h[0])].decode('ascii')

    def label(self, value):
        candidates = sorted(self.names.get(value & 0x7fffffff, ()), key=lambda x: (len(x), x))
        return candidates[0] if candidates else f'#{value & 0x7fffffff:08x}'

    def node(self, kind, parent, depth):
        if kind not in self.counts or depth > 256:
            raise ValueError('Invalid CAT node tag/depth')
        self.counts[kind] += 1
        offset, name = self.p - 1, self.r('I')
        result = {'kind': kind, 'offset': offset, 'name_raw_uint': name}
        if kind in 'ir':
            result.update(name=self.text(name), target=self.text(self.r('I')), name_source='string')
        else:
            label = self.label(name) if name & 0x80000000 else self.text(name)
            result.update(name=label,
                          name_hash=f'{(name & 0x7fffffff) if name & 0x80000000 else hashstring(label):08x}',
                          name_source='hash_candidate' if name & 0x80000000 else 'string')
        result['path'] = parent + '/' + result['name']
        if kind not in 'ir':
            result['condition_offsets'] = [self.r('I') for _ in range(self.r('B'))]
            result['track_offsets'] = [self.r('I') for _ in range(self.r('B'))] if kind in 'nl' else []
            result['children'] = [self.node(chr(self.r('B')), result['path'], depth + 1)
                                  for _ in range(self.r('H'))]
        return result

    def track(self, offset, seen=None):
        seen = set() if seen is None else seen
        if offset in seen or len(seen) > 256:
            raise ValueError('Cyclic/deep CAT attribute inheritance')
        seen.add(offset)
        position = self.data + offset
        if not self.data <= position < self.strings - 1:
            raise ValueError('CAT track outside data section')
        base = self.unpack('<H', position)[0]
        position += 2
        attrs = self.track(offset + base, seen)['attrs'].copy() if base else {}
        while True:
            if position + 2 > self.strings:
                raise ValueError('CAT attribute header outside data')
            word = self.unpack('<H', position)[0]
            position += 2
            size, field = 1 << ((word >> 1) & 3), word >> 3
            if position + size > self.strings:
                raise ValueError('CAT attribute payload outside data')
            raw = self.d[position:position + size]
            attr = {'hex': raw.hex(), 'uint': int.from_bytes(raw, 'little'),
                    'file_offset': position, 'byte_count': size}
            if size == 4:
                value = struct.unpack('<f', raw)[0]
                if math.isfinite(value):
                    attr['float'] = value
                candidates = self.names.get(attr['uint'] & 0x7fffffff)
                if candidates:
                    attr['hash_names'] = sorted(candidates)
            if position - self.data in self.relocs:
                attr['string'] = self.relocs[position - self.data]
            attrs[str(field)] = attr
            position += size
            if not word & 1:
                break
        type_hash = attrs.get('0', {}).get('uint', 0) & 0x7fffffff
        return {'offset': offset, 'base': base, 'type': self.label(type_hash),
                'type_hash': f'{type_hash:08x}', 'attrs': attrs}

    def flat(self, node=None):
        node = self.root if node is None else node
        yield node
        for child in node.get('children', []):
            yield from self.flat(child)

    def conditions(self, node):
        result = []
        for offset in node.get('condition_offsets', []):
            position = self.data + offset
            if not self.data <= position <= self.strings - 4:
                raise ValueError('CAT condition outside data section')
            value = self.unpack('<I', position)[0]
            result.append({'file_offset': position, 'offset_relative_data': offset,
                           'type_hash': f'{value:08x}',
                           'class_name_candidate': self.label(value),
                           'evaluation': 'not_reconstructed'})
        return result


def digest(data):
    return hashlib.sha256(data).hexdigest()


def source(path, root=ROOT):
    path = Path(path)
    try:
        name = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        name = str(path.resolve())
    return {'path': name, 'sha256': digest(path.read_bytes())}


def verified_bytes(path, expected):
    data = Path(path).read_bytes()
    if digest(data) != expected:
        raise ValueError(f'Source SHA-256 mismatch: {path}')
    return data


def load_groups(props, root=ROOT):
    """Join existing own-group exports, verifying AGR slices and embedded HXD."""
    groups, file_cache = {}, {}
    for model in props['models']:
        record = model['source_group']
        key = (record['dictionary'], record['group'])
        if key not in groups:
            payload = json.loads(gzip.decompress(verified_bytes(root / record['path'], record['sha256'])))
            agr_source = payload['group']['source']
            agr = verified_bytes(root / agr_source['path'], agr_source['sha256'])
            hxd_source = payload['dictionary']['source']
            hxd_path = Path(hxd_source['path'])
            if str(hxd_path) not in file_cache:
                file_cache[str(hxd_path)] = verified_bytes(hxd_path, hxd_source['sha256'])
            hxd = file_cache[str(hxd_path)]
            if 'embedded_offset' in hxd_source:
                offset, size = hxd_source['embedded_offset'], hxd_source['embedded_size']
                hxd = hxd[offset:offset + size]
                if digest(hxd) != hxd_source['embedded_sha256']:
                    raise ValueError('Embedded HXD SHA-256 mismatch')
            clips = []
            for clip in payload['clips']:
                raw = agr[clip['agr_offset']:clip['agr_offset'] + clip['agr_size']]
                if digest(raw) != clip['agr_sha256']:
                    raise ValueError('Original AGR clip slice changed')
                hxd_record = clip['hxd_record']
                if hashstring(clip['name']) != hxd_record['name_hash']:
                    raise ValueError('Original HXD name/hash mismatch')
                clips.append({k: clip[k] for k in ('name', 'ordinal', 'hxd_record',
                              'agr_offset', 'agr_size', 'agr_sha256', 'duration')})
            groups[key] = {'id': f'{key[0]}:{key[1]}', 'name': record['name'],
                           'curve_export': source(root / record['path'], root),
                           'hxd_source': hxd_source, 'agr_source': agr_source, 'clips': clips}
        if {c['name'] for c in groups[key]['clips']} != {c['name'] for c in model['clips']}:
            raise ValueError('Model/group clip identity mismatch')
    return groups


def guard_facts(cat, ancestors, model_hashes):
    result = []
    for node in ancestors:
        for condition in cat.conditions(node):
            condition['owner_node_offset'] = node['offset']
            condition['owner_path_candidate'] = node['path']
            if condition['type_hash'] == f'{hashstring("PropModelID"):08x}':
                position = condition['file_offset'] + 8
                if position + 4 > cat.strings:
                    raise ValueError('PropModelID candidate payload outside CAT data')
                value = cat.unpack('<I', position)[0]
                condition.update(payload_u32_at_plus_8=value, payload_file_offset=position,
                                 exact_model_name_hash_matches=model_hashes.get(value, []),
                                 comparison_semantics='not_reconstructed_from_native_factory')
            result.append(condition)
    return result


def walk_ancestors(node, ancestors=()):
    chain = (*ancestors, node)
    yield node, chain
    for child in node.get('children', []):
        yield from walk_ancestors(child, chain)


def clip_matches(track, full_hashes, short_names):
    """Exact full HXD hash has identity; a short token has only compatibility.

    Neither result establishes that a model instantiates this CAT or that a
    branch is reachable outside a mission. FXTESTG is absent from these indexes.
    """
    attrs = track['attrs']
    result = []
    if '24' in attrs and attrs['24']['uint'] in full_hashes:
        result.append({'kind': 'exact_hxd_full_name_hash', 'attribute': 24,
                       'file_offset': attrs['24']['file_offset'],
                       'clip_ids': full_hashes[attrs['24']['uint']]})
    for field in ('88', '96'):
        token = attrs.get(field, {}).get('string')
        if token and token.upper() in short_names:
            result.append({'kind': 'short_name_compatibility_only_model_binding_unproven',
                           'attribute': int(field), 'file_offset': attrs[field]['file_offset'],
                           'token': token, 'clip_ids': short_names[token.upper()]})
    return result


def verified_cats(root, known_names):
    """Read only CAT extents from archives; do not hash the 1.9 GB World IMG."""
    catalogs = []
    for relative in ('local/extracted/Stream/World', 'local/extracted/Act/Act'):
        directory = root / relative
        manifest_path = directory / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        image = Path(manifest['source']['image']['path'])
        with image.open('rb') as archive:
            for entry in manifest['entries']:
                if not entry['path'].lower().endswith('.cat'):
                    continue
                path = directory / entry['path']
                data = verified_bytes(path, entry['extracted_sha256'])
                archive.seek(entry['offset_bytes'])
                extent = archive.read(entry['allocated_bytes'])
                if data != extent or digest(extent) != entry['source_extent_sha256']:
                    raise ValueError('CAT differs from original archive extent')
                cat = Cat(path, known_names)
                catalogs.append((cat, {'id': relative.split('/')[-1] + ':' + str(entry['index']),
                    **source(path, root), 'archive_index': entry['index'],
                    'archive_image': manifest['source']['image'],
                    'archive_offset_bytes': entry['offset_bytes'],
                    'archive_extent_bytes': entry['allocated_bytes'],
                    'archive_extent_verified_this_run': True,
                    'archive_full_image_sha256_rehashed_this_run': False,
                    'manifest': source(manifest_path, root),
                    'header': list(cat.h), 'counts': cat.counts}))
    return catalogs


def native_sources(root):
    result = []
    for directory, address, role in [
        ('full-20261020', '5fb550', 'CAT loading'),
        ('full-20261020', '5fb380', 'CAT header allocation counts'),
        ('full-20261020', '5fb110', 'CAT child node tag dispatch'),
        ('full-20261020', '5fa940', 'compressed track attribute inheritance'),
        ('full-20261020', '5faad0', 'condition factory caller'),
        ('full-20261020', '61a650', 'class hash factory lookup'),
        ('bindings-seeded-20261020', '5c5400', 'PAnimSetActionNode Lua binding')]:
        path = root / f'local/native/decompiled/{directory}/functions/0x{address}.c'
        text = path.read_text(encoding='utf-8')
        result.append({**source(path, root), 'address': '0x' + address, 'role': role,
            'rea_evidence_id': re.search(r'Evidence: (ev_[0-9a-f]+)', text)[1],
            'analysis_copy_sha256': re.search(r'Input SHA-256: ([0-9a-f]{64})', text)[1]})
    return result


def garage_script_evidence(root):
    from catalog_world_specials import verified_script_source
    listing = root / 'local/disassembled/main.luasm'
    manifest = listing.parent / 'manifest.json'
    verified = verified_script_source(listing, root / 'local/scripts/main.lur', manifest,
                                     json.loads(manifest.read_text())['files'])
    functions, current = {}, None
    for line_number, line in enumerate(listing.read_text(encoding='utf-8').splitlines(), 1):
        if line.startswith('.function'):
            current = line.split(None, 1)[1]
            functions[current] = []
        if current:
            functions[current].append((line_number, line))
    result = []
    for function in ('main/f32', 'main/f33'):
        lines = functions[function]
        constants = {}
        for number, line in lines:
            match = re.match(r'^\.constant\s+(k\d+)\s+(".*")$', line)
            if match:
                constants[match[1]] = {'value': json.loads(match[2]), 'line': number}
        expected = ('/Global/Door/DoorFunctions/Closing/BIKEGAR' if function.endswith('f32')
                    else '/Global/Door/DoorFunctions/Opening/BIKEGAR')
        if constants['k8']['value'] != expected or constants['k7']['value'] != 'PAnimSetActionNode':
            raise ValueError('Retained garage listing structure changed')
        calls = []
        for index, (number, line) in enumerate(lines):
            if re.match(r'^getglobal\s+r2\s+k7\b', line):
                block = [text for _, text in lines[index:index + 6]]
                patterns = [r'^getglobal\s+r2\s+k7\b', r'^getglobal\s+r3\s+k1\b',
                    r'^gettable\s+r3\s+r3\s+(k2|k3)\b', r'^loadk\s+r4\s+k8\b',
                    r'^loadk\s+r5\s+k9\b', r'^call\s+r2\s+4\s+1\b']
                if len(block) != 6 or not all(re.match(p, b) for p, b in zip(patterns, block)):
                    raise ValueError('Garage direct call no longer matches verified instruction sequence')
                trigger_key = re.match(patterns[2], block[2])[1]
                calls.append({'instruction_line_start': number, 'instruction_line_end': number + 5,
                              'trigger_constant': constants[trigger_key]})
        if len(calls) != 2:
            raise ValueError('Expected exactly two verified garage callback calls')
        result.append({'function': function, 'node_path': expected,
            'action_file': constants['k9'], 'calls': calls,
            'guard_observations': ['Callback compares actor with gPlayer.',
                'Callback compares trigger with _BA_BMXGARAGE or _BMX_GARAGEDOOR.',
                'Callback tests F_OnABike() and AreaGetVisible() against 0 or 62.',
                'An upvalue gates and records opening/closing; surrounding bytecode was not executed.'],
            'free_roam_status': 'global_main_callback_not_mission_script; registration_and_trigger_placement_require_join'})
    return {'source': verified, 'callbacks': result}


def garage_dat_bindings(root, props, script):
    """Bind the two scripted garage targets; shared AGR does not imply placement."""
    from catalog_dat_door_objects import dat_groups
    from catalog_population import fields
    directory = root / 'local/extracted/DAT/Trigger'
    manifest_path = directory / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    models = [m for m in props['models'] if m['model_key'] == '10121_bikegar']
    if len(models) != 1 or models[0]['model_id'] != 10121:
        raise ValueError('Expected original BikeGar model identity is missing')
    model = models[0]
    ide = model['original_ide']
    raw_ide = verified_bytes(ide['path'], ide['sha256']).decode('utf-8-sig').splitlines()
    if raw_ide[ide['line'] - 1] != ide['raw']:
        raise ValueError('BikeGar IDE source line changed')
    result = []
    expected = [('tbusines_doors.dat', 'BA_BMXGarage', 0), ('tBMX.dat', 'BMX_GarageDoor', 62)]
    with Path(manifest['source']['image']['path']).open('rb') as image:
        for filename, trigger_name, area in expected:
            entries = [e for e in manifest['entries'] if e['name'] == filename]
            if len(entries) != 1:
                raise ValueError('Garage DAT archive identity is ambiguous')
            entry = entries[0]
            path = directory / entry['path']
            data = verified_bytes(path, entry['extracted_sha256'])
            image.seek(entry['offset_bytes'])
            extent = image.read(entry['allocated_bytes'])
            if data != extent or digest(extent) != entry['source_extent_sha256']:
                raise ValueError('Garage DAT differs from original IMG extent')
            selected = [(group['perimeter'], trigger) for group in dat_groups(path)
                        for trigger in group['triggers'] if fields(trigger).get('TRIGGERNAME') == trigger_name]
            if len(selected) != 1:
                raise ValueError('Garage trigger is missing or ambiguous')
            perimeter, trigger = selected[0]
            pf, tf = fields(perimeter), fields(trigger)
            if (pf.get('PLACEMENT', '').casefold() != model['name'].casefold()
                    or pf.get('ISDOOR') is not False or tf.get('AREACODE') != area):
                raise ValueError('Garage trigger/model/flag/area binding differs')
            callbacks = []
            for callback in script['callbacks']:
                for call in callback['calls']:
                    constant = call['trigger_constant']['value']
                    if constant.startswith('_') and hashstring(constant[1:]) == hashstring(trigger_name):
                        callbacks.append({'function': callback['function'], 'node_path': callback['node_path'],
                                          **call})
            if len(callbacks) != 2:
                raise ValueError('Garage trigger lacks the two verified scripted callbacks')
            result.append({'id': f'{filename}:{trigger["line"]}', 'source': source(path, root),
                'manifest': source(manifest_path, root), 'archive_index': entry['index'],
                'archive_image': manifest['source']['image'], 'archive_offset_bytes': entry['offset_bytes'],
                'archive_extent_bytes': entry['allocated_bytes'], 'original_extent_verified_this_run': True,
                'perimeter': perimeter, 'trigger': trigger, 'model_key': model['model_key'],
                'model_id': model['model_id'], 'original_ide': ide,
                'source_isdoor_raw': False, 'trigger_name': trigger_name, 'area_code_raw': area,
                'position_source_game_m': tf['POSITION'], 'yaw_pitch_roll_degrees_raw': tf['YAWPITCHROLL'],
                'yaw_degrees_raw': tf['YAW'], 'script_callbacks': callbacks,
                'binding': 'exact_DAT_PLACEMENT_to_IDE_model_and_verified_main_callback_target',
                'runtime_integrated': False,
                'limitations': ['ISDOOR FALSE is retained; this placement is outside the ISDOOR TRUE door catalog.',
                    'SBikeGar shares an animation group but is not the model selected by these two DAT triggers.',
                    'Callback registration, live activation, effective locks and final engine rotation remain separate work.']})
    return result


def action_candidates(references, contexts, files, groups):
    """Concrete development targets, with evidence gaps blocking scheduling."""
    filenames = {item['id']: Path(item['path']).name for item in files}

    def refs(filename, nodes):
        selected = [r for r in references if filenames[r['cat_id']] == filename
                    and r['node_file_offset'] in nodes]
        if {r['node_file_offset'] for r in selected} != set(nodes):
            raise ValueError('Expected contextual CAT evidence is absent')
        return [r['id'] for r in selected]

    def context(filename, nodes, track_class):
        selected = [t for t in contexts if filenames[t['cat_id']] == filename
                    and t['node_file_offset'] in nodes
                    and t['track']['type_hash'] == f'{hashstring(track_class):08x}']
        if {t['node_file_offset'] for t in selected} != set(nodes):
            raise ValueError('Expected contextual track evidence is absent')
        return [{'cat_id': t['cat_id'], 'node_file_offset': t['node_file_offset'],
                 'track_file_offset': t['track_file_offset']} for t in selected]

    return [
        {'id': 'bike_garage_open_close', 'models': ['10121_bikegar'],
         'shared_group_without_proven_binding_to_these_triggers': ['10195_sbikegar'],
         'clip_names': ['BIKEGAR\\OPEN', 'BIKEGAR\\CLOSE'],
         'prop_reference_ids': refs('Door.cat', [5591, 5612]),
         'caller_tracks': context('Door.cat', [2476, 3045], 'Execute'),
         'evidence': 'Verified main/f32 and main/f33 callbacks target BA_BMXGarage and BMX_GarageDoor; original DAT PLACEMENT selects BikeGar 10121 for both, with ISDOOR FALSE. Named Door/BikeGar nodes execute DefaultOpening/DefaultClosing with short OPEN/CLOSE tokens.',
         'known_guard_observations': ['gPlayer comparison', 'F_OnABike() test', 'visible area 0 or 62', 'opening state upvalue', 'CAT candidate PropModelID operands match BikeGar/SBikeGar hashes'],
         'implementable_now': 'Explicit one-shot replay of own BIKEGAR clips; controller state skeleton with original callback guards documented.',
         'blocks_original_behavior': ['Recover F_OnABike exclusions, callback registration, collision and exact condition evaluation.'],
         'free_roam_status': 'global_script_callbacks_and_DAT_model_binding_proven; complete_activation_not_reconstructed'},
        {'id': 'carnival_attendant_gestures', 'models': ['10220_carni01', '10222_carni02', '10223_carni03'],
         'clip_names': ['CARNI01\\CARNY_BARK', 'CARNI01\\CARNY_CMERE', 'CARNI01\\CARNY_PLAYIT', 'CARNI01\\RIDE_CALLOUT'],
         'prop_reference_ids': refs('CarniPropPed.cat', [497, 522, 547, 572, 823, 848, 873, 898]) + refs('CarniPropPedRides.cat', [344, 369, 394, 419]),
         'evidence': 'Exact full HXD hashes in PropAnimation tracks, including ATTRACT referencing RIDE_CALLOUT.',
         'known_guard_observations': ['Candidate IsSpeechPreloaded conditions', 'Ancestor time/state conditions preserved in each reference'],
         'implementable_now': 'Explicit one-shot replay of the four own-group gesture clips.',
         'blocks_original_behavior': ['Model-to-CAT default binding', 'Speech readiness/audio scheduling', 'Time rules, random selection and zone activation'],
         'free_roam_status': 'not_established_by_clip_identity'},
        {'id': 'armoire_hide', 'models': ['10203_armoir'], 'clip_names': ['NLOCK01A\\ARMOIR_HIDE'],
         'prop_reference_ids': refs('NLockA.cat', [7749]),
         'evidence': 'ARMOIR_HIDE short token and a candidate PropModelID record whose +8 operand hashes exactly to Armoir; own IDE group is NLock01A.',
         'known_guard_observations': ['Model-name hash Armoir at CAT byte 10717', 'Ancestor action path and condition records retained'],
         'implementable_now': 'Explicit replay of the own-group hiding animation.',
         'blocks_original_behavior': ['Default action-tree binding', 'Pedestrian animation synchronization, hiding visibility and interactions', 'PropModelID factory/comparison semantics'],
         'free_roam_status': 'not_established'},
        {'id': 'soda_machine_pedestrian_sequence', 'models': ['10117_pxfraffy'], 'clip_names': [],
         'caller_tracks': context('SodaMach.cat', [453, 494, 568, 704, 741, 811, 836, 944], 'Animation'),
         'evidence': 'SodaMach contains USE_FRAFFY/USE_FRAFFY2, HasMoney/HasSoda state paths, pedestrian animation hashes, can attachment and attribute tracks. The exported object has only FXTESTG dummy IDLE.',
         'known_guard_observations': ['Candidate AnimationGroupLoaded', 'Candidate HasEnoughCash', 'Candidate IsPlayer', 'Candidate WeaponModelRequest', 'Candidate WeightedRandom'],
         'implementable_now': 'Plan the pedestrian sequence and can attachment from source records; object remains in bind pose.',
         'blocks_original_behavior': ['Model-to-CAT binding', 'Cost/health parameter semantics', 'Ped animation windows, input, can spawn/attachment, audio and state interpreter'],
         'free_roam_status': 'not_established'},
        {'id': 'snow_mound_damage_stages', 'models': ['10107_snowmnd'],
         'clip_names': ['SNOWMND\\DAMAGE01', 'SNOWMND\\DAMAGE02', 'SNOWMND\\DAMAGE03', 'SNOWMND\\DAMAGE04', 'SNOWMND\\DAMAGE05'],
         'prop_reference_ids': refs('Mound.cat', [220, 241, 262, 283, 304]),
         'evidence': 'Nested Mound CAT nodes use matching short DAMAGE tokens; this is compatible with the group but is not a model-to-CAT binding.',
         'known_guard_observations': ['Candidate ConditionFlag record at byte 342 reused by damage stages'],
         'implementable_now': 'Explicit replay of own damage clips; preserve the original declared order 01,02,03,04,05.',
         'blocks_original_behavior': ['Model-to-CAT binding', 'Damage event meaning/thresholds, collision and state persistence'],
         'free_roam_status': 'not_established'},
        {'id': 'portapoo_context_separation', 'models': ['10189_portapoo'],
         'clip_names': ['PORTAPOO\\FALLDOWN'],
         'prop_reference_ids': refs('StealthDoor.cat', [1074, 1893]),
         'evidence': 'Exact FALLDOWN hash in StealthDoor and separate IDLE hash beneath Mission505; another exact FALLDOWN occurs in mission 5_05 CAT. Hit/entry/exit short-token branches also exist.',
         'known_guard_observations': ['HitLocation, ActionRequest and IsPlayer candidates are retained; Mission505 is a separately observed branch name, not a universal flag'],
         'implementable_now': 'Explicit one-shot clip replay only.',
         'blocks_original_behavior': ['Resolve parametric tipping, damage events and model binding', 'Separate mission-driven use from world interactions before scheduling'],
         'free_roam_status': 'mixed_context_not_established'},
        {'id': 'doorstr_node_name_counterexample', 'models': ['10044_doorstr1', '10045_drpdoor', '10118_celldoor'],
         'prop_reference_ids': refs('Doorstr.cat', [68, 85]),
         'evidence': 'Node labeled OPEN has exact DOORSTR1\\CLOSE hash; child labeled Close has DOORSTR1\\OPEN. Clip identity follows the HXD hash, never the node label.',
         'implementable_now': 'Use hash-based clip mapping during any controlled preview.',
         'blocks_original_behavior': ['Runtime action-tree binding and reachability'],
         'free_roam_status': 'not_established'}]


def catalog(root=ROOT):
    root = Path(root)
    manifest_path = root / 'evidence/skinned-props-export-v04-rgb.json'
    props = json.loads(manifest_path.read_text(encoding='utf-8'))
    garage_script = garage_script_evidence(root)
    garage_bindings = garage_dat_bindings(root, props, garage_script)
    verified_bytes(root / props['animation_library']['path'], props['animation_library']['sha256'])
    groups = load_groups(props, root)
    full_hashes, short_names, model_hashes = defaultdict(list), defaultdict(list), defaultdict(list)
    for model in props['models']:
        model_hashes[hashstring(model['name'])].append(model['model_key'])
    dummy_groups = {(m['source_group']['dictionary'], m['source_group']['group'])
                    for m in props['models'] if m.get('shared_dummy_group')}
    for key, group in groups.items():
        for clip in group['clips']:
            clip['id'] = group['id'] + ':' + str(clip['ordinal'])
            if key not in dummy_groups:
                full_hashes[clip['hxd_record']['name_hash']].append(clip['id'])
                short_names[clip['name'].rsplit('\\', 1)[-1].upper()].append(clip['id'])
    exe = root / 'local/native/Bully.steam-original.exe.unpacked.exe'
    known = defaultdict(set)
    for text in ascii_strings(exe.read_bytes()):
        if len(text) > 1:
            known[hashstring(text)].add(text)
    for model in props['models']:
        known[hashstring(model['name'])].add(model['name'])
    cats = verified_cats(root, known)
    from bully_agr import read_hxd
    mainped_path = Path(props['models'][0]['original_ide']['path']).parents[1] / 'Anim/MAINPED.HXD'
    mainped = {clip['name_hash']: clip for clip in read_hxd(mainped_path)['animations']}
    references, interactions, files, counts = [], [], [], defaultdict(int)
    for cat, cat_source in cats:
        # The filename is another candidate, checked by hash like every label.
        cat.names[hashstring(cat.path.stem)] = cat.names.get(hashstring(cat.path.stem), set()) | {cat.path.stem}
        used = False
        for node, ancestors in walk_ancestors(cat.root):
            for offset in node.get('track_offsets', []):
                track = cat.track(offset)
                counts['tracks_checked'] += 1
                prop_track = track['type_hash'] in (f'{hashstring("PropAnimation"):08x}',
                                                   f'{hashstring("PropAnimationParametric"):08x}')
                regular_animation = track['type_hash'] in (f'{hashstring("Animation"):08x}',
                                                           f'{hashstring("AnimationParametric"):08x}')
                matches = clip_matches(track, full_hashes, short_names if prop_track else {}) if prop_track or regular_animation else []
                if matches:
                    reference = {'id': f'{cat_source["id"]}:{node["offset"]}:{offset}',
                        'cat_id': cat_source['id'], 'node_file_offset': node['offset'],
                        'node_path_candidate': node['path'], 'node_name_source': node['name_source'],
                        'track_file_offset': cat.data + offset, 'track_class_hash': track['type_hash'],
                        'track_domain': 'prop_animation' if prop_track else 'regular_animation',
                        'track_class_name_candidate': track['type'], 'matches': matches,
                        'ancestor_conditions': guard_facts(cat, ancestors, model_hashes),
                        'scheduling': 'not_enabled', 'outside_mission_reachability': 'not_established'}
                    references.append(reference)
                    used = True
                if cat.path.stem in ('SodaMach', 'CarniPropPed', 'CarniPropPedRides', 'Mound', 'NLockA', 'Door', 'AsySwtch', 'KeySwitch', 'StealthDoor'):
                    interesting = (track['type_hash'] in {f'{hashstring(x):08x}' for x in
                        ('Animation', 'Execute', 'Sequence', 'PlayOnProp', 'PropAttachEx', 'PropSetPedAction',
                         'PropUseText', 'AttributesForCash', 'PropFlags', 'PropEntityFlagsTrack')})
                    if interesting:
                        if regular_animation and track['attrs'].get('24', {}).get('uint') in mainped:
                            clip = mainped[track['attrs']['24']['uint']]
                            track['mainped_hxd_animation'] = {key: clip[key] for key in
                                ('name', 'name_hash', 'offset', 'index', 'duration', 'group')}
                        interactions.append({'cat_id': cat_source['id'], 'node_file_offset': node['offset'],
                            'node_path_candidate': node['path'], 'track_file_offset': cat.data + offset,
                            'track': track, 'ancestor_conditions': guard_facts(cat, ancestors, model_hashes),
                            'semantics': 'metadata_only_not_executed'})
                        used = True
        if used:
            files.append(cat_source)
    by_clip = defaultdict(lambda: defaultdict(list))
    for ref in references:
        for match in ref['matches']:
            for clip in match['clip_ids']:
                by_clip[clip][match['kind']].append(ref['id'])
    models = []
    for model in props['models']:
        key = (model['source_group']['dictionary'], model['source_group']['group'])
        rows = []
        for clip in groups[key]['clips']:
            rows.append({'clip_id': clip['id'], 'name': clip['name'],
                         'references': dict(by_clip[clip['id']]), 'autoplay_authorized_by_this_catalog': False})
        models.append({'model_key': model['model_key'], 'name': model['name'],
            'original_ide': model['original_ide'], 'source_nif': model['source_nif'],
            'source_group_id': groups[key]['id'], 'shared_dummy_group': model.get('shared_dummy_group'),
            'placement_count': model['placement_count'], 'zones': model['zones'], 'clips': rows,
            'model_to_CAT_default_binding': 'not_reconstructed',
            'runtime_policy': 'unchanged_existing_four_selected_loops_otherwise_bind_pose'})
    return {'schema': 'rea-bully-prop-actions-static-v1',
        'classification': 'static_clip_and_action_metadata_not_original_gameplay_runtime',
        'sources': {'skinned_props': source(manifest_path, root), 'name_candidate_executable': source(exe, root),
                    'mainped_hxd': source(mainped_path, root), 'generator': source(Path(__file__), root)},
        'summary': {'models': len(models), 'dummy_models': sum(bool(m['shared_dummy_group']) for m in models),
            'non_dummy_models': sum(not m['shared_dummy_group'] for m in models),
            'clip_instances': sum(len(m['clips']) for m in models), 'unique_groups': len(groups),
            'cat_files_checked': len(cats), 'cat_tracks_checked': counts['tracks_checked'],
            'cat_files_with_relevant_metadata': len(files), 'object_clip_references': len(references),
            'prop_animation_references': sum(r['track_domain'] == 'prop_animation' for r in references),
            'exact_full_hash_references': sum(any(m['kind'] == 'exact_hxd_full_name_hash' for m in r['matches']) for r in references),
            'proven_garage_trigger_bindings': len(garage_bindings), 'new_autoplay_clips': 0},
        'groups': list(groups.values()), 'models': models, 'cat_sources': files,
        'prop_animation_references': references, 'context_tracks': interactions,
        'action_candidates': action_candidates(references, interactions, files, list(groups.values())),
        'garage_global_script': garage_script, 'garage_dat_bindings': garage_bindings,
        'native_static_sources': native_sources(root),
        'limitations': ['A full HXD hash proves clip identity in that CAT, not model instantiation or branch reachability.',
            'Short clip tokens are compatibility candidates across groups, never an automatic model-to-CAT binding.',
            'All node/class labels produced from hashes are candidates; raw hashes and byte offsets are retained.',
            'Condition classes, boolean composition and payload meanings require factory/runtime reconstruction.',
            'Mission-specific and global CAT branches remain unscheduled; no trigger is inferred from a name or geometric loop.',
            'The 52 FXTESTG models have no non-dummy object clips; this does not mean their pedestrian interactions are absent.',
            'Only archive CAT extents, group files, AGR slices and HXD sources were reverified; the complete World IMG was not rehashed.',
            'No Unreal, game execution, native analysis, rendering or GPU task was run.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True, help='New JSON path; existing report is never overwritten.')
    args = parser.parse_args()
    if args.report.exists():
        parser.error('Report already exists; choose a new path.')
    # Keep this lightweight analysis subordinate to the user playing a game.
    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    except AttributeError:
        pass
    result = catalog()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'report': str(args.report), 'summary': result['summary']}))


if __name__ == '__main__':
    main()

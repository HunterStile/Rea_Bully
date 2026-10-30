"""Read-only shortlist of source-backed ambient work; no scheduler is enabled."""
import argparse
from collections import defaultdict
import hashlib
import json
from functools import lru_cache
from pathlib import Path
import re
import struct

from catalog_population import read_trigger_blocks, fields, poi_points
from catalog_prop_actions import Cat, ascii_strings, hashstring, source, walk_ancestors
from plan_world_population import activation
from plan_unreal_world import zone_area

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def checked(ref):
    path = ROOT/ref['path']
    if hashlib.sha256(path.read_bytes()).hexdigest() != ref['sha256']:
        raise ValueError('Source changed: ' + str(path))
    return path


@lru_cache(maxsize=None)
def archive_source(path, directory):
    """Verify one extracted extent against both manifest and original IMG bytes."""
    manifest_path = directory/'manifest.json'
    manifest = read(manifest_path)
    relative = path.resolve().relative_to(directory.resolve()).as_posix().casefold()
    entry = next(e for e in manifest['entries'] if Path(e['path']).as_posix().casefold() == relative)
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != entry['extracted_sha256']:
        raise ValueError('Extracted source changed')
    image = Path(manifest['source']['image']['path'])
    with image.open('rb') as stream:
        stream.seek(entry['offset_bytes'])
        extent = stream.read(entry['allocated_bytes'])
    if extent != data or hashlib.sha256(extent).hexdigest() != entry['source_extent_sha256']:
        raise ValueError('Extracted source differs from original archive')
    return {**source(path), 'manifest': source(manifest_path),
            'archive_image': str(image), 'archive_offset_bytes': entry['offset_bytes'],
            'archive_extent_bytes': entry['allocated_bytes'], 'original_extent_verified': True,
            'whole_archive_rehashed': False}


def sitting_cat_witnesses(coverage):
    names = {'NPC_GENERIC\\SIT_DOWN_NPC_BENCH', 'NPC_GENERIC\\SIT_IDLE_NPC_BENCH',
             'NPC_GENERIC\\SIT_DOWN2_NPC', 'NPC_GENERIC\\SIT_IDLE_NPC'}
    clips = {c['hxd_record']['name_hash']: c for c in coverage['clips']
             if c['dictionary'] == 9 and c['hxd_record']['name'] in names}
    known = defaultdict(set)
    for text in ascii_strings((ROOT/'local/native/Bully.steam-original.exe.unpacked.exe').read_bytes()):
        if len(text) > 1:
            known[hashstring(text)].add(text)
    witnesses = []
    for directory in (ROOT/'local/extracted/Stream/World', ROOT/'local/extracted/Act/Act'):
        for path in sorted(directory.rglob('*.cat')):
            data = path.read_bytes()
            if not any(struct.pack('<I', value) in data for value in clips):
                continue
            cat = Cat(path, known)
            rows = []
            for node, ancestors in walk_ancestors(cat.root):
                for offset in node.get('track_offsets', []):
                    track = cat.track(offset)
                    value = track['attrs'].get('24', {}).get('uint')
                    if track['type_hash'] == f'{hashstring("Animation"):08x}' and value in clips:
                        rows.append({'node_offset': node['offset'], 'node_path_candidate': node['path'],
                            'node_label_source': node['name_source'], 'track_offset': cat.data + offset,
                            'animation_operand_offset': track['attrs']['24']['file_offset'],
                            'hxd_name_hash': value, 'clip': clips[value]['hxd_record']['name'],
                            'ancestor_conditions': [condition for ancestor in ancestors
                                for condition in cat.conditions(ancestor)]})
            if rows:
                witnesses.append({'source': archive_source(path, directory), 'references': rows,
                    'test_asset': 'test' in path.stem.lower(),
                    'mission_named_asset': bool(re.match(r'^\d_', path.stem)), 'scheduling_proven': False})
    return witnesses


def placements(world, keys):
    result = []
    for zone in world['zones']:
        for row in zone['placements']:
            if row['model_key'] in keys:
                ipb = checked({'path': zone['source_ipb'], 'sha256': zone['source_ipb_sha256']})
                result.append({'zone': zone['name'], 'area': zone_area(zone)[0],
                    'source_ipb': archive_source(ipb, ROOT/'local/extracted/Stream/World'),
                    'source_ipb_sha256': zone['source_ipb_sha256'], 'instance_index': row['instance_index'],
                    'model_key': row['model_key'], 'transform': row['unreal_transform'],
                    'claim': 'original IPB instance; does not establish action-tree attachment or activation'})
    return result


def clip_facts(clips, coverage):
    result = []
    for clip in clips:
        path = ROOT/clip['source_path']
        original = next(g['source'] for g in coverage['groups'] if
                        g['dictionary'] == clip['dictionary'] and g['group'] == clip['group'])
        checked(original)
        data = path.read_bytes()
        start, size = clip['agr_offset'], clip['agr_size']
        if hashlib.sha256(data[start:start+size]).hexdigest() != clip['agr_sha256']:
            raise ValueError('Original animation slice changed')
        extracted = ROOT/'local/extracted/Stream/World'
        origin = archive_source(path, extracted) if path.resolve().is_relative_to(extracted.resolve()) else {
            **source(path), 'source_kind': 'standalone_owned_Steam_file', 'inventory_sha256_verified': True}
        result.append({'name': clip['hxd_record']['name'], 'dictionary': clip['dictionary'],
            'group': clip['group'], 'agr_source': origin,
            'agr_offset': start, 'agr_size': size, 'agr_sha256': clip['agr_sha256'],
            'duration': clip['duration'], 'slice_verified_this_run': True})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('New diagnostic path required')
    paths = {name: ROOT/path for name, path in {
        'coverage': 'evidence/animation-coverage-v04-final.json',
        'actions': 'evidence/prop-actions-static-v04-final-r2.json',
        'props': 'evidence/skinned-props-export-v04-rgb.json',
        'world': 'local/world-export-with-collectables-v04.json',
        'generator': 'tools/audit_ambient_candidates.py',
        'load_action_trees': 'local/decompiled/LoadActionTrees.lua',
    }.items()}
    coverage, actions, props, world = (read(paths[key]) for key in ('coverage', 'actions', 'props', 'world'))
    for ref in actions['sources'].values():
        checked(ref)
    models = {m['model_key']: m for m in props['models']}
    selected_keys = {'10117_pxfraffy', '10220_carni01', '10222_carni02', '10223_carni03'}
    verified_models = []
    for key in sorted(selected_keys):
        model = models[key]
        for name in ('source_nif', 'source_group', 'original_ide'):
            checked(model[name])
        checked({'path': model['output'], 'sha256': model['output_sha256']})
        imported_path = ROOT/'local/world-import/props'/f'{key}.json'
        imported = read(imported_path)
        if imported['source_sha256'] != model['output_sha256'] or imported['status'] != 'prop_assets_verified':
            raise ValueError('Original prop import mismatch')
        verified_models.append({'model_key': key, 'source_nif': model['source_nif'],
            'source_group': model['source_group'], 'original_ide': model['original_ide'],
            'import_checkpoint': source(imported_path), 'runtime_assets_present': True,
            'runtime_activity_tested': False})
    sitting_dat = ROOT/'local/extracted/DAT/Trigger/ischool_sitting.dat'
    sitting = []
    for block in read_trigger_blocks(sitting_dat):
        if block['type'] == 'POI':
            head = fields(block)
            for point in poi_points(block):
                sitting.append({'name': head['NAME'], 'area_code': head['AREACODE'], 'poi': point,
                                'ue_position_cm': [100*point['POIPOINT'][0], -100*point['POIPOINT'][1], 100*point['POIPOINT'][2]]})
    dat_activation = activation('SchoolHallways', 'ischool_sitting.DAT')
    # Keep source coordinates and flags, while avoiding embedding recovered script bodies.
    dat_activation.pop('call')
    sitting_clips = [c for c in coverage['clips'] if c['dictionary'] == 9 and c['hxd_record']['name'] in {
        'NPC_GENERIC\\SIT_DOWN_NPC_BENCH', 'NPC_GENERIC\\SIT_IDLE_NPC_BENCH',
        'NPC_GENERIC\\SIT_DOWN2_NPC', 'NPC_GENERIC\\SIT_IDLE_NPC'}]
    cats = {c['id']: c for c in actions['cat_sources']}
    selected_cat_ids = {'World:6535', 'World:6536', 'World:6594'}
    cat_sources = [archive_source(checked(cats[key]), ROOT/'local/extracted/Stream/World')
                   for key in sorted(selected_cat_ids)]
    soda_tracks = [{'node_path_candidate': c['node_path_candidate'], 'node_file_offset': c['node_file_offset'],
        'track_file_offset': c['track_file_offset'], 'clip': c['track']['mainped_hxd_animation'],
        'ancestor_conditions': c['ancestor_conditions']}
        for c in actions['context_tracks'] if c['cat_id'] == 'World:6594' and 'mainped_hxd_animation' in c['track']]
    carnival = next(a for a in actions['action_candidates'] if a['id'] == 'carnival_attendant_gestures')
    candidates = [
        {'priority': 1, 'id': 'school_sitting_spot',
         'activity': 'Pedestrian sits at the original second-floor school POI',
         'source_dat': archive_source(sitting_dat, ROOT/'local/extracted/DAT/Trigger'),
         'global_area_activation': dat_activation, 'points': sitting, 'clip_identity': clip_facts(sitting_clips, coverage),
         'cat_witnesses': sitting_cat_witnesses(coverage),
         'ambient_tree_load': {'source': source(paths['load_action_trees']), 'line': 75, 'path': 'Act/Anim/Ambient.act'},
         'native_poi_name_registration': {'source': source(ROOT/'local/native/decompiled/full-20261020/functions/0x488b90.c'),
             'observed': 'Native function references the Sitting_Spot name; this alone does not establish its scheduler.'},
         'non_mission_evidence': 'DAT loaded by registered SchoolHallways area script; POI type Sitting_Spot, no mission callback needed to establish its existence.',
         'scheduler_limits': ['The only school sitting point is AFTERNOON FALSE; enabled MORNING, EARLYCLASS, LATECLASS and LUNCH.',
             'DEFAULT category, male restriction and MAX=1 retained; original native selection and occupancy arbitration remain unrecovered.',
             'CAT clip references do not yet prove the exact seat anchor or branch selected for this POI.'],
         'implementation_proposal': ['Resolve the non-test AI sitting branch to this POI type; retain all source timing flags.',
             'Export source sit-down and idle curves on one already verified male pedestrian with existing skeleton math validation.',
             'Add a bounded sit/stand controller only after seat height, facing and capsule restoration validate in the actual school geometry.'],
         'ready_to_enable': False},
        {'priority': 2, 'id': carnival['id'], 'activity': 'Carnival attendant invitation gestures',
         'model_keys': carnival['models'], 'placements': placements(world, set(carnival['models'])),
         'clip_names': carnival['clip_names'], 'exact_cat_reference_ids': carnival['prop_reference_ids'],
         'non_mission_evidence': 'Original world IPB placements and own animation group are proven; free-roam CAT activation is not proven.',
         'implementation_proposal': ['Extend existing ambient prop state with explicit manual one-shot CARNY_CMERE or CARNY_BARK, returning to the already selected CARNY_IDLE.',
             'Restrict to three verified CARNI models at original IPB poses; label the activation as reconstructed exploration interaction.',
             'Runtime probe checks original clip identity, bone motion, return to idle and no rider/Pinky clips being selected.'],
         'scheduler_limits': carnival['blocks_original_behavior'], 'ready_for_explicit_reconstructed_interaction_prototype': True,
         'ready_to_enable_original_autonomous_scheduler': False},
        {'priority': 3, 'id': 'soda_machine_pedestrian_sequence', 'activity': 'Player uses original soda machine',
         'model_keys': ['10117_pxfraffy'], 'placements': placements(world, {'10117_pxfraffy'}),
         'pedestrian_animation_tracks': soda_tracks,
         'clip_identity': clip_facts([c for c in coverage['clips'] if c['dictionary'] == 9 and
             c['hxd_record']['name'] in {t['clip']['name'] for t in soda_tracks}], coverage),
         'non_mission_evidence': 'Twenty original IPB machine placements; global prop-tree list contains SodaMach.act. Neither fact proves each instance uses that CAT.',
         'object_animation_policy': 'pxFraffy owns dummy FXTESTG IDLE only; keep machine in source bind pose. The activity belongs to pedestrian clips.',
         'implementation_proposal': ['Resolve machine-to-SodaMach binding, then export the exact SODA/SODAALL clip and retain the CAT segment operands; do not add unrelated coin-insertion clips from names alone.',
             'Recover FraffyCan model 362 and original attachment timing before showing pickup/drink.',
             'Implement explicit player input with cancellable positioning and original segment windows; keep cash/health effects disabled until their native parameter meanings verify.'],
         'scheduler_limits': ['Default CAT binding remains unproven.', 'CAT attribute windows are source metadata, not yet executed scheduler semantics.',
             'Can attachment, audio, cash/health effects and exact player alignment need independent verification.'], 'ready_to_enable': False}
    ]
    fountain_path = ROOT/'local/extracted/DAT/Trigger/ischool_fountains.dat'
    fountain_blocks = read_trigger_blocks(fountain_path)
    rejected = {'id': 'school_water_fountains', 'source': archive_source(fountain_path, ROOT/'local/extracted/DAT/Trigger'),
        'original_non_mission_trigger_count': sum(b['type'] == 'TRIGGER' and fields(b).get('ISMISSIONSPECIFIC') is False for b in fountain_blocks),
        'original_placement_name': 'SCFount',
        'blocker': 'No exact SCFount IDE/model binding found in current original Objects configuration or world export. Do not substitute scHall_DkFountain by similar name.',
        'scheduling_enabled': False}
    result = {'schema': 'rea-bully-ambient-candidate-audit-v1', 'diagnostic_only': True,
        'sources': {key: source(path) for key, path in paths.items()}, 'verified_models': verified_models,
        'verified_cat_sources': cat_sources, 'candidates': candidates, 'deferred_alternative': rejected,
        'runtime_changed': False, 'new_autoplay_clips': 0,
        'limits': ['No Unreal instance launched; no source game files changed.',
            'Exact clip identity, original placement and original scheduler reachability are separate proof requirements.',
            'Archive extents were compared to original IMG bytes; complete archive hashes were not recomputed.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({'output': str(args.output), 'candidates': len(candidates),
        'sitting_CATs': len(candidates[0]['cat_witnesses']), 'carnival_placements': len(candidates[1]['placements']),
        'soda_placements': len(candidates[2]['placements']), 'new_autoplay_clips': 0}))


if __name__ == '__main__':
    main()

"""Promote original area entries only after matching engine traversal evidence."""
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SCENE_FILES = ('unreal/Content/Maps/WorldExploration.umap', 'unreal/Content/World/DA_World.uasset')
LABELS = {
    'Art_Room': 'Aula di arte', 'Asylum': 'Manicomio', 'Auditorium': 'Auditorium',
    'Auto_Shop': 'Officina scolastica', 'BMXTrack': 'Pista BMX', 'Barber': 'Barbiere',
    'Bike_Shop_Rich': 'Negozio di biciclette', 'Bio_Lab': 'Laboratorio di biologia',
    'BoxingRing': 'Palestra di pugilato', 'Boys_Dorm': 'Dormitorio maschile',
    'Chem_Lab': 'Laboratorio di chimica', 'Chem_Plant': 'Impianto chimico',
    'Comic_Shop_Rich': 'Negozio di fumetti', 'Freak_Show': 'Freak show',
    'Girls_Dorm': 'Dormitorio femminile', 'GroceryStore': 'Alimentari',
    'Hair_Salon': 'Parrucchiere', 'HouseOfMirrors': 'Casa degli specchi',
    'Island_3': 'Isola 3', 'Janitors_Room': 'Stanza del custode', 'JunkYard': 'Discarica',
    'Library': 'Biblioteca', 'MainMap': 'Bullworth - esterni', 'Midway': 'Luna park',
    'Observatory': 'Osservatorio', 'Pool': 'Piscina', 'Poor_Cloth': 'Negozio di vestiti - quartiere povero',
    'Poor_Hair': 'Barbiere - quartiere povero', 'Prep_House': 'Casa dei prep',
    'Principal': 'Ufficio del preside', 'Rich_Cloth': 'Negozio di vestiti - quartiere ricco',
    'School_Hallways': 'Scuola - corridoi', 'Souvenir': 'Negozio di souvenir',
    'Staff_Room': 'Sala professori', 'TGOKart': 'Pista go-kart', 'Tenement': 'Caseggiato',
    'Test_Area': 'Area di test originale', 'Trailer': 'Roulotte', 'WareHouse': 'Magazzino',
    'classroom': 'Aula', 'iDropS': 'Rifugio dropouts', 'iGrsrS': 'Rifugio greasers',
    'iJockS': 'Rifugio atleti', 'iMGRaceA': 'Pista arcade A', 'iMGRaceB': 'Pista arcade B',
    'iMGRaceC': 'Pista arcade C', 'iPrepS': 'Rifugio prep',
}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot_scene(root=ROOT):
    root = Path(root)
    files = [root/name for name in REQUIRED_SCENE_FILES]
    zones = sorted((root/'unreal/Content/World/Zones').glob('*.umap'))
    if not zones or any(not path.is_file() for path in files):
        raise ValueError('Complete saved world scene required for traversal evidence')
    return {path.relative_to(root).as_posix(): sha256(path) for path in [*files, *zones]}


def promote_entries(plan, report, run, scope, plan_digest, runtime_digest, scene_hashes, *, require_all=False):
    """One proven entry per recovered area; never promote untested coordinates."""
    if (run.get('tour_sha256') != plan_digest or run.get('runtime_sha256') != runtime_digest or
            run.get('scene_asset_hashes') != scene_hashes or not scene_hashes or
            run.get('scene_unchanged_during_run') is not True or
            run.get('runtime_unchanged_during_run') is not True or
            run.get('tour_unchanged_during_run') is not True or
            run.get('exit_code') not in (0, 1) or report.get('error')):
        raise ValueError('Tour evidence belongs to another plan, runtime or saved scene')
    source = {c['name']: c for c in plan['checkpoints']}
    if len(source) != len(plan['checkpoints']):
        raise ValueError('Duplicate source checkpoint identity')
    records = report.get('checkpoints', [])
    reported_names = [r.get('source_checkpoint', {}).get('name') for r in records]
    if len(reported_names) != len(set(reported_names)) or set(reported_names) != set(source):
        raise ValueError('Tour report is incomplete or duplicates a source checkpoint')
    passed = {}
    gates = ('passed', 'grounded_on_recovered_geometry', 'expected_zone_visible',
             'active_area_matches_source', 'grounded_after_walk', 'grounded_after_walk_on_recovered_geometry',
             'same_player_after_walk', 'active_area_matches_source_after_walk', 'expected_zone_visible_after_walk')
    for row in records:
        checkpoint = row['source_checkpoint']
        if checkpoint != source[checkpoint['name']]:
            raise ValueError('Runtime checkpoint differs from its source coordinate/provenance')
        if row.get('passed') is not True:
            continue
        distance = row.get('walked_cm')
        if (any(row.get(gate) is not True for gate in gates) or
                row.get('active_area') != checkpoint['area'] or
                not isinstance(distance, (float, int)) or isinstance(distance, bool) or
                not math.isfinite(distance) or distance <= 50 or
                not row.get('floor_component') or not row.get('floor_component_after_walk')):
            raise ValueError('A passing checkpoint lacks actual area/floor/walk evidence')
        passed.setdefault(checkpoint['area'], []).append(checkpoint)
    expected_areas = {area['name'] for area in scope['areas']}
    if not expected_areas or set(passed) - expected_areas:
        raise ValueError('Tour contains an area outside the source scope')
    missing = expected_areas - set(passed)
    if missing and require_all:
        raise ValueError('No verified navigation entry for: ' + ', '.join(sorted(missing)))
    if not passed:
        raise ValueError('No verified navigation entry is available')
    entries = []
    for area in sorted(passed):
        candidates = passed[area]
        # Prefer campus for the exterior destination, then a stable source id.
        chosen = min(candidates, key=lambda c: (c.get('zone') != 'tschool', c['name']))
        position = chosen['position_cm']
        yaw = chosen.get('yaw_degrees', 0)
        if (len(position) != 3 or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                        and math.isfinite(v) for v in [*position, yaw])):
            raise ValueError('Navigation entry has an invalid original coordinate or facing')
        entries.append({'id': chosen['name'], 'area': area, 'display_name': LABELS.get(area, area),
            'position_feet_cm': position, 'yaw_degrees': yaw, 'source_checkpoint': chosen,
            'runtime_verified': True})
    return sorted(entries, key=lambda entry: (entry['area'] != 'MainMap', entry['display_name'].casefold()))


def verify_references(value):
    """Rehash nested source references without changing historic evidence files."""
    if isinstance(value, dict):
        if set(('path', 'sha256')) <= value.keys():
            if sha256(ROOT/value['path']) != value['sha256']:
                raise ValueError('Navigation source changed: ' + value['path'])
        for child in value.values():
            verify_references(child)
    elif isinstance(value, list):
        for child in value:
            verify_references(child)


def reference(path):
    return {'path': str(Path(path).resolve()), 'sha256': sha256(path)}


def build_manifest(plan_path, report_path, scope_path, *, root=ROOT, require_all=False):
    """Bind successful traversal positions to the still-current local scene."""
    root = Path(root)
    run_path = Path(report_path).with_suffix('.run.json')
    read = lambda path: json.loads(Path(path).read_text(encoding='utf-8-sig'))
    plan, report, run, scope = map(read, (plan_path, report_path, run_path, scope_path))
    scope_ref = reference(scope_path)
    if scope_ref not in plan.get('sources', []):
        raise ValueError('Tour plan does not reference the selected original scope')
    verify_references(plan)
    verify_references(scope)
    runtime = root/'unreal/Binaries/Win64/UnrealEditor-ReaBullyRuntime.dll'
    scene = snapshot_scene(root)
    runtime_digest = sha256(runtime)
    entries = promote_entries(plan, report, run, scope, sha256(plan_path), runtime_digest, scene,
                              require_all=require_all)
    areas = {entry['area'] for entry in entries}
    return {'schema': 'rea-bully-area-navigation-v1',
        'status': 'traversal_verified_navigation_import_pending',
        'entries': entries, 'pending_areas': sorted(a['name'] for a in scope['areas'] if a['name'] not in areas),
        'runtime_sha256': runtime_digest, 'scene_asset_hashes': scene,
        'sources': {name: reference(path) for name, path in (
            ('tour_plan', plan_path), ('tour_report', report_path), ('tour_run', run_path), ('scope', scope_path))},
        'limitations': ['Direct area selection is a reconstructed exploration control, not an original transition graph.',
            'Each entry passed sampled streaming, recovered floor and keyboard walking checks in this saved scene.',
            'This does not verify an entire area, missions or all world activities.']}

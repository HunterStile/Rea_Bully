"""Bind the single verified original baseball-booth floor helper as hidden collision.

The preview's no-draw exclusion is a visibility rule, not proof that the original
COL body should be discarded. NOGO/WALKABLE models are deliberately not promoted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from area_navigation_contracts import reference
from export_nif import placement_matrix
from export_world_collisions import triangle_geometry
from plan_unreal_world import placement_identity, zone_area
from plan_world_collisions import adapter_namespace
from unreal_import_world_collisions import read_source

ROOT = Path(__file__).resolve().parents[1]
MODEL_KEY = '07816_fgrd_nd_baltosop'
MODEL_ID = 7816
ZONE = 'iballtos'
DEFAULT_STATIC = ROOT/'local/world-export-v07-material-recovery.json'
DEFAULT_COLLISIONS = ROOT/'evidence/world-collisions-ue-winding-v07-r2.json'
DEFAULT_IMPORT = ROOT/'local/world-import/collisions-v06-ue'/f'{MODEL_KEY}.json'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def require(value, message):
    if not value:
        raise ValueError(message)


def make_plan(static_path=DEFAULT_STATIC, collisions_path=DEFAULT_COLLISIONS, import_path=DEFAULT_IMPORT):
    static, collisions, imported = map(read, (static_path, collisions_path, import_path))
    require(collisions['sources']['world_catalog'] == reference(static_path), 'Helper and visible world catalogs differ')
    source_model, = [m for m in static['models'] if m['model_key'] == MODEL_KEY]
    collision, = [m for m in collisions['models'] if m['model_key'] == MODEL_KEY]
    zone, = [z for z in static['zones'] if z['name'] == ZONE]
    original, = [p for p in zone['placements'] if p['model_key'] == MODEL_KEY]
    require(source_model['model_id'] == MODEL_ID and source_model['status'] == 'excluded' and
            source_model['reason'] == 'utility/no-draw volume excluded from visible static preview' and
            zone_area(zone)[0] == 'Midway', 'This plan permits only the individually verified original booth floor')
    require(reference(zone['source_ipb'])['sha256'] == zone['source_ipb_sha256'], 'Original helper IPB changed')
    placement_identity(original)
    definition = source_model['definition']
    ide = Path(definition['source'])
    raw_line = ide.read_text(encoding='utf-8-sig').splitlines()[definition['line']-1]
    fields = [f.strip() for f in raw_line.split(',')]
    require(len(fields) == 13 and fields[:3] == ['7816', 'FGRD_ND_baltosOP', 'iballtos'] and fields[5] == '16',
            'Original helper IDE identity/raw fields changed')
    read_source(collision)  # Exact source body, explicit winding adaptation, unchanged vertex bytes.
    prefix = adapter_namespace(collision, collisions, imported)
    expected = {'status': 'collision_assets_verified', 'model_key': MODEL_KEY,
                'source_glb_sha256': collision['output_sha256'], 'source_col': collision['source_col'],
                'source_collision': collision['source_collision'], 'triangles': 2,
                'body_policy': 'complex_as_simple_double_sided', 'nanite': False,
                'replace_visual_collision': True, 'missing_primitive_counts': {'spheres': 0, 'boxes': 0, 'lines_uninterpreted': 0}}
    require(all(imported.get(k) == v for k, v in expected.items()) and
            imported['mesh'].startswith(prefix+MODEL_KEY+'/'), 'Imported original helper collision differs')
    require(collision['source_collision']['native_lookup'] == {'kind': 'id', 'value': MODEL_ID} and
            collision['source_collision']['world_model_candidates'] == [MODEL_KEY], 'Helper COL native identity is ambiguous')
    col_bytes = Path(collision['source_col']['path']).read_bytes()
    require(hashlib.sha256(col_bytes).hexdigest() == collision['source_col']['sha256'], 'Original helper COL changed')
    geometry = triangle_geometry(col_bytes, collision['source_collision'])
    matrix = placement_matrix(original)
    positions = []
    for x, y, z in geometry['positions']:
        game = (x, -z, y)
        world = [sum(matrix[row*4+col]*game[col] for col in range(3))+matrix[row*4+3] for row in range(3)]
        positions.append([100*world[0], -100*world[1], 100*world[2]])
    require(len(positions) == 4 and max(p[2] for p in positions)-min(p[2] for p in positions) < .001,
            'Individually verified flat floor body changed')
    placement = {'model_key': MODEL_KEY, 'mesh': imported['mesh'], 'transform': original['unreal_transform'],
        'source_glb_sha256': collision['output_sha256'],
        'source': {'zone': ZONE, 'instance_index': original['instance_index'], 'source_ipb_sha256': zone['source_ipb_sha256']},
        'collision_only_helper': True, 'replace_visual_collision': False,
        'source_col': collision['source_col'], 'source_collision': collision['source_collision'],
        'triangles': 2, 'status': 'original_helper_import_verified_placement_runtime_pending'}
    return {'schema': 'rea-bully-original-collision-helpers-v1', 'status': 'source_helper_collision_plan_runtime_pending',
        'allowlisted_model_ids': [MODEL_ID],
        'source_reports': {'static_catalog': reference(static_path), 'collision_export': reference(collisions_path),
                           'import_checkpoint': reference(import_path), 'ide': reference(ide),
                           'ipb': reference(zone['source_ipb'])},
        'zones': [{'name': ZONE, 'area': 'Midway', 'source_ipb_sha256': zone['source_ipb_sha256'], 'placements': [placement]}],
        'summary': {'helper_models': 1, 'helper_placements': 1, 'triangles': 2},
        'source_diagnostic': {'ide_line': definition['line'], 'ide_raw_line': raw_line, 'ide_raw_csv_fields': fields,
            'ide_sixth_csv_token_raw': fields[5], 'ide_flag_bit_semantics_not_inferred': True,
            'preview_exclusion_reason': source_model['reason'], 'original_COL_model_id': MODEL_ID,
            'original_body_sha256': collision['source_collision']['sha256'], 'positions_world_cm': positions,
            'triangle_indices_original': [list(face) for face in geometry['triangles']], 'floor_height_world_cm': positions[0][2],
            'original_surface_ids_raw': collision['source_collision']['triangle_surface_ids_raw']},
        'limitations': ['The helper is original collision-only geometry at its exact IPB placement; no visual mesh is added.',
            'No existing visual collision or booth roof COL is disabled by this additive helper plan.',
            'The no-draw preview exclusion is not interpreted as absence of original collision.',
            'Only model ID 7816 is approved here; other no-draw/NOGO/WALKABLE helpers remain unclassified.',
            'The saved world and runtime traversal of the unchanged Midway source checkpoint remain to be tested.']}


def validate(plan):
    sources = plan['source_reports']
    expected = make_plan(sources['static_catalog']['path'], sources['collision_export']['path'], sources['import_checkpoint']['path'])
    require(plan == expected, 'Helper plan differs from its original source and verified import')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static', type=Path, default=DEFAULT_STATIC)
    parser.add_argument('--collisions', type=Path, default=DEFAULT_COLLISIONS)
    parser.add_argument('--import-checkpoint', type=Path, default=DEFAULT_IMPORT)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    plan = make_plan(args.static, args.collisions, args.import_checkpoint)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(plan, stream, indent=2)
    print(json.dumps(plan['summary']))


if __name__ == '__main__':
    main()

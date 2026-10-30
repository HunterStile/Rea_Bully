"""Run independent numeric or actual background Blender checks on the outdoor cast."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from catalog_npc_locomotion import lower_priority
from prepare_npc_runtime_manifest import reference, checked
from town_npc_profiles import TOWN_IDS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--blender', action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else sys.argv[1:])
    if args.report.exists():
        raise FileExistsError('New validation report required')
    manifest = json.loads(args.manifest.read_text())
    ids = [row['ped_id'] for row in manifest['npcs']]
    if (manifest['status'] != 'exported_pending_independent_pose_validation' or
            sorted(ids) != sorted(TOWN_IDS) or manifest['pending']):
        raise ValueError('Expected all eight source-verified outdoor candidates')
    priority = lower_priority()
    if not args.blender:
        from validate_town_npc_math import validate
        result = validate(args.manifest, args.report, priority, expected_ids=tuple(TOWN_IDS))
        print(json.dumps(result['summary']))
        return
    import bpy
    from validate_npc_blender import validate
    if not bpy.app.background:
        raise RuntimeError('Background Blender required')
    result = {'schema': 'rea-bully-town-npc-blender-validation-v1', 'status': 'running', 'passed': False,
              'candidate_manifest': reference(args.manifest), 'validator': reference(__file__),
              'pose_validator': reference(ROOT/'tools/validate_npc_blender.py'),
              'background': True, 'render_performed': False, 'host': bpy.app.version_string,
              'blender_build_hash': bpy.app.build_hash.decode(), 'process_priority': priority,
              'sampling': 'Every animation key plus nine uniformly spaced instants per clip', 'models': [],
              'limitations': ['CPU skin deformation checks do not establish native interpolation, visual parity or Unreal behavior.',
                             'Civilian duplicate idle-name resolution uses the explicit primary IDE-bank project policy.']}
    try:
        for npc in manifest['npcs']:
            checked(npc['export_report'])
            result['models'].append(validate(npc))
        result.update(status='passed_cpu_pose_validation_pending_unreal', passed=True)
    except Exception as exc:
        result.update(status='failed', error=str(exc))
        raise
    finally:
        with args.report.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2)
    print(json.dumps({'passed': result['passed'], 'models': len(result['models'])}))


if __name__ == '__main__':
    main()

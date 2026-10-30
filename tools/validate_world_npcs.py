"""Validate the additional exploration cast; use --blender inside background Blender."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from world_npc_profiles import ADDITIONAL
from prepare_npc_runtime_manifest import reference, checked
from catalog_npc_locomotion import lower_priority


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=ROOT/'evidence/npc-candidate-manifest-v07.json')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--blender', action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else sys.argv[1:])
    if args.report.exists():
        raise FileExistsError('New validation report required')
    priority = lower_priority()
    manifest = json.loads(args.manifest.read_text())
    ids = [n['ped_id'] for n in manifest['npcs']]
    pending = [n['ped_id'] for n in manifest.get('pending', [])]
    if (manifest['status'] != 'exported_pending_independent_pose_validation' or not ids
            or sorted(ids+pending) != sorted(ADDITIONAL)):
        raise ValueError('Additional cast identity set changed')
    if not args.blender:
        from validate_world_npc_math import validate
        result = validate(args.manifest, args.report, priority, expected_ids=tuple(ids))
        print(json.dumps(result['summary']))
        return
    import bpy
    from validate_npc_blender import validate
    if not bpy.app.background:
        raise RuntimeError('CPU pose verification requires background Blender')
    result = {'schema': 'rea-bully-world-npc-blender-validation-v1', 'status': 'running', 'passed': False,
              'candidate_manifest': reference(args.manifest), 'validator': reference(__file__),
              'pose_validator': reference(ROOT/'tools/validate_npc_blender.py'),
              'background': True, 'render_performed': False, 'host': bpy.app.version_string,
              'blender_build_hash': bpy.app.build_hash.decode(), 'process_priority': priority,
              'sampling': 'Every animation key plus nine uniformly spaced instants per clip', 'models': [],
              'limitations': ['CPU skin deformation validation does not establish original interpolation, visuals or Unreal behavior.',
                             'Source rest-matrix loss and partition-weight differences remain documented by numeric validation.']}
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

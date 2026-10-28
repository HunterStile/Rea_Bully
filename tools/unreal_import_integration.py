"""Import the scene's verified NPC, prop, door and audio assets in one UE process."""
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))


def main():
    import unreal
    from unreal_import_npcs import main as npcs
    from unreal_import_world_props import main as props
    from unreal_import_world_doors import main as doors
    from unreal_import_world_audio import main as audio

    report = Path(os.environ['BULLY_UE_REPORT'])
    stages = [('npcs', npcs, 'npc_assets_verified'), ('props', props, 'prop_assets_verified'),
              ('doors', doors, 'door_assets_verified'),
              ('audio', audio, 'audio_assets_imported_pcm_verified_runtime_pending')]
    paths = {name: report.with_name(report.stem+'-'+name+'.json') for name, _, _ in stages}
    if report.exists() or any(path.exists() for path in paths.values()):
        raise RuntimeError('New integration report and stage paths required')
    records = []
    try:
        for name, operation, expected in stages:
            os.environ['BULLY_UE_REPORT'] = str(paths[name])
            unreal.log('REA_INTEGRATION_IMPORT_STAGE ' + name)
            operation()
            payload = paths[name].read_bytes()
            evidence = json.loads(payload.decode('utf-8-sig'))
            if evidence.get('status') != expected:
                raise RuntimeError('Integration import stage did not verify: ' + name)
            records.append({'stage': name, 'report': str(paths[name]),
                            'sha256': hashlib.sha256(payload).hexdigest(), 'status': expected})
            unreal.SystemLibrary.collect_garbage()
    finally:
        os.environ['BULLY_UE_REPORT'] = str(report)
    with report.open('x', encoding='utf-8') as stream:
        json.dump({'status': 'integration_assets_verified', 'stages': records,
            'scene_build_and_runtime_validation': 'pending',
            'engine': unreal.SystemLibrary.get_engine_version()}, stream, indent=2)


if __name__ == '__main__':
    main()

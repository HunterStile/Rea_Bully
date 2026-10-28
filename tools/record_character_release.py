"""Record hashes and verified evidence for the local Jimmy exploration milestone."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'evidence/exploration-release-v0.2.json'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(name):
    return json.loads((ROOT / name).read_text(encoding='utf-8-sig'))


def main():
    if TARGET.exists():
        raise FileExistsError('Preserve the existing release record')
    checks = ['grounded_on_original_geometry', 'keyboard_moves_player', 'jump_rises_and_lands',
              'sprint_increases_speed', 'wall_blocks_capsule', 'reset_respawns_player', 'fall_respawns_player',
              'original_skeletal_character', 'locomotion_changes_original_bones', 'character_faces_movement',
              'first_person_toggle', 'third_person_toggle']
    render = read('evidence/jimmy-exploration-final.json')
    launcher = read('evidence/jimmy-exploration-launcher.json')
    run = read('evidence/jimmy-exploration-final.run.json')
    assert all(result.get('passed') and all(result.get(key) is True for key in checks)
               for result in (render, launcher))
    dll = ROOT / 'unreal/Binaries/Win64/UnrealEditor-ReaBullyRuntime.dll'
    assert run['engine_exit_code'] == 0
    assert run['runtime_sha256_before'] == run['runtime_sha256_after'] == digest(dll)
    log = Path(run['log']).read_text(encoding='utf-8-sig')
    error_counts = {needle: log.count(needle) for needle in
                    ['Error:', 'Failed to compile', 'missing usage flag', 'LogPython: Error']}
    assert not any(error_counts.values()), error_counts
    steam = Path('C:/Program Files (x86)/Steam/steamapps/common/Bully Scholarship Edition/Bully.exe')
    steam_sha = digest(steam)
    assert steam_sha == '36ff31682081a61ba564cb5e09cd6240f79791879cf1fbfb8e6252f75faddc6c'
    paths = [dll, ROOT / 'Esplora scuola.cmd', ROOT / 'unreal/Content/Maps/SchoolExploration.umap']
    paths += sorted((ROOT / 'unreal/Source').rglob('*.h')) + sorted((ROOT / 'unreal/Source').rglob('*.cpp'))
    paths += sorted((ROOT / 'unreal/Source').rglob('*.cs')) + sorted((ROOT / 'unreal/Config').glob('*.ini'))
    paths += sorted((ROOT / 'unreal/Content/Original/Jimmy').rglob('*.uasset'))
    for name in ['local/converted/jimmy/jimmy_final_animated.glb', 'local/jimmy-exploration-final.png',
                 'local/jimmy-exploration-final-moving.png', 'evidence/jimmy-unreal-import-v1.json',
                 'evidence/jimmy-final-export.json', 'evidence/jimmy-final-animation-numeric.json',
                 'evidence/player-animation-heading.json', 'evidence/jimmy-exploration-final.json',
                 'evidence/jimmy-exploration-launcher.json', 'evidence/jimmy-exploration-final.run.json']:
        paths.append(ROOT / name)
    images = []
    for path in paths:
        if path.suffix == '.png':
            dimensions = list(struct.unpack_from('>II', path.read_bytes(), 16))
            assert dimensions == [1600, 900]
            images.append({'path': str(path.relative_to(ROOT)), 'dimensions': dimensions,
                           'review': 'Inspected actual Unreal game capture; original Jimmy, textures, coherent pose and readable HUD.'})
    record = {'recorded_at_utc': datetime.now(timezone.utc).isoformat(), 'version': '0.2.0',
              'status': 'original_jimmy_exploration_verified', 'ORIGINAL_GAME_COMPLETE': False,
              'engine': 'Unreal Engine 5.8.3', 'checks_passed': checks,
              'python_tests': {'command': 'py -3 -m unittest discover -s tests -v', 'passed': 29},
              'render_error_counts': error_counts, 'images': images,
              'original_steam_executable': {'path': str(steam), 'sha256': steam_sha, 'unchanged': True},
              'files': [{'path': str(path.relative_to(ROOT)), 'bytes': path.stat().st_size,
                         'sha256': digest(path)} for path in paths],
              'limitations': ['Original character and four animation clips; movement and jump physics use Unreal CharacterMovement.',
                              'Original collision, AI, mission services, combat, interactions and complete world remain incomplete.',
                              'Fixed simulation step captures are not a performance benchmark or proof of superior fidelity.']}
    TARGET.write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'status': record['status'], 'checks': len(checks), 'files': len(paths), 'record': str(TARGET)}))


if __name__ == '__main__':
    main()

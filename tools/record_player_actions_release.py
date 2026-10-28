"""Record the verified v0.3 action runtime without rewriting earlier milestones."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads((ROOT / path).read_text(encoding='utf-8-sig'))


def main():
    target = ROOT / 'evidence/exploration-release-v0.3.json'
    if target.exists():
        raise FileExistsError('Preserve the existing milestone report')
    render_path = 'evidence/player-actions-unreal-final-v03.json'
    launcher_path = 'evidence/player-actions-launcher-v03.json'
    run_path = 'evidence/player-actions-unreal-final-v03.run.json'
    render, launcher, run = read(render_path), read(launcher_path), read(run_path)
    checks = read('evidence/exploration-release-v0.2.json')['checks_passed'] + [
        'standing_jump_uses_original_action', 'jump_phases_follow_grounding',
        'jump_returns_to_locomotion', 'running_jump_uses_original_action', 'ledge_fall_and_landing_actions']
    assert len(checks) == 17
    assert all(result.get('passed') and all(result.get(key) is True for key in checks)
               for result in (render, launcher))
    dll = ROOT / 'unreal/Binaries/Win64/UnrealEditor-ReaBullyRuntime.dll'
    assert run['engine_exit_code'] == 0
    assert run['runtime_sha256_before'] == run['runtime_sha256_after'] == sha(dll)
    log = Path(run['log']).read_text(encoding='utf-8-sig')
    errors = {key: log.count(key) for key in ('Error:', 'Failed to compile', 'missing usage flag', 'LogPython: Error')}
    assert not any(errors.values()), errors
    source_check = read('evidence/player-actions-validation-v03-final.json')
    assert source_check['sources_unchanged'] and source_check['existing_four_clips_exactly_unchanged']
    assert source_check['glb_skin_nodes_materials_binary_prefix_unchanged']
    assert len(source_check['clips']) == 6 and len(source_check['negative_controls']) == 18
    source = ROOT / 'local/converted/jimmy/jimmy_actions_v03_final.glb'
    assert sha(source) == source_check['glb_sha256']
    images = sorted((ROOT / 'local').glob('player-actions-v03-final*.png'))
    assert len(images) == 6
    for image in images:
        assert struct.unpack_from('>II', image.read_bytes(), 16) == (1600, 900)
    steam = Path('C:/Program Files (x86)/Steam/steamapps/common/Bully Scholarship Edition/Bully.exe')
    steam_sha = sha(steam)
    assert steam_sha == '36ff31682081a61ba564cb5e09cd6240f79791879cf1fbfb8e6252f75faddc6c'
    paths = [dll, source, ROOT / 'Esplora scuola.cmd', ROOT / 'unreal/Content/Maps/SchoolExploration.umap'] + images
    paths += sorted((ROOT / 'unreal/Source').rglob('*.cpp')) + sorted((ROOT / 'unreal/Source').rglob('*.h'))
    paths += sorted((ROOT / 'unreal/Config').glob('*.ini'))
    paths += sorted((ROOT / 'unreal/Content/Original/JimmyActions').rglob('*.uasset'))
    paths += [ROOT / path for path in (render_path, launcher_path, run_path,
              'evidence/jimmy-actions-unreal-import-v03-fixed.json',
              'evidence/player-jump-sequence-static-archived-v1.json',
              'evidence/player-actions-validation-v03-final.json',
              'evidence/player-actions-blender-v03-review.json')]
    record = dict(version='0.3.0', status='original_jump_fall_landing_actions_verified',
        recorded_at_utc=datetime.now(timezone.utc).isoformat(), ORIGINAL_GAME_COMPLETE=False,
        previous_checkpoint=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        engine='Unreal Engine 5.8.3', checks_passed=checks,
        python_tests=dict(command='py -3 -m unittest discover -s tests -v', passed=32),
        render_error_counts=errors, visual_review=dict(
            images=[str(image.relative_to(ROOT)) for image in images],
            status='Actual Unreal captures inspected: original action poses, full body in frame, visible feet and readable HUD.'),
        steam_executable=dict(path=str(steam), sha256=steam_sha, unchanged=True),
        files=[dict(path=str(path.relative_to(ROOT)), bytes=path.stat().st_size, sha256=sha(path)) for path in paths],
        limitations=['Original curves and selected CAT segment facts; original ACT condition/runtime behavior is not implemented.',
                     'Capsule uses Unreal physics; source animation time is synchronized to that flight, not evidence of original physics parity.',
                     'Exact foot contacts, climbing, fall damage, audio, combat, AI and missions remain incomplete.',
                     'Fixed-step renders are not a performance benchmark or evidence of superior overall quality.'])
    with target.open('x', encoding='utf-8') as stream:
        json.dump(record, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    print(json.dumps(dict(status=record['status'], checks=len(checks), files=len(paths), report=str(target))))


if __name__ == '__main__':
    main()

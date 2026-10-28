"""Frame the nearby recovered pedestrian beside Jimmy without moving source placements."""
import json
import os
from pathlib import Path
import unreal

ROOT = Path(__file__).resolve().parents[1]
report = Path(os.environ['BULLY_UE_REPORT'])
if report.exists():
    raise RuntimeError('New framing report required')
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
if not levels.load_level('/Game/Maps/WorldExploration'):
    raise RuntimeError('Integrated world map missing')
starts = [actor for actor in actors.get_all_level_actors() if isinstance(actor, unreal.PlayerStart)]
if len(starts) != 1:
    raise RuntimeError('Expected one player start in integrated world')
before = starts[0].get_actor_rotation()
starts[0].set_actor_rotation(unreal.Rotator(pitch=0, yaw=210, roll=0), False)
world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
if not unreal.EditorLoadingAndSavingUtils.save_map(world, '/Game/Maps/WorldExploration'):
    raise RuntimeError('Could not save integrated camera framing')
with report.open('x', encoding='utf-8') as stream:
    json.dump({'status': 'world_start_framed', 'yaw_degrees': 210,
        'previous_rotation': str(before), 'position': str(starts[0].get_actor_location()),
        'source_npc_placements_changed': False, 'purpose': 'Keep Cornelius visible beside Jimmy at the exploration start.'}, stream, indent=2)

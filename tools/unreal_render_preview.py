"""Render the saved original-asset scene with Unreal's real renderer, then exit."""
import hashlib
import json
import os
from pathlib import Path
import time
import unreal

root = Path(__file__).resolve().parents[1]
image = Path(os.environ.get("BULLY_RENDER_IMAGE", str(root / "local/unreal-campus.png")))
report = Path(os.environ.get("BULLY_RENDER_REPORT", str(root / "evidence/unreal-render.json")))
if image.exists() or report.exists():
    raise RuntimeError("Choose new screenshot and report paths")
level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
map_path = os.environ.get("BULLY_MAP", "/Game/Maps/CampusPreview")
if not level.load_level(map_path):
    raise RuntimeError("Preview map missing")
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
camera = next((actor for actor in actors if isinstance(actor, unreal.CameraActor)
               and actor.get_actor_label() == "REA_OverviewCamera"), None)
if camera is None:
    raise RuntimeError("Overview camera missing")
unreal.AutomationLibrary.finish_loading_before_screenshot()
task = unreal.AutomationLibrary.take_high_res_screenshot(1600, 1000, str(image), camera=camera, delay=2.0)
if not task.is_valid_task():
    raise RuntimeError("Engine could not schedule screenshot")
started = time.monotonic()


def finish(delta):
    if task.is_task_done():
        if image.is_file():
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps({"status": "rendered", "map": map_path,
                "image": str(image), "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                "engine": unreal.SystemLibrary.get_engine_version(),
                "limitations": ["Static scene rendering only; no gameplay validation or original-game visual comparison."]}, indent=2), encoding="utf-8")
        unreal.unregister_slate_post_tick_callback(callback)
        unreal.SystemLibrary.quit_editor()
    elif time.monotonic() - started > 180:
        unreal.log_error("Screenshot exceeded 180 second task deadline")
        unreal.unregister_slate_post_tick_callback(callback)
        unreal.SystemLibrary.quit_editor()


callback = unreal.register_slate_post_tick_callback(finish)

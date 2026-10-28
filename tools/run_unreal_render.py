"""Render a saved preview map using installed Unreal; keep logs and success evidence."""
from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", default="/Game/Maps/SchoolZonePreview")
    parser.add_argument("--image", type=Path, default=ROOT / "local/unreal-school-zone.png")
    parser.add_argument("--report", type=Path, default=ROOT / "evidence/unreal-school-render.json")
    parser.add_argument("--engine", type=Path, default=Path(
        "C:/Program Files/Epic Games/UE_5.8/Engine/Binaries/Win64/UnrealEditor-Cmd.exe"))
    args = parser.parse_args()
    if not args.engine.is_file():
        parser.error("UnrealEditor-Cmd executable missing")
    if args.image.exists() or args.report.exists():
        parser.error("Choose new screenshot/report paths to preserve existing evidence")
    env = os.environ.copy()
    env.update(BULLY_MAP=args.map, BULLY_RENDER_IMAGE=str(args.image.resolve()),
               BULLY_RENDER_REPORT=str(args.report.resolve()))
    env["UE-LocalDataCachePath"] = str(ROOT / "unreal/DerivedDataCache")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log = ROOT / "local" / ("unreal-render-" + stamp + ".log")
    command = [str(args.engine), str(ROOT / "unreal/ReaBully.uproject"), "-RenderOffscreen",
        "-unattended", "-nop4", "-nosplash", "-nosound", "-NoCrashDialog", "-sm6",
        "-DDC=InstalledNoZenLocalFallback", "-LocalDataCachePath=" + env["UE-LocalDataCachePath"],
        "-ExecCmds=py " + str(ROOT / "tools/unreal_render_preview.py"), "-abslog=" + str(log),
        "-userdir=" + str(ROOT / "unreal/Saved/User")]
    log.parent.mkdir(parents=True, exist_ok=True)
    args.image.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with log.with_suffix(".stdout.log").open("xb") as stream:
        try:
            process = subprocess.run(command, cwd=ROOT, env=env, stdout=stream,
                                     stderr=subprocess.STDOUT, timeout=900)
        except subprocess.TimeoutExpired:
            print(f"Render exceeded 900 seconds. Log: {log}")
            return 1
    print(f"Render exit {process.returncode}; image={args.image.is_file()}; report={args.report.is_file()}")
    return 0 if process.returncode == 0 and args.image.is_file() and args.report.is_file() else 1


if __name__ == "__main__":
    raise SystemExit(main())

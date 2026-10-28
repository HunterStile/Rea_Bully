"""Prepare or test the school exploration in the installed Unreal engine."""
from __future__ import annotations
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import struct

ROOT = Path(__file__).resolve().parents[1]
ENGINE = Path("C:/Program Files/Epic Games/UE_5.8/Engine/Binaries/Win64/UnrealEditor-Cmd.exe")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "test", "render"])
    parser.add_argument("--report", type=Path)
    parser.add_argument("--image", type=Path)
    args = parser.parse_args()
    runtime = ROOT / "unreal/Binaries/Win64/UnrealEditor-ReaBullyRuntime.dll"
    if not runtime.is_file():
        parser.error("Compiled exploration runtime missing")
    runtime_sha = hashlib.sha256(runtime.read_bytes()).hexdigest()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report = (args.report or ROOT / f"evidence/exploration-{args.action}-{stamp}.json").resolve()
    image = (args.image or ROOT / f"local/exploration-{stamp}.png").resolve()
    if report.exists() or (args.action == "render" and image.exists()):
        parser.error("New evidence paths required")
    report.parent.mkdir(parents=True, exist_ok=True)
    image.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["UE-LocalDataCachePath"] = str(ROOT / "unreal/DerivedDataCache")
    env["BULLY_UE_REPORT"] = str(report)
    log = ROOT / f"local/exploration-{args.action}-{stamp}.log"
    command = [str(ENGINE), str(ROOT / "unreal/ReaBully.uproject")]
    if args.action == "prepare":
        command += ["-NullRHI", "-ExecutePythonScript=" + str(ROOT / "tools/unreal_prepare_exploration.py")]
    else:
        command += ["/Game/Maps/SchoolExploration", "-game", "-ReaBullySmokeReport=" + str(report),
                    "-ExecCmds=t.MaxFPS 60", "-windowed", "-ResX=1600", "-ResY=900"]
        command += ["-RenderOffscreen", "-sm6", "-ForceRes", "-UseFixedTimeStep", "-FPS=60",
                    "-ReaBullyScreenshot=" + str(image)] if args.action == "render" else ["-NullRHI"]
    command += ["-unattended", "-nop4", "-nosplash", "-nosound", "-NoCrashDialog",
                "-DDC=InstalledNoZenLocalFallback", "-LocalDataCachePath=" + env["UE-LocalDataCachePath"],
                "-abslog=" + str(log), "-userdir=" + str(ROOT / "unreal/Saved/User")]
    print(f"{args.action}: {report}; log={log}", flush=True)
    with log.with_suffix(".stdout.log").open("xb") as stream:
        try:
            process = subprocess.run(command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=1200)
        except subprocess.TimeoutExpired:
            print(f"Engine task timed out. Log: {log}")
            return 1
    record = {"action": args.action, "engine_exit_code": process.returncode, "command": command,
              "log": str(log), "report": str(report), "image": str(image) if args.action == "render" else None,
              "runtime_dll": str(runtime), "runtime_sha256_before": runtime_sha,
              "runtime_sha256_after": hashlib.sha256(runtime.read_bytes()).hexdigest(),
              "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest() if args.action == "render" and image.is_file() else None,
              "image_size_pixels": list(struct.unpack_from(">II", image.read_bytes(), 16)) if args.action == "render" and image.is_file() else None}
    report.with_suffix(".run.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    if process.returncode or not report.is_file():
        print(f"Engine exit {process.returncode}; report exists={report.is_file()}. See {log}")
        return 1
    result = json.loads(report.read_text(encoding="utf-8-sig"))
    passed = result.get("status") == "prepared_and_saved" if args.action == "prepare" else result.get("passed") is True
    if args.action == "render":
        passed = passed and image.is_file()
    print(json.dumps({"passed": passed, "report": str(report)}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

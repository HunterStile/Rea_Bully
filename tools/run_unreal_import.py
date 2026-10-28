"""Import converted original assets using installed Unreal, with caches inside the workspace."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "local/converted/campus_main")
    parser.add_argument("--engine", type=Path,
                        default=Path("C:/Program Files/Epic Games/UE_5.8/Engine/Binaries/Win64/UnrealEditor-Cmd.exe"))
    parser.add_argument("--report", type=Path, default=ROOT / "evidence/unreal-campus.json")
    parser.add_argument("--frame-only", action="store_true", help="Frame existing preview, without importing assets")
    parser.add_argument("--map", default="/Game/Maps/CampusPreview")
    parser.add_argument("--asset-root", default="/Game/Original/Campus")
    args = parser.parse_args()
    if not args.engine.is_file() or not args.source.exists():
        parser.error("Existing UnrealEditor-Cmd and converted GLB source required")
    if args.report.exists():
        parser.error("Select a new --report path to preserve previous verification")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log = ROOT / "local" / ("unreal-import-" + stamp + ".log")
    stdout = log.with_suffix(".stdout.log")
    env = os.environ.copy()
    env["BULLY_GLB"] = str(args.source.resolve())
    env["BULLY_UE_REPORT"] = str(args.report.resolve())
    env["BULLY_MAP"] = args.map
    env["BULLY_ASSET_FOLDER"] = args.asset_root
    env["UE-LocalDataCachePath"] = str(ROOT / "unreal/DerivedDataCache")
    command = [str(args.engine), str(ROOT / "unreal/ReaBully.uproject"), "-unattended", "-nop4",
               "-NullRHI", "-nosplash", "-nosound", "-NoCrashDialog",
               "-DDC=InstalledNoZenLocalFallback", "-LocalDataCachePath=" + env["UE-LocalDataCachePath"],
               "-ExecutePythonScript=" + str(ROOT / "tools" / ("unreal_frame_preview.py" if args.frame_only
                                                                 else "unreal_import_preview.py")),
               "-abslog=" + str(log), "-userdir=" + str(ROOT / "unreal/Saved/User")]
    log.parent.mkdir(parents=True, exist_ok=True)
    with stdout.open("xb") as stream:
        result = subprocess.run(command, env=env, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
    print(json.dumps({"engine_exit_code": result.returncode, "report": str(args.report),
                      "report_written": args.report.is_file(), "log": str(log)}))
    # Unreal may return zero even when its Python script raises: require the report.
    if result.returncode or not args.report.is_file():
        return 1
    report = json.loads(args.report.read_text(encoding="utf-8"))
    expected = "framed_and_saved" if args.frame_only else "imported_and_saved"
    return 0 if report.get("status") == expected else 1


if __name__ == "__main__":
    raise SystemExit(main())

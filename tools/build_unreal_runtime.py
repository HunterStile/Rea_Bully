"""Build the exploration runtime using a workspace-local MSVC/Windows SDK."""
from __future__ import annotations
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ENGINE = Path("C:/Program Files/Epic Games/UE_5.8")
PORTABLE = ROOT / ".tools/portable-build/msvc"


def junction(link: Path, target: Path) -> None:
    if not target.is_dir():
        raise RuntimeError(f"Missing local toolchain directory: {target}")
    if link.exists():
        if link.resolve() != target.resolve():
            raise RuntimeError(f"Unexpected existing SDK path: {link}")
        return
    link.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True,
                   capture_output=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=["ReaBullyEditor", "ReaBully"], default="ReaBullyEditor")
    args = parser.parse_args()
    versions = sorted((PORTABLE / "VC/Tools/MSVC").glob("*"))
    if not versions:
        parser.error("Local MSVC not found; see docs/exploration-runtime.md")
    msvc = versions[-1]
    auto = ROOT / ".tools/unreal-sdk"
    junction(auto / "HostWin64/Win64/VS2022" / msvc.name, msvc)
    junction(auto / "HostWin64/Win64/Windows Kits/10", PORTABLE / "Windows Kits/10")
    # UE's AutoSDK lookup only probes legacy directory names. The target remains
    # the real 4.8 SDK; this is a local lookup alias, not a claimed 4.6.2 install.
    junction(auto / "HostWin64/Win64/Windows Kits/NETFXSDK/4.6.2",
             ROOT / ".tools/portable-build/netfx/Program Files/Windows Kits/NETFXSDK/4.8")
    env = os.environ.copy()
    env["UE_SDKS_ROOT"] = str(auto)
    env["UE-LocalDataCachePath"] = str(ROOT / "unreal/DerivedDataCache")
    command = [str(ENGINE / "Engine/Build/BatchFiles/Build.bat"), args.target, "Win64", "Development",
               "-Project=" + str(ROOT / "unreal/ReaBully.uproject"), "-WaitMutex", "-NoHotReload", "-NoUBA",
               "-Compiler=VisualStudio2022", "-WindowsSdkVersion=10.0.22621.0"]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log = ROOT / "local" / f"unreal-runtime-build-{stamp}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    print(f"Building {args.target}. Log: {log}", flush=True)
    with log.open("xb") as stream:
        process = subprocess.run(command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
    report = {"target": args.target, "exit_code": process.returncode, "command": command,
              "toolchain_root": str(msvc), "environment_scope": "child_process_only",
              "log": str(log), "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest()}
    destination = ROOT / "evidence" / f"unreal-runtime-build-{stamp}.json"
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())

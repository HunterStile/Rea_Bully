"""Run the public REA CLI without installing tools or editing global settings."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def existing_file(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"File not found: {path}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("resources", "analyze", "decompile"))
    parser.add_argument("binary", type=existing_file, help="Explicit installed executable to read")
    parser.add_argument("--node", default=os.environ.get("REA_NODE") or shutil.which("node"))
    parser.add_argument("--rea", default=os.environ.get("REA_CLI"), help="Existing rea.mjs entry point")
    parser.add_argument("--ghidra", help="Existing extracted Ghidra 12.1.x directory")
    parser.add_argument("--java-home", default=os.environ.get("JAVA_HOME"))
    parser.add_argument("--procedure", help="Exact native function address/name for decompile")
    parser.add_argument("--snapshot", type=Path, help="REA native analysis snapshot path")
    parser.add_argument("--output", required=True, type=Path, help="New JSON output file")
    parser.add_argument("--timeout", default=600, type=int, help="CLI deadline in seconds")
    parser.add_argument("--startup-timeout", type=int,
                        help="Ghidra import/autoanalysis deadline in seconds, child process only")
    parser.add_argument("--dry-run", action="store_true", help="Validate paths and print invocation")
    args = parser.parse_args()

    if not args.node or not args.rea:
        parser.error("Supply existing --node and --rea paths (or REA_NODE / REA_CLI).")
    try:
        node, rea = existing_file(args.node), existing_file(args.rea)
    except argparse.ArgumentTypeError as error:
        parser.error(str(error))
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.startup_timeout is not None and not 1 <= args.startup_timeout <= 2147483:
        parser.error("--startup-timeout must be 1..2147483 seconds")
    if args.operation == "decompile" and not args.procedure:
        parser.error("decompile requires --procedure; choose it from observed REA inventory")
    if args.operation != "decompile" and args.procedure:
        parser.error("--procedure applies only to decompile")
    if args.operation == "resources" and args.snapshot:
        parser.error("--snapshot applies only to native analyze/decompile")

    env = os.environ.copy()
    if args.startup_timeout is not None:
        env["REA_GHIDRA_STARTUP_TIMEOUT_MS"] = str(args.startup_timeout * 1000)
    if args.java_home:
        java = Path(args.java_home).expanduser().resolve()
        if not (java / "bin" / "java.exe").is_file() or not (java / "bin" / "javac.exe").is_file():
            parser.error(f"Full Windows JDK not found: {java}")
        env["JAVA_HOME"] = str(java)
    if args.operation != "resources":
        ghidra_value = args.ghidra or env.get("GHIDRA_INSTALL_DIR")
        if not ghidra_value:
            candidates = list((ROOT / ".tools").glob("ghidra_12.1*"))
            candidates += [nested for path in candidates if path.is_dir()
                           for nested in path.glob("ghidra_12.1*_PUBLIC")]
            matches = sorted(path for path in candidates
                             if (path / "support" / "analyzeHeadless.bat").is_file())
            if len(matches) == 1:
                ghidra_value = str(matches[0])
        if not ghidra_value:
            parser.error("No Ghidra installed. Supply --ghidra; this launcher never downloads software.")
        ghidra = Path(ghidra_value).expanduser().resolve()
        if not (ghidra / "support" / "analyzeHeadless.bat").is_file():
            parser.error(f"Ghidra headless launcher not found: {ghidra}")
        env["GHIDRA_INSTALL_DIR"] = str(ghidra)
        env["REA_ANALYSIS_PROVIDER"] = "ghidra"

    operation = "inspect-pe-resources" if args.operation == "resources" else args.operation
    command = [str(node), str(rea), operation, str(args.binary)]
    if args.procedure:
        command.extend(["--procedure", args.procedure])
    if args.operation != "resources":
        command.extend(["--provider", "ghidra"])
    if args.snapshot:
        command.extend(["--snapshot", str(args.snapshot.resolve())])
    command.extend(["--format", "json"])

    output = args.output.expanduser().resolve()
    stderr_path = output.with_suffix(".stderr.txt")
    meta_path = output.with_suffix(".meta.json")
    if any(path.exists() for path in (output, stderr_path, meta_path)):
        parser.error("Output or sidecar already exists; select a new --output to preserve evidence")
    if args.dry_run:
        print(json.dumps({"command": command, "output": str(output)}, indent=2))
        return 0

    with args.binary.open("rb") as source:
        source_digest = hashlib.file_digest(source, "sha256").hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    timed_out = False
    with output.open("xb") as stdout, stderr_path.open("xb") as stderr:
        try:
            result = subprocess.run(command, env=env, cwd=ROOT, stdout=stdout, stderr=stderr,
                                    timeout=args.timeout, check=False)
            exit_code = result.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = 124
    with args.binary.open("rb") as source:
        final_digest = hashlib.file_digest(source, "sha256").hexdigest()
    metadata = {
        "command": command, "started_utc": started,
        "finished_utc": datetime.now(timezone.utc).isoformat(),
        "exit_code": exit_code, "timed_out": timed_out,
        "source_sha256_before": source_digest, "source_sha256_after": final_digest,
        "source_unchanged": source_digest == final_digest,
        "provider_environment": {key: env.get(key) for key in
                                 ("GHIDRA_INSTALL_DIR", "JAVA_HOME", "REA_ANALYSIS_PROVIDER",
                                  "REA_GHIDRA_STARTUP_TIMEOUT_MS")},
        "stdout": str(output), "stderr": str(stderr_path),
    }
    with meta_path.open("x", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2)
        handle.write("\n")
    print(f"REA exit {exit_code}; output: {output}")
    return exit_code if source_digest == final_digest else 1


if __name__ == "__main__":
    sys.exit(main())

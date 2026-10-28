"""Recover readable Lua from locally extracted Bully chunks without executing them.

Requires an operator-supplied unluac JAR. Output is analysis material, not verified
original source. Every result records input/tool/output hashes and diagnostics.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def recover(source: Path, target: Path, java: str, jar: Path, timeout: int) -> dict:
    raw = source.read_bytes()
    result = {"input": str(source.resolve()), "input_sha256": digest(raw),
              "input_size": len(raw), "status": "failed"}
    if raw[:5] != b"\x1bLua\x50":
        result["error"] = "Expected a Lua 5.0 bytecode header"
        return result
    try:
        process = subprocess.run([java, "-Xmx256m", "-jar", str(jar), str(source)],
                                 capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        result["error"] = f"Decompiler exceeded {timeout} seconds"
        return result
    result["exit_code"] = process.returncode
    result["diagnostics"] = process.stderr.decode("utf-8", errors="replace")
    if process.returncode != 0 or not process.stdout.strip():
        result["error"] = "Decompiler failed or returned no source"
        return result
    # Java fork emits CRCRLF on this host. Preserve text, normalize line endings.
    output = process.stdout.replace(b"\r\r\n", b"\n").replace(b"\r\n", b"\n")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as stream:
        stream.write(output)
    result.update(status="decompiled_unverified", output=str(target.resolve()),
                  output_sha256=digest(output), output_size=len(output),
                  contains_goto=bool(re.search(rb"(?m)^\s*goto\s+", output)),
                  original_runtime_compatibility="unverified",
                  behavioral_equivalence="unverified")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path, help="New, absent output directory")
    parser.add_argument("--jar", required=True, type=Path)
    parser.add_argument("--java", default=shutil.which("java"))
    parser.add_argument("--workers", type=int, default=4, choices=range(1, 9))
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()
    if not args.java or not args.jar.is_file():
        parser.error("Existing Java executable and unluac JAR are required")
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    sources = sorted(args.input.rglob("*.lur"))
    if not sources:
        parser.error("No .lur files found")
    if len({p.relative_to(args.input).as_posix().casefold() for p in sources}) != len(sources):
        parser.error("Case-insensitive output filename collision")
    args.output.mkdir(parents=True, exist_ok=False)
    jar = args.jar.resolve()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda p: recover(p, args.output / p.relative_to(args.input).with_suffix('.lua'),
                                                  args.java, jar, args.timeout), sources))
    successful = sum(row["status"] == "decompiled_unverified" for row in results)
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "tool": {"name": "unluac", "jar": str(jar), "sha256": digest(jar.read_bytes())},
        "input_directory": str(args.input.resolve()),
        "summary": {"total": len(results), "decompiled": successful,
                    "failed": len(results) - successful,
                    "outputs_containing_goto": sum(r.get("contains_goto", False) for r in results)},
        "limitations": [
            "Decompiler output is reconstructed source; original names/comments may be absent.",
            "Successful exit does not establish valid syntax or equivalent gameplay behavior.",
            "goto output cannot run in the original Lua 5.0 runtime unchanged.",
            "Native Bully APIs and game data are not supplied by these scripts.",
        ],
        "files": results,
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest["summary"]))
    return 0 if successful == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

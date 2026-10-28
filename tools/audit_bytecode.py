"""Disassemble local Lua chunks and verify byte-identical reassembly.

Logical chunk length is obtained from the assembler output. Original sector
padding is retained in the extracted input and checked separately for zeroes.
No game script is executed.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def audit(source: Path, output: Path, java: str, jar: Path) -> dict:
    original = source.read_bytes()
    output.parent.mkdir(parents=True, exist_ok=True)
    listing = output.with_suffix('.luasm')
    assembled = output.with_suffix('.lur')
    record = {"input": source.name, "input_sha256": sha(original), "verified": False}
    try:
        process = subprocess.run([java, "-Xmx256m", "-jar", str(jar), "--disassemble", str(source)],
                                 capture_output=True, timeout=60)
        if process.returncode:
            record["error"] = process.stderr.decode(errors="replace")
            return record
        listing.write_bytes(process.stdout)
        process = subprocess.run([java, "-Xmx256m", "-jar", str(jar), "--assemble", "--output",
                                  str(assembled), str(listing)], capture_output=True, timeout=60)
        if process.returncode:
            record["error"] = process.stderr.decode(errors="replace")
            return record
        recovered = assembled.read_bytes()
        equal = original[:len(recovered)] == recovered
        padding = original[len(recovered):]
        record.update(logical_bytes=len(recovered), allocated_bytes=len(original),
                      padding_bytes=len(padding), zero_padding=not any(padding),
                      reassembled_sha256=sha(recovered),
                      original_logical_sha256=sha(original[:len(recovered)]),
                      listing=listing.name, listing_sha256=sha(listing.read_bytes()),
                      verified=equal and not any(padding))
    except subprocess.TimeoutExpired:
        record["error"] = "60 second tool deadline exceeded"
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--java", required=True)
    parser.add_argument("--jar", required=True, type=Path)
    args = parser.parse_args()
    sources = sorted(args.input.rglob("*.lur"))
    if not sources:
        parser.error("No .lur inputs")
    if len({p.relative_to(args.input).as_posix().casefold() for p in sources}) != len(sources):
        parser.error("Output filename collision")
    args.output.mkdir(parents=True, exist_ok=False)
    jar = args.jar.resolve(strict=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        files = list(pool.map(lambda p: audit(p, args.output / p.relative_to(args.input), args.java, jar), sources))
    report = {"tool_sha256": sha(jar.read_bytes()),
              "method": "Unluac disassemble -> assemble -> byte comparison, no execution",
              "limitations": ["Roundtrip verifies bytecode preservation, not decompiled Lua source or gameplay behavior."],
              "summary": {"total": len(files), "byte_identical": sum(r["verified"] for r in files)},
              "files": files}
    (args.output / "manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"]))
    return 0 if all(r["verified"] for r in files) else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Resume unexported entries with Ghidra's public per-function timeout API."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from native_batch_export import ROOT, COPY, COPY_SHA, ORIGINAL, ORIGINAL_SHA, GHIDRA, JAVA, REA, digest, save

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--addresses", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seeds", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 60:
        parser.error("Per-function timeout must be 1..60s")
    if digest(COPY) != COPY_SHA or digest(ORIGINAL) != ORIGINAL_SHA:
        parser.error("Source identity mismatch")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    project = output / "project"
    project.mkdir()
    (output / "home").mkdir()
    scripts = output / "scripts"
    scripts.mkdir()
    shutil.copy2(ROOT / "tools/NativeBoundedExport.java", scripts / "NativeBoundedExport.java")
    shutil.copy2(REA.parent.parent / "bridge/ghidra/ReaGhidraApplySeeds.java", scripts / "ReaGhidraApplySeeds.java")
    index_path = ROOT / "evidence/rea" / ("native-bounded-" + output.name + ".json")
    if index_path.exists():
        parser.error("Evidence destination already exists; select a new output directory")
    script = ROOT / "tools/NativeBoundedExport.java"
    addresses = json.loads(args.addresses.read_text(encoding="utf-8"))
    command = [str(GHIDRA / "support/analyzeHeadless.bat"), str(project), "bounded-bully", "-import", str(COPY),
               "-readOnly", "-deleteProject", "-scriptPath", str(scripts),
               "-preScript", "ReaGhidraApplySeeds.java", str(args.seeds.resolve()),
               "-postScript", "NativeBoundedExport.java", str(output), str(args.addresses.resolve()), COPY_SHA, str(args.timeout),
               "-log", str(output / "ghidra.log"), "-scriptlog", str(output / "script.log")]
    metadata = {"classification": "complementary_Ghidra_public_API_not_REA_Evidence", "complete": False,
                "method": "Single Ghidra headless import, seeded profile, DecompInterface.decompileFunction timeout per function",
                "source": str(ORIGINAL), "source_sha256_before": ORIGINAL_SHA,
                "analysis_copy": str(COPY), "analysis_sha256_before": COPY_SHA, "ghidra_version": "12.1.4",
                "requested": len(addresses), "addresses_file": str(args.addresses.resolve()), "addresses_sha256": digest(args.addresses),
                "seed_file": str(args.seeds.resolve()), "seed_sha256": digest(args.seeds), "script_sha256": digest(script),
                "timeout_seconds": args.timeout, "output_directory": str(output), "command": command,
                "started_utc": datetime.now(timezone.utc).isoformat(),
                "baseline_inventory_evidence_id": "ev_76916556908c6b68dd83d589e159f1e69911c9b2652172143930e4b52c0798fe",
                "seeded_inventory_evidence_id": "ev_63026081f7d3de5bfa216b9b4ee454a627342cbcf53271c14dfefb40152b19ce",
                "limitations": ["Complementary exporter results are not REA Evidence records.",
                                "Seeded function labels are caller-declared candidates, not original symbols.",
                                "Timeouts preserve assembly and explicit incomplete pseudocode status.",
                                "Pseudocode is not original source, compilable game code, or runtime equivalence."]}
    save(index_path, metadata)
    environment = os.environ.copy()
    environment["JAVA_HOME"] = str(JAVA)
    environment["JAVA_TOOL_OPTIONS"] = '-Duser.home="' + str(output / "home") + '"'
    for variable, directory in [("APPDATA", "appdata"), ("LOCALAPPDATA", "localappdata")]:
        isolated = output / "home" / directory
        isolated.mkdir()
        environment[variable] = str(isolated)
    print(f"Output: {output}; timeout per funzione: {args.timeout}s", flush=True)
    with (output / "console.log").open("wb") as console:
        result = subprocess.run(command, cwd=ROOT, env=environment, stdout=console, stderr=subprocess.STDOUT, check=False)
    metadata["exit_code"] = result.returncode
    rows = output / "functions.jsonl"
    entries = [json.loads(line) for line in rows.read_text(encoding="utf-8").splitlines() if line] if rows.exists() else []
    metadata["functions"] = entries
    metadata["processed"] = len(entries)
    metadata["result_counts"] = dict(Counter(item["status"] for item in entries))
    metadata["complete"] = result.returncode == 0 and len(entries) == len(addresses)
    metadata["source_sha256_after"] = digest(ORIGINAL)
    metadata["source_unchanged"] = metadata["source_sha256_after"] == ORIGINAL_SHA
    metadata["analysis_sha256_after"] = digest(COPY)
    metadata["analysis_unchanged"] = metadata["analysis_sha256_after"] == COPY_SHA
    metadata["finished_utc"] = datetime.now(timezone.utc).isoformat()
    save(index_path, metadata)
    save(output / "index.json", metadata)
    print(json.dumps({key: metadata[key] for key in ["complete", "processed", "result_counts", "source_unchanged"]}, indent=2), flush=True)
    return 0 if metadata["complete"] and metadata["source_unchanged"] else 1

if __name__ == "__main__":
    sys.exit(main())

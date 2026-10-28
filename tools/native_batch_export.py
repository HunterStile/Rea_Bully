"""Export all recovered functions using one persistent REA/Ghidra MCP session."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT.parent / "Rea_ArcRiders/tools/runtime"
NODE = RUNTIME / "node-v24.18.0-win-x64/node.exe"
REA = RUNTIME / "node_modules/rea-agents/scripts/rea.mjs"
GHIDRA = ROOT / ".tools/ghidra_12.1.4_PUBLI/ghidra_12.1.4_PUBLIC"
JAVA = Path("C:/Program Files/Java/jdk-21")
ORIGINAL = Path("C:/Program Files (x86)/Steam/steamapps/common/Bully Scholarship Edition/Bully.exe")
ORIGINAL_SHA = "36ff31682081a61ba564cb5e09cd6240f79791879cf1fbfb8e6252f75faddc6c"
COPY = ROOT / "local/native/Bully.steam-original.exe.unpacked.exe"
COPY_SHA = "457b595ae70e92031b04954de36525e52dd9ab5eb5f7364480ad2542e8a7d67b"
SAMPLES = ["0x5bfbd0", "0x5bfbb0", "0x5cfb30", "0x5d1450"]

def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)

def unwrap(response):
    if "error" in response:
        raise RuntimeError(json.dumps(response["error"]))
    result = response["result"]
    if result.get("isError"):
        raise RuntimeError(json.dumps(result))
    if "structuredContent" in result:
        return result["structuredContent"]
    for block in result.get("content", []):
        if block.get("type") == "text":
            return json.loads(block["text"])
    raise RuntimeError("No JSON MCP result")

class Session:
    def __init__(self, destination, seed_file=None):
        environment = os.environ.copy()
        environment.update(GHIDRA_INSTALL_DIR=str(GHIDRA), JAVA_HOME=str(JAVA), REA_ANALYSIS_PROVIDER="ghidra",
                           REA_GHIDRA_STARTUP_TIMEOUT_MS="1500000")
        if seed_file:
            environment["REA_GHIDRA_SEED_FILE"] = str(seed_file.resolve())
        self.log = (destination / "server.stderr.txt").open("xb")
        self.notifications = (destination / "notifications.jsonl").open("w", encoding="utf-8")
        self.process = subprocess.Popen([str(NODE), str(REA), "--mcp"], cwd=ROOT, env=environment,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                                        text=True, encoding="utf-8")
        self.inbox = queue.Queue()
        self.next_id = 0
        def read():
            try:
                for line in self.process.stdout:
                    self.inbox.put(json.loads(line))
            except Exception as error:
                self.inbox.put(error)
            finally:
                self.inbox.put(EOFError("REA server output ended"))
        threading.Thread(target=read, daemon=True).start()

    def send(self, request):
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()

    def call(self, method, parameters, timeout=1800):
        self.next_id += 1
        identifier = self.next_id
        self.send({"jsonrpc": "2.0", "id": identifier, "method": method, "params": parameters})
        start, progress = time.monotonic(), 30
        while time.monotonic() - start < timeout:
            try:
                response = self.inbox.get(timeout=5)
            except queue.Empty:
                elapsed = time.monotonic() - start
                if elapsed >= progress:
                    print(f"  {parameters.get('name', method)}: elaborazione {elapsed:.0f}s ...", flush=True)
                    progress += 30
                continue
            if isinstance(response, Exception):
                raise response
            if response.get("id") == identifier:
                return response
            self.notifications.write(json.dumps(response) + "\n")
            self.notifications.flush()
        self.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                   "params": {"requestId": identifier, "reason": "Operator deadline"}})
        raise TimeoutError(f"REA {method} exceeded {timeout}s")

    def tool(self, name, arguments):
        return unwrap(self.call("tools/call", {"name": name, "arguments": arguments}))

    def stop(self):
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=30)
        self.notifications.close()
        self.log.close()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=COPY)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--all", action="store_true", help="Export every analyzed procedure")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--seeds", type=Path, help="Explicit REA Ghidra analysis seed TSV")
    parser.add_argument("--addresses", type=Path, help="JSON array of explicit addresses, instead of --all")
    parser.add_argument("--append-new-from", type=Path,
                        help="Also export inventory entries absent from this prior export's inventory")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.all and args.addresses:
        parser.error("--all and --addresses are mutually exclusive")
    if args.seeds and not args.seeds.is_file():
        parser.error("Seed file not found")
    if not 1 <= args.batch_size <= 128:
        parser.error("--batch-size must be 1..128")
    binary = args.binary.resolve()
    if (ROOT / "local") not in binary.parents:
        parser.error("Target must be an analysis copy under this workspace's local directory")
    for path in [NODE, REA, ORIGINAL, binary, JAVA / "bin/java.exe", GHIDRA / "support/analyzeHeadless.bat"]:
        if not path.is_file():
            parser.error(f"File required: {path}")
    if digest(ORIGINAL) != ORIGINAL_SHA:
        parser.error("Installed executable differs from verified source")
    copy_hash = digest(binary)
    if binary == COPY.resolve() and copy_hash != COPY_SHA:
        parser.error("Analysis copy differs from recorded artifact")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output = (args.output or ROOT / "local/native/decompiled" / run_id).resolve()
    index_path = ROOT / "evidence/rea" / f"native-export-{run_id}.json"
    manifest = {"run_id": run_id, "started_utc": datetime.now(timezone.utc).isoformat(),
                "method": "Public REA MCP, one persistent Ghidra session, batch_decompile",
                "source": str(ORIGINAL), "source_sha256_before": ORIGINAL_SHA,
                "analysis_copy": str(binary), "analysis_sha256_before": copy_hash,
                "rea_version": json.loads((REA.parent.parent / "package.json").read_text())["version"],
                "ghidra_version": "12.1.4", "java_home": str(JAVA), "output_directory": str(output),
                "batch_size": args.batch_size, "export_all": args.all, "complete": False,
                "batches": [], "functions": [],
                "limitations": ["Pseudocode is not original source or a recompilable game.",
                                "Coverage is limited to provider-recovered functions, including imports and thunks.",
                                "Static analysis does not establish runtime behavior or complete code discovery.",
                                "Candidate binding names remain separate from provider-generated labels."]}
    if args.seeds:
        manifest["analysis_seed_file"] = str(args.seeds.resolve())
        manifest["analysis_seed_sha256"] = digest(args.seeds)
    if args.dry_run:
        print(json.dumps(manifest, indent=2))
        return 0
    output.mkdir(parents=True, exist_ok=False)
    records, functions = output / "rea-records", output / "functions"
    records.mkdir()
    functions.mkdir()
    session, opened = None, False
    def checkpoint():
        manifest["source_sha256_after"] = digest(ORIGINAL)
        manifest["source_unchanged"] = manifest["source_sha256_after"] == ORIGINAL_SHA
        manifest["analysis_sha256_after"] = digest(binary)
        manifest["analysis_unchanged"] = manifest["analysis_sha256_after"] == copy_hash
        manifest["succeeded"] = sum(item["status"] == "ok" for item in manifest["functions"])
        manifest["failed"] = len(manifest["functions"]) - manifest["succeeded"]
        save(index_path, manifest)
        save(output / "index.json", manifest)
    print(f"Output: {output}\nIndice: {index_path}", flush=True)
    checkpoint()
    try:
        session = Session(output, args.seeds)
        init = session.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                           "clientInfo": {"name": "Bully native export", "version": "2"}})
        save(records / "initialize.json", init)
        session.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        catalog = session.call("tools/list", {})
        save(records / "tools-list.json", catalog)
        tools = {item["name"] for item in catalog["result"]["tools"]}
        for name in ["open_binary", "binary_overview", "list_procedures", "batch_decompile", "close_binary"]:
            if name not in tools:
                raise RuntimeError(f"REA does not advertise {name}")
        opening = {"path": str(binary), "provider_id": "ghidra"}
        if args.snapshot:
            opening["snapshot_path"] = str(args.snapshot.resolve())
        save(records / "open.json", session.tool("open_binary", opening))
        opened = True
        print("REA: overview ...", flush=True)
        overview = session.tool("binary_overview", {})
        save(records / "overview.json", overview)
        manifest["overview_evidence_id"] = overview.get("evidence_id")
        print("REA: inventario funzioni ...", flush=True)
        procedures = session.tool("list_procedures", {})
        save(records / "procedures.json", procedures)
        manifest["inventory_evidence_id"] = procedures.get("evidence_id")
        inventory = procedures["normalized_result"]
        manifest["inventory_count"] = len(inventory)
        addresses = ([item["address"] for item in inventory] if args.all else
                     json.loads(args.addresses.read_text(encoding="utf-8")) if args.addresses else SAMPLES)
        if not isinstance(addresses, list) or not all(isinstance(item, str) for item in addresses):
            raise ValueError("--addresses must contain a JSON array of address strings")
        if args.append_new_from:
            baseline = json.loads(args.append_new_from.read_text(encoding="utf-8"))
            previous = json.loads((Path(baseline["output_directory"]) / "rea-records/procedures.json").read_text(encoding="utf-8"))
            known = {item["address"].lower() for item in previous["normalized_result"]}
            selected = {item.lower() for item in addresses}
            additions = [item["address"] for item in inventory
                         if item["address"].lower() not in known | selected]
            addresses.extend(additions)
            manifest["baseline_index"] = str(args.append_new_from.resolve())
            manifest["additional_new_procedure_entries"] = len(additions)
        manifest["requested"] = len(addresses)
        checkpoint()
        for offset in range(0, len(addresses), args.batch_size):
            selected = addresses[offset:offset + args.batch_size]
            print(f"REA batch {offset + 1}..{offset + len(selected)}/{len(addresses)} ...", flush=True)
            start = time.monotonic()
            result = session.tool("batch_decompile", {"addresses": selected})
            record = records / f"batch-{offset:06d}.json"
            save(record, result)
            batch = result["normalized_result"]
            manifest["batches"].append({"offset": offset, "count": len(selected),
                                        "evidence_id": result.get("evidence_id"),
                                        "record": str(record.relative_to(output)),
                                        "elapsed_seconds": round(time.monotonic() - start, 3),
                                        "succeeded": batch["succeeded"], "failed": batch["failed"]})
            for item in batch["items"]:
                metadata = {key: value for key, value in item.items() if key != "pseudocode"}
                metadata["evidence_id"] = result.get("evidence_id")
                if item["status"] == "ok":
                    address = item["procedure"].get("address", item["address"])
                    target = functions / (re.sub(r"[^A-Za-z0-9_.-]", "_", address) + ".c")
                    pseudocode = item["pseudocode"].replace("\r\n", "\n").replace("\r", "\n")
                    target.write_text("/* REA/Ghidra pseudocode; not original source.\n"
                                      f" * Input SHA-256: {copy_hash}\n"
                                      f" * Evidence: {result.get('evidence_id')}\n"
                                      f" * Requested address: {item['address']}\n */\n\n" + pseudocode,
                                      encoding="utf-8", newline="\n")
                    metadata.update(file=str(target.relative_to(output)), bytes=target.stat().st_size, sha256=digest(target))
                manifest["functions"].append(metadata)
            checkpoint()
        manifest["complete"] = True
        manifest["finished_utc"] = datetime.now(timezone.utc).isoformat()
        checkpoint()
        print(f"Completato: {manifest['succeeded']} pseudocodici; {manifest['failed']} esiti senza pseudocodice.", flush=True)
        return 0 if manifest["source_unchanged"] and manifest["analysis_unchanged"] else 1
    except (Exception, KeyboardInterrupt) as error:
        manifest["interruption"] = repr(error)
        checkpoint()
        print(f"Interrotto: {error}", file=sys.stderr, flush=True)
        return 1
    finally:
        if session:
            try:
                if opened and session.process.poll() is None:
                    save(records / "close.json", session.tool("close_binary", {}))
                elif opened:
                    save(records / "close-not-completed.json", {"reason": "REA server process already exited",
                                                                "protocol_close_binary_completed": False})
            finally:
                session.stop()

if __name__ == "__main__":
    sys.exit(main())

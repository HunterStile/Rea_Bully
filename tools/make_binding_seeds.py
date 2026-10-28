"""Prepare explicitly labelled function-boundary candidates for readable code only."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from native_batch_export import COPY, COPY_SHA, ORIGINAL_SHA, ROOT

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    bindings = json.loads((ROOT / "evidence/lua_bindings.json").read_text(encoding="utf-8"))
    data = COPY.read_bytes()
    if hashlib.sha256(data).hexdigest() != COPY_SHA or bindings["source_sha256"] != ORIGINAL_SHA:
        raise ValueError("Identity mismatch: do not seed ciphertext or another edition")
    groups = {}
    for table in bindings["tables"]:
        for entry in table["entries"]:
            function = int(entry["function_va"], 16)
            pair = struct.unpack_from("<II", data, entry["table_file_offset"])
            if pair != (int(entry["name_va"], 16), function):
                raise ValueError("Pointer pair changed")
            name = entry["name"].encode("ascii") + b"\0"
            offset = entry["name_file_offset"]
            if data[offset:offset + len(name)] != name or not 0x401000 <= function < 0x8fc000:
                raise ValueError("Candidate string or executable section boundary mismatch")
            groups.setdefault(function, []).append(entry["name"])
    lines, entries = [], []
    for function, names in sorted(groups.items()):
        label = "candidate_lua_" + names[0]
        lines.append(f"0x{function:x}\tfunction\t{label}\n")
        entries.append({"address": hex(function), "analyst_label": label, "candidate_names": names})
    encoded = "".join(lines).encode("utf-8")
    with args.output.open("xb") as stream:
        stream.write(encoded)
    metadata = {"format": "rea-ghidra-seeds-v1", "classification": "caller_declared_analysis_candidates",
                "target_sha256": COPY_SHA, "original_sha256": ORIGINAL_SHA,
                "basis": "Static pointer pairs and ASCII names preserved byte-exact in readable analysis copy",
                "seed_sha256": hashlib.sha256(encoded).hexdigest(), "function_candidates": len(entries),
                "pair_count": sum(len(value) for value in groups.values()),
                "limitations": ["Labels are analyst-assigned candidate names, not original debug symbols.",
                                "Function creation and decompiler success require separate REA seed reports.",
                                "Static pointer tables do not prove Lua registration or runtime behavior."], "entries": entries}
    args.output.with_suffix(".meta.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    priority = ["0x5bfbd0", "0x5bfbb0", "0x5cfb30", "0x5d1450"]
    addresses = priority + [e["address"] for e in entries if e["address"] not in priority]
    args.output.with_suffix(".addresses.json").write_text(json.dumps(addresses, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: metadata[key] for key in ["function_candidates", "pair_count", "seed_sha256"]}, indent=2))

if __name__ == "__main__":
    main()

"""Correlate static Lua binding candidates with exact REA procedure entries."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]

def address(value):
    try:
        return int(value, 16)
    except (ValueError, TypeError):
        return None

def catalog(export_index):
    index = json.loads(export_index.read_text(encoding="utf-8"))
    target = Path(index["analysis_copy"])
    binary = target.read_bytes()
    if hashlib.sha256(binary).hexdigest() != index["analysis_sha256_before"]:
        raise ValueError("Analysis input identity changed")
    bindings = json.loads((ROOT / "evidence/lua_bindings.json").read_text(encoding="utf-8"))
    inventory_path = Path(index["output_directory"]) / "rea-records/procedures.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    procedures = {address(item["address"]): item for item in inventory["normalized_result"]
                  if address(item["address"]) is not None}
    exports = {address(item["address"]): item for item in index["functions"]
               if address(item["address"]) is not None}
    entries = []
    for table in bindings["tables"]:
        for candidate in table["entries"]:
            function_address = address(candidate["function_va"])
            provider = procedures.get(function_address)
            exported = exports.get(function_address)
            pointers = struct.unpack_from("<II", binary, candidate["table_file_offset"])
            pair_matches = pointers == (address(candidate["name_va"]), function_address)
            name_bytes = candidate["name"].encode("ascii") + b"\0"
            start = candidate["name_file_offset"]
            item = {"candidate_name": candidate["name"], "function_address": candidate["function_va"],
                    "table_file_offset": candidate["table_file_offset"],
                    "name_address": candidate["name_va"], "pointer_pair_matches_analysis_copy": pair_matches,
                    "name_bytes_match_analysis_copy": binary[start:start + len(name_bytes)] == name_bytes,
                    "provider_exact_entry_found": provider is not None,
                    "provider_name": provider["value"] if provider else None,
                    "pseudocode_status": exported["status"] if exported else "not_exported",
                    "inventory_evidence_id": inventory["evidence_id"]}
            if exported:
                item["pseudocode_evidence_id"] = exported["evidence_id"]
                if exported["status"] == "ok":
                    item["pseudocode_file"] = str(Path(index["output_directory"]) / exported["file"])
                    item["pseudocode_sha256"] = exported["sha256"]
                else:
                    item["pseudocode_error"] = exported.get("error")
            entries.append(item)
    return {"classification": "static_binding_candidate_correlation_not_runtime_registration_proof",
            "source_binding_artifact": "evidence/lua_bindings.json",
            "original_source_sha256": bindings["source_sha256"],
            "analysis_sha256": index["analysis_sha256_before"], "export_index": str(export_index.resolve()),
            "export_complete": index["complete"], "provider_inventory_evidence_id": inventory["evidence_id"],
            "summary": {"candidate_pairs": len(entries), "unique_candidate_names": len({e["candidate_name"] for e in entries}),
                        "unique_function_addresses": len({e["function_address"] for e in entries}),
                        "pointer_pairs_matching_copy": sum(e["pointer_pair_matches_analysis_copy"] for e in entries),
                        "name_bytes_matching_copy": sum(e["name_bytes_match_analysis_copy"] for e in entries),
                        "provider_exact_entries": sum(e["provider_exact_entry_found"] for e in entries),
                        "pseudocode_status_counts": dict(Counter(e["pseudocode_status"] for e in entries))},
            "limitations": ["Candidate names originate from static pointer pairs, not original debug symbols.",
                            "An exact provider entry match corroborates a function boundary, not Lua registration or behavior.",
                            "Duplicate function addresses and standard-library candidates remain represented.",
                            "No runtime execution or behavioral equivalence is established."], "entries": entries}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("index", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = catalog(args.index)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps(result["summary"], indent=2))

"""Summarize native exports, separating expected external no-body results."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

def summarize(index_path, verify=False):
    index = json.loads(index_path.read_text(encoding="utf-8"))
    output = Path(index["output_directory"])
    inventory = json.loads((output / "rea-records/procedures.json").read_text(encoding="utf-8"))
    by_address = {entry["address"].lower(): entry for entry in inventory["normalized_result"]}
    classification = Counter()
    internal_failures, expected_bodyless, verification_errors, warning_functions = [], [], [], []
    warning_markers = Counter()
    bytes_written = 0
    for entry in index["functions"]:
        address = entry["address"]
        procedure = by_address.get(address.lower(), {})
        external = procedure.get("procedure", {}).get("external", address.startswith("EXTERNAL:"))
        if entry["status"] == "ok":
            category = "external_pseudocode" if external else "internal_pseudocode"
            target = output / entry["file"]
            bytes_written += entry["bytes"]
            contents = target.read_text(encoding="utf-8")
            warnings = []
            for comment in re.findall(r"/\*.*?\*/|//[^\r\n]*", contents, re.DOTALL):
                markers = sorted(set(re.findall(r"\b(?:WARNING|UNIMPLEMENTED)\b", comment, re.IGNORECASE)))
                if markers:
                    warning_markers.update(marker.upper() for marker in markers)
                    warnings.append({"markers": [marker.upper() for marker in markers],
                                     "excerpt": " ".join(comment.split())[:300]})
            if warnings:
                warning_functions.append({"address": address, "provider_name": entry.get("procedure", {}).get("name"),
                                          "file": str(target), "comments": warnings})
            if verify and (not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != entry["sha256"]):
                verification_errors.append(str(target))
        elif external:
            category = "no_body_expected_external"
            expected_bodyless.append({"address": address, "name": procedure.get("value"),
                                      "evidence_id": entry["evidence_id"]})
        else:
            category = "internal_decompiler_failure"
            internal_failures.append(entry)
        classification[category] += 1
    overview = json.loads((output / "rea-records/overview.json").read_text(encoding="utf-8"))
    return {"export_index": str(index_path.resolve()), "complete": index["complete"],
            "method": index["method"], "source_sha256": index["source_sha256_before"],
            "analysis_sha256": index["analysis_sha256_before"],
            "source_unchanged": index["source_unchanged"], "analysis_unchanged": index["analysis_unchanged"],
            "overview_evidence_id": index.get("overview_evidence_id"),
            "inventory_evidence_id": inventory["evidence_id"],
            "inventory_count": len(by_address),
            "inventory_external": sum(e.get("procedure", {}).get("external", False) for e in by_address.values()),
            "inventory_internal": sum(not e.get("procedure", {}).get("external", False) for e in by_address.values()),
            "inventory_thunks": sum(e.get("procedure", {}).get("thunk", False) for e in by_address.values()),
            "requested_count": index.get("requested"), "processed_count": len(index["functions"]),
            "result_categories": dict(classification), "pseudocode_bytes": bytes_written,
            "output_directory": str(output), "closed_session_record_exists": (output / "rea-records/close.json").is_file(),
            "file_hashes_verified": verify, "file_verification_errors": verification_errors,
            "pseudocode_warning_function_count": len(warning_functions),
            "pseudocode_warning_marker_counts": dict(warning_markers),
            "warning_scan_method": "Literal WARNING/UNIMPLEMENTED markers in C comment-pattern matches; no new native analysis",
            "functions_with_warning_comments": warning_functions,
            "analysis_profile": overview["analysis_profile"],
            "expected_external_no_body": expected_bodyless, "internal_failures": internal_failures,
            "limitations": index["limitations"] + ["Expected external imports have no DLL implementation body in this executable.",
                                                     "Function-boundary seeds are caller-declared analysis candidates, not recovered debug symbols."]}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("index", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--verify-files", action="store_true")
    args = parser.parse_args()
    result = summarize(args.index, args.verify_files)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({key: result[key] for key in ["complete", "inventory_count", "inventory_external", "processed_count", "result_categories", "pseudocode_bytes", "file_verification_errors"]}, indent=2))

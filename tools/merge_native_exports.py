"""Audit coverage across REA inventories and the bounded complementary export."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from native_batch_export import ROOT, ORIGINAL, ORIGINAL_SHA, COPY, COPY_SHA, digest, save

def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def key(value):
    return hex(int(value, 16)) if value.lower().startswith("0x") else value.lower()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--seeded", required=True, type=Path)
    parser.add_argument("--bounded", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists")
    baseline, seeded, bounded = load(args.baseline), load(args.seeded), load(args.bounded)
    inventory, results = {}, {}
    for label, metadata in [("rea_baseline", baseline), ("rea_seeded", seeded)]:
        origin = Path(metadata["output_directory"])
        record = load(origin / "rea-records/procedures.json")
        for item in record["normalized_result"]:
            address = key(item["address"])
            merged = inventory.setdefault(address, {"address": address, "inventories": [], "external": item.get("procedure", {}).get("external", False)})
            merged["inventories"].append({"source": label, "evidence_id": record["evidence_id"], "provider_name": item["value"]})
        for item in metadata["functions"]:
            address = key(item["address"])
            result = {**item, "source": label}
            if item["status"] == "ok":
                result["file"] = str(origin / item["file"])
            elif inventory.get(address, {}).get("external"):
                result["status"] = "no_body_expected_external"
            results[address] = result
    origin = Path(bounded["output_directory"])
    verification_errors, bounded_warnings = [], {}
    for item in bounded.get("functions", []):
        address = key(item["address"])
        result = {**item, "source": "ghidra_bounded_complement"}
        for file_field, hash_field in [("file", "sha256"), ("assembly_file", "assembly_sha256")]:
            if file_field in item:
                target = origin / item[file_field]
                result[file_field] = str(target)
                if not target.is_file() or digest(target) != item[hash_field]:
                    verification_errors.append(str(target))
                if file_field == "file" and target.is_file():
                    warnings = []
                    for comment in re.findall(r"/\*.*?\*/|//[^\r\n]*", target.read_text(encoding="utf-8"), re.DOTALL):
                        markers = sorted(set(re.findall(r"\b(?:WARNING|UNIMPLEMENTED)\b", comment, re.IGNORECASE)))
                        if markers:
                            warnings.append({"markers": [m.upper() for m in markers], "excerpt": " ".join(comment.split())[:300]})
                    if warnings:
                        bounded_warnings[address] = warnings
        results[address] = result
    missing = sorted(set(inventory) - set(results))
    entries = [{**entry, "result": results.get(address)} for address, entry in sorted(inventory.items())]
    categories = Counter(entry["result"]["status"] if entry["result"] else "not_attempted" for entry in entries)
    source_warnings = {"ghidra_bounded_complement": bounded_warnings}
    for label, name in [("rea_baseline", "native-baseline-interrupted-summary.json"),
                        ("rea_seeded", "native-seeded-verified-summary.json")]:
        source_warnings[label] = {key(e["address"]): e["comments"] for e in load(ROOT / "evidence/rea" / name)["functions_with_warning_comments"]}
    warning_functions, marker_counts = [], Counter()
    for entry in entries:
        result = entry["result"]
        if result and result["status"] == "ok":
            warnings = source_warnings.get(result["source"], {}).get(entry["address"], [])
            if warnings:
                warning_functions.append({"address": entry["address"], "source": result["source"], "comments": warnings})
                for warning in warnings:
                    marker_counts.update(warning["markers"])
    report = {"classification": "multi_profile_static_native_recovery_not_original_source",
              "source_sha256": ORIGINAL_SHA, "analysis_sha256": COPY_SHA,
              "source_unchanged": digest(ORIGINAL) == ORIGINAL_SHA, "analysis_unchanged": digest(COPY) == COPY_SHA,
              "inputs": [{"path": str(path.resolve()), "sha256": digest(path)} for path in [args.baseline, args.seeded, args.bounded]],
              "summary": {"inventory_union_entries": len(inventory), "internal_entries": sum(not i["external"] for i in inventory.values()),
                          "external_entries": sum(i["external"] for i in inventory.values()), "status_counts": dict(categories),
                          "unattempted_entries": len(missing), "new_artifact_verification_errors": len(verification_errors),
                          "pseudocode_functions_with_warning_comments": len(warning_functions),
                          "warning_marker_counts": dict(marker_counts)},
              "complete_inventory_attempt_coverage": not missing and bounded["complete"],
              "new_artifact_verification_errors": verification_errors, "unattempted_addresses": missing,
              "baseline_file_verification": "evidence/rea/native-baseline-interrupted-summary.json",
              "seeded_file_verification": "evidence/rea/native-seeded-verified-summary.json",
              "functions_with_warning_comments": warning_functions,
              "limitations": ["Inventory union is not proof that all game functions have been found.",
                              "Multiple explicit analysis profiles contribute different function boundaries and names.",
                              "Seeded labels are caller assertions; default-only entries are retained separately.",
                              "Bounded Ghidra exports are complementary results, not REA Evidence records.",
                              "Status ok means usable decompiler text, not behavioral equivalence or a compilable game."], "entries": entries}
    save(args.output, report)
    summary = {k: v for k, v in report.items() if k not in ("entries", "functions_with_warning_comments")}
    save(args.output.with_suffix(".summary.json"), summary)
    print(json.dumps(report["summary"], indent=2))

if __name__ == "__main__":
    main()

"""Link observed GETGLOBAL operands in verified disassembly to static PE table candidates."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("disassembly", type=Path)
    parser.add_argument("bindings", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    binding_source = json.loads(args.bindings.read_text(encoding="utf-8"))
    bindings = defaultdict(list)
    for table in binding_source["tables"]:
        for entry in table["entries"]:
            bindings[entry["name"]].append(entry["function_va"])
    references = defaultdict(Counter)
    declarations = defaultdict(set)
    files = sorted(args.disassembly.rglob("*.luasm"))
    for file in files:
        for line in file.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r'^(getglobal|setglobal)\s+r\d+\s+k\d+\s*;\s*k\d+ = "([A-Za-z_][A-Za-z_0-9]*)"$', line)
            if match:
                opcode, name = match.groups()
                if opcode == "getglobal":
                    references[name][file.relative_to(args.disassembly).with_suffix('').as_posix()] += 1
                else:
                    declarations[name].add(file.relative_to(args.disassembly).with_suffix('').as_posix())
    records = []
    for name, occurrences in references.items():
        records.append({"name": name, "getglobal_instructions": sum(occurrences.values()),
                        "scripts": dict(sorted(occurrences.items())),
                        "candidate_function_vas": sorted(set(bindings.get(name, []))),
                        "defined_by_available_scripts": sorted(declarations.get(name, [])),
                        "implementation_status": "not_implemented_in_new_engine"})
    records.sort(key=lambda r: (-r["getglobal_instructions"], r["name"]))
    report = {"source_executable_sha256": binding_source["source_sha256"],
              "summary": {"scripts_examined": len(files), "distinct_global_reads": len(records),
                          "reads_matching_static_binding_names": sum(bool(r["candidate_function_vas"]) for r in records)},
              "method": "GETGLOBAL identifier operands from unluac listings, matched by exact name",
              "limitations": ["Global reads do not establish function calls or observed execution.",
                              "Candidate table names are not proof of Lua registration.",
                              "Dynamic global accesses and unresolved aliases are outside this inventory.",
                              "No native gameplay service has been implemented by this inventory."],
              "references": records}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(report["summary"]))


if __name__ == "__main__":
    main()

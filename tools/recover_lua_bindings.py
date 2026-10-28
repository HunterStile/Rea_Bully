"""Find static {ASCII name pointer, code pointer} table candidates in PE32.

This does not disassemble functions or prove that a table is registered with Lua.
Addresses are tied to the exact executable SHA-256, never copied from another build.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

from inspect_bully import inspect_pe


def recover(path: Path) -> dict:
    data = path.read_bytes()
    pe = inspect_pe(path)
    if pe.get("architecture") != "x86":
        raise ValueError("Expected PE32 x86")
    base = int(pe["image_base"], 16)
    sections = pe["sections"]

    def section_at(address: int):
        return next((s for s in sections if base + s["rva"] <= address
                     < base + s["rva"] + min(s["virtual_size"], s["file_size"])), None)

    def pair_at(offset: int):
        if offset + 8 > len(data):
            return None
        name_address, code_address = struct.unpack_from("<II", data, offset)
        name_section = section_at(name_address)
        code_section = section_at(code_address)
        if not name_section or not code_section or code_section["name"] != ".text":
            return None
        if name_section["name"] not in (".rdata", ".data"):
            return None
        name_offset = name_section["file_offset"] + name_address - base - name_section["rva"]
        end = data.find(b"\0", name_offset, min(len(data), name_offset + 100))
        if end < 0:
            return None
        name = data[name_offset:end]
        if not re.fullmatch(rb"[A-Za-z_][A-Za-z_0-9]{1,95}", name):
            return None
        return {"name": name.decode("ascii"), "table_file_offset": offset,
                "name_file_offset": name_offset, "name_va": hex(name_address),
                "function_va": hex(code_address), "function_file_offset":
                code_section["file_offset"] + code_address - base - code_section["rva"]}

    tables = []
    for section in sections:
        if section["name"] not in (".data", ".rdata"):
            continue
        offset = section["file_offset"]
        limit = min(len(data), offset + section["file_size"])
        while offset + 8 <= limit:
            run = []
            cursor = offset
            while cursor + 8 <= limit:
                pair = pair_at(cursor)
                if pair is None:
                    break
                run.append(pair)
                cursor += 8
            if len(run) >= 4:
                tables.append({"file_offset": offset, "section": section["name"],
                               "entry_count": len(run), "entries": run})
                offset = cursor
            else:
                offset += 4
    return {"source": str(path.resolve()), "source_sha256": hashlib.sha256(data).hexdigest(),
            "method": "Static PE32 pointer pairs, at least 4 contiguous identifier/code pairs",
            "classification": "candidate_registration_tables",
            "summary": {"tables": len(tables), "entries": sum(t["entry_count"] for t in tables)},
            "limitations": ["Table registration and function behavior are unverified.",
                            "Includes possible non-game or standard-library bindings.",
                            "Runtime-generated, singleton, and differently laid out tables are not recovered."],
            "tables": tables}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = recover(args.executable)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result["summary"]))


if __name__ == "__main__":
    main()

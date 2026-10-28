"""Read PE layout, section entropy and selected byte ranges without executing it."""

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import struct


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    length = len(data)
    return -sum((count / length) * math.log2(count / length) for count in Counter(data).values())


def inspect(path: Path) -> dict:
    data = path.read_bytes()
    if data[:2] != b"MZ":
        raise ValueError("Missing MZ header")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("Missing PE header")
    machine, count = struct.unpack_from("<HH", data, pe + 4)
    optional_size = struct.unpack_from("<H", data, pe + 20)[0]
    optional = pe + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    if magic != 0x10B:
        raise ValueError("This inspector admits PE32 only")
    image_base = struct.unpack_from("<I", data, optional + 28)[0]
    entry_rva = struct.unpack_from("<I", data, optional + 16)[0]
    section_start = optional + optional_size
    sections = []
    for index in range(count):
        offset = section_start + index * 40
        name = data[offset:offset + 8].split(b"\0")[0].decode("ascii", errors="replace")
        virtual_size, rva, raw_size, raw_offset = struct.unpack_from("<IIII", data, offset + 8)
        flags = struct.unpack_from("<I", data, offset + 36)[0]
        if raw_offset + raw_size > len(data):
            raise ValueError(f"Section outside file: {name}")
        raw = data[raw_offset:raw_offset + raw_size]
        windows = [entropy(raw[start:start + 4096]) for start in range(0, len(raw), 4096)
                   if len(raw[start:start + 4096]) == 4096]
        sections.append({"name": name, "rva": hex(rva), "va": hex(image_base + rva),
                         "virtual_bytes": virtual_size, "file_offset": raw_offset, "file_bytes": raw_size,
                         "characteristics": hex(flags), "executable": bool(flags & 0x20000000),
                         "readable": bool(flags & 0x40000000), "writable": bool(flags & 0x80000000),
                         "shannon_entropy_bits_per_byte": entropy(raw),
                         "zero_byte_fraction": raw.count(0) / len(raw) if raw else None,
                         "complete_4k_windows": len(windows),
                         "window_entropy_min": min(windows) if windows else None,
                         "window_entropy_max": max(windows) if windows else None,
                         "first_32_bytes_hex": raw[:32].hex()})

    def sample(name: str, va: int) -> dict:
        rva = va - image_base
        for section in sections:
            relative = rva - int(section["rva"], 16)
            if 0 <= relative < section["file_bytes"]:
                file_offset = section["file_offset"] + relative
                return {"name": name, "va": hex(va), "rva": hex(rva), "section": section["name"],
                        "file_offset": file_offset, "bytes": 32,
                        "hex": data[file_offset:file_offset + 32].hex()}
        return {"name": name, "va": hex(va), "file_backed_mapping": None}

    return {"source": str(path.resolve()), "source_sha256": hashlib.sha256(data).hexdigest(),
            "source_bytes": len(data), "method": "Read-only PE32 header/range parser and Shannon byte entropy",
            "classification": "local_static_observations_not_REA_Evidence", "machine": hex(machine),
            "image_base": hex(image_base), "entrypoint_rva": hex(entry_rva),
            "entrypoint_va": hex(image_base + entry_rva), "sections": sections,
            "samples": [sample("PE_entrypoint", image_base + entry_rva),
                        sample("candidate_Wait", 0x5BFBD0), sample("candidate_GetTimer", 0x5BFBB0),
                        sample("candidate_PedCreatePoint", 0x5CFB30),
                        sample("candidate_PlayerSetControl", 0x5D1450)],
            "limitations": ["Entropy and section names do not prove encryption, compression, DRM or a specific packer.",
                            "Byte samples are file bytes, not a running process memory observation.",
                            "Candidate binding names are supplied by static pointer tables, not recovered symbols.",
                            "No target execution, process attachment, byte transformation or license-state change."]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = inspect(args.binary)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    print(json.dumps(result, indent=2))

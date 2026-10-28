"""Read-only inventory of a locally installed Bully copy; writes metadata only."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import struct


def header(data: bytes) -> dict:
    return {
        "hex": data[:64].hex(" "),
        "ascii": "".join(chr(value) if 32 <= value < 127 else "." for value in data[:64]),
    }


def cstring(data: bytes, offset: int) -> str:
    end = data.find(b"\0", offset)
    if end < 0:
        end = len(data)
    return data[offset:end].decode("ascii", errors="replace")


def inspect_pe(path: Path) -> dict:
    data = path.read_bytes()
    if data[:2] != b"MZ":
        return {"error": "Not a DOS/PE executable"}
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe_offset:pe_offset + 4] != b"PE\0\0":
        return {"error": "Missing PE signature"}
    machine, section_count, timestamp = struct.unpack_from("<HHI", data, pe_offset + 4)
    optional_size = struct.unpack_from("<H", data, pe_offset + 20)[0]
    optional = pe_offset + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    is_64 = magic == 0x20B
    if magic not in (0x10B, 0x20B):
        return {"error": "Unknown optional header", "magic": hex(magic)}
    image_base = struct.unpack_from("<Q" if is_64 else "<I", data, optional + (24 if is_64 else 28))[0]
    sections = []
    for index in range(section_count):
        offset = optional + optional_size + index * 40
        name = data[offset:offset + 8].rstrip(b"\0").decode("ascii", errors="replace")
        virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from("<IIII", data, offset + 8)
        sections.append({"name": name, "rva": virtual_address, "virtual_size": virtual_size,
                         "file_offset": raw_offset, "file_size": raw_size})

    def rva_offset(rva: int) -> int:
        for section in sections:
            if section["rva"] <= rva < section["rva"] + max(section["virtual_size"], section["file_size"]):
                return section["file_offset"] + rva - section["rva"]
        if rva < len(data):
            return rva
        raise ValueError(f"Unmapped RVA {rva:#x}")

    import_directory = optional + (112 if is_64 else 96) + 8
    imports_rva, imports_size = struct.unpack_from("<II", data, import_directory)
    imports = []
    if imports_rva:
        descriptor = rva_offset(imports_rva)
        for _ in range(4096):
            original, _, _, name_rva, first = struct.unpack_from("<IIIII", data, descriptor)
            if not (original or name_rva or first):
                break
            symbols = []
            thunk = rva_offset(original or first)
            step = 8 if is_64 else 4
            for _ in range(65536):
                address = struct.unpack_from("<Q" if is_64 else "<I", data, thunk)[0]
                if not address:
                    break
                if address & (1 << (63 if is_64 else 31)):
                    symbols.append({"ordinal": address & 0xFFFF})
                else:
                    symbols.append(cstring(data, rva_offset(address) + 2))
                thunk += step
            imports.append({"dll": cstring(data, rva_offset(name_rva)), "symbols": symbols})
            descriptor += 20
    pertinent = re.compile(rb"lua|gamebryo|NiStream|NiNode|\.nif|\.nft|\.agr|\.lur|Scripts\\|World\.img|Bully", re.I)
    strings = []
    for match in re.finditer(rb"[\x20-\x7e]{5,}", data):
        if pertinent.search(match.group()) and len(match.group()) <= 240:
            strings.append({"file_offset": match.start(), "value": match.group().decode("ascii")})
    return {"machine_hex": hex(machine), "architecture": "x64" if is_64 else "x86",
            "pe_timestamp": timestamp, "image_base": hex(image_base), "sections": sections,
            "imports_directory_size": imports_size, "imports": imports,
            "pertinent_strings": strings}


def inspect_directory(path: Path, root: Path) -> dict:
    data = path.read_bytes()
    records = []
    for offset in range(0, len(data) - 31, 32):
        sector, sectors, raw_name = struct.unpack_from("<II24s", data, offset)
        name = raw_name.split(b"\0", 1)[0].decode("ascii", errors="replace")
        if name:
            records.append({"name": name, "offset": sector * 2048, "sector_count": sectors,
                            "padded_size": sectors * 2048})
    archive = next((candidate for candidate in path.parent.iterdir()
                    if candidate.is_file() and candidate.stem.lower() == path.stem.lower()
                    and candidate.suffix.lower() == ".img"), None)
    samples = []
    by_extension = Counter(Path(record["name"]).suffix.lower() or "<none>" for record in records)
    invalid = []
    if archive:
        archive_size = archive.stat().st_size
        seen = Counter()
        with archive.open("rb") as stream:
            for record in records:
                if record["offset"] + record["padded_size"] > archive_size:
                    invalid.append(record["name"])
                ext = Path(record["name"]).suffix.lower()
                if seen[ext] < 2:
                    stream.seek(record["offset"])
                    samples.append({**record, "header": header(stream.read(min(64, record["padded_size"])))})
                    seen[ext] += 1
    return {"path": path.relative_to(root).as_posix(), "entry_count": len(records),
            "trailing_bytes": len(data) % 32, "entries_by_extension": dict(sorted(by_extension.items())),
            "archive_path": archive.relative_to(root).as_posix() if archive else None,
            "archive_present": archive is not None, "invalid_bounds_count": len(invalid),
            "invalid_bounds_examples": invalid[:10], "headers_by_extension": samples,
            "records": records}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.game_root.resolve(strict=True)
    output = args.output.resolve()
    if output == root or root in output.parents:
        parser.error("Output must be outside the game installation")
    files = []
    by_extension = Counter()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            first = stream.read(64)
            digest.update(first)
            while block := stream.read(4 * 1024 * 1024):
                digest.update(block)
        ext = path.suffix.lower() or "<none>"
        by_extension[ext] += 1
        files.append({"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size,
                      "sha256": digest.hexdigest(), "header": header(first)})
    result = {"schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
              "game_root": str(root), "method": "Read-only files; SHA-256; headers only; no extracted payloads",
              "file_count": len(files), "total_bytes": sum(item["bytes"] for item in files),
              "files_by_extension": dict(sorted(by_extension.items())), "files": files,
              "pe": inspect_pe(root / "Bully.exe"),
              "archives": [inspect_directory(path, root) for path in sorted(root.rglob("*"))
                           if path.is_file() and path.suffix.lower() == ".dir"]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "files": result["file_count"],
                      "bytes": result["total_bytes"], "architecture": result["pe"]["architecture"],
                      "missing_archives": [item["path"] for item in result["archives"]
                                           if not item["archive_present"]]}, indent=2))


if __name__ == "__main__":
    main()

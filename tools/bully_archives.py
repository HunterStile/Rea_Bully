"""Read the external 32-byte DIR / raw IMG layout verified in local Bully files.

Only sector extents are known: extracted files retain their 2048-byte padding.
This tool never opens game installation files for writing.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import struct
from typing import BinaryIO

SECTOR_SIZE = 2048
RECORD = struct.Struct("<II24s")
WORKSPACE = Path(__file__).resolve().parents[1]
RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)


class ArchiveError(ValueError):
    """Input cannot be safely interpreted with the verified layout."""


@dataclass(frozen=True)
class Entry:
    index: int
    name: str
    offset_sectors: int
    size_sectors: int

    @property
    def offset_bytes(self) -> int:
        return self.offset_sectors * SECTOR_SIZE

    @property
    def allocated_bytes(self) -> int:
        return self.size_sectors * SECTOR_SIZE

    def metadata(self) -> dict:
        return {**asdict(self), "offset_bytes": self.offset_bytes,
                "allocated_bytes": self.allocated_bytes}


def safe_name(raw: bytes) -> str:
    """Names occupy 24 ASCII bytes; shorter names have only NULs after them."""
    head, separator, padding = raw.partition(b"\0")
    if len(raw) != 24 or padding.strip(b"\0"):
        raise ArchiveError("filename field must be 24 bytes with valid NUL padding")
    try:
        name = head.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ArchiveError("non-ASCII filename: unsupported encoding") from exc
    if (not name or name in {".", ".."} or name[-1:] in {".", " "}
            or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in name)
            or RESERVED.match(name)):
        raise ArchiveError(f"unsafe filename: {name!r}")
    return name


def read_directory(path: Path, image_size: int | None = None) -> list[Entry]:
    data = path.read_bytes()
    if not data or len(data) % RECORD.size:
        raise ArchiveError("DIR must contain a nonempty sequence of 32-byte records")
    entries = []
    for index, (offset, size, raw) in enumerate(RECORD.iter_unpack(data)):
        try:
            name = safe_name(raw)
            if size == 0:
                raise ArchiveError("zero-sector entry is not a verified format")
            entry = Entry(index, name, offset, size)
            if image_size is not None and entry.offset_bytes + entry.allocated_bytes > image_size:
                raise ArchiveError("entry extends beyond IMG bounds")
            entries.append(entry)
        except ArchiveError as exc:
            raise ArchiveError(f"{path.name}, record {index}: {exc}") from exc
    return entries


def layout_issues(entries: list[Entry], *, indexed: bool = False) -> list[str]:
    names = Counter(e.name.casefold() for e in entries)
    issues = [] if indexed else [f"duplicate filename: {name}" for name, count in sorted(names.items()) if count > 1]
    previous_end = 0
    for entry in sorted(entries, key=lambda e: (e.offset_bytes, e.index)):
        if entry.offset_bytes < previous_end:
            issues.append(f"overlapping extent at record {entry.index}")
        previous_end = max(previous_end, entry.offset_bytes + entry.allocated_bytes)
    return issues


def companion_image(directory: Path) -> Path | None:
    matches = [p for p in directory.parent.iterdir()
               if p.is_file() and p.name.casefold() == (directory.stem + ".img").casefold()]
    if len(matches) > 1:
        raise ArchiveError(f"ambiguous IMG companion for {directory}")
    return matches[0] if matches else None


def digest_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return digest_stream(stream)


def file_metadata(path: Path) -> dict:
    with path.open("rb") as stream:
        header = stream.read(32).hex()
    return {"size_bytes": path.stat().st_size, "sha256": sha256(path), "first_32_bytes_hex": header}


def describe(directory: Path, root: Path | None = None) -> dict:
    directory = directory.resolve(strict=True)
    image = companion_image(directory)
    display = lambda p: p.relative_to(root).as_posix() if root else str(p)
    result = {"directory": display(directory), "dir_metadata": file_metadata(directory),
              "image": display(image) if image else None,
              "format": "external-dir-32-byte-le-sector-2048", "sector_size": SECTOR_SIZE}
    if image:
        result["img_metadata"] = file_metadata(image)
    try:
        entries = read_directory(directory, image.stat().st_size if image else None)
        issues = layout_issues(entries)
        if any(entry.name.casefold() == "manifest.json" for entry in entries):
            issues.append("entry conflicts with extraction manifest.json")
        if image is None:
            issues.insert(0, "missing companion IMG; directory extents have no verified backing file")
        indexed_issues = layout_issues(entries, indexed=True)
        if image is None:
            indexed_issues.insert(0, "missing companion IMG")
        result.update(entry_count=len(entries), allocated_bytes=sum(e.allocated_bytes for e in entries),
                      entries=[e.metadata() for e in entries], issues=issues,
                      extraction_supported=not issues,
                      indexed_extraction_supported=not indexed_issues)
    except ArchiveError as exc:
        result.update(issues=[str(exc)], extraction_supported=False, indexed_extraction_supported=False)
    return result


def inventory(source: Path) -> dict:
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise ArchiveError("inventory source must be a directory")
    directories = sorted((p for p in source.rglob("*") if p.is_file() and p.suffix.lower() == ".dir"),
                         key=lambda p: p.relative_to(source).as_posix().casefold())
    archives = [describe(path, source) for path in directories]
    paired = {a["image"].casefold() for a in archives if a["image"]}
    orphan_images = [{"path": p.relative_to(source).as_posix(), **file_metadata(p)}
                     for p in sorted(source.rglob("*"))
                     if p.is_file() and p.suffix.lower() == ".img"
                     and p.relative_to(source).as_posix().casefold() not in paired]
    return {"schema_version": 1, "source_root": str(source),
            "method": "read-only DIR record parsing; SHA-256 of full DIR/IMG; no payload trimming",
            "archive_count": len(archives), "paired_count": sum(a["image"] is not None for a in archives),
            "entry_count": sum(a.get("entry_count", 0) for a in archives),
            "extractable_entry_count": sum(a.get("entry_count", 0) for a in archives if a["extraction_supported"]),
            "indexed_extractable_entry_count": sum(a.get("entry_count", 0) for a in archives if a["indexed_extraction_supported"]),
            "archives": archives, "orphan_images": orphan_images}


def workspace_target(path: Path, workspace: Path = WORKSPACE) -> Path:
    target = path.resolve()
    if not target.is_relative_to(workspace.resolve()) or target == workspace.resolve():
        raise ArchiveError("output must be inside the workspace, below its root")
    return target


def write_new_json(path: Path, value: dict, workspace: Path = WORKSPACE) -> None:
    path = workspace_target(path, workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=True)
        stream.write("\n")


def extract(directory: Path, output: Path, workspace: Path = WORKSPACE, *, indexed: bool = False) -> dict:
    directory = directory.resolve(strict=True)
    image = companion_image(directory)
    if image is None:
        raise ArchiveError("cannot extract without a companion IMG")
    output = workspace_target(output, workspace)
    if output.is_relative_to(directory.parent):
        raise ArchiveError("output must not be inside the source archive directory")
    entries = read_directory(directory, image.stat().st_size)
    issues = layout_issues(entries, indexed=indexed)
    if not indexed and any(entry.name.casefold() == "manifest.json" for entry in entries):
        issues.append("entry conflicts with extraction manifest.json")
    if issues:
        raise ArchiveError("; ".join(issues))
    # Validation and source hashes precede output creation. Existing output is never overwritten.
    source_metadata = {"directory": {"path": str(directory), **file_metadata(directory)},
                       "image": {"path": str(image), **file_metadata(image)}}
    image_stat = image.stat()
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": 1, "source": source_metadata,
                "format": "external-dir-32-byte-le-sector-2048", "sector_size": SECTOR_SIZE,
                "padding_preserved": True, "logical_sizes_known": False,
                "indexed_paths": indexed,
                "entry_count": len(entries), "entries": []}
    with image.open("rb") as source:
        for entry in entries:
            relative = f"{entry.index:05d}/{entry.name}" if indexed else entry.name
            target = output / relative
            if indexed:
                target.parent.mkdir()
            digest = hashlib.sha256()
            remaining = entry.allocated_bytes
            source.seek(entry.offset_bytes)
            with target.open("xb") as destination:
                while remaining:
                    chunk = source.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        raise ArchiveError(f"IMG truncated while extracting {entry.name}")
                    destination.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
            extracted_hash = sha256(target)
            source_hash = digest.hexdigest()
            if extracted_hash != source_hash:
                raise ArchiveError(f"post-write SHA-256 mismatch: {entry.name}")
            manifest["entries"].append({**entry.metadata(), "path": relative,
                                        "source_extent_sha256": source_hash,
                                        "extracted_sha256": extracted_hash,
                                        "verified": True})
    after = image.stat()
    if (image_stat.st_size, image_stat.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ArchiveError("source IMG changed during extraction")
    write_new_json(output / "manifest.json", manifest, workspace)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inv = commands.add_parser("inventory", help="inventory all DIR/IMG files recursively")
    inv.add_argument("source", type=Path)
    inv.add_argument("--output", type=Path, required=True)
    listing = commands.add_parser("list", help="validate and list an external DIR")
    listing.add_argument("directory", type=Path)
    ext = commands.add_parser("extract", help="extract every entry to a new workspace directory")
    ext.add_argument("directory", type=Path)
    ext.add_argument("--output", type=Path, required=True)
    ext.add_argument("--indexed", action="store_true",
                     help="preserve duplicate basenames in per-record 00000/ directories; overlaps remain unsupported")
    args = parser.parse_args(argv)
    try:
        if args.command == "inventory":
            value = inventory(args.source)
            write_new_json(args.output, value)
            print(json.dumps({key: value[key] for key in ("archive_count", "paired_count", "entry_count", "extractable_entry_count", "indexed_extractable_entry_count")}))
        elif args.command == "list":
            print(json.dumps(describe(args.directory), indent=2))
        else:
            value = extract(args.directory, args.output, indexed=args.indexed)
            print(json.dumps({"entry_count": value["entry_count"], "output": str(args.output), "all_hashes_verified": True}))
        return 0
    except (ArchiveError, OSError) as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())

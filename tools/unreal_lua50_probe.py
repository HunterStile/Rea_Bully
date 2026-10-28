"""Load original Bully chunks through a private C ABI inside the current host.

Executing this file in Unreal Editor Python records an in-process host probe.
It does not load/save maps, register callbacks, start play, execute Lua, or quit
the editor. The reusable probe() also supports the standalone build validation.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_HEADER = "1b4c7561500104040406080909043bafef4b"
DEFAULT_DLL = ROOT / ".tools/lua50/build-msvc-x64/lua50_probe.dll"
DEFAULT_MANIFEST = ROOT / "evidence/lua50-validation.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bounded_workspace_file(value: str | Path, *, must_exist: bool = True) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve(strict=must_exist)
    if not path.is_relative_to(ROOT):
        raise ValueError(f"probe path must remain inside workspace: {path}")
    return path


def probe(*, dll_path: Path = DEFAULT_DLL, manifest_path: Path = DEFAULT_MANIFEST,
          host: str = "standalone_python", engine_version: str | None = None) -> dict:
    dll_path = bounded_workspace_file(dll_path)
    manifest_path = bounded_workspace_file(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = manifest["files"]
    if len(records) != 567:
        raise ValueError(f"expected 567 original chunks, found {len(records)}")
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        raise ValueError("probe DLL requires an x64 host")
    library = ctypes.CDLL(str(dll_path))
    loadfile = library.bully_lua50_loadfile
    loadfile.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_char), ctypes.c_size_t]
    loadfile.restype = ctypes.c_int
    header_probe = library.bully_lua50_chunk_header
    header_probe.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t]
    header_probe.restype = ctypes.c_int
    header = (ctypes.c_ubyte * 18)()
    if header_probe(header, len(header)) != 18 or bytes(header).hex() != EXPECTED_HEADER:
        raise ValueError("DLL-generated header differs from original Bully profile")
    results = []
    for record in records:
        source = bounded_workspace_file(record["path"])
        if digest(source) != record["sha256"]:
            raise ValueError(f"original chunk SHA-256 differs from manifest: {source}")
        # Original corpus paths are ASCII; do not silently use a lossy code page.
        encoded = str(source).encode("ascii")
        error = ctypes.create_string_buffer(4096)
        status = loadfile(encoded, error, len(error))
        results.append({"path": source.relative_to(ROOT).as_posix(), "sha256": record["sha256"],
                        "load_status": status, "error": error.value.decode("utf-8", errors="replace") or None})
    controls = []
    for record in manifest["negative_controls"]:
        source = bounded_workspace_file(record["path"])
        error = ctypes.create_string_buffer(4096)
        status = loadfile(str(source).encode("ascii"), error, len(error))
        controls.append({"path": source.relative_to(ROOT).as_posix(), "sha256": digest(source),
                         "load_status": status, "error": error.value.decode("utf-8", errors="replace") or None})
    if len(controls) != 3 or any(row["load_status"] == 0 for row in controls):
        raise ValueError("a malformed control was accepted by the DLL")
    # Guard bytes verify that error truncation respects capacities 0/1/8/32.
    boundary_checks = []
    invalid_path = str(ROOT / "local/__lua50_missing_probe_file__.lur").encode("ascii")
    if Path(invalid_path.decode("ascii")).exists():
        raise ValueError("missing-file boundary-control path unexpectedly exists")
    for capacity in (0, 1, 8, 32):
        guarded = (ctypes.c_char * 66)(*([b"X"] * 66))
        pointer = ctypes.cast(ctypes.byref(guarded, 1), ctypes.POINTER(ctypes.c_char))
        status = loadfile(invalid_path, pointer, capacity)
        raw = bytes(guarded)
        intact = raw[0:1] == b"X" and raw[1 + capacity:] == b"X" * (65 - capacity)
        terminated = capacity == 0 or b"\0" in raw[1:1 + capacity]
        if status == 0 or not intact or not terminated:
            raise ValueError(f"error buffer boundary test failed at capacity {capacity}")
        boundary_checks.append({"capacity": capacity, "load_status": status,
                                "guard_bytes_preserved": intact, "nul_terminated_when_nonempty": terminated})
    error = ctypes.create_string_buffer(32)
    argument_checks = {
        "null_path": loadfile(None, error, len(error)),
        "empty_path": loadfile(b"", error, len(error)),
        "null_error_buffer_nonzero_capacity": loadfile(invalid_path, None, 1),
        "null_error_buffer_zero_capacity": loadfile(invalid_path, None, 0),
        "null_header_buffer": header_probe(None, 18),
        "short_header_buffer": header_probe(header, 17),
    }
    if any(argument_checks[name] != -1 for name in argument_checks if name != "null_error_buffer_zero_capacity"):
        raise ValueError("DLL rejected-argument contract failed")
    if argument_checks["null_error_buffer_zero_capacity"] <= 0:
        raise ValueError("zero-capacity error buffer did not preserve Lua file error")
    return {"schema_version": 1, "host": host, "engine_version": engine_version,
            "host_pid": os.getpid(), "host_python": sys.version, "host_executable": sys.executable,
            "host_platform": platform.platform(), "pointer_bytes": ctypes.sizeof(ctypes.c_void_p),
            "dll": {"path": dll_path.relative_to(ROOT).as_posix(), "sha256": digest(dll_path)},
            "source_manifest": {"path": manifest_path.relative_to(ROOT).as_posix(), "sha256": digest(manifest_path)},
            "generated_chunk_header_hex": bytes(header).hex(), "file_count": len(results),
            "load_failures": sum(row["load_status"] != 0 for row in results), "files": results,
            "negative_controls": controls, "error_buffer_checks": boundary_checks,
            "argument_checks": argument_checks, "game_chunk_execution": False,
            "native_bindings_registered": False, "standard_libraries_opened": False,
            "vm_pointers_cross_abi": False,
            "limitations": ["In-process DLL loading and parsing only; no gameplay, mission execution, engine-native bindings, or scheduler validation."]}


def main() -> None:
    import unreal
    output = bounded_workspace_file(os.environ.get("BULLY_LUA_REPORT", "evidence/unreal-lua50.json"), must_exist=False)
    if output.exists():
        raise FileExistsError(f"choose a new probe report path: {output}")
    dll = bounded_workspace_file(os.environ.get("BULLY_LUA_DLL", str(DEFAULT_DLL)))
    expected = ROOT / "evidence/lua50-dll-validation.json"
    if expected.is_file():
        previous = json.loads(expected.read_text(encoding="utf-8"))
        if digest(dll) != previous["dll"]["sha256"]:
            raise ValueError("DLL differs from independently validated standalone artifact")
    result = probe(dll_path=dll, host="UnrealEditorPython",
                   engine_version=unreal.SystemLibrary.get_engine_version())
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    unreal.log(f"REA Lua load-only probe: {result['file_count']} chunks, {result['load_failures']} failures; report {output}")
    if result["load_failures"]:
        raise RuntimeError("original chunks failed inside Unreal; inspect the saved probe report")


if __name__ == "__main__":
    main()

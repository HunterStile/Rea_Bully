"""Build and validate a private MSVC x64, float32 Lua 5.0.2 VM.

The authenticated original source is retained separately from a patched build
tree. Chunk string lengths are explicitly 32-bit on disk, while the VM uses
native 64-bit size_t/pointers. Game chunks are loaded, never called.
"""
from __future__ import annotations

import argparse
from collections import Counter
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / ".tools/lua50"
URL = "https://www.lua.org/ftp/lua-5.0.2.tar.gz"
EXPECTED_SHA256 = "a6c85d85f912e1c321723084389d63dee7660b81b8292452b190ea7190dd73bc"
EXPECTED_HEADER = "1b4c7561500104040406080909043bafef4b"
FLAGS = ["/nologo", "/c", "/TC", "/O2", "/MD", "/W3", "/fp:precise",
         "/D_CRT_SECURE_NO_WARNINGS", "/DUSE_FLOAT", '/DLUA_USER_H="luser_number.h"']


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, *, env=None, cwd=ROOT, accepted=(0,)):
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
    if result.returncode not in accepted:
        raise RuntimeError(f"command failed ({result.returncode}): {command}\n{result.stdout}\n{result.stderr}")
    return result


def acquire_source() -> Path:
    BASE.mkdir(parents=True, exist_ok=True)
    archive = BASE / "lua-5.0.2.tar.gz"
    if not archive.exists():
        payload = urllib.request.urlopen(URL, timeout=60).read()
        if hashlib.sha256(payload).hexdigest() != EXPECTED_SHA256:
            raise ValueError("download SHA-256 differs from lua.org FTP checksum")
        archive.write_bytes(payload)
    if digest(archive) != EXPECTED_SHA256:
        raise ValueError("cached source archive SHA-256 mismatch")
    original = BASE / "lua-5.0.2"
    if not original.exists():
        with tarfile.open(archive) as stream:
            stream.extractall(BASE, filter="data")
    # Compare every source/header byte with the authenticated tar, including on rebuild.
    with tarfile.open(archive) as stream:
        for member in stream.getmembers():
            if member.isfile():
                data = stream.extractfile(member).read()
                if (BASE / member.name).read_bytes() != data:
                    raise ValueError(f"original source tree changed: {member.name}")
    return original


def prepare_tree(original: Path) -> tuple[Path, list[dict]]:
    tree = BASE / "bully-f32"
    # Reuse output directory but reconstruct each original file byte-for-byte before patching.
    shutil.copytree(original, tree, dirs_exist_ok=True)
    changes = {
        "src/lundump.c": [
            ('#include "lua.h"', '#include "lua.h"\n#include <stdint.h>'),
            (' size_t x;\n LoadBlock(S,&x,sizeof(x));\n return x;',
             ' uint32_t x; /* Bully serialized size_t is 32-bit, independent of host ABI. */\n LoadBlock(S,&x,sizeof(x));\n return (size_t)x;'),
            ('TESTSIZE(sizeof(size_t), "size_t");', 'TESTSIZE(sizeof(uint32_t), "size_t");')],
        "src/ldump.c": [
            ('#include <stddef.h>', '#include <stddef.h>\n#include <stdint.h>\n#include <stdlib.h>'),
            ('static void DumpSize(size_t x, DumpState* D)\n{\n DumpBlock(&x,sizeof(x),D);\n}',
             'static void DumpSize(size_t x, DumpState* D)\n{\n uint32_t wire;\n if (x > UINT32_MAX) abort(); /* Cannot encode a larger Bully string. */\n wire=(uint32_t)x;\n DumpBlock(&wire,sizeof(wire),D);\n}'),
            ('DumpByte(sizeof(size_t),D);', 'DumpByte(sizeof(uint32_t),D);')],
        "src/llimits.h": [
            ('typedef unsigned long lu_mem;', 'typedef size_t lu_mem; /* Required on Windows LLP64. */'),
            ('#define MAX_LUMEM\tULONG_MAX', '#define MAX_LUMEM\t((lu_mem)(~(lu_mem)0))'),
            ('#define IntPoint(p)  ((lu_hash)(p))', '#define IntPoint(p)  ((lu_hash)(size_t)(p))')],
    }
    metadata = []
    diff = []
    for relative, replacements in changes.items():
        path = tree / relative
        before = path.read_text()
        after = before
        for old, new in replacements:
            if after.count(old) != 1:
                raise ValueError(f"patch anchor count differs from 1: {relative}: {old!r}")
            after = after.replace(old, new)
        path.write_text(after, encoding="utf-8", newline="\n")
        diff.extend(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                         fromfile=f"original/{relative}", tofile=f"bully-f32/{relative}"))
        metadata.append({"path": relative, "original_sha256": digest(original / relative),
                         "patched_sha256": digest(path)})
    patch = BASE / "bully-f32.patch"
    patch.write_text("".join(diff), encoding="utf-8", newline="\n")
    return tree, metadata


def msvc_environment() -> tuple[dict, Path]:
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio/Installer/vswhere.exe"
    instances = json.loads(run([str(vswhere), "-products", "*", "-requires",
                                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-format", "json"]).stdout)
    if not instances:
        raise RuntimeError("existing MSVC x64 toolchain not found; no installation attempted")
    installation = Path(instances[-1]["installationPath"])
    batch = installation / "VC/Auxiliary/Build/vcvars64.bat"
    script = BASE / "capture-msvc-environment.cmd"
    script.write_text(f'@call "{batch}" >nul\n@if errorlevel 1 exit /b %errorlevel%\n@set\n', encoding="utf-8")
    result = run([os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(script)])
    env = os.environ.copy()
    for line in result.stdout.splitlines():
        if "=" in line and not line.startswith("="):
            key, value = line.split("=", 1)
            env[key] = value
    return env, installation


def build(tree: Path, env: dict) -> tuple[Path, Path, dict]:
    output = BASE / "build-msvc-x64"
    output.mkdir(exist_ok=True)
    objdir = output / "obj"
    objdir.mkdir(exist_ok=True)
    compiler = Path(shutil.which("cl.exe", path=env.get("PATH")) or "")
    librarian = Path(shutil.which("lib.exe", path=env.get("PATH")) or "")
    sources = sorted((tree / "src").glob("*.c")) + sorted((tree / "src/lib").glob("*.c"))
    objects = []
    log = []
    for source in sources:
        obj = objdir / f"{source.stem}.obj"
        result = run([str(compiler), *FLAGS, f"/I{tree / 'include'}", f"/I{tree / 'src'}", f"/I{tree / 'etc'}",
                      f"/Fo{obj}", str(source)], env=env)
        log.append(result.stdout + result.stderr)
        objects.append(obj)
    library = output / "lua50_bully_f32.lib"
    result = run([str(librarian), "/nologo", f"/OUT:{library}", *map(str, objects)], env=env)
    log.append(result.stdout + result.stderr)
    harness = ROOT / "tools/lua50_validate.c"
    harness_object = objdir / "lua50_validate.obj"
    result = run([str(compiler), *FLAGS, f"/I{tree / 'include'}", f"/I{tree / 'src'}", f"/I{tree / 'etc'}",
                  f"/Fo{harness_object}", str(harness)], env=env)
    log.append(result.stdout + result.stderr)
    executable = output / "lua50_validate.exe"
    linker = Path(shutil.which("link.exe", path=env.get("PATH")) or "")
    result = run([str(linker), "/nologo", "/MACHINE:X64", f"/OUT:{executable}",
                  str(harness_object), str(library)], env=env)
    log.append(result.stdout + result.stderr)
    (output / "build.log").write_text("\n".join(log), encoding="utf-8")
    version = run([str(compiler)], env=env, accepted=(0, 2))
    info = {"compiler": str(compiler), "compiler_sha256": digest(compiler),
            "compiler_banner": (version.stdout + version.stderr).strip(),
            "flags": FLAGS, "source_files": [p.relative_to(tree).as_posix() for p in sources],
            "source_file_count": len(sources), "static_library": str(library.relative_to(ROOT)),
            "static_library_sha256": digest(library), "executable": str(executable.relative_to(ROOT)),
            "executable_sha256": digest(executable), "harness_sha256": digest(harness),
            "build_script_sha256": digest(Path(__file__)), "build_log": str((output / 'build.log').relative_to(ROOT))}
    return executable, library, info


def validate(executable: Path, metadata: dict, patched_files: list[dict]) -> dict:
    paths = sorted((ROOT / "local/scripts").glob("*.lur"))
    paths += sorted((ROOT / "local/extracted/Stream/World").rglob("*.lur"))
    if len(paths) != 567:
        raise ValueError(f"expected 515 Scripts plus 52 World chunks, found {len(paths)}")
    filelist = BASE / "all-chunks.txt"
    filelist.write_text("\n".join(str(p.relative_to(ROOT)) for p in paths) + "\n", encoding="ascii")
    result = run([str(executable), "--filelist", str(filelist)], accepted=(0, 1))
    evidence = json.loads(result.stdout)
    if evidence["config"]["generated_chunk_header_hex"] != EXPECTED_HEADER:
        raise ValueError("built VM dump header does not match original Bully header")
    for entry, path in zip(evidence["files"], paths, strict=True):
        payload = path.read_bytes()
        entry.update(sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload),
                     header_hex=payload[:18].hex())
        if entry["header_hex"] != EXPECTED_HEADER:
            raise ValueError(f"unexpected original chunk header: {path}")
    # Corruption controls prove that loading is checked, rather than blindly accepted.
    controls = BASE / "controls"
    controls.mkdir(exist_ok=True)
    payload = paths[0].read_bytes()
    malformed = {"wrong-number-width.lur": payload[:13] + b"\x08" + payload[14:],
                 "wrong-size-width.lur": payload[:7] + b"\x08" + payload[8:],
                 "truncated.lur": payload[:18]}
    for name, data in malformed.items():
        (controls / name).write_bytes(data)
    control_list = controls / "paths.txt"
    control_list.write_text("\n".join(str((controls / n).relative_to(ROOT)) for n in malformed) + "\n", encoding="ascii")
    checked = json.loads(run([str(executable), "--filelist", str(control_list)], accepted=(1,)).stdout)
    if checked["load_failures"] != len(malformed):
        raise ValueError("malformed negative control was unexpectedly accepted")
    evidence.update(schema_version=1, source={"url": URL, "sha256": EXPECTED_SHA256,
                    "license": "MIT", "upstream_unchanged_tree": ".tools/lua50/lua-5.0.2",
                    "patched_tree": ".tools/lua50/bully-f32", "patched_files": patched_files,
                    "patch_sha256": digest(BASE / 'bully-f32.patch')},
                    build=metadata, scope={"Scripts": 515, "World": 52},
                    native_bindings_registered=False, standard_libraries_opened=False,
                    negative_controls=checked["files"],
                    load_status_counts=dict(Counter(str(e["load_status"]) for e in evidence["files"])))
    return evidence


def build_probe_dll(output: Path) -> dict:
    """Link the already validated static library, preserving the original report."""
    from unreal_lua50_probe import probe
    baseline_path = ROOT / "evidence/lua50-validation.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    library = ROOT / baseline["build"]["static_library"]
    if digest(library) != baseline["build"]["static_library_sha256"]:
        raise ValueError("static library differs from original standalone validation")
    tree = BASE / "bully-f32"
    env, installation = msvc_environment()
    compiler = Path(shutil.which("cl.exe", path=env.get("PATH")) or "")
    linker = Path(shutil.which("link.exe", path=env.get("PATH")) or "")
    if digest(compiler) != baseline["build"]["compiler_sha256"]:
        raise ValueError("probe must use the same compiler as the validated static library")
    directory = BASE / "build-msvc-x64"
    source = ROOT / "tools/lua50_probe_dll.c"
    obj = directory / "obj/lua50_probe_dll.obj"
    dll = directory / "lua50_probe.dll"
    compiled = run([str(compiler), *FLAGS, f"/I{tree / 'include'}", f"/I{tree / 'etc'}",
                    f"/Fo{obj}", str(source)], env=env)
    linked = run([str(linker), "/nologo", "/DLL", "/MACHINE:X64", f"/OUT:{dll}",
                  str(obj), str(library)], env=env)
    log = directory / "dll-build.log"
    log.write_text(compiled.stdout + compiled.stderr + linked.stdout + linked.stderr, encoding="utf-8")
    result = probe(dll_path=dll)
    result["build"] = {"compiler": str(compiler), "compiler_sha256": digest(compiler),
                       "visual_studio_installation": str(installation), "flags": FLAGS,
                       "link_flags": ["/DLL", "/MACHINE:X64"],
                       "static_library": library.relative_to(ROOT).as_posix(),
                       "static_library_sha256": digest(library),
                       "probe_source_sha256": digest(source),
                       "probe_python_sha256": digest(ROOT / "tools/unreal_lua50_probe.py"),
                       "build_script_sha256": digest(Path(__file__)),
                       "build_log": log.relative_to(ROOT).as_posix()}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--probe-dll", action="store_true",
                        help="link and ctypes-test a private DLL from the existing validated static library")
    args = parser.parse_args()
    default = ROOT / ("evidence/lua50-dll-validation.json" if args.probe_dll else "evidence/lua50-validation.json")
    output = (args.output or default).resolve()
    if not output.is_relative_to(ROOT):
        raise ValueError("evidence output must stay inside the workspace")
    if args.probe_dll:
        if output.exists():
            raise FileExistsError("choose a new DLL validation report output")
        evidence = build_probe_dll(output)
        print(json.dumps({"file_count": evidence["file_count"], "load_failures": evidence["load_failures"],
                          "game_chunk_execution": False, "dll": evidence["dll"], "evidence": str(output)}))
        return 0 if evidence["load_failures"] == 0 else 1
    original = acquire_source()
    tree, patched = prepare_tree(original)
    environment, installation = msvc_environment()
    executable, library, metadata = build(tree, environment)
    metadata["visual_studio_installation"] = str(installation)
    evidence = validate(executable, metadata, patched)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"file_count": evidence["file_count"], "load_failures": evidence["load_failures"],
                      "game_chunk_execution": False, "static_library": str(library), "evidence": str(output)}))
    return 0 if evidence["load_failures"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

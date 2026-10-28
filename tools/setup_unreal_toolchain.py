"""Fetch the portable C++ toolchain into .tools, without a system installation."""
from __future__ import annotations
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REVISION = "49b58850aa5e5c1e65f090d4651ee76607883e11"
SCRIPT_SHA = "1791cccca594ff083ce8d3e0a139e6156d2c312e24332a909e608010f0230a51"
URL = f"https://gist.githubusercontent.com/mmozeiko/7f3162ec2988e81e56d5c4e22cde9977/raw/{REVISION}/portable-msvc.py"


def main() -> int:
    work = ROOT / ".tools/portable-build"
    work.mkdir(parents=True, exist_ok=True)
    source = ROOT / ".tools/portable-msvc.py"
    if not source.exists():
        urllib.request.urlretrieve(URL, source)
    if hashlib.sha256(source.read_bytes()).hexdigest() != SCRIPT_SHA:
        raise RuntimeError("Portable downloader source differs from pinned revision")
    compiler = work / "msvc/VC/Tools/MSVC/14.44.35207/bin/Hostx64/x64/cl.exe"
    if not compiler.exists():
        with (work / "install.log").open("xb") as stream:
            subprocess.run([sys.executable, str(source), "--vs", "2022", "--msvc-version", "14.44",
                            "--sdk-version", "22621", "--accept-license"], cwd=work,
                           stdout=stream, stderr=subprocess.STDOUT, check=True)
    channel_url = "https://aka.ms/vs/17/release/channel"
    channel_data = urllib.request.urlopen(channel_url).read()
    channel = json.loads(channel_data)
    manifest_payload = next(p for p in channel["channelItems"] if p["id"] == "Microsoft.VisualStudio.Manifests.VisualStudio")["payloads"][0]
    manifest_data = urllib.request.urlopen(manifest_payload["url"]).read()
    manifest_sha = hashlib.sha256(manifest_data).hexdigest()
    # The Microsoft endpoint currently returns a compact manifest whose bytes
    # differ from the channel's advertised size/hash. Preserve that distinction;
    # do not describe the manifest itself as publisher-digest-verified. Payload
    # hashes are still checked by the portable downloader before extraction.
    manifest_digest_matches = manifest_sha.lower() == manifest_payload.get("sha256", "").lower()
    (work / "vs-channel.json").write_bytes(channel_data)
    (work / "vs-manifest.json").write_bytes(manifest_data)
    netfx = work / "netfx/Program Files/Windows Kits/NETFXSDK/4.8/Include/um/mscoree.h"
    if not netfx.exists():
        subprocess.run([sys.executable, str(ROOT / "tools/setup_unreal_netfx.py")], check=True, cwd=ROOT)
    signature_env = os.environ.copy()
    signature_env.pop("PSModulePath", None)
    signature = subprocess.run([shutil.which("pwsh") or "powershell", "-NoProfile", "-Command",
        "Get-AuthenticodeSignature -LiteralPath '" + str(compiler).replace("'", "''")
        + "' | Select-Object @{n='Status';e={$_.Status.ToString()}},@{n='Publisher';e={$_.SignerCertificate.Subject}} | ConvertTo-Json"],
        check=True, stdout=subprocess.PIPE, env=signature_env)
    signature_record = json.loads(signature.stdout.decode("utf-8-sig"))
    if signature_record["Status"] != "Valid" or "O=Microsoft Corporation" not in signature_record["Publisher"]:
        raise RuntimeError("Compiler must have a valid Microsoft Authenticode signature")
    compiler_result = subprocess.run([str(compiler)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    record = {
        "scope": "Workspace files only; child build processes use UE_SDKS_ROOT. No global environment or registry edits.",
        "downloader": {"source": URL, "revision": REVISION, "sha256": SCRIPT_SHA},
        "microsoft_channel": {"url": channel_url, "sha256": hashlib.sha256(channel_data).hexdigest()},
        "microsoft_manifest": {"url": manifest_payload["url"], "sha256": manifest_sha,
            "received_bytes": len(manifest_data), "advertised_bytes": manifest_payload.get("size"),
            "advertised_sha256": manifest_payload.get("sha256"), "advertised_digest_matches": manifest_digest_matches,
            "trust_limit": "HTTPS content; manifest publisher digest not validated when advertised bytes differ."},
        "compiler": {"path": str(compiler), "sha256": hashlib.sha256(compiler.read_bytes()).hexdigest(),
                     "version_output": compiler_result.stdout.decode("utf-8", errors="replace"),
                     "authenticode": signature_record},
        "windows_sdk": "10.0.22621.0", "netfx_sdk": "4.8",
        "netfx_lookup_alias": "UE AutoSDK probes 4.6.2; build helper points that local directory at the actual 4.8 SDK.",
        "payload_hash_check": "Portable downloader checks each payload against Microsoft's manifest before extracting.",
        "redistribution": "Downloaded Microsoft build tools remain in ignored .tools and are not distributed with the project."
    }
    report = ROOT / "evidence/unreal-local-toolchain.json"
    report.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"Local toolchain ready. Evidence: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

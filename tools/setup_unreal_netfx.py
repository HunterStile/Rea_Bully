"""Extract the official .NET Framework SDK into the local build toolchain."""
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
work = ROOT / ".tools/portable-build"
manifest = json.loads((work / "vs-manifest.json").read_text(encoding="utf-8"))
package = next(p for p in manifest["packages"] if p["id"] == "Microsoft.Net.4.8.SDK")
cache = work / "netfx-downloads"
cache.mkdir(exist_ok=True)
provenance = []
for payload in package["payloads"]:
    target = cache / Path(payload["fileName"]).name
    if not target.exists():
        urllib.request.urlretrieve(payload["url"], target)
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual.lower() != payload["sha256"].lower():
        raise RuntimeError(f"SDK payload hash mismatch: {target}")
    provenance.append({"file": str(target.relative_to(ROOT)), "url": payload["url"], "sha256": actual})
output = (work / "netfx").resolve()
if not output.is_relative_to(ROOT / ".tools"):
    raise RuntimeError("SDK extraction must stay inside the workspace")
output.mkdir(exist_ok=True)
result = subprocess.run(["msiexec", "/a", str(cache / "sdk_tools48.msi"), "/qn",
                         "TARGETDIR=" + str(output), "/l*v", str(cache / "extract.log")], check=True)
record = {"method": "Administrative extraction only; no system SDK installation or registry edits",
          "sdk": "Microsoft.Net.4.8.SDK", "payloads": provenance, "output": str(output),
          "exit_code": result.returncode}
(ROOT / "evidence/unreal-netfx-sdk.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
print(json.dumps(record, indent=2))

"""Audit original NIF alpha states against the already imported school assets."""
import hashlib
import json
from collections import Counter
from pathlib import Path
import re
from export_nif import Nif, texture_basename

ROOT = Path(__file__).resolve().parents[1]
zone = json.loads((ROOT / "evidence/school-zone-export.json").read_text())
imported = json.loads((ROOT / "evidence/unreal-school-zone.json").read_text())
paths = imported["imported_asset_paths"]
records = []
for model in zone["models"]:
    if model["status"] != "exported":
        continue
    source = Path(model["export"]["source"])
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    assert source_sha == model["export"]["source_sha256"]
    nif = Nif(source)
    prefix = "/Game/Original/SchoolZone/" + Path(model["output"]).stem + "/"
    for block_index, kind in enumerate(nif.types):
        if kind not in ("NiTriShape", "NiTriStrips"):
            continue
        shape = nif.block(block_index)
        props = [nif.block(i) for i in shape["properties"]]
        alpha = next((p for p in props if p["type"] == "NiAlphaProperty"), None)
        if alpha is None:
            continue
        flags = alpha["flags"]
        source_blend, destination_blend = (flags >> 1) & 15, (flags >> 5) & 15
        mode = "additive_one_one" if flags & 1 and source_blend == destination_blend == 0 else "standard_alpha" if flags & 1 and (source_blend, destination_blend) == (6, 7) else "unsupported_blend" if flags & 1 else "alpha_test" if flags & 512 else "opaque"
        material = next((p for p in props if p["type"] == "NiMaterialProperty"), {})
        texturing = next((p for p in props if p["type"] == "NiTexturingProperty"), {})
        base = texturing.get("slots", [None])[0]
        texture = texture_basename(nif.block(base["source"])["filename"]) if base else None
        stencil = next((p for p in props if p["type"] == "NiStencilProperty"), {})
        material_name = re.sub(r"[^A-Za-z0-9_]", "_", shape["name"])
        matches = [p for p in paths if p.startswith(prefix) and "/Materials/" in p and p.rsplit(".", 1)[-1] == material_name]
        if mode == "additive_one_one" and len(matches) != 1:
            raise RuntimeError(f"Ambiguous original material mapping: {shape['name']}: {matches}")
        texture_name = re.sub(r"[^A-Za-z0-9_]", "_", texture or "")
        textures = [p for p in paths if p.startswith(prefix) and "/Textures/" in p and p.rsplit(".", 1)[-1] == texture_name]
        if mode == "additive_one_one" and len(textures) != 1:
            raise RuntimeError(f"Ambiguous texture mapping: {texture}: {textures}")
        records.append({"model": model["name"], "model_id": model["model_id"], "source_nif": str(source), "source_sha256": source_sha,
            "shape_block": block_index, "shape_name": shape["name"], "instance_count": model["instance_count"],
            "alpha_flags": flags, "source_blend": source_blend, "destination_blend": destination_blend,
            "alpha_test": bool(flags & 512), "test_function": (flags >> 10) & 7, "threshold_byte": alpha["threshold"],
            "mode": mode, "diffuse_rgb": material.get("diffuse", [1, 1, 1]), "material_alpha": material.get("alpha", 1),
            "source_texture": texture, "double_sided": ((stencil.get("flags", 0) >> 10) & 3) == 3,
            "has_vertex_rgb": bool(nif.block(shape["data"])["colors"]),
            "unreal_original_material": matches[0] if len(matches) == 1 else None,
            "unreal_original_texture": textures[0] if len(textures) == 1 else None,
            "original_texture_sources": model["export"]["texture_sources"]})
report = {"schema_version": 1, "schema_reference": "https://raw.githubusercontent.com/niftools/nifxml/master/nif.xml",
    "source_zone_report_sha256": hashlib.sha256((ROOT / "evidence/school-zone-export.json").read_bytes()).hexdigest(),
    "counts": dict(Counter(r["mode"] for r in records)), "records": records,
    "limitations": ["Only direct NiAlphaProperty on exported shape blocks is catalogued; inherited/custom shader states remain unresolved.",
                    "ONE=0, SRC_COLOR=2, SRC_ALPHA=6 and INV_SRC_ALPHA=7 follow nifxml AlphaFunction and AlphaFlags.",
                    "Additive mapping restores source/destination blend semantics; original lighting, fog, special slots and out-of-range vertex alpha are not recovered."]}
out = ROOT / "evidence/school-material-catalog.json"
out.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps({"counts": report["counts"], "report": str(out)}, indent=2))

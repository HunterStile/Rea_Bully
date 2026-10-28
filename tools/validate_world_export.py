"""Validate every generated world GLB and placement without requiring Unreal."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import struct

from export_zone import digest


def validate_glb(path, expected):
    raw = path.read_bytes()
    assert struct.unpack_from("<III", raw) == (0x46546c67, 2, len(raw)), "GLB header/length mismatch"
    length, kind = struct.unpack_from("<I4s", raw, 12)
    assert kind == b"JSON", "First GLB chunk is not JSON"
    doc = json.loads(raw[20:20+length])
    bin_length, bin_kind = struct.unpack_from("<I4s", raw, 20+length)
    assert bin_kind == b"BIN\0" and 28+length+bin_length == len(raw), "GLB binary chunk mismatch"
    binary = memoryview(raw)[28+length:]
    for view in doc["bufferViews"]:
        assert view["byteOffset"] % 4 == 0, "Misaligned buffer view"
        assert view["byteOffset"] + view["byteLength"] <= bin_length, "Buffer view exceeds BIN chunk"
    for accessor in doc["accessors"]:
        assert all(math.isfinite(v) for v in accessor.get("min", []) + accessor.get("max", [])), "Nonfinite accessor bounds"
        if accessor["componentType"] != 5126:
            continue
        view = doc["bufferViews"][accessor["bufferView"]]
        start = view["byteOffset"] + accessor.get("byteOffset", 0)
        size = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[accessor["type"]] * accessor["count"] * 4
        assert all(math.isfinite(v[0]) for v in struct.iter_unpack("<f", binary[start:start+size])), "Nonfinite original attribute reached output"
    vertices = triangles = 0
    for mesh in doc["meshes"]:
        for primitive in mesh["primitives"]:
            position = doc["accessors"][primitive["attributes"]["POSITION"]]
            indices = doc["accessors"][primitive["indices"]]
            assert indices["count"] % 3 == 0, "Triangle index count is not divisible by three"
            view = doc["bufferViews"][indices["bufferView"]]
            fmt, size = {5123: ("<H", 2), 5125: ("<I", 4)}[indices["componentType"]]
            start = view["byteOffset"] + indices.get("byteOffset", 0)
            assert all(i[0] < position["count"] for i in struct.iter_unpack(fmt, binary[start:start+indices["count"]*size])), "Triangle references absent vertex"
            vertices += position["count"]
            triangles += indices["count"] // 3
    assert len(doc["meshes"]) == expected["shapes"], "Shape count differs from export report"
    assert vertices == expected["vertices"] and triangles == expected["triangles"], "Geometry counts differ from report"
    return {"shapes": len(doc["meshes"]), "vertices": vertices, "triangles": triangles}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("evidence/world-export.json"))
    parser.add_argument("--output", type=Path, default=Path("evidence/world-export-validation.json"))
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    errors, checked = [], []
    for model in report["models"]:
        if model["status"] != "exported":
            continue
        try:
            path = Path(model["output"])
            assert digest(path) == model["output_sha256"], "Generated GLB SHA-256 differs from report"
            counts = validate_glb(path, model["export"])
            checked.append({"model_key": model["model_key"], "sha256": model["output_sha256"], **counts})
        except (AssertionError, OSError, ValueError, KeyError, struct.error) as exc:
            errors.append({"model_key": model["model_key"], "error": str(exc)})
    placements = [p for z in report["zones"] for p in z["placements"]]
    finite_transforms = all(all(math.isfinite(v) for v in (*p["unreal_transform"]["translation_cm"],
        *p["unreal_transform"]["rotation_quaternion_xyzw"], *p["unreal_transform"]["scale_xyz"],
        *p["gltf_matrix_column_major"])) for p in placements)
    if not finite_transforms:
        errors.append({"error": "Nonfinite generated placement transforms"})
    result = {"schema_version": 1, "passed": not errors and report.get("complete", False),
        "source_report_sha256": digest(args.report), "models_by_status": dict(Counter(m["status"] for m in report["models"])),
        "glbs_checked": len(checked), "placements_checked": len(placements), "all_placement_transforms_finite": finite_transforms,
        "checks": ["SHA-256 output", "GLB chunk extents", "aligned buffer views", "all float attributes finite", "triangle index bounds", "geometry totals match export", "placement transforms finite"],
        "errors": errors, "models": checked}
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "models"}, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()

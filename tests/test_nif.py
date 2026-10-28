"""Reference pixel vectors, bounds checks, and optional owned-game integration."""
from io import BytesIO
import json
import hashlib
import math
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from export_nif import FormatError, Glb, Nif, Reader, decode_pixels, determinant3, export, placement_matrix, point, read_instances
from export_zone import ResolutionError, choose_definition, resolve_asset


class PixelTests(unittest.TestCase):
    def test_bc1_reference_red(self):
        from PIL import Image
        pixel = {"mips": [(4, 4, 0)], "format": 4,
                 "pixels": struct.pack("<HHI", 0xF800, 0x0000, 0)}
        png, width, height = decode_pixels(None, pixel)
        self.assertEqual((width, height), (4, 4))
        self.assertEqual(set(Image.open(BytesIO(png)).getdata()), {(255, 0, 0, 255)})

    def test_bc3_reference_transparent_white(self):
        from PIL import Image
        pixel = {"mips": [(4, 4, 0)], "format": 6,
                 "pixels": bytes(8) + struct.pack("<HHI", 0xFFFF, 0, 0)}
        png, _, _ = decode_pixels(None, pixel)
        self.assertEqual(set(Image.open(BytesIO(png)).getdata()), {(255, 255, 255, 0)})

    def test_block_bounds_fail_loudly(self):
        with self.assertRaises(FormatError):
            Reader(b"\0\0").read("I")
        with self.assertRaises(FormatError):
            Reader(b"\0").array("I", 2**32)

    def test_nonfinite_uv_rejected_even_without_json_bounds(self):
        with self.assertRaises(FormatError):
            Glb().accessor([(float("nan"), 0)], "f", "VEC2", 5126)


class TransformTests(unittest.TestCase):
    def test_ipb_quaternion_conjugation_and_gltf_axes(self):
        instance = {"position": [0, 0, 0], "scale": [1, 1, 1],
                    "quaternion_xyzw": [0, 0, math.sqrt(0.5), math.sqrt(0.5)]}
        actual = point(placement_matrix(instance), [1, 0, 0])
        for value, expected in zip(actual, [0, 0, 1]):
            self.assertAlmostEqual(value, expected)

    def test_nonuniform_normal_uses_inverse_transpose(self):
        instance = {"position": [8, 9, 10], "scale": [2, 1, 1], "quaternion_xyzw": [0, 0, 0, 1]}
        actual = point(placement_matrix(instance), [1, 1, 0], normal=True)
        for value, expected in zip(actual, [1 / math.sqrt(5), 0, -2 / math.sqrt(5)]):
            self.assertAlmostEqual(value, expected)

    def test_mirror_is_detected(self):
        instance = {"position": [0, 0, 0], "scale": [-1, 1, 1], "quaternion_xyzw": [0, 0, 0, 1]}
        self.assertLess(determinant3(placement_matrix(instance)), 0)

    def test_conflicting_ide_definition_is_not_selected_by_order(self):
        row = {"model_id": 7, "name": "model", "texture_dictionary": "one", "section": "objs", "source": "a.ide", "line": 2}
        other = {**row, "texture_dictionary": "two", "source": "b.ide"}
        with self.assertRaises(ResolutionError):
            choose_definition({"model_id": 7, "name": "model"}, {7: [row, other]})


WORLD = Path(__file__).resolve().parents[1] / "local/extracted/Stream/World"


@unittest.skipUnless((WORLD / "11187/70wagon.nif").exists(), "Requires owned Bully archive extraction")
class LocalGameTests(unittest.TestCase):
    def test_original_vehicle_to_glb(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "vehicle.glb"
            report = export(WORLD / "11187/70wagon.nif", [WORLD / "11214/70wagon.nft"], output)
            # Independent format reference counts for this vehicle.
            self.assertEqual((report["shapes"], report["vertices"], report["triangles"], report["texture_count"]),
                             (64, 2723, 2022, 6))
            self.assertEqual(report["missing_textures"], [])
            self.assertEqual(report["original_vertex_alpha_range_ignored"][1], 100)
            data = output.read_bytes()
            self.assertEqual(struct.unpack_from("<III", data), (0x46546C67, 2, len(data)))
            size, kind = struct.unpack_from("<I4s", data, 12)
            self.assertEqual(kind, b"JSON")
            document = json.loads(data[20:20+size])
            bin_size, bin_kind = struct.unpack_from("<I4s", data, 20+size)
            self.assertEqual(bin_kind, b"BIN\0")
            self.assertEqual(len(document["images"]), 6)
            self.assertEqual(len(document["meshes"]), 64)
            for mesh in document["meshes"]:
                for primitive in mesh["primitives"]:
                    color = primitive["attributes"].get("COLOR_0")
                    if color is not None:
                        self.assertEqual(document["accessors"][color]["type"], "VEC3")
            for view in document["bufferViews"]:
                self.assertEqual(view["byteOffset"] % 4, 0)
                self.assertLessEqual(view["byteOffset"] + view["byteLength"], bin_size)
            for accessor in document["accessors"]:
                for value in accessor.get("min", []) + accessor.get("max", []):
                    self.assertTrue(math.isfinite(value))

    def test_school_placement_from_owned_ipb(self):
        instances = read_instances(WORLD / "07582/tschool.ipb")
        main = next(i for i in instances if i["name"] == "SC1b_bldg_main")
        self.assertEqual(main["model_id"], 5143)
        self.assertAlmostEqual(main["position"][0], 186.239, places=3)
        self.assertEqual(main["scale"], [1, 1, 1])

    def test_real_nif_truncation_rejected(self):
        data = (WORLD / "11187/70wagon.nif").read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            bad = Path(directory) / "truncated.nif"
            bad.write_bytes(data[:512])
            with self.assertRaises(FormatError):
                Nif(bad)

    def test_differing_duplicate_payloads_with_same_model_name_stay_ambiguous(self):
        original = (WORLD / "05422/SC1b_bldg_main.nif").read_bytes()
        self.assertIn(b"Scene Root", original)
        changed = original.replace(b"Scene Root", b"scene Root", 1)
        with tempfile.TemporaryDirectory() as directory:
            candidates = []
            for index, data in enumerate((original, changed)):
                path = Path(directory) / f"candidate{index}.nif"
                path.write_bytes(data)
                candidates.append({"index": index, "path": str(path), "manifest_sha256": hashlib.sha256(data).hexdigest()})
            with self.assertRaises(ResolutionError):
                resolve_asset("SC1b_bldg_main.nif", {"sc1b_bldg_main.nif": candidates}, model_name="SC1b_bldg_main")
            # Equal payloads are explicitly equivalent, not an ambiguity.
            same = [candidates[0], {**candidates[0], "index": 2}]
            _, decision = resolve_asset("SC1b_bldg_main.nif", {"sc1b_bldg_main.nif": same}, model_name="SC1b_bldg_main")
            self.assertIn("identical verified bytes", decision["selection_reason"])


if __name__ == "__main__":
    unittest.main()

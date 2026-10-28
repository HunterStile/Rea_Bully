"""Original item schema, unconditional placement provenance and real GLB checks."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from catalog_world_specials import parse_ipb
from export_collectables import read_item_definitions, select_item_definition, verified_pending_groups
from export_nif import FormatError, Nif, load_textures
from export_zone import digest
from validate_world_export import validate_glb


class ItemSchemaTests(unittest.TestCase):
    def test_item_section_and_exact_id_name_select_preserve_source_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "default.ide"
            path.write_text("objs\n500,Wrong,Wrong,1,20\nend\nitem\n500\tRubBand\tRubBand # original\n474, GeekCard, GeekCard\nend\n")
            definitions = read_item_definitions(path)
            selected = select_item_definition({"model_id": 500, "name": "Rubband"}, definitions)
            self.assertEqual(selected["name"], "RubBand")
            self.assertEqual(selected["texture_dictionary"], "RubBand")
            self.assertEqual(selected["line"], 5)
            self.assertEqual(selected["source_sha256"], digest(path))
            with self.assertRaises(FormatError):
                select_item_definition({"model_id": 500, "name": "GeekCard"}, definitions)

    def test_conflicting_definitions_and_unrecognized_item_columns_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "default.ide"
            path.write_text("item\n500 RubBand Original\n500 RubBand Different\nend\n")
            with self.assertRaises(FormatError):
                select_item_definition({"model_id": 500, "name": "RubBand"}, read_item_definitions(path))
            path.write_text("item\n500 RubBand Original unexpected\nend\n")
            with self.assertRaises(FormatError):
                read_item_definitions(path)


class ItemSourceTests(unittest.TestCase):
    def fixture(self, root):
        raw = struct.pack("<i64sf3f3f4f2I", 500, b"Rubband", 1., 1., 2., 3., 1., 1., 1., 0., 0., 0., 1., 0, 123)
        data = b"Ipl$" + b"\0"*15 + struct.pack("<I", 2) + b"inst" + struct.pack("<I", 0) + b"inst" + struct.pack("<I", 1) + raw
        path = root / "zone.ipb"; path.write_bytes(data)
        source_sha = digest(path)
        record = parse_ipb(data)["sections"][1]["records"][0]
        reference = {"zone": "zone", "source_ipb": str(path), "source_ipb_sha256": source_sha, "source_archive_index": 10,
                     **{k: record[k] for k in ("section_index", "record_index", "record_offset", "record_bytes", "record_sha256")}}
        pending = {"source": reference, "original_record": record, "model_key_candidate": "00500_rubband"}
        catalog = {"models": [], "zones": [{"name": "zone", "source_ipb_sha256": source_sha, "supplementary_pending": [pending]}],
                   "supplementary_extension": {"pending_placements": [pending]}}
        ide = root / "default.ide"; ide.write_text("item\n500 RubBand RubBand\nend\n")
        return catalog, read_item_definitions(ide), path

    def test_pending_item_group_preserves_original_source_transform(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog, definitions, _ = self.fixture(Path(directory))
            groups = verified_pending_groups(catalog, definitions)
            self.assertEqual(list(groups), ["00500_rubband"])
            self.assertEqual(groups["00500_rubband"]["placements"][0]["record"]["position"], [1., 2., 3.])
            self.assertEqual(groups["00500_rubband"]["placements"][0]["source"]["section_index"], 1)

    def test_changed_raw_source_report_transform_or_section_is_rejected(self):
        for mutation in ("source", "transform", "section"):
            with tempfile.TemporaryDirectory() as directory:
                catalog, definitions, path = self.fixture(Path(directory))
                pending = catalog["zones"][0]["supplementary_pending"][0]
                if mutation == "source":
                    path.write_bytes(path.read_bytes() + b"\0")
                elif mutation == "transform":
                    pending["original_record"]["position"][0] += 1
                else:
                    pending["source"]["section_index"] = 0
                with self.subTest(mutation=mutation), self.assertRaises(FormatError):
                    verified_pending_groups(catalog, definitions)


class LocalItemGeometryTests(unittest.TestCase):
    def test_all_three_real_conversions_keep_valid_geometry_and_original_hashes(self):
        path = Path(__file__).resolve().parents[1] / "evidence/collectables-export-v04.json"
        if not path.exists():
            self.skipTest("Local item conversion report unavailable")
        report = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(report["passed"])
        self.assertEqual(report["summary"]["source_item_placements"], 121)
        self.assertEqual({m["model_key"] for m in report["models"]}, {"00500_rubband", "00474_geekcard", "00476_radio"})
        for model in report["models"]:
            self.assertEqual(digest(Path(model["output"])), model["output_sha256"])
            self.assertEqual(digest(Path(model["export"]["source"])), model["export"]["source_sha256"])
            self.assertEqual(validate_glb(Path(model["output"]), model["export"]), model["validation"])
            self.assertFalse(model["export"]["missing_textures"])
            self.assertFalse(model["export"]["geometry_omissions"])
            if model["model_key"] == "00500_rubband":
                self.assertTrue(model["shading_pending"])
                self.assertEqual(model["export"]["invalid_color_policy"], "base_material_only")
                self.assertEqual(len(model["export"]["vertex_color_omissions"]), 1)
            else:
                self.assertFalse(model["export"]["vertex_color_omissions"])

    def test_original_vertices_indices_uvs_and_embedded_texture_bytes_are_preserved(self):
        path = Path(__file__).resolve().parents[1] / "evidence/collectables-export-v04.json"
        if not path.exists():
            self.skipTest("Local item conversion report unavailable")
        report = json.loads(path.read_text(encoding="utf-8"))
        for model in report["models"]:
            raw = Path(model["output"]).read_bytes()
            length = struct.unpack_from("<I", raw, 12)[0]
            doc, binary = json.loads(raw[20:20+length]), raw[28+length:]
            def view_bytes(index):
                view = doc["bufferViews"][index]
                return binary[view["byteOffset"]:view["byteOffset"]+view["byteLength"]]
            def attribute_bytes(index):
                accessor = doc["accessors"][index]
                self.assertEqual(accessor.get("byteOffset", 0), 0)
                return view_bytes(accessor["bufferView"])
            nif = Nif(Path(model["export"]["source"]))
            data = [nif.block(i) for i, kind in enumerate(nif.types) if kind in {"NiTriShapeData", "NiTriStripsData"}]
            self.assertEqual(len(data), 1)
            primitive = doc["meshes"][0]["primitives"][0]
            positions = list(struct.iter_unpack("<3f", attribute_bytes(primitive["attributes"]["POSITION"])))
            # These three original models have only identity transforms below
            # their model root; source model-root transforms are separately
            # retained in the report and excluded by the explicit entity frame.
            self.assertEqual(positions, [(v[0], v[2], -v[1]) for v in data[0]["vertices"]])
            expected_indices = b"".join(struct.pack("<3H", *triangle) for triangle in data[0]["triangles"])
            self.assertEqual(attribute_bytes(primitive["indices"]), expected_indices)
            self.assertEqual(model["export"]["source_uv_selection"][0]["selected_base_uv_set"], 0)
            expected_uv = b"".join(struct.pack("<2f", u, 1-v) for u, v in data[0]["uv_sets"][0])
            self.assertEqual(attribute_bytes(primitive["attributes"]["TEXCOORD_0"]), expected_uv)
            textures = load_textures([Path(t["path"]) for t in model["export"]["texture_sources"]])
            for image in doc["images"]:
                self.assertEqual(view_bytes(image["bufferView"]), textures[image["name"]]["png"])


if __name__ == "__main__":
    unittest.main()

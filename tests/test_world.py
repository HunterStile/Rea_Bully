"""World placement coordinate contracts and original local inventory coverage."""
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from export_nif import IDENTITY, point, placement_matrix, select_static_geometry, FormatError, Nif, export
from export_world import gltf_matrix, unreal_transform, world_bounds, current_checkpoint
from export_zone import digest
from catalog_world_placements import placement_key, owner_order


class WorldTransformTests(unittest.TestCase):
    def test_dedup_treats_q_and_negative_q_as_same_rotation_but_keeps_distinct_positions(self):
        original = {"model_key": "00001_model", "position": [1, 2, 3], "scale": [1, 1, 1], "quaternion_xyzw": [0, 0, 0, 1]}
        equivalent = {**original, "quaternion_xyzw": [0, 0, 0, -1]}
        nearby = {**original, "position": [1.000000001, 2, 3]}
        self.assertEqual(placement_key(original), placement_key(equivalent))
        self.assertNotEqual(placement_key(original), placement_key(nearby))

    def test_known_quarter_turn_yields_unreal_positive_y(self):
        instance = {"position": [1, 2, 3], "scale": [1, 1, 1],
            "quaternion_xyzw": [0, 0, math.sqrt(.5), math.sqrt(.5)]}
        actual = unreal_transform(instance)
        self.assertEqual(actual["translation_cm"], [100, -200, 300])
        self.assertEqual(actual["rotation_quaternion_xyzw"], instance["quaternion_xyzw"])
        # Original conjugated source rotation sends X to -Y; glTF sends it +Z.
        matrix = gltf_matrix(instance)
        result = [matrix[row] + matrix[12+row] for row in range(3)]
        for actual, expected in zip(result, [1, 3, -1]):
            self.assertAlmostEqual(actual, expected)

    def test_gltf_matrix_matches_baked_export_with_nonuniform_and_mirrored_scale(self):
        instance = {"position": [17, -3, 9], "scale": [-2, 3, .5],
            "quaternion_xyzw": [0, math.sqrt(.5), 0, math.sqrt(.5)]}
        matrix = gltf_matrix(instance)
        source_point = [2, 3, 4]
        gltf_point = point(IDENTITY, source_point)
        actual = [sum(matrix[col*4+row] * gltf_point[col] for col in range(3)) + matrix[12+row] for row in range(3)]
        expected = point(placement_matrix(instance), source_point)
        for a, b in zip(actual, expected):
            self.assertAlmostEqual(a, b)

    def test_world_bounds_include_negative_scale_and_metres_to_centimetres(self):
        instance = {"position": [1, 2, 3], "scale": [-2, 3, 1], "quaternion_xyzw": [0, 0, 0, 1]}
        actual = world_bounds(instance, {"min": [0, 0, 0], "max": [100, 200, 100]})
        self.assertEqual(actual, {"min": [-100, -200, 300], "max": [100, 400, 400]})

    def test_resume_rejects_changed_generated_or_source_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source, target = directory / "source", directory / "target"
            source.write_bytes(b"original")
            target.write_bytes(b"derived")
            record = {"status": "exported", "output": str(target), "output_sha256": digest(target),
                "export": {"source": str(source), "source_sha256": digest(source), "texture_sources": [], "source_uv_selection": []}}
            self.assertTrue(current_checkpoint(record))
            source.write_bytes(b"changed")
            self.assertFalse(current_checkpoint(record))


class OriginalWorldInventoryTests(unittest.TestCase):
    def test_every_original_instance_is_represented_once_in_each_zone(self):
        path = Path(__file__).resolve().parents[1] / "evidence/world-coverage.json"
        if not path.exists():
            self.skipTest("Local verified world inventory unavailable")
        report = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(report["zones"]), 85)
        self.assertEqual(sum(z["placement_count"] for z in report["zones"]), 7689)
        keys = {m["model_key"] for m in report["models"]}
        self.assertEqual(len(keys), 2989)
        for zone in report["zones"]:
            self.assertEqual([p["instance_index"] for p in zone["placements"]], list(range(zone["placement_count"])))
            self.assertTrue(all(p["model_key"] in keys for p in zone["placements"]))


class SourceGeometryRecoveryTests(unittest.TestCase):
    class FakeNif:
        def __init__(self, uv_index):
            self.uv_index = uv_index

        def block(self, _):
            return {"type": "NiTexturingProperty", "slots": [{"flags": 0x3200 | self.uv_index}]}

    def mesh(self):
        return {"vertices": [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)],
            "normals": [], "colors": [], "triangles": [(0, 1, 2), (1, 2, 3)],
            "uv_sets": [[(0, 0), (1, 0), (0, 1), (float("nan"), 1)], [(0, 0), (1, 0), (0, 1), (1, 1)]]}

    def test_base_map_uses_source_uv_channel_in_flags(self):
        mesh = self.mesh()
        actual, metadata = select_static_geometry(self.FakeNif(1), {"properties": [7]}, mesh)
        self.assertEqual(actual["uv_sets"], [mesh["uv_sets"][1]])
        self.assertEqual(metadata, {"selected_base_uv_set": 1})

    def test_missing_source_uv_channel_is_rejected(self):
        with self.assertRaises(FormatError):
            select_static_geometry(self.FakeNif(2), {"properties": [7]}, self.mesh())

    def test_original_max_uv_scale_is_centered_before_gltf_v_flip(self):
        class TransformedNif(self.FakeNif):
            def block(self, index):
                result = super().block(index)
                result["slots"][0]["uv_transform"] = (0, 0, 2, 2, 0, 1, .5, .5)
                return result
        actual, metadata = select_static_geometry(TransformedNif(1), {"properties": [7]}, self.mesh())
        self.assertEqual(actual["uv_sets"][0][0], (-.5, -.5))
        self.assertEqual(actual["uv_sets"][0][-1], (1.5, 1.5))
        self.assertEqual(metadata["base_uv_transform"][5], 1)

    def test_real_custom_shader_maps_parse_to_end_of_original_block(self):
        inventory = Path(__file__).resolve().parents[1] / "evidence/world-coverage.json"
        if not inventory.exists():
            self.skipTest("Requires original owned-game extraction")
        world = inventory.parents[1] / "local/extracted/Stream/World"
        paths = list(world.glob("*/DL_waterinAS_A.nif"))
        if not paths:
            self.skipTest("Original custom shader fixture unavailable")
        nif = Nif(paths[0])
        maps = [nif.block(i)["shader_slots"] for i, kind in enumerate(nif.types) if kind == "NiTexturingProperty"]
        self.assertEqual([m["map_id"] for group in maps for m in group if m], [0, 1])

    def test_omission_retains_only_original_faces_and_index_mapping(self):
        actual, metadata = select_static_geometry(self.FakeNif(0), {"properties": [7]}, self.mesh(), "omit_invalid_faces")
        self.assertEqual(actual["triangles"], [(0, 1, 2)])
        self.assertEqual(metadata["omitted_source_triangle_indices"], [1])
        self.assertEqual(metadata["exported_vertex_to_source_vertex"], [0, 1, 2])
        self.assertEqual(metadata["nonfinite_source_vertex_indices"], [3])
        self.assertTrue(all(math.isfinite(v) for uv in actual["uv_sets"][0] for v in uv))

    def test_base_material_only_preserves_geometry_without_normalising_invalid_rgb(self):
        mesh = self.mesh()
        mesh["colors"] = [(100, 100, 100, 100)] * 4
        actual, metadata = select_static_geometry(self.FakeNif(1), {"properties": [7]}, mesh,
            invalid_color_policy="base_material_only")
        self.assertEqual(actual["vertices"], mesh["vertices"])
        self.assertEqual(actual["triangles"], mesh["triangles"])
        self.assertEqual(actual["colors"], [])
        omitted = metadata["vertex_color_omission"]
        self.assertEqual((omitted["source_rgb_min"], omitted["source_rgb_max"]), (100, 100))
        self.assertTrue(omitted["shading_pending"])
        self.assertEqual(len(omitted["source_color_float32_rgba_sha256"]), 64)

    def test_real_nan_uvs_are_rejected_by_default_and_explicitly_bounded_in_recovery(self):
        source = Path(__file__).resolve().parents[1] / "local/extracted/Stream/World/04412/BU1p_cliff01.nif"
        if not source.exists():
            self.skipTest("Requires original owned-game NIF")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "cliff.glb"
            with self.assertRaises(FormatError):
                export(source, [], target)
            result = export(source, [], target, invalid_geometry_policy="omit_invalid_faces")
            self.assertEqual(sum(len(m["omitted_source_triangle_indices"]) for m in result["geometry_omissions"]), 4)
            self.assertEqual(len(result["geometry_omissions"]), 1)


if __name__ == "__main__":
    unittest.main()

"""Supplementary joins preserve source identity and do not activate specials."""
import copy
import hashlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from catalog_world_specials import parse_ipb
from extend_world_placements import extend_catalog
from export_nif import FormatError
from export_world import world_bounds


def record(model_id=12, name=b"test", x=1., tag="inst"):
    return struct.pack("<i64sf3f3f4f" + ("4I" if tag == "spec" else "2I"),
        model_id, name, 1., x, 2., 3., -1., 2., 1., 0., 0., 0., 1., 17, 19,
        *([123, 5] if tag == "spec" else []))


class SupplementaryJoinTests(unittest.TestCase):
    def fixture(self, root):
        chunks = [("inst", [record()]),
                  ("inst", [record(x=9.), record(500, b"RubBand", x=12.)]),
                  ("spec", [record(x=20., tag="spec")])]
        data = b"Ipl$" + b"\0"*15 + struct.pack("<I", len(chunks)) + b"".join(
            tag.encode() + struct.pack("<I", len(records)) + b"".join(records) for tag, records in chunks)
        ipb = root / "zone.ipb"
        ipb.write_bytes(data)
        ipb_sha = hashlib.sha256(data).hexdigest()
        parsed = parse_ipb(data)
        parsed.update(name="zone", source_ipb=str(ipb), source_ipb_sha256=ipb_sha, source_archive_index=10, status="parsed")
        glb = root / "test.glb"
        glb.write_bytes(b"existing unchanged GLB payload")
        entity_bounds = {"min": [0, 0, 0], "max": [100, 100, 100]}
        first = dict(parsed["sections"][0]["records"][0], model_key="00012_test", instance_index=0, status="exported")
        first["world_bounds_unreal_cm"] = world_bounds(first, entity_bounds)
        model = {"model_key": "00012_test", "model_id": 12, "name": "test", "status": "exported",
                 "output": str(glb), "output_sha256": hashlib.sha256(glb.read_bytes()).hexdigest(),
                 "bounds_entity_unreal_cm": entity_bounds, "placement_count": 1, "zones": ["zone"]}
        baseline = {"source_world_sha256": "original archive", "complete": True,
                    "models": [model], "zones": [{"name": "zone", "placements": [first], "placement_count": 1,
                    "source_ipb_sha256": ipb_sha, "zone_manifest": "unchanged old manifest", "zone_manifest_sha256": "old"}],
                    "summary": {"placement_count": 1, "placements_by_status": {"exported": 1}}}
        specials = {"existing_static_catalog_sha256_read_only": "baseline sha", "source_world_sha256": "original archive",
                    "zones": [parsed], "errors": []}
        return baseline, specials, glb

    def test_adds_known_static_only_and_retains_unknown_and_conditional_records(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, specials, glb = self.fixture(Path(directory))
            before = copy.deepcopy(baseline)
            result = extend_catalog(baseline, specials, "baseline sha")
            self.assertEqual(baseline, before)
            self.assertEqual(len(result["models"]), 1)
            zone = result["zones"][0]
            self.assertEqual(zone["placement_count"], 2)
            self.assertEqual(zone["source_placement_count"], 3)
            self.assertEqual(zone["placements"][1]["unreal_transform"]["translation_cm"], [900., -200., 300.])
            self.assertEqual(zone["placements"][1]["source_ipb_record"]["section_index"], 1)
            self.assertEqual(zone["placements"][1]["instance_index"], 1)
            self.assertEqual(result["supplementary_extension"]["summary"]["spec_records_excluded"], 1)
            self.assertEqual(zone["supplementary_pending"][0]["model_key_candidate"], "00500_rubband")
            self.assertEqual(result["models"][0]["placement_count"], 2)
            self.assertNotIn("zone_manifest", zone)
            self.assertEqual(hashlib.sha256(glb.read_bytes()).hexdigest(), baseline["models"][0]["output_sha256"])

    def test_rejects_changed_source_bytes_or_changed_existing_glb(self):
        for target in ["ipb", "glb"]:
            with tempfile.TemporaryDirectory() as directory:
                baseline, specials, glb = self.fixture(Path(directory))
                path = Path(specials["zones"][0]["source_ipb"]) if target == "ipb" else glb
                path.write_bytes(path.read_bytes() + b"changed")
                with self.assertRaises(FormatError):
                    extend_catalog(baseline, specials, "baseline sha")

    def test_rejects_report_transform_tampering_wrong_baseline_and_double_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, specials, _ = self.fixture(Path(directory))
            with self.assertRaises(FormatError):
                extend_catalog(baseline, specials, "different baseline")
            changed = copy.deepcopy(specials)
            changed["zones"][0]["sections"][1]["records"][0]["position"][0] += 1
            with self.assertRaises(FormatError):
                extend_catalog(baseline, changed, "baseline sha")
            extended = extend_catalog(baseline, specials, "baseline sha")
            with self.assertRaises(FormatError):
                extend_catalog(extended, specials, "baseline sha")


if __name__ == "__main__":
    unittest.main()

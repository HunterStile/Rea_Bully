"""Placement boundaries, conditional state and padded Lua evidence contracts."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from catalog_world_specials import evaluate_condition, parse_ipb, script_evidence
from export_nif import FormatError, read_instances


def packed_placement(tag="inst", name=b"test", kind=5, condition=123):
    return struct.pack("<i64sf3f3f4f" + ("4I" if tag == "spec" else "2I"),
        12, name, 1.0, 1., 2., 3., -1., 2., 1., 0., 0., 0., 1.,
        17, 19, *([condition, kind] if tag == "spec" else []))


def ipb(sections):
    return b"Ipl$" + b"\0"*15 + struct.pack("<I", len(sections)) + b"".join(
        tag.encode("ascii") + struct.pack("<I", len(records)) + b"".join(records)
        for tag, records in sections)


class IPBTests(unittest.TestCase):
    def test_repeated_inst_and_spec_use_declared_boundaries_not_embedded_tags(self):
        data = ipb([("inst", [packed_placement(name=b"specinst")]),
                    ("prop", [b"\x04spec"]),
                    ("inst", [packed_placement()]),
                    ("spec", [packed_placement("spec", kind=5, condition=0x7a5a89f2)])])
        result = parse_ipb(data + b"\0"*32)
        self.assertEqual([s["tag"] for s in result["sections"]], ["inst", "prop", "inst", "spec"])
        record = result["sections"][-1]["records"][0]
        self.assertEqual(record["condition_hash_120"], 0x7a5a89f2)
        self.assertEqual(record["condition_kind_124"], 5)
        self.assertEqual(record["instance_name_hash_116"], 19)
        self.assertEqual(record["unreal_transform"]["translation_cm"], [100., -200., 300.])
        self.assertEqual(result["parsed_end_offset"], len(data))
        self.assertEqual(result["zero_sector_padding_bytes"], 32)

    def test_perm_uses_fourth_u32_for_payload_count(self):
        # REA decompilation misidentifies local stack values; disassembly at
        # 0x433967 / 0x4339af / 0x4339f6 uses the fourth field at esp+0x10.
        perm = b"\x03abc" + struct.pack("<4I", 90, 91, 92, 2) + struct.pack("<2f", 1., 2.)
        result = parse_ipb(ipb([("perm", [perm]), ("inst", [packed_placement()])]))
        self.assertEqual(result["sections"][0]["records"][0]["entry_count"], 2)
        self.assertEqual(result["sections"][1]["records"][0]["name"], "test")

    def test_reject_truncation_unknown_tag_count_and_nonzero_trailing_payload(self):
        good = ipb([("inst", [packed_placement()])])
        for data in [good[:-1], good + b"\x01", ipb([("zzzz", [])]),
                     good[:19] + struct.pack("<I", 100001) + good[23:]]:
            with self.subTest(data_len=len(data)), self.assertRaises(FormatError):
                parse_ipb(data)

    def test_all_original_first_sections_match_independent_existing_parser(self):
        root = Path(__file__).resolve().parents[1]
        source = root / "evidence/world-specials.json"
        if not source.exists():
            self.skipTest("Local catalog unavailable")
        catalog = json.loads(source.read_text(encoding="utf-8"))
        total_inst = total_spec = 0
        for zone in catalog["zones"]:
            path = Path(zone["source_ipb"])
            if not path.exists():
                self.skipTest("Local original IPBs unavailable")
            data = path.read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), zone["source_ipb_sha256"])
            parsed = parse_ipb(data)
            first = parsed["sections"][0]
            self.assertEqual(first["tag"], "inst")
            independent = read_instances(path)
            self.assertEqual(len(first["records"]), len(independent))
            for actual, old in zip(first["records"], independent):
                for key in ["model_id", "name", "position", "scale", "quaternion_xyzw"]:
                    self.assertEqual(actual[key], old[key])
            total_inst += sum(s["record_count"] for s in parsed["sections"] if s["tag"] == "inst")
            total_spec += sum(s["record_count"] for s in parsed["sections"] if s["tag"] == "spec")
        self.assertEqual(len(catalog["zones"]), 85)
        self.assertEqual((total_inst, total_spec), (7819, 1076))


class ConditionTests(unittest.TestCase):
    def test_mission_unknown_is_preserved_and_groups_need_explicit_enabling(self):
        self.assertIsNone(evaluate_condition(1, 123))
        self.assertFalse(evaluate_condition(2, 123))
        self.assertIsNone(evaluate_condition(3, 123))
        self.assertIsNone(evaluate_condition(4, 123))
        self.assertFalse(evaluate_condition(5, 123))
        self.assertTrue(evaluate_condition(5, 123, enabled_group_hashes={123}))
        self.assertIsNone(evaluate_condition(6, 123))
        self.assertTrue(evaluate_condition(7, 123))
        self.assertFalse(evaluate_condition(7, 123, active_mission_hash=123))

    def test_native_override_disable_wins_and_unhandled_kind_defaults_false(self):
        self.assertTrue(evaluate_condition(5, 123, override_hash=123, override_kind=5))
        self.assertFalse(evaluate_condition(5, 123, enabled_group_hashes={123}, override_hash=123, override_kind=0))
        self.assertIs(evaluate_condition(99, 123), False)
        self.assertFalse(evaluate_condition(5, -1, override_hash=-1, override_kind=5))


class ScriptEvidenceTests(unittest.TestCase):
    def fixture(self, root):
        original = root / "local/scripts/main.lur"
        listing = root / "local/disassembled/main.luasm"
        original.parent.mkdir(parents=True)
        listing.parent.mkdir(parents=True)
        assembled = b"\x1bLua-original-logical-content"
        original.write_bytes(assembled + b"\0"*16)
        listing.with_suffix(".lur").write_bytes(assembled)
        listing.write_text('.function main\n.constant k0 "AreaLoadSpecialEntities"\n.constant k1 "Christmas"\n'
                           'getglobal r0 k0\nloadk r1 k1\nloadbool r2 1 0\ncall r0 3 1\n', encoding="utf-8")
        sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        entry = {"input": "main.lur", "input_sha256": sha(original), "verified": True,
                 "logical_bytes": len(assembled), "allocated_bytes": len(assembled)+16,
                 "padding_bytes": 16, "zero_padding": True, "listing": "main.luasm",
                 "listing_sha256": sha(listing), "reassembled_sha256": sha(listing.with_suffix(".lur")),
                 "original_logical_sha256": hashlib.sha256(assembled).hexdigest()}
        (listing.parent / "manifest.json").write_text(json.dumps({"files": [entry]}), encoding="utf-8")
        return original, listing

    def test_admits_verified_logical_bytecode_with_zero_archive_padding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            result = script_evidence(root, {0x7a5a89f2})
            self.assertEqual(len(result["verified_listing_sources"]), 1)
            self.assertEqual(result["rejected_listings"], [])
            self.assertEqual(result["direct_group_operations"][0]["name"], "Christmas")
            self.assertTrue(result["direct_group_operations"][0]["enabled"])

    def test_rejects_changed_listing_or_nonzero_padding(self):
        for changed in ["listing", "padding"]:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                original, listing = self.fixture(root)
                if changed == "listing":
                    listing.write_text(listing.read_text().replace("Christmas", "Halloween"))
                else:
                    original.write_bytes(original.read_bytes()[:-1] + b"\x01")
                result = script_evidence(root, {0x7a5a89f2})
                self.assertEqual(result["verified_listing_sources"], [])
                self.assertEqual(len(result["rejected_listings"]), 1)


if __name__ == "__main__":
    unittest.main()

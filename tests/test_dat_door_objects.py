"""DAT association/count failures and original school-door source provenance."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from catalog_dat_door_objects import dat_groups, datload_calls, door_action_catalog, pose, select_definition, verify_archive_reference, verify_source
from catalog_door_links import name_hash
from catalog_population import fields
from export_nif import FormatError


DAT = '''NPATHS 0
NPOINTS 0
NPERIMETERS 1
NTRIGGERS 1
NPOIS 0
PERIMETER
 BEGIN
  PLACEMENT "ESCDoorL"
  ISDOOR TRUE
  HEIGHT 1.000000
  DEPTH 0.000000
  NPERIMETERPOINTS 2
  PERIMETERPOINTX 0
  PERIMETERPOINTY 1
  PERIMETERPOINTX 1
  PERIMETERPOINTY 1
 END
NTRIGGERS 1
TRIGGER
 BEGIN
  TRIGGERNAME "DT_source"
  AREACODE 2
  POSITION 1, 2, 3
  YAWPITCHROLL 10, 20, 30
  YAW -90
  ZHEIGHT 1
  ISMISSIONSPECIFIC FALSE
  ISLOCKED TRUE
  POPULATION
   BEGIN
    UNKNOWN 1
   END
 END
'''


class DatDoorParsingTests(unittest.TestCase):
    def test_group_and_nested_record_extents_preserve_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "door.dat"
            data = DAT.replace("\n", "\r\n").encode() + bytes(73)
            path.write_bytes(data)
            group, = dat_groups(path)
            self.assertEqual(group["declared_trigger_count"], 1)
            self.assertEqual(fields(group["perimeter"])["PLACEMENT"], "ESCDoorL")
            trigger, = group["triggers"]
            self.assertEqual(fields(trigger)["TRIGGERNAME"], "DT_source")
            for block in (group["perimeter"], trigger):
                raw = data[block["byte_offset"]:block["byte_offset"]+block["byte_length"]]
                self.assertEqual(hashlib.sha256(raw).hexdigest(), block["raw_sha256"])
                self.assertTrue(raw.endswith(b" END\r\n"))
            result, issues = pose(trigger)
            self.assertFalse(issues)
            self.assertEqual(result["position_unreal_cm"], [100, -200, 300])
            self.assertEqual(result["yaw_pitch_roll_degrees_raw"], [10, 20, 30])
            self.assertEqual(result["yaw_degrees_raw"], -90)
            self.assertNotIn("unreal_rotation", result)

    def test_global_count_group_count_and_nonzero_padding_are_rejected(self):
        variants = [DAT.replace("NPERIMETERS 1", "NPERIMETERS 2"),
            DAT.replace("NTRIGGERS 1", "NTRIGGERS 2", 1),
            DAT.replace("NTRIGGERS 1\nTRIGGER", "NTRIGGERS 2\nTRIGGER")]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "door.dat"
            for variant in variants:
                path.write_text(variant)
                with self.subTest(variant=variant), self.assertRaises(FormatError):
                    dat_groups(path)
            path.write_bytes(DAT.encode()+b"\0\0X")
            with self.assertRaises(ValueError):
                dat_groups(path)

    def test_duplicate_pose_field_or_invalid_flag_is_ambiguous(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "door.dat"
            for variant in (DAT.replace("  YAW -90", "  YAW -90\n  YAW 90"), DAT.replace("ISLOCKED TRUE", "ISLOCKED 1")):
                path.write_text(variant)
                trigger = dat_groups(path)[0]["triggers"][0]
                result, issues = pose(trigger)
                self.assertTrue(issues)
                self.assertNotIn("native_rotation_recipe", result)

    def test_bytecode_call_uses_full_constant_and_resets_function_scope(self):
        listing = '''.function main/f0
.constant k0 "DATLoad"
.constant k1 "tindustrial_doors.DAT"
.constant k2 5
getglobal r0 k0
loadk r1 k1 ; k1 = "tindustrial_doors.DA" (truncated)
loadk r2 k2
call r0 3 1
.function main/f1
getglobal r0 k0
loadk r1 k1
loadk r2 k2
call r0 3 1
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.luasm"
            path.write_text(listing)
            call, = datload_calls(path)
            self.assertEqual(call["dat_name"], "tindustrial_doors.DAT")
            self.assertEqual(call["slot_raw"], 5)
            self.assertEqual(call["function"], "main/f0")

    def test_native_name_hash_ambiguity_is_not_resolved_by_first_match(self):
        row = {"model_id": 10029, "name": "ESCDoorL", "texture_dictionary": "ESCDoorL", "section": "panm",
            "source": "props.ide", "source_sha256": "a", "line": 86,
            "raw_columns": ["10029", "ESCDoorL", "ESCDoorL", "ESCDoorL", "null", "0", "1", "0"]}
        hashed = name_hash("ESCDoorL")
        selected, bad = select_definition("escdoorl", {hashed: [row]})
        self.assertEqual(selected["model_id"], 10029)
        self.assertFalse(bad)
        other = {**row, "model_id": 10030}
        selected, bad = select_definition("ESCDoorL", {hashed: [row, other]})
        self.assertIsNone(selected)
        self.assertEqual(len(bad), 2)

    def test_source_hash_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.dat"
            path.write_bytes(b"original")
            reference = {"path": str(path), "sha256": hashlib.sha256(b"original").hexdigest()}
            self.assertEqual(verify_source(reference), path)
            path.write_bytes(b"changed")
            with self.assertRaises(FormatError):
                verify_source(reference)

    def test_matching_file_hash_does_not_replace_original_archive_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.nif"
            path.write_bytes(b"changed")
            reference = {"path": str(path), "sha256": hashlib.sha256(b"changed").hexdigest()}
            archive = {path.resolve(): {"index": 1, "verified": True, "extracted_sha256": hashlib.sha256(b"original").hexdigest()}}
            with self.assertRaises(FormatError):
                verify_archive_reference(reference, archive)


class LocalDoorProvenanceTests(unittest.TestCase):
    def report(self):
        path = ROOT / "evidence/door-objects-v05.json"
        if not path.is_file():
            self.skipTest("Local source-bound door catalog unavailable")
        report = json.loads(path.read_text(encoding="utf-8"))
        if not Path(report["source_manifest"]["path"]).is_file():
            self.skipTest("Original local DAT extraction unavailable")
        return report

    def test_all_reported_objects_match_original_trigger_and_perimeter_extents(self):
        report = self.report()
        by_file = {}
        for reference in report["dat_files"]:
            path = verify_source(reference)
            groups = dat_groups(path)
            by_file[path.name] = {t["line"]: (g["perimeter"], t) for g in groups for t in g["triggers"]}
            expected = sum(len(g["triggers"]) for g in groups if fields(g["perimeter"]).get("ISDOOR") is True)
            self.assertEqual(expected, reference["door_candidates"])
        self.assertEqual(len(report["objects"]), 161)
        self.assertEqual(len({o["id"] for o in report["objects"]}), 161)
        for record in report["objects"]:
            perimeter, trigger = by_file[record["dat"]][record["trigger"]["line"]]
            self.assertEqual(record["perimeter"], perimeter)
            self.assertEqual(record["trigger"], trigger)
            self.assertEqual(record["pose"]["position_source_game_m"], fields(trigger)["POSITION"])
            self.assertFalse(record["runtime_integrated"])
        selected = {r["name"]: r for r in report["objects"] if r["dat"] == "tschool_doors.dat"}
        left, right = selected["DT_tschool_SchoolFrontDoorL"], selected["tschool_SchoolFrontDoorR"]
        self.assertEqual(left["model_key"], "10029_escdoorl")
        self.assertEqual(right["model_key"], "10030_escdoorr")
        self.assertEqual(left["pose"]["position_source_game_m"], [208.039001, -73.229698, 8.57168])
        self.assertEqual(left["pose"], right["pose"])
        self.assertTrue(left["source_islocked_raw"])
        self.assertTrue(right["source_islocked_raw"])
        self.assertEqual(len(left["existing_transition_links"]), 1)
        self.assertFalse(right["existing_transition_links"])

    def test_original_cat_strings_and_agr_clips_join_without_inventing_execution(self):
        report = self.report()
        actual = door_action_catalog(ROOT, report["models"])
        self.assertEqual(actual, {k: v for k, v in report["door_action_catalog"].items() if k != "original_archive_index"})
        self.assertEqual(report["door_action_catalog"]["original_archive_index"], 6543)
        self.assertEqual({x["clip_leaf"] for x in actual["links"]}, {"IDLE", "OPEN", "CLOSE", "PARA"})
        for model in report["models"]:
            agr = verify_source(model["source_agr"]).read_bytes()
            for reference in (model["converted_glb"], model["source_nif"], model["source_group"], model["skin_report"]):
                verify_source(reference)
            for clip in model["clips"]:
                raw = agr[clip["agr_offset"]:clip["agr_offset"]+clip["agr_size"]]
                self.assertEqual(hashlib.sha256(raw).hexdigest(), clip["agr_sha256"])
            if model["model_key"] in {"10029_escdoorl", "10030_escdoorr"}:
                durations = {c["name"].split("\\")[-1]: c["duration"] for c in model["clips"]}
                self.assertAlmostEqual(durations["OPEN"], .6)
                self.assertAlmostEqual(durations["CLOSE"], 22/30)


if __name__ == "__main__":
    unittest.main()

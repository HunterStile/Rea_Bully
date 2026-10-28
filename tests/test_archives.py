"""Boundary and extraction tests for the observed external DIR/IMG layout."""

import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

from tools.bully_archives import ArchiveError, extract, inventory, read_directory, safe_name


def record(name=b"test.lur", offset=0, size=1):
    return struct.pack("<II24s", offset, size, name)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.directory = self.source / "Scripts.dir"
        self.image = self.source / "Scripts.img"

    def tearDown(self):
        self.temp.cleanup()

    def make_pair(self, records=None, payload=None):
        self.directory.write_bytes(records or record())
        self.image.write_bytes(payload if payload is not None else b"\x1bLua\x50" + bytes(2043))

    def test_extract_retains_sector_padding_and_verifies_manifest(self):
        payload = b"\x1bLua\x50" + bytes(2043) + bytes(range(256)) * 8
        self.make_pair(record() + record(b"other.lur", 1), payload)
        original_hash = hashlib.sha256(payload).hexdigest()
        out = self.root / "local" / "scripts"
        result = extract(self.directory, out, self.root)
        self.assertEqual((out / "test.lur").read_bytes(), payload[:2048])
        self.assertEqual((out / "other.lur").read_bytes(), payload[2048:])
        self.assertEqual(result["entry_count"], 2)
        self.assertTrue(result["padding_preserved"])
        self.assertFalse(result["logical_sizes_known"])
        for entry in result["entries"]:
            self.assertEqual(entry["source_extent_sha256"], entry["extracted_sha256"])
            self.assertTrue(entry["verified"])
        self.assertEqual(json.loads((out / "manifest.json").read_text()), result)
        self.assertEqual(hashlib.sha256(self.image.read_bytes()).hexdigest(), original_hash)

    def test_bounds_validation_happens_before_output_creation(self):
        for offset, size in [(1, 1), (0, 2), (0xFFFFFFFF, 0xFFFFFFFF)]:
            with self.subTest(offset=offset, size=size):
                self.make_pair(record(offset=offset, size=size))
                out = self.root / "bad"
                with self.assertRaisesRegex(ArchiveError, "bounds"):
                    extract(self.directory, out, self.root)
                self.assertFalse(out.exists())

    def test_rejects_truncated_table_and_zero_extents(self):
        for data in [b"", record()[:-1], record(size=0)]:
            with self.subTest(data=data):
                self.directory.write_bytes(data)
                with self.assertRaises(ArchiveError):
                    read_directory(self.directory)

    def test_rejects_path_traversal_and_windows_special_names(self):
        for name in [b"../x", b"..\\x", b"/tmp/x", b"C:x", b"x:y", b"CON", b"COM1.txt", b"a.", b"a ", b"a\x01b", b"\xff", b"a\0hidden"]:
            with self.subTest(name=name):
                with self.assertRaises(ArchiveError):
                    safe_name(name.ljust(24, b"\0"))

    def test_full_width_names_need_no_nul_terminator(self):
        self.assertEqual(safe_name(b"PunishmentSystemHack.lur"), "PunishmentSystemHack.lur")

    def test_duplicate_casefold_names_and_overlaps_cannot_extract(self):
        for records in [record(b"A.lur") + record(b"a.lur", 1),
                        record() + record(b"other.lur", 0)]:
            with self.subTest(records=records):
                self.make_pair(records, bytes(4096))
                with self.assertRaises(ArchiveError):
                    extract(self.directory, self.root / "out", self.root)
                self.assertFalse((self.root / "out").exists())

    def test_orphan_directory_is_inventory_evidence_not_extractable(self):
        self.directory.write_bytes(record())
        result = inventory(self.source)
        self.assertEqual(result["archive_count"], 1)
        self.assertEqual(result["paired_count"], 0)
        self.assertEqual(result["entry_count"], 1)
        self.assertEqual(result["extractable_entry_count"], 0)
        self.assertIn("missing companion IMG", result["archives"][0]["issues"][0])
        with self.assertRaises(ArchiveError):
            extract(self.directory, self.root / "out", self.root)

    def test_existing_output_and_escape_are_rejected(self):
        self.make_pair()
        existing = self.root / "existing"
        existing.mkdir()
        marker = existing / "keep"
        marker.write_text("untouched")
        with self.assertRaises(FileExistsError):
            extract(self.directory, existing, self.root)
        self.assertEqual(marker.read_text(), "untouched")
        for out in [self.root.parent / "escape", self.root, self.source / "out"]:
            with self.subTest(output=out):
                with self.assertRaises(ArchiveError):
                    extract(self.directory, out, self.root)

    def test_manifest_filename_conflict_is_rejected_before_writing(self):
        self.make_pair(record(b"MANIFEST.JSON"))
        info = inventory(self.source)["archives"][0]
        self.assertFalse(info["extraction_supported"])
        self.assertTrue(info["indexed_extraction_supported"])
        with self.assertRaisesRegex(ArchiveError, "manifest"):
            extract(self.directory, self.root / "out", self.root)
        self.assertFalse((self.root / "out").exists())

    def test_indexed_extraction_preserves_duplicate_names_and_contents(self):
        self.make_pair(record(b"same.nif") + record(b"same.nif", 1), b"a" * 2048 + b"b" * 2048)
        out = self.root / "indexed"
        result = extract(self.directory, out, self.root, indexed=True)
        self.assertTrue(result["indexed_paths"])
        self.assertEqual((out / "00000/same.nif").read_bytes(), b"a" * 2048)
        self.assertEqual((out / "00001/same.nif").read_bytes(), b"b" * 2048)
        self.assertEqual([e["path"] for e in result["entries"]], ["00000/same.nif", "00001/same.nif"])
        self.assertNotEqual(result["entries"][0]["extracted_sha256"], result["entries"][1]["extracted_sha256"])
        info = inventory(self.source)["archives"][0]
        self.assertFalse(info["extraction_supported"])
        self.assertTrue(info["indexed_extraction_supported"])

    def test_indexed_mode_still_rejects_overlapping_extents(self):
        self.make_pair(record() + record(b"other.nif", 0))
        with self.assertRaisesRegex(ArchiveError, "overlapping"):
            extract(self.directory, self.root / "out", self.root, indexed=True)
        self.assertFalse((self.root / "out").exists())

    def test_indexed_manifest_name_is_safe_under_record_directory(self):
        self.make_pair(record(b"manifest.json"))
        out = self.root / "out"
        result = extract(self.directory, out, self.root, indexed=True)
        self.assertEqual(result["entries"][0]["path"], "00000/manifest.json")
        self.assertTrue((out / "manifest.json").is_file())
        self.assertEqual((out / "00000/manifest.json").stat().st_size, 2048)

    def test_orphan_img_and_case_insensitive_companion(self):
        self.make_pair()
        self.image.rename(self.source / "Scripts.IMG")
        (self.source / "unknown.img").write_bytes(b"unknown")
        result = inventory(self.source)
        self.assertEqual(result["paired_count"], 1)
        self.assertTrue(result["archives"][0]["extraction_supported"])
        self.assertEqual(result["orphan_images"][0]["path"], "unknown.img")


if __name__ == "__main__":
    unittest.main()

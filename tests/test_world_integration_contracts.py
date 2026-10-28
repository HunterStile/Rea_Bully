"""An absent/incomplete import must fail before generated maps can change."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from world_integration_contracts import require_ambient_npcs


class ScenePreflight(unittest.TestCase):
    def test_missing_or_old_cast_report_cannot_start_new_scene(self):
        plan = {'placements': [{'ped_id': 9}, {'ped_id': 11}]}
        for report in (None, {'status': 'npc_assets_verified', 'npcs': [{'ped_id': 8}, {'ped_id': 50}]},
                       {'status': 'npc_assets_verified', 'npcs': [{'ped_id': 9}]}):
            with self.subTest(report=report), self.assertRaises(ValueError):
                require_ambient_npcs(plan, report)

    def test_named_characters_must_be_unique_and_all_imported(self):
        imported = {'status': 'npc_assets_verified', 'npcs': [{'ped_id': 9}, {'ped_id': 11}]}
        self.assertEqual(set(require_ambient_npcs({'placements': [{'ped_id': 9}, {'ped_id': 11}]}, imported)), {9, 11})
        with self.assertRaises(ValueError):
            require_ambient_npcs({'placements': [{'ped_id': 9}, {'ped_id': 9}]}, imported)


if __name__ == '__main__':
    unittest.main()

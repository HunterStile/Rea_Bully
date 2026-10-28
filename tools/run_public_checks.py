"""Run synthetic unit checks without game files, Unreal, or private evidence.

The full local suite also verifies recovered assets and requires the owner's
local data. These modules exercise parsers and contracts using synthetic input.
"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULES = (
    'test_agr',
    'test_archives',
    'test_door_links',
    'test_npc',
    'test_player_actions',
    'test_population',
    'test_world_collision_plan_adapter',
    'test_world_collision_winding',
    'test_world_collisions',
    'test_world_extensions',
    'test_world_import',
    'test_world_import_resume',
    'test_world_integration_contracts',
    'test_world_props',
    'test_world_release',
)


def main():
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT/'tests'))
    suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    # A missing dependency must not silently turn public checks into a pass.
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == '__main__':
    raise SystemExit(main())

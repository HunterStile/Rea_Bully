"""Run stdlib synthetic source checks with private data access blocked."""
from pathlib import Path
import os
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('test_agr', 'test_archives', 'test_door_links', 'test_npc', 'test_player_actions', 'test_population', 'test_world_collision_plan_adapter', 'test_world_collision_winding', 'test_world_collisions', 'test_world_extensions', 'test_world_import', 'test_world_import_resume', 'test_world_integration_contracts', 'test_world_props', 'test_world_release', 'test_area_navigation', 'test_world_material_recovery', 'test_world_prop_plan', 'test_world_tour_runner', 'test_collision_import_geometry.CollisionImportGeometry', 'test_prop_gestures.CarnivalGesturePlan.test_exact_original_six_instances_keep_pose_and_identity', 'test_prop_gestures.CarnivalGesturePlan.test_changed_model_pose_or_duplicate_instance_is_rejected', 'test_world_scope.WorldScopeTests.test_same_geometry_in_distinct_outdoor_areas_is_not_deduplicated', 'test_world_scope.WorldScopeTests.test_original_area_overrides_filename_and_unmapped_policy_is_explicit', 'test_world_scope.WorldScopeTests.test_multiple_ipbs_in_one_area_get_a_containing_owner_without_changing_pose', 'test_world_scope.WorldScopeTests.test_entry_metadata_never_becomes_grounded_or_mission_trigger', 'test_world_scope.WorldScopeTests.test_missing_original_entry_stays_pending_instead_of_using_bounds_centroid', 'test_world_scope.WorldScopeTests.test_transition_arrival_heading_is_preserved_for_the_walk', 'test_record_world_release', 'test_world_render_evidence', 'test_world_batch_assembly', 'test_world_build_coordinator')


def main():
    forbidden = []

    def reject_private_access(event, args):
        if event != 'open' or not isinstance(args[0], (str, bytes, os.PathLike)):
            return
        path = Path(os.fsdecode(args[0])).resolve()
        if any(path.is_relative_to(ROOT / name) for name in ('local', 'evidence', 'unreal')):
            forbidden.append(str(path.relative_to(ROOT)))
            raise RuntimeError('Public source checks cannot read game data, private evidence or Unreal files')
        writes = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        if path.is_relative_to(ROOT) and args[2] & writes:
            raise RuntimeError('Public source checks cannot write to the source checkout')

    sys.dont_write_bytecode = True
    sys.addaudithook(reject_private_access)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'tests'))
    suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() and not result.skipped and not forbidden else 1


if __name__ == '__main__':
    raise SystemExit(main())

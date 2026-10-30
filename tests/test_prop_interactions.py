"""Original garage scope, source proof and exclusion regressions."""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from plan_prop_interactions import CATALOG, checked_bindings, make_plan, read, require_interaction_import


class GarageInteractionPlan(unittest.TestCase):
    def test_original_dat_extents_and_own_animation_import_join(self):
        plan = make_plan()
        rows = plan['placements']
        self.assertEqual({r['source_name'] for r in rows}, {'BA_BMXGarage', 'BMX_GarageDoor'})
        self.assertEqual({(r['zone'], r['area']) for r in rows},
                         {('tbusines', 'MainMap'), ('tBMX', 'BMXTrack')})
        self.assertLess(plan['model']['original_bounds_max_error_cm'], .002)
        # These DAT creations are distinct from all four exported IPB garages.
        outside = next(r for r in rows if r['area'] == 'MainMap')
        self.assertAlmostEqual(outside['transform']['translation_cm'][0], 45849.3988, places=5)
        self.assertAlmostEqual(outside['transform']['translation_cm'][1], 31311.7004, places=5)
        self.assertEqual(plan['model']['original_clips'],
                         {'IDLE': 'BIKEGAR\\IDLE', 'OPEN': 'BIKEGAR\\OPEN', 'CLOSE': 'BIKEGAR\\CLOSE'})
        self.assertEqual(plan['summary']['new_autoplay_clips'], 0)

    def test_mission_object_never_enters_exploration_overlay(self):
        catalog = read(CATALOG)
        for record in catalog['garage_dat_bindings'][0]['trigger']['fields']:
            if record['key'] == 'ISMISSIONSPECIFIC':
                record['value'] = True
        with self.assertRaisesRegex(ValueError, 'DAT binding changed'):
            list(checked_bindings(catalog))

    def test_shared_group_does_not_authorize_another_model_or_trigger(self):
        original = read(CATALOG)
        for field, value in [('model_key', '10195_sbikegar'), ('id', 'arbitrary.dat:23'),
                             ('trigger_name', 'other_garage'), ('source_isdoor_raw', True)]:
            catalog = copy.deepcopy(original)
            catalog['garage_dat_bindings'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                list(checked_bindings(catalog))

    def test_callback_direction_and_dat_position_cannot_be_relabelled(self):
        original = read(CATALOG)
        catalog = copy.deepcopy(original)
        catalog['garage_dat_bindings'][0]['script_callbacks'][0]['node_path'] = '/Global/Door/DoorFunctions/Opening/BIKEGAR'
        with self.assertRaisesRegex(ValueError, 'callback join changed'):
            list(checked_bindings(catalog))
        catalog = copy.deepcopy(original)
        catalog['garage_dat_bindings'][0]['position_source_game_m'][0] += 1
        with self.assertRaisesRegex(ValueError, 'DAT binding changed'):
            list(checked_bindings(catalog))

    def test_builder_rejects_stale_proof_and_swapped_clip_direction(self):
        plan = read(ROOT/'evidence/prop-interactions-plan-v07.json')
        # Pure contract fixture, never serialized as an engine import report.
        imported = {'status': 'prop_interaction_assets_verified_runtime_pending',
            'source_plan': {'sha256': 'a'*64}, 'model_key': plan['model']['model_key'],
            'source_sha256': plan['model']['source_sha256'], 'clips': plan['model']['clips'].copy(),
            'prop_data': plan['model']['prop_data'], 'placements': 2,
            'data': plan['placements'][0]['interaction_data'] + '.DA_GarageInteraction'}
        self.assertIs(require_interaction_import(plan, imported, 'a'*64), imported)
        with self.assertRaisesRegex(ValueError, 'differs from the source plan'):
            require_interaction_import(plan, imported, 'b'*64)
        changed = copy.deepcopy(imported)
        changed['clips']['OPEN'], changed['clips']['CLOSE'] = changed['clips']['CLOSE'], changed['clips']['OPEN']
        with self.assertRaisesRegex(ValueError, 'differs from the source plan'):
            require_interaction_import(plan, changed, 'a'*64)
        changed = copy.deepcopy(imported)
        changed['data'] = '/Game/Unverified/DA_SameModel'
        with self.assertRaisesRegex(ValueError, 'data path differs'):
            require_interaction_import(plan, changed, 'a'*64)
        with self.assertRaisesRegex(ValueError, 'requires a verified import'):
            require_interaction_import(plan, None, 'a'*64)


if __name__ == '__main__':
    unittest.main()

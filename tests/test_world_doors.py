"""Original DAT regressions and native ABI anchors; no Unreal visual claim."""
import copy
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from plan_world_doors import (action_clips, door_transform, load_models, native_door_matrix,
                             plan, read, rotation_evidence, travel_yaw)
from plan_unreal_world import plan as world_plan


class DoorPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doors = read(ROOT/'evidence/door-objects-v05.json')
        cls.models = load_models()
        cls.world = world_plan(read(ROOT/'evidence/world-export.json'), {})
        cls.links = read(ROOT/'evidence/world-door-links.json')
        cls.areas = read(ROOT/'evidence/world-zone-links.json')
        # In-memory fixtures deliberately use non-asset strings. No fixture is
        # ever written as a verified engine import checkpoint.
        cls.imports = {m['model_key']: {'status': 'door_assets_verified',
            'model_key': m['model_key'], 'source_sha256': m['output_sha256'],
            'data': 'TEST_FIXTURE_NOT_AN_ASSET', 'original_clips': action_clips(m)} for m in cls.models['models']}

    def test_original_front_door_basis_and_shared_pivot_distinct_leaves(self):
        left = next(d for d in self.doors['objects'] if d['name'] == 'DT_tschool_SchoolFrontDoorL')
        right = next(d for d in self.doors['objects'] if d['name'] == 'tschool_SchoolFrontDoorR')
        self.assertEqual(left['pose']['yaw_pitch_roll_degrees_raw'], [0, 0, 0])
        self.assertEqual(left['pose']['yaw_degrees_raw'], -90)
        a, b = door_transform(left['pose']), door_transform(right['pose'])
        self.assertEqual(a, b)
        self.assertNotEqual(left['model_key'], right['model_key'])
        self.assertAlmostEqual(a['rotation_quaternion_xyzw'][2], -math.sqrt(.5), places=12)
        self.assertAlmostEqual(a['rotation_quaternion_xyzw'][3], math.sqrt(.5), places=12)
        for actual, expected in zip(a['translation_cm'], [20803.9001, 7322.9698, 857.168]):
            self.assertAlmostEqual(actual, expected, places=8)

    def test_native_premultiplication_is_noncommutative(self):
        # Hand-derived basis: X->Z, Y->-Y, Z->X for native Z then Y then X.
        actual = native_door_matrix([90, 90, 90], 0)
        expected = [0, 0, 1, 0, -1, 0, 1, 0, 0]
        for a, b in zip(actual, expected):
            self.assertAlmostEqual(a, b, places=12)

    def test_native_travel_heading_forward_axis(self):
        # Original 4154a0 forward=(-sin(h),cos(h)); reflect Y and compare UE X-forward.
        for degrees in (0, 90, -90, 180, 37):
            heading = math.radians(degrees)
            yaw = math.radians(travel_yaw(heading))
            self.assertAlmostEqual(math.cos(yaw), -math.sin(heading), places=12)
            self.assertAlmostEqual(math.sin(yaw), -math.cos(heading), places=12)
        front = next(x for x in self.links['links'] if x['name'] == 'DT_tschool_SchoolFrontDoorL')
        self.assertEqual(travel_yaw(front['destination_position']['native_heading_radians']), -90)

    def test_original_asm_confirms_mode2_call_abi(self):
        import pefile
        path = ROOT/'local/native/Bully.steam-original.exe.unpacked.exe'
        pe = pefile.PE(str(path), fast_load=True)
        with path.open('rb') as stream:
            for anchor in rotation_evidence()['asm_anchors']:
                data = bytes.fromhex(anchor['bytes_hex'])
                offset = pe.get_offset_from_rva(int(anchor['va'], 16)-pe.OPTIONAL_HEADER.ImageBase)
                stream.seek(offset)
                self.assertEqual(stream.read(len(data)), data)
        pe.close()

    def test_all_original_dat_rotations_are_finite_unit_rotations(self):
        for door in self.doors['objects']:
            transform = door_transform(door['pose'])
            q = transform['rotation_quaternion_xyzw']
            self.assertTrue(all(math.isfinite(v) for v in q))
            self.assertAlmostEqual(sum(v*v for v in q), 1, places=12)
        tiny_tilt = next(d for d in self.doors['objects'] if d['id'] == 'tindustrial_doors.dat:92')
        q = door_transform(tiny_tilt['pose'])['rotation_quaternion_xyzw']
        self.assertNotEqual(q[0], 0)
        self.assertNotEqual(q[1], 0)

    def test_complete_candidate_join_keeps_scobs_pending_and_no_inferred_pair_transition(self):
        result = plan(self.doors, self.models, self.imports, self.world, self.links, self.areas)
        self.assertEqual(result['summary'], {'source_doors': 161, 'placed': 160, 'pending': 1, 'excluded': 0})
        self.assertEqual(result['pending'][0]['id'], 'PAn_Main.dat:878')
        doors = {p['name']: p for z in result['zones'] for p in z['placements']}
        self.assertIsNotNone(doors['DT_tschool_SchoolFrontDoorL']['transition'])
        self.assertIsNone(doors['tschool_SchoolFrontDoorR']['transition'])
        self.assertTrue(doors['DT_tschool_SchoolFrontDoorL']['source_locked_raw'])
        self.assertTrue(doors['DT_tschool_SchoolFrontDoorL']['exploration_unlocked'])

    def test_mission_and_missing_import_never_become_runtime_placements(self):
        doors = copy.deepcopy(self.doors)
        doors['objects'] = doors['objects'][:1]
        doors['objects'][0]['is_mission_specific_raw'] = True
        result = plan(doors, self.models, self.imports, self.world, self.links, self.areas)
        self.assertEqual(result['summary']['excluded'], 1)
        self.assertEqual(result['summary']['placed'], 0)
        doors['objects'][0]['is_mission_specific_raw'] = False
        result = plan(doors, self.models, {}, self.world, self.links, self.areas)
        self.assertEqual(result['summary']['placed'], 0)
        self.assertEqual(result['pending'][0]['reason'], 'unreal_door_import_pending')

    def test_imported_source_identity_mismatch_is_rejected(self):
        imports = copy.deepcopy(self.imports)
        imports['10029_escdoorl']['source_sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            plan(self.doors, self.models, imports, self.world, self.links, self.areas)


if __name__ == '__main__':
    unittest.main()

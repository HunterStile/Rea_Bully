"""Carnival gestures require exact original instances and immutable clip bindings."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from export_world import unreal_transform
from plan_prop_gestures import BINDINGS, make_plan, require_gesture_import, source_placements

ROOT = Path(__file__).resolve().parents[1]


class CarnivalGesturePlan(unittest.TestCase):
    def fixture(self):
        instances = [{'model_id': 0, 'name': 'irrelevant'} for _ in range(37)]
        rows = []
        for index, key in BINDINGS.items():
            model_id, name = key.split('_', 1)
            instance = {'model_id': int(model_id), 'name': name, 'position': [index, 2., 3.],
                'quaternion_xyzw': [0., 0., 0., 1.], 'scale': [1., 1., 1.]}
            instances[index] = instance
            rows.append({'model_key': key, 'instance_index': index, 'unreal_transform': unreal_transform(instance)})
        world = {'zones': [{'name': 'zone_carn1', 'classification_sources': [],
            'source_ipb_sha256': 'fixture', 'placements': rows}]}
        return world, instances

    def test_exact_original_six_instances_keep_pose_and_identity(self):
        world, instances = self.fixture()
        result = source_placements(world, instances)
        self.assertEqual(len(result), 6)
        self.assertEqual(result[0]['source_id'], 'ipb:zone_carn1:26')
        self.assertEqual(result[0]['transform'], unreal_transform(instances[26]))
        self.assertTrue(all(p['travel_policy'] == 'none' for p in result))

    def test_changed_model_pose_or_duplicate_instance_is_rejected(self):
        for mode in ('model', 'pose', 'duplicate'):
            world, instances = self.fixture()
            rows = world['zones'][0]['placements']
            if mode == 'model':
                rows[0]['model_key'] = '10223_carni03'
            elif mode == 'pose':
                rows[0]['unreal_transform']['translation_cm'][0] += .01
            else:
                rows[-1] = copy.deepcopy(rows[0])
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                source_placements(world, instances)

    def test_real_plan_reproduces_from_original_bytes(self):
        expected = json.loads((ROOT/'evidence/prop-gestures-plan-v07.json').read_text())
        self.assertEqual(make_plan(), expected)
        self.assertEqual(expected['summary']['new_autoplay_clips'], 0)
        self.assertTrue(all(m['original_clips']['gesture'] == 'CARNI01\\CARNY_CMERE' for m in expected['models']))

    def test_import_rejects_swapped_clip_stale_evidence_and_wrong_data_asset(self):
        plan = json.loads((ROOT/'evidence/prop-gestures-plan-v07.json').read_text())
        imported = {'status': 'prop_gesture_assets_verified_runtime_pending',
            'source_plan': {'sha256': 'a'*64}, 'placements': 6,
            'models': [{'model_key': m['model_key'], 'source_sha256': m['source_sha256'],
                'prop_data': m['prop_data'], 'clips': copy.deepcopy(m['clips']),
                'data': m['gesture_data'] + '.DA_CarnivalGesture'} for m in plan['models']]}
        self.assertEqual(set(require_gesture_import(plan, imported, 'a'*64)), set(BINDINGS.values()))
        for mode in ('clip', 'digest', 'asset', 'duplicate'):
            changed = copy.deepcopy(imported)
            if mode == 'clip':
                changed['models'][0]['clips']['gesture'] = changed['models'][0]['clips']['idle']
            elif mode == 'digest':
                changed['source_plan']['sha256'] = 'b'*64
            elif mode == 'asset':
                changed['models'][0]['data'] = '/Game/Foreign/Data.Data'
            else:
                changed['models'][1] = copy.deepcopy(changed['models'][0])
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                require_gesture_import(plan, changed, 'a'*64)


if __name__ == '__main__':
    unittest.main()

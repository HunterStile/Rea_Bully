"""Skinned object duplicate ownership follows the same original areas as geometry."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from plan_world_props import plan


class OriginalPropAreas(unittest.TestCase):
    def fixture(self, areas, classification):
        placement = {'model_key': 'prop', 'instance_index': 0, 'unreal_transform': {
            'translation_cm': [10, 20, 30], 'rotation_quaternion_xyzw': [0, 0, 0, 1],
            'scale_xyz': [1, 1, 1]}}
        world = {'zones': [{'name': name, 'classification': classification,
            'source_archive_index': i, 'source_ipb_sha256': 'source-' + name,
            'classification_sources': [{'area': area}], 'placements': [copy.deepcopy(placement)]}
            for i, (name, area) in enumerate(areas)]}
        props = {'models': [{'model_key': 'prop', 'placement_count': len(areas),
            'output_sha256': 'original-prop', 'clips': []}]}
        imports = {'prop': {'status': 'prop_assets_verified', 'source_sha256': 'original-prop',
            'ambient_loop_original': None, 'model_key': 'prop', 'data': 'data', 'mesh': 'mesh',
            'own_skeleton': 'skeleton'}}
        return world, props, imports

    def test_distinct_outdoor_areas_keep_same_pose_object(self):
        world, props, imports = self.fixture([('tschool', 'MainMap'), ('ftest', 'Island_3')], 'outdoor')
        result = plan(world, props, imports)
        self.assertEqual(result['summary']['placed'], 2)
        self.assertEqual(result['summary']['duplicates'], 0)
        self.assertEqual([row['area'] for row in result['zones']], ['MainMap', 'Island_3'])
        self.assertEqual(result['zones'][1]['placements'][0]['transform'],
                         world['zones'][1]['placements'][0]['unreal_transform'])

    def test_two_ipbs_in_one_area_share_duplicate_owner(self):
        world, props, imports = self.fixture([('iballtos', 'Midway'), ('ishoot', 'Midway')], 'indoor')
        result = plan(world, props, imports)
        self.assertEqual(result['summary']['placed'], 1)
        self.assertEqual(result['summary']['duplicates'], 1)
        duplicate = result['zones'][1]['duplicates'][0]
        self.assertEqual(duplicate['owner']['zone'], 'iballtos')
        self.assertEqual(duplicate['source']['zone'], 'ishoot')
        world['zones'][1]['placements'][0]['unreal_transform']['translation_cm'][0] += 1
        self.assertEqual(plan(world, props, imports)['summary']['placed'], 2)


if __name__ == '__main__':
    unittest.main()

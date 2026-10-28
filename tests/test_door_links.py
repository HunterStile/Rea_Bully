from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from catalog_door_links import name_hash, build_links, split_points


class DoorLinkTests(unittest.TestCase):
    def trigger(self,name='DT_Front',area=0):
        return {'name':name,'source':{'path':'doors.dat'},'area_code':area,'islocked_raw':True}

    def point(self,name='DT_Front',area=2,coordinates=None):
        return {'name':name,'source':{'path':'doors.dat'},'area_code':area,'line':1,
            'points':[{'source_position':coordinates or [1.,2.,3.]}], 'point_count_valid':True}

    def build(self,triggers,points,bounds=None):
        return build_links(triggers,points,{'doors.dat'},{0:'MainMap',2:'School_Hallways'},bounds or {},{})

    def test_hash_matches_native_arithmetic_and_uppercase(self):
        self.assertEqual(name_hash('a'),65)
        self.assertEqual(name_hash('aB'),65*131+66)
        self.assertEqual(name_hash('DT_Front'),name_hash('dt_front'))
        self.assertLessEqual(name_hash('a'*500),0x7fffffff)

    def test_destination_comes_from_point_area_not_trigger_area(self):
        links,_=self.build([self.trigger()],[self.point()])
        self.assertEqual(links[0]['source_area_name'],'MainMap')
        self.assertEqual(links[0]['destination_area_name'],'School_Hallways')
        self.assertEqual(links[0]['destination_position']['source_position'],[1.,2.,3.])

    def test_similar_name_or_nearby_position_does_not_create_link(self):
        links,unmatched=self.build([self.trigger('DT_Front_Left')],[self.point('DT_Front_Right')])
        self.assertEqual(links,[])
        self.assertEqual(len(unmatched),1)

    def test_lowercase_prefix_does_not_get_native_point_flag(self):
        links,unmatched=self.build([self.trigger('dt_front')],[self.point('dt_front')])
        self.assertEqual(links,[])
        self.assertEqual(unmatched,[])

    def test_sv_multiple_matches_need_actual_pool_order(self):
        links,_=self.build([self.trigger('SV_Front',0),self.trigger('SV_Front',2)],[self.point('SV_Front')])
        self.assertEqual(len(links),2)
        self.assertTrue(all(l['sv_first_matching_trigger_pool_order_needed'] for l in links))

    def test_outside_area_bounds_flagged_without_changing_destination(self):
        bounds={2:{'source_zeroed':False,'original_bounds':[0,10,0,10,0,10]}}
        links,_=self.build([self.trigger()],[self.point(coordinates=[100,2,3])],bounds)
        self.assertIs(links[0]['destination_inside_original_area_bounds'],False)
        self.assertEqual(links[0]['destination_position']['source_position'],[100,2,3])

    def test_empty_destination_is_not_proven(self):
        point=self.point();point['points']=[]
        links,_=self.build([self.trigger()],[point])
        self.assertFalse(links[0]['destination_coordinate_proven'])
        self.assertIsNone(links[0]['destination_position'])

    def test_camera_uses_second_original_point_and_native_heading(self):
        block={'fields':[{'key':'POINTPOINT','value':[1.,2.,3.],'line':10},
            {'key':'POINTYAWPITCHROLL','value':[90.,0.,0.],'line':11},
            {'key':'POINTPOINT','value':[4.,5.,6.],'line':12},
            {'key':'POINTYAWPITCHROLL','value':[0.,0.,0.],'line':13}]}
        point=self.point();point['points']=split_points(block)
        links,_=self.build([self.trigger()],[point])
        self.assertEqual(links[0]['destination_position']['native_heading_radians'],0.)
        self.assertEqual(links[0]['destination_camera_point']['source_position'],[4.,5.,6.])


if __name__=='__main__':
    unittest.main()

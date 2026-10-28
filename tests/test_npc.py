"""NPC motion extraction must retain own proportions and source vertical bob."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from export_npc import channels


class NpcMotionTests(unittest.TestCase):
    def setUp(self):
        self.bones=[dict(name=str(i),translation=[0.,0.,0.]) for i in range(36)]
        self.bones[1]=dict(name='Root',translation=[0.,0.,1.02])
        self.bones[35]['name']='ARROW'
        self.tracks=[dict(rotation=[],translation=[]) for _ in range(36)]
        self.tracks[1]['translation']=[[0.,[0.,0.,.85]],[.5,[.05,.6,.87]],[1.,[0.,1.2,.85]]]

    def test_root_only_locomotion_extracts_source_planar_curve_and_keeps_own_height(self):
        original=copy.deepcopy(self.tracks)
        out,motion=channels(self.tracks,self.bones,[0.,0.,.85],True)
        self.assertEqual(motion['bone'],1)
        self.assertEqual(motion['displacement'],[0.,1.2,0.])
        self.assertEqual([p[:2] for _,p in out[1]['translation']],[[0.,0.]]*3)
        self.assertAlmostEqual(out[1]['translation'][1][1][2],1.04)
        self.assertEqual(self.tracks,original)

    def test_arrow_has_priority_and_preserves_nontravel_hip_sway(self):
        self.tracks[35]['translation']=[[0.,[0.,0.,0.]],[1.,[0.,1.2,0.]]]
        out,motion=channels(self.tracks,self.bones,[0.,0.,.85],True)
        self.assertEqual(motion['bone'],35)
        self.assertAlmostEqual(out[1]['translation'][1][1][0],.05)

    def test_stationary_motion_is_not_extracted_from_source_pose(self):
        out,motion=channels(self.tracks,self.bones,[0.,0.,.85],False)
        self.assertIsNone(motion['bone'])
        self.assertAlmostEqual(out[1]['translation'][1][1][0],.05)
        self.assertAlmostEqual(out[1]['translation'][-1][1][1],1.2)


if __name__=='__main__':unittest.main()

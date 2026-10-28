"""Root-motion policy must preserve source crouch and remove only ARROW travel."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from bully_agr import FormatError
from export_player_actions import action_channels


class ActionMotionTests(unittest.TestCase):
    def setUp(self):
        self.skeleton=[dict(name=str(i),translation=[0.,0.,0.]) for i in range(36)]
        self.skeleton[1]=dict(name='Root',translation=[0.,0.,.85])
        self.skeleton[35]['name']='ARROW'
        self.tracks=[dict(rotation=[],translation=[]) for _ in range(36)]

    def test_landing_initial_crouch_uses_idle_reference(self):
        self.tracks[1]['translation']=[[0.,[0.,0.,.70]],[.5,[0.,0.,.80]]]
        out=action_channels(self.tracks,self.skeleton,[0.,0.,.86],.5)
        self.assertAlmostEqual(out[1]['translation'][0][1][2],.69)
        self.assertAlmostEqual(out[1]['translation'][-1][1][2],.79)

    def test_arrow_extraction_keeps_crouch_and_union_time_keys(self):
        self.tracks[1]['translation']=[[0.,[0.,0.,.86]],[1.,[0.,4.,1.36]]]
        self.tracks[35]['translation']=[[0.,[0.,0.,0.]],[.5,[0.,2.,.25]],[1.,[0.,4.,.5]]]
        original=copy.deepcopy(self.tracks)
        out=action_channels(self.tracks,self.skeleton,[0.,0.,.86],1.)
        self.assertEqual([k[0] for k in out[1]['translation']],[0.,.5,1.])
        for _,p in out[1]['translation']:
            self.assertAlmostEqual(p[1],0.)
            self.assertAlmostEqual(p[2],.85)
        self.assertEqual(self.tracks,original)

    def test_missing_root_or_wrong_bone_order_rejected(self):
        with self.assertRaises(FormatError):
            action_channels(self.tracks,self.skeleton,[0.,0.,.86],1.)
        self.tracks[1]['translation']=[[0.,[0.,0.,.86]]]
        self.skeleton[35]['name']='wrong'
        with self.assertRaises(FormatError):
            action_channels(self.tracks,self.skeleton,[0.,0.,.86],1.)


if __name__=='__main__':
    unittest.main()

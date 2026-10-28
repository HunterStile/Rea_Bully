"""Skin corruption controls and exact owned-asset bind/partition checks."""
from pathlib import Path
import math
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from export_character import CharacterNif, converted, influences, quaternion, skeleton
from export_nif import FormatError, IDENTITY, matmul, matrix


class SkinTests(unittest.TestCase):
    def test_weights_keep_original_joint_order_and_normalize_float_roundoff(self):
        data={'bones':[{'weights':[(0,.25),(1,1.)]},{'weights':[(0,.74999994)]}]}
        _,j,w,t=influences(data,2)
        self.assertEqual(j,[(0,1,0,0),(0,0,0,0)])
        self.assertAlmostEqual(sum(w[0]),1)
        self.assertLess(abs(t[0]-1),1e-6)

    def test_corrupt_skin_weights_fail(self):
        cases=[{'bones':[{'weights':[(0,float('nan'))]}]},
               {'bones':[{'weights':[(0,-1.)]}]},
               {'bones':[{'weights':[(1,1.)]}]},
               {'bones':[{'weights':[(0,.1)]}]},
               {'bones':[{'weights':[]}]},
               {'bones':[{'weights':[(0,.2)]} for _ in range(5)]}]
        for data in cases:
            with self.subTest(data=data),self.assertRaises(FormatError):influences(data,1)

    def test_axis_conversion_rotation_matches_vector_mapping(self):
        m=matrix({'rotation':(0,-1,0,1,0,0,0,0,1),'translation':(1,2,3),'scale':1})
        c=converted(m)
        self.assertEqual((c[3],c[7],c[11]),(1,3,-2))
        self.assertEqual(tuple(round(x,8) for x in quaternion((c[0],c[1],c[2],c[4],c[5],c[6],c[8],c[9],c[10]))),
                         (0.,round(math.sqrt(.5),8),0.,round(math.sqrt(.5),8)))

    def test_owned_player_skin_and_rest_consistency(self):
        path=Path(__file__).resolve().parents[1]/'local/extracted/Stream/World/10611/PLAYER.nif'
        if not path.exists():self.skipTest('Owned asset not extracted')
        n=CharacterNif(path);bones=skeleton(n);lookup={b['source_block']:b for b in bones}
        self.assertEqual(len(bones),36)
        self.assertEqual([b['name'] for b in bones[:3]],['Dummy','Root','Root Pelvis'])
        skinned=0;maxerror=0
        for i,k in enumerate(n.types):
            if k not in ('NiTriShape','NiTriStrips'):continue
            shape=n.block(i)
            if shape['skin']<0:continue
            skinned+=1;s=n.block(shape['skin']);sd=n.block(s['data']);mesh=n.block(shape['data'])
            n.block(s['partition']) # Full decode must consume original partition block.
            inf,_,_,_=influences(sd,len(mesh['vertices']))
            for v,a in zip(mesh['vertices'],inf):
                p=[0.,0.,0.]
                for b,w in a:
                    m=matmul(lookup[s['bones'][b]]['world_matrix'],matrix(sd['bones'][b]))
                    p=[p[r]+w*(sum(m[r*4+c]*v[c] for c in range(3))+m[r*4+3]) for r in range(3)]
                maxerror=max(maxerror,math.dist(p,v))
        self.assertEqual(skinned,4)
        self.assertLess(maxerror,.0024)
        self.assertGreater(maxerror,.002) # Original bind transforms retained, not fabricated inverses.


if __name__=='__main__':unittest.main()

"""AGR float positions, source discontinuities and corrupt input boundaries."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from bully_agr import FormatError, decode_animation
from export_animation_library import player_channels


def float_curve(positions):
    # A real key chain: identity float quaternion at t=0 and t=1, second key
    # references the first key; position index is uint32, values are metres.
    return (struct.pack('<4If',0x100,1000,2,0,1.) +
            struct.pack('<HH4f',0,0,1.,0.,0.,0.) +
            struct.pack('<HH4f',0,65535,1.,0.,0.,0.) + positions)


class AgrTests(unittest.TestCase):
    def test_float_positions_preserve_float_metres_and_key_time(self):
        data=float_curve(struct.pack('<I3fI3f',0,1.25,-.125,.875,1,1.5,.25,.75))
        curve=decode_animation(data,2.)
        self.assertEqual(curve['tracks'][0]['translation'],[[0.,[1.25,-.125,.875]],[2.,[1.5,.25,.75]]])
        self.assertEqual(curve['separate_position_count'],2)
        self.assertEqual(curve['separate_position_encoding'],'uint32,float32_xyz_metres')

    def test_quantized_positions_remain_signed_millimetres(self):
        data=(struct.pack('<4If',0x100,1001,1,0,1.)+
              struct.pack('<HH4h',0,0,0,0,0,32767)+struct.pack('<H3h',0,-125,1500,875))
        self.assertEqual(decode_animation(data)['tracks'][0]['translation'],[[0.,[-.125,1.5,.875]]])

    def test_float_position_absent_key_rejected(self):
        with self.assertRaisesRegex(FormatError,'absent key'):
            decode_animation(float_curve(struct.pack('<I3f',65536,0.,0.,0.)))

    def test_truncated_float_position_rejected(self):
        with self.assertRaisesRegex(FormatError,'outside file'):
            decode_animation(float_curve(struct.pack('<I2f',0,0.,0.)))

    def test_nonfinite_separate_and_inline_position_rejected(self):
        with self.assertRaisesRegex(FormatError,'Nonfinite position'):
            decode_animation(float_curve(struct.pack('<I3f',0,float('nan'),0.,0.)))
        data=struct.pack('<4IfHH7f',0x100,999,1,0,1.,0,0,1.,0.,0.,0.,0.,0.,float('inf'))
        with self.assertRaisesRegex(FormatError,'Nonfinite position'):
            decode_animation(data)

    def test_duplicate_source_instants_rejected_for_playback_retained_for_archive(self):
        data=float_curve(struct.pack('<I3fI3f',0,1.,0.,0.,0,2.,0.,0.))
        with self.assertRaisesRegex(FormatError,'Duplicate'):
            decode_animation(data)
        curve=decode_animation(data,strict_times=False)
        self.assertEqual(curve['tracks'][0]['translation'],[[0.,[1.,0.,0.]],[0.,[2.,0.,0.]]])
        self.assertFalse(curve['duplicate_instants'][0]['keys'][0]['identical_values'])

    def test_corrupt_chain_and_nonfinite_header_rejected(self):
        data=bytearray(float_curve(b''))
        struct.pack_into('<H',data,20,1)
        with self.assertRaisesRegex(FormatError,'backward-link'):
            decode_animation(data)
        data=bytearray(float_curve(b''))
        struct.pack_into('<f',data,16,float('nan'))
        with self.assertRaisesRegex(FormatError,'duration'):
            decode_animation(data,1.)

    def test_source_absent_root_translation_stays_absent(self):
        skeleton=[dict(name=str(i),translation=[0.,0.,0.]) for i in range(36)]
        skeleton[1]['name']='Root';skeleton[35]['name']='ARROW'
        tracks=[dict(rotation=[],translation=[]) for _ in range(36)]
        result=player_channels(tracks,skeleton,[0.,0.,.862],1.)
        self.assertEqual(result[1]['translation'],[])
        self.assertEqual(result[35]['translation'],[])


if __name__=='__main__':
    unittest.main()

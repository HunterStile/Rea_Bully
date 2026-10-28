import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from catalog_population import read_trigger_blocks, fields, mapping, poi_points, text_lines, read_peds, ue_position


class PopulationParserTests(unittest.TestCase):
    def parse(self, text):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'source.dat'
            path.write_bytes(text.encode()+b'\0'*32)
            return read_trigger_blocks(path)

    def test_nested_population_preserves_root_position_and_four_periods(self):
        text = 'TRIGGER\nBEGIN\nPOSITION 151, -57, 4\nPOPULATIONDATA\nBEGIN\n'
        for period in ['DAY','CLASS','NIGHT','CURFEW']:
            text += f'POPULATION_{period}\nBEGIN\nTOTAL 3\nSTUDENT 2\nEND\n'
        text += 'END\nFORCEPOPREAD FALSE\nEND\n'
        block = self.parse(text)[0]
        f = mapping(block['fields'])
        self.assertEqual(f['POSITION'], [151.,-57.,4.])
        self.assertEqual(len(f['POPULATIONDATA']),4)
        self.assertEqual(f['POPULATIONDATA']['POPULATION_CLASS']['STUDENT'],2)
        self.assertIs(f['FORCEPOPREAD'],False)

    def test_two_poi_points_do_not_overwrite_group_name_or_flags(self):
        blocks = self.parse('POI\nBEGIN\nNAME "Nerds"\nAREACODE 0\nNPOIPOINTS 2\n'
            'GENDER "Both"\nNAME ""\nTYPE "Hang_Out"\nPOIPOINT 1, 2, 3\nMORNING TRUE\n'
            'GENDER "Female"\nNAME ""\nTYPE "Hang_Out"\nPOIPOINT 4, 5, 6\nMORNING FALSE\nEND\n')
        points = poi_points(blocks[0])
        self.assertEqual(len(points),2)
        self.assertEqual(points[0]['POIPOINT'],[1.,2.,3.])
        self.assertTrue(points[0]['MORNING'])
        self.assertFalse(points[1]['MORNING'])

    def test_tabs_and_repeated_path_points_retained(self):
        block = self.parse('PATH\nBEGIN\nPATHNAME\t"Route"\nNPATHPOINTS 2\nPATHPOINT 1, 2, 3\nWAITTIME 5\nPATHPOINT 4, 5, 6\nWAITTIME 0\nEND\n')[0]
        self.assertEqual(fields(block)['PATHNAME'],'Route')
        self.assertEqual([f['value'] for f in block['fields'] if f['key']=='WAITTIME'],[5,0])

    def test_nonzero_content_after_zero_tail_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'source.dat'
            path.write_bytes(b'NPATHS 0\0hidden')
            with self.assertRaisesRegex(ValueError,'Non-zero data'):
                text_lines(path)

    def test_unclosed_nested_population_rejected(self):
        with self.assertRaisesRegex(ValueError,'unclosed'):
            self.parse('TRIGGER\nBEGIN\nPOPULATIONDATA\nBEGIN\nEND\n')

    def test_coordinate_conversion_keeps_height_and_reflects_y(self):
        self.assertEqual(ue_position([271.118988,-48.6423,6.72256]),[27111.8988,4864.23,672.256])

    def test_repeated_ide_sections_and_unique_raw_retained(self):
        record='50 PFH1_Seth PFH1_Seth_W 0 Huge PREFECT STAT_PF_BASIC_S Authority F_Pref null null 1 /Global/Authority Act/Anim/Authority.act /Global/AI Act/AI/AI.act N_Seth'
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'default.ide'
            path.write_text('peds\n'+record+'\nend\nobjs\n571 wheel wheel 1 100 0\nend\npeds\n'+record.replace('50 ','579 ',1)+'\nend\n')
            peds=read_peds(path)
        self.assertEqual([p['id'] for p in peds],[50,579])
        self.assertEqual(peds[0]['animation_groups'],['Authority','F_Pref','null','null'])
        self.assertEqual(peds[0]['unique_raw'],1)


if __name__=='__main__':
    unittest.main()

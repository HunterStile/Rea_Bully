"""Exercise exact original CAT/HXD selection, including rejected family mismatches."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from bully_agr import FormatError, read_hxd
from catalog_npc_locomotion import (CAT_ROOT, HXD, NEW_PED_IDS, PROFILES, CatReferences,
                                    catalog, prove_profile, verify_ped_selection)
from export_npc import SELECTION, texture_variant
from plan_ambient_cast import quota_plan


class NPCProfiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.population=json.loads((ROOT/'evidence/world-population.json').read_text())
        cls.evidence=catalog()

    def ped(self, ped_id):
        return next(ped for ped in self.population['ped_definitions'] if ped['id']==ped_id)

    def test_bucky_seth_profiles_and_old_evidence_remain_compatible(self):
        expected={8:[('F_Nerds','F_NERDS\\IDLE','idle'),('F_Nerds','F_NERDS\\WALK','walk'),
                     ('C_Player','C_PLAYER\\RUN','run'),('C_Player','F_NERDS\\RUN','sprint'),
                     ('IDLE_NERD_A','F_NERDS\\ND_IDLE_A','ambient_idle')],
                 50:[('F_Pref','F_PREF\\IDLE_A','idle'),('C_Player','C_PLAYER\\WALK','walk'),
                     ('C_Player','C_PLAYER\\RUN','run'),('C_Player','C_PLAYER\\RUNFAST','sprint'),
                     ('IDLE_AUTH_A','F_PREF\\AU_IDLE_A_01','ambient_idle')]}
        for ped_id, clips in expected.items():
            with self.subTest(ped_id=ped_id):
                self.assertEqual(SELECTION[ped_id], clips)
                proof=verify_ped_selection(self.ped(ped_id),ROOT/'evidence/npc-locomotion-cat-v04.json')
                self.assertEqual([(c['group'],c['name'],c['role']) for c in proof['clips']],clips)

    def test_six_new_peds_have_exact_idetree_cat_and_hxd_chains(self):
        with tempfile.TemporaryDirectory() as temporary:
            report=Path(temporary)/'cats.json'
            report.write_text(json.dumps(self.evidence))
            for ped_id in NEW_PED_IDS:
                with self.subTest(ped_id=ped_id):
                    proof=verify_ped_selection(self.ped(ped_id),report)
                    self.assertEqual([c['role'] for c in proof['clips']],['idle','walk','run','sprint','ambient_idle'])
                    self.assertEqual(proof['action_root'],self.ped(ped_id)['action_root'])

    def test_another_ped_tree_is_rejected_before_export(self):
        ped=copy.deepcopy(self.ped(72))
        ped['action_root']='/Global/B_Striker_A'
        with self.assertRaises(FormatError):
            verify_ped_selection(ped,ROOT/'evidence/npc-locomotion-cat-v05.json')

    def test_tampered_report_cannot_select_other_clips(self):
        proof=copy.deepcopy(self.evidence)
        proof['profiles']['student_male']['clips'][0]['name']='F_NERDS\\IDLE'
        with tempfile.TemporaryDirectory() as temporary:
            report=Path(temporary)/'tampered.json'
            report.write_text(json.dumps(proof))
            with self.assertRaises(FormatError):
                verify_ped_selection(self.ped(72),report)

    def test_actual_nerd_sprint_is_in_player_bank_and_wrong_bank_is_rejected(self):
        profile=copy.deepcopy(PROFILES['nerd'])
        row=list(profile['clips'][3]); row[0]='F_Nerds'; profile['clips'][3]=tuple(row)
        cats={name:CatReferences(CAT_ROOT/(name+'.cat')) for name in ('N_Ranged_A','Walk_Nerd')}
        with patch.dict(PROFILES,{'nerd':profile}), self.assertRaises(FormatError):
            prove_profile('nerd',read_hxd(HXD),cats)

    def test_exact_bully_winter_slots_and_existing_nerd_convention(self):
        for ped_id,number in ((85,'03'),(99,'02')):
            available={f'bully{number}_md_w_{slot}.tga' for slot in ('d','s','n')}
            for slot in ('d','s','n'):
                self.assertEqual(texture_variant(f'bully{number}_md_{slot}.tga',available,ped_id),
                                 f'bully{number}_md_w_{slot}.tga')
            with self.assertRaises(FormatError):
                texture_variant('bully01_md_d.tga',available,ped_id)
        self.assertEqual(texture_variant('ndh3_bucky_md_d.tga',{'ndh3_bucky_w_md_d.tga'},8),
                         'ndh3_bucky_w_md_d.tga')

    def test_original_day_quotas_do_not_create_duplicate_named_characters(self):
        regions=json.loads((ROOT/'evidence/ambient-population-regions.json').read_text())['regions']
        pools={'NERD':[8,9,11],'STUDENT':[72,73],'BULLY':[85,99]}
        plan=quota_plan(regions,pools)
        school=next(region for region in plan if region['name']=='SchoolArea')
        day=next(period for period in school['periods'] if period['period']=='DAY_OUTOFCLASS')
        self.assertEqual(day['original_quotas']['TOTAL'],15)
        self.assertEqual(day['max_distinct_candidates_within_quota'],6)
        self.assertIsNone(day['actors_to_spawn'])
        self.assertEqual(sum(c['unfilled_slots_with_this_pool'] for c in day['categories']),9)
        pools['STUDENT'].append(72)
        with self.assertRaises(FormatError):
            quota_plan(regions,pools)


if __name__=='__main__':
    unittest.main()

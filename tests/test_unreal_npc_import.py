"""Replay the imported Bucky names at the NPC-to-locomotion mapping seam."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from unreal_import_npcs import map_roles, verify_source_record


class Sequence:
    def __init__(self, name, skeleton, duration):
        self.name = name
        self.properties = {'skeleton': skeleton, 'sequence_length': duration}

    def get_name(self):
        return self.name

    def get_editor_property(self, key):
        return self.properties[key]


class NPCMapping(unittest.TestCase):
    def setUp(self):
        self.npc = json.loads((ROOT/'evidence/npc-import-manifest-v04.json').read_text())['npcs'][0]
        self.skeleton = object()
        self.stem = '008_NDH3_Bucky'
        self.sequences = [Sequence(self.stem+c['glb_animation_name'], self.skeleton, c['duration'])
                          for c in self.npc['clips']]

    def test_saved_bucky_names_map_to_all_five_original_roles(self):
        roles = map_roles(self.npc, self.sequences, self.skeleton, self.stem)
        self.assertEqual(set(roles), {'idle', 'walk', 'run', 'sprint', 'ambient_idle'})
        self.assertEqual(roles['run'][0].get_name(), '008_NDH3_BuckyC_PLAYER_RUN')

    def test_other_character_prefix_is_rejected_even_with_same_clip(self):
        self.sequences[0].name = '050_PFH1_Seth_'+self.npc['clips'][0]['glb_animation_name']
        with self.assertRaises(RuntimeError):
            map_roles(self.npc, self.sequences, self.skeleton, self.stem)

    def test_other_skeleton_is_rejected(self):
        self.sequences[0].properties['skeleton'] = object()
        with self.assertRaises(RuntimeError):
            map_roles(self.npc, self.sequences, self.skeleton, self.stem)

    def test_two_importer_name_variants_of_one_clip_are_ambiguous(self):
        clip = self.npc['clips'][0]
        self.sequences.append(Sequence(self.stem+'_'+clip['glb_animation_name'], self.skeleton, clip['duration']))
        with self.assertRaises(RuntimeError):
            map_roles(self.npc, self.sequences, self.skeleton, self.stem)

    def test_saved_source_digest_is_required_even_when_path_matches(self):
        source = ROOT/self.npc['source_glb']['path']
        matching = json.dumps([{'FileMD5': 'A' * 32}])
        record = verify_source_record(source, 'a' * 32, [str(source)], matching)
        self.assertEqual(record['imported_md5'], 'a' * 32)
        for invalid in (json.dumps([{'FileMD5': 'b' * 32}]), json.dumps([{}]), '[]'):
            with self.subTest(import_record=invalid), self.assertRaises(RuntimeError):
                verify_source_record(source, 'a' * 32, [str(source)], invalid)
        with self.assertRaises(RuntimeError):
            verify_source_record(source, 'a' * 32, [str(source.with_name('different.glb'))], matching)


if __name__ == '__main__':
    unittest.main()

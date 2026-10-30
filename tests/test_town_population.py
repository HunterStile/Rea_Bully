"""Outdoor preview keeps source coordinates, quotas and clip ambiguities explicit."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from plan_town_population import select_point, inside_region, validate
from town_npc_manifest import load_runtime_manifest
from town_npc_transforms import compare_town_skin
from validate_npc_candidates import CharacterNif, glb


class TownPopulation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = json.loads((ROOT/'evidence/ambient-runtime-plan-v07-r5.json').read_text())
        cls.population = json.loads((ROOT/'evidence/world-population.json').read_text())
        cls.peds = {p['id']: p for p in cls.population['ped_definitions']}

    def test_all_31_skins_and_placements_preserve_previous_23_identities(self):
        manifest = load_runtime_manifest(ROOT/'evidence/npc-import-manifest-v07-r5.json')
        previous = json.loads((ROOT/'evidence/npc-import-manifest-v07-r4.json').read_text())
        self.assertEqual(manifest['npcs'][:23], previous['npcs'])
        self.assertEqual(len(manifest['npcs']), 31)
        self.assertEqual(len({p['ped_id'] for p in self.plan['placements']}), 31)
        self.assertEqual(self.plan['zone_counts'], {'tschool': 8, 'isc_lib': 1, 'ischool': 14,
                                                  'tbusines': 4, 'trich': 2, 'tindust': 2})
        self.assertTrue(validate(self.plan))
        self.assertFalse(self.plan['runtime_spawn_validated'])

    def test_outdoor_regions_and_behaviors_are_explicit_source_choices(self):
        outdoor = [p for p in self.plan['placements'] if 'population_region' in p['source_evidence']]
        self.assertEqual(len(outdoor), 8)
        self.assertEqual({p['source_evidence']['population_region']['name'] for p in outdoor},
                         {'DownTown', 'RichArea', 'PoorArea', 'IndustrialArea_DropOutEnclave'})
        for p in outdoor:
            evidence = p['source_evidence']
            point = evidence['original_poi_group']['points'][evidence['point_index']]
            self.assertTrue(inside_region(point['POIPOINT'], evidence['population_region']))
            self.assertFalse(evidence['original_event_action_activated'])
            self.assertEqual(p['behavior'], 'original_point_family_idle_preview')

    def test_disabled_or_wrong_faction_outdoor_points_are_rejected(self):
        sample = next(p for p in self.plan['placements'] if p['ped_id'] == 42)
        evidence = sample['source_evidence']
        original, index = evidence['original_poi_group'], evidence['point_index']
        for key, value in (('MAX', 0), ('AFTERNOON', False), ('PEDTYPE', 'PREPPY'), ('GENDER', 'Female')):
            group = copy.deepcopy(original)
            group['points'][index][key] = value
            with self.assertRaises(ValueError):
                select_point(self.peds[42], group, index, 'tindust', outdoor=True)
        # Original industrial Smoking[0] really has MAX=0; it was not promoted.
        with self.assertRaises(ValueError):
            select_point(self.peds[42], original, 0, 'tindust', outdoor=True)

    def test_businessman_global_translation_cannot_be_dropped(self):
        ped = self.peds[76]
        nif = CharacterNif(Path(ped['model_candidates'][0]['path']))
        export = json.loads((ROOT/'evidence/npc-076-town-export-v07.json').read_text())
        doc, blob = glb(Path(export['output']['path']))
        result = compare_town_skin(nif, doc, blob)
        self.assertTrue(result[4]['preserved_global_translation']['exact_original_translation'])
        changed = copy.deepcopy(doc)
        changed['nodes'][37]['matrix'][12] = 0.
        with self.assertRaisesRegex(ValueError, 'global skin translation changed'):
            compare_town_skin(nif, changed, blob)

    def test_civilian_name_ambiguity_retains_both_original_banks(self):
        cat = json.loads((ROOT/'evidence/npc-town-cat-v07.json').read_text())
        resolution = cat['profiles']['civilian_male']['original_group_resolution']
        self.assertFalse(resolution['native_precedence_recovered'])
        self.assertEqual(resolution['selected_group'], 'CV_Male')
        self.assertEqual({r['group'] for r in resolution['same_hash_alternatives']}, {'CV_Male', 'F_Adult'})
        self.assertEqual(len({r['record']['duration'] for r in resolution['same_hash_alternatives']}), 2)


if __name__ == '__main__':
    unittest.main()

"""Source-specific transform recovery must preserve global motion and binds."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from recover_transformed_npcs import recovery_options, source_transforms
from validate_npc_candidates import (CharacterNif, C, np, nif_matrix, accumulated,
                                    deform_gltf, gltf_matrix, glb)
from validate_recovered_npcs import factor_translation, recovered_skin


class NpcTransformRecovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.peds = {p['id']: p for p in json.loads((ROOT/'evidence/world-population.json').read_text())['ped_definitions']}

    def source(self, ped_id):
        return CharacterNif(Path(self.peds[ped_id]['model_candidates'][0]['path']))

    def candidate(self, ped_id):
        report = json.loads((ROOT/f'evidence/npc-{ped_id:03d}-export-v07-recovered.json').read_text())
        return glb(Path(report['output']['path']))

    def test_max_and_pedro_need_no_global_wrapper_or_bind_adjustment(self):
        for ped_id in (49, 69):
            nif = self.source(ped_id)
            options, rows = recovery_options(ped_id, nif)
            self.assertEqual(options, {'allow_transformed_skin_shape': True})
            self.assertTrue(any(r['local_shape_nonidentity'] > 1 for r in rows))
            self.assertTrue(all(np.array_equal(r['product'], np.eye(4)) for r in rows))
            doc, blob = self.candidate(ped_id)
            result = recovered_skin(nif, doc, blob)
            self.assertFalse(result[4]['preserved_global_translation']['present'])
            self.assertTrue(all(r['inverse_bind_matrices_exact'] for r in result[4]['shapes']))

    def test_original_earnest_wrapper_is_required_and_exact(self):
        nif = self.source(10)
        doc, _ = self.candidate(10)
        _, _, offset, proof = factor_translation(nif, doc)
        self.assertTrue(proof['exact_original_translation'])
        self.assertTrue(np.array_equal(offset, [0., 0., 0.013427734375]))
        changed = copy.deepcopy(doc)
        changed['nodes'][37]['matrix'][14] = 0.
        with self.assertRaisesRegex(ValueError, 'global skin translation changed'):
            factor_translation(nif, changed)
        changed = copy.deepcopy(doc)
        changed['nodes'].pop()
        changed['scenes'][0]['nodes'] = [0, 36]
        with self.assertRaisesRegex(ValueError, 'wrapper'):
            factor_translation(nif, changed)

    def test_earnest_full_source_deformation_equals_factored_evaluation(self):
        nif = self.source(10)
        doc, blob = self.candidate(10)
        bones, parents, shapes, binds, report = recovered_skin(nif, doc, blob)
        source_worlds = accumulated([nif_matrix(bone) for _, bone in bones], parents)
        gltf_worlds = accumulated([gltf_matrix(node) for node in doc['nodes'][:36]], parents)
        translation = np.asarray(source_transforms(nif)[0]['product'])
        wrapper = np.asarray(doc['nodes'][37]['matrix']).reshape(4, 4).T
        max_error = 0.
        for shape in shapes:
            # Evaluate the original geometry/skin product before every source
            # bone and bind, independently of the factored comparison helper.
            expected = []
            for vertex, weights in zip(shape['source_vertices_h'], shape['source_influences']):
                total = sum(w for _, w in weights)
                p = sum(w/total*(translation @ source_worlds[j] @ shape['source_binds'][j] @ vertex)
                        for j, w in weights)
                expected.append((C @ p)[:3])
            actual = deform_gltf(shape['positions'], shape['joints'], shape['weights'],
                                 [wrapper @ world for world in gltf_worlds], binds)
            max_error = max(max_error, float(np.linalg.norm(actual-np.asarray(expected), axis=1).max()))
        self.assertAlmostEqual(max_error, report['rest_pose']['max_vertex_distance_m'], places=9)

    def test_cancellation_adapter_rejects_earnest_residual(self):
        with self.assertRaisesRegex(ValueError, 'does not cancel exactly'):
            recovery_options(49, self.source(10))


if __name__ == '__main__':
    unittest.main()

"""Factor only a verified common original translation for outdoor NPC comparisons."""
import copy
from recover_transformed_npcs import source_transforms
from validate_npc_candidates import C, np, require, compare_skin
from validate_recovered_npcs import shifted_bounds


def compare_town_skin(nif, doc, blob):
    rows = source_transforms(nif)
    product = np.asarray(rows[0]['product'])
    error = max(float(np.max(np.abs(np.asarray(row['product'])-np.eye(4)))) for row in rows)
    if error <= 1e-5:
        result = compare_skin(nif, doc, blob)
        offset = np.zeros(3)
        proof = {'present': False, 'original_geometry_skin_residual_max_m': error,
                 'policy': 'Historical cancellation tolerance retained; this tiny residual is not claimed zero'}
    else:
        require(all(np.array_equal(row['product'], product) for row in rows) and
                np.array_equal(product[:3, :3], np.eye(3)) and
                np.array_equal(product[3], [0., 0., 0., 1.]),
                'Only an original common pure translation can be factored')
        require(len(doc['nodes']) == 38 and doc['scenes'][0]['nodes'] == [37, 36],
                'Expected one original translation wrapper')
        wrapper = doc['nodes'][37]
        require(set(wrapper) == {'name', 'matrix', 'children'} and wrapper['children'] == [0] and
                wrapper['name'] == doc['nodes'][36]['name']+'_OriginalGeometrySkinTransform',
                'Original translation wrapper structure changed')
        expected = C @ product @ C.T
        actual = np.asarray(wrapper['matrix']).reshape(4, 4).T
        require(np.array_equal(actual, expected), 'Original global skin translation changed')
        replacements = {}
        for row in rows:
            data = copy.deepcopy(nif.block(row['skin_data_block']))
            inverse_geometry = np.linalg.inv(row['geometry_world'])
            data['transform'] = {**data['transform'], 'scale': 1.,
                'rotation': inverse_geometry[:3, :3].ravel().tolist(),
                'translation': inverse_geometry[:3, 3].tolist()}
            replacements[row['skin_data_block']] = data

        class TranslatedFrame:
            types = nif.types

            def block(self, index):
                return replacements[index] if index in replacements else nif.block(index)

        local_doc = copy.deepcopy(doc)
        local_doc['nodes'].pop()
        local_doc['scenes'][0]['nodes'] = [0, 36]
        result = compare_skin(TranslatedFrame(), local_doc, blob)
        offset = expected[:3, 3]
        proof = {'present': True, 'source_product_game_m': product.tolist(),
                 'gltf_wrapper_matrix': actual.tolist(), 'exact_original_translation': True,
                 'comparison_frame': 'Identical translation factored from both evaluations; distance errors unchanged',
                 'source_shapes': rows}
    for shape in result[2]:
        shape['verified_common_translation_gltf'] = offset
    shifted_bounds(result[4]['rest_pose'], offset)
    result[4]['preserved_global_translation'] = proof
    return result

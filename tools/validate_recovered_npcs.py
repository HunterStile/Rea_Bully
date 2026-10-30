"""Validate recovered own skins, including an exactly preserved global translation.

Historical validators remain unchanged. A common source translation is verified
against the actual GLB wrapper, factored from both coordinate frames, and added
back to measured bounds. Translation preserves every vertex-distance error.
"""
import argparse
import copy
import importlib.util
import json
from pathlib import Path

from catalog_npc_locomotion import lower_priority
from recover_transformed_npcs import ROOT, source_transforms, recovery_options
from validate_npc_candidates import (C, np, require, reference, compare_skin,
                                    compare_clip, CharacterNif)
from world_npc_profiles import ADDITIONAL


def factor_translation(nif, doc):
    rows = source_transforms(nif)
    product = np.asarray(rows[0]['product'])
    if all(np.array_equal(row['product'], np.eye(4)) for row in rows):
        return nif, doc, np.zeros(3), {'present': False}
    # This scope is the measured Earnest NIF; no arbitrary transform relaxation.
    recovery_options(10, nif)
    require(len(doc['nodes']) == 38 and doc['scenes'][0]['nodes'] == [37, 36],
            'Expected exact original-translation skeleton wrapper')
    wrapper = doc['nodes'][37]
    require(set(wrapper) == {'name', 'matrix', 'children'} and wrapper['children'] == [0],
            'Original translation wrapper structure changed')
    expected = C @ product @ C.T
    actual = np.asarray(wrapper['matrix']).reshape(4, 4).T
    require(np.array_equal(actual, expected), 'Original global skin translation changed')
    require(wrapper['name'] == doc['nodes'][36]['name']+'_OriginalGeometrySkinTransform',
            'Original translation wrapper identity changed')
    replacements = {}
    for row in rows:
        # Source deformation = product * boneWorld * inverseBind * vertex.
        # Evaluate in the common translated frame: geometryWorld * overall'
        # is identity. Original vertices, bone transforms, binds, and weights
        # are untouched. The full product is independently checked above.
        before = copy.deepcopy(nif.block(row['skin_data_block']))
        inverse_geometry = np.linalg.inv(row['geometry_world'])
        before['transform'] = {
            **before['transform'], 'scale': 1.,
            'rotation': inverse_geometry[:3, :3].ravel().tolist(),
            'translation': inverse_geometry[:3, 3].tolist()}
        replacements[row['skin_data_block']] = before

    class TranslatedFrame:
        types = nif.types

        def block(self, index):
            return replacements[index] if index in replacements else nif.block(index)

    local_doc = copy.deepcopy(doc)
    local_doc['nodes'].pop()
    local_doc['scenes'][0]['nodes'] = [0, 36]
    return TranslatedFrame(), local_doc, expected[:3, 3], {
        'present': True, 'source_product_game_m': product.tolist(),
        'gltf_wrapper_matrix': actual.tolist(), 'exact_original_translation': True,
        'comparison_frame': 'Identical translation factored from both evaluations; error distances unchanged',
        'source_shapes': rows}


def shifted_bounds(sample, offset):
    sample['gltf_bounds_m'] = {
        key: (np.asarray(value)+offset).tolist() for key, value in sample['gltf_bounds_m'].items()}


def recovered_skin(nif, doc, blob):
    framed, local_doc, offset, proof = factor_translation(nif, doc)
    result = compare_skin(framed, local_doc, blob)
    for shape in result[2]:
        shape['verified_common_translation_gltf'] = offset
    shifted_bounds(result[4]['rest_pose'], offset)
    result[4]['preserved_global_translation'] = proof
    return result


def recovered_clip(clip, tracks, idle, bones, parents, shapes, inverse_binds, doc, blob, animation):
    # Only skeleton nodes are read by the original key/pose evaluator. The
    # wrapper has already been checked against original source matrices.
    result = compare_clip(clip, tracks, idle, bones, parents, shapes, inverse_binds, doc, blob, animation)
    offset = shapes[0]['verified_common_translation_gltf']
    require(all(np.array_equal(shape['verified_common_translation_gltf'], offset) for shape in shapes),
            'Per-shape global translation differs')
    for sample in result['pose_samples']:
        shifted_bounds(sample, offset)
    return result


def validate(manifest_path, output):
    require(not output.exists(), 'New numeric validation report required')
    manifest = json.loads(manifest_path.read_text())
    require(manifest['pending'] == [] and
            sorted(row['ped_id'] for row in manifest['npcs']) == sorted(ADDITIONAL),
            'Recovered candidate scope is incomplete')
    # A fresh module instance lets us compose the immutable orchestration with
    # the explicit transform checks without changing any historical globals.
    spec = importlib.util.spec_from_file_location('recovered_npc_math', ROOT/'tools/validate_world_npc_math.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.compare_skin, module.compare_clip = recovered_skin, recovered_clip
    intermediate = ROOT/'local'/('factored-'+output.name)
    result = module.validate(manifest_path, intermediate, lower_priority(), expected_ids=tuple(ADDITIONAL))
    result['validator_sources'] += [reference(__file__), reference(ROOT/'tools/recover_transformed_npcs.py')]
    result['scope']['global_skin_transform'] = (
        'Common original translation matched exactly to GLB wrapper, factored from both evaluations; reported bounds include translation')
    result['limitations'].append('Global translation preservation is numerically proven; Unreal wrapper import remains unverified.')
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(validate(args.manifest, args.report)['summary']))

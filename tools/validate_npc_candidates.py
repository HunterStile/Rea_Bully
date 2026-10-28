"""Compare original NPC NIF/AGR data with candidate GLBs, using CPU math only.

Every animation key is compared. Skin deformation is sampled at t=0 only,
plus the unanimated rest pose. This neither renders nor validates the engine,
native interpolation, Root rebasing policy, AI, collision or animation blends.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
from pathlib import Path

# Keep the small numeric audit on one CPU thread even on BLAS-enabled NumPy.
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
import numpy as np

from bully_agr import FormatError, decode_animation, read_hxd
from catalog_npc_locomotion import NEW_PED_IDS, lower_priority
from export_character import CharacterNif
from validate_player_actions import glb

ROOT = Path(__file__).resolve().parents[1]
C = np.array([[1., 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]])
POSE_TOLERANCE_M = 2e-6


def require(condition, message):
    if not condition:
        raise FormatError(message)


def reference(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def verify(record, cache):
    path = (ROOT / record["path"]).resolve()
    if path not in cache:
        cache[path] = path.read_bytes()
    require(hashlib.sha256(cache[path]).hexdigest() == record["sha256"], f"Source hash mismatch: {path}")
    return path, cache[path]


def nif_matrix(node):
    result = np.eye(4)
    result[:3, :3] = np.asarray(node["rotation"]).reshape(3, 3) * node["scale"]
    result[:3, 3] = node["translation"]
    return result


def quaternion_matrix(value):
    value = np.asarray(value, dtype=float)
    require(np.isfinite(value).all() and np.linalg.norm(value) > 0, "Invalid quaternion")
    x, y, z, w = value / np.linalg.norm(value)
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
        [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
        [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])


def gltf_matrix(node):
    require("matrix" not in node, "Unexpected matrix node in own-skeleton NPC GLB")
    result = np.eye(4)
    result[:3, :3] = quaternion_matrix(node.get("rotation", [0, 0, 0, 1])) @ np.diag(node.get("scale", [1, 1, 1]))
    result[:3, 3] = node.get("translation", [0, 0, 0])
    return result


def accessor(doc, blob, index):
    item = doc["accessors"][index]
    view = doc["bufferViews"][item["bufferView"]]
    width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[item["type"]]
    dtype = {5126: "<f4", 5123: "<u2", 5125: "<u4"}[item["componentType"]]
    require(not item.get("sparse") and not item.get("normalized") and not view.get("byteStride"),
        "Unexpected sparse, normalized or interleaved candidate accessor")
    offset = view.get("byteOffset", 0) + item.get("byteOffset", 0)
    size = item["count"] * width * np.dtype(dtype).itemsize
    require(0 <= item.get("byteOffset", 0) <= view["byteLength"]-size, "Accessor outside buffer view")
    return np.frombuffer(blob, dtype=dtype, count=item["count"]*width, offset=offset).copy().reshape(item["count"], width)


def linear_sample(keys, time):
    times = [key[0] for key in keys]
    index = bisect.bisect_right(times, time)
    if index == 0:
        return np.asarray(keys[0][1], dtype=float)
    if index == len(keys):
        return np.asarray(keys[-1][1], dtype=float)
    ratio = (time-times[index-1])/(times[index]-times[index-1])
    return (1-ratio)*np.asarray(keys[index-1][1]) + ratio*np.asarray(keys[index][1])


def source_skeleton(nif):
    roots = [i for i, kind in enumerate(nif.types) if kind == "NiNode" and nif.block(i)["name"] == "Dummy"]
    require(len(roots) == 1, "Original NPC has no unique Dummy root")
    bones, parents, seen = [], [], set()
    def visit(index, parent):
        node = nif.block(index)
        if node["type"] != "NiNode":
            return
        require(index not in seen, "Original skeleton contains a cycle or repeated child")
        seen.add(index)
        own_index = len(bones)
        bones.append((index, node))
        parents.append(parent)
        for child in node["children"]:
            visit(child, own_index)
    visit(roots[0], None)
    return bones, parents


def accumulated(local, parents):
    result = []
    for index, matrix in enumerate(local):
        parent = parents[index]
        require(parent is None or parent < index, "Skeleton is not parent-before-child")
        result.append(matrix if parent is None else result[parent] @ matrix)
    return result


def deform_gltf(positions, joints, weights, worlds, inverse_binds):
    """Independent linear blend skinning of the actual GLB accessors."""
    homogeneous = np.c_[positions, np.ones(len(positions))]
    result = np.zeros((len(positions), 3))
    for slot in range(joints.shape[1]):
        matrices = np.array([worlds[joint] @ inverse_binds[joint] for joint in joints[:, slot]])
        result += np.einsum("nij,nj->ni", matrices, homogeneous)[:, :3] * weights[:, slot, None]
    return result


def compare_deformation(shapes, source_worlds, gltf_worlds, inverse_binds):
    coordinate_error, distance_error, all_points = 0., 0., []
    for shape in shapes:
        # Source evaluation uses original vertices and sparse NiSkinData weights.
        matrices = {joint: source_worlds[joint] @ bind for joint, bind in shape["source_binds"].items()}
        expected = []
        for vertex, influences in zip(shape["source_vertices_h"], shape["source_influences"]):
            total = sum(weight for _, weight in influences)
            result = sum(weight/total * (matrices[joint] @ vertex) for joint, weight in influences)
            expected.append((C @ result)[:3])
        actual = deform_gltf(shape["positions"], shape["joints"], shape["weights"], gltf_worlds, inverse_binds)
        difference = actual - np.asarray(expected)
        coordinate_error = max(coordinate_error, float(np.max(np.abs(difference))))
        distance_error = max(distance_error, float(np.linalg.norm(difference, axis=1).max()))
        all_points.extend(actual)
    points = np.asarray(all_points)
    require(np.isfinite(points).all(), "Nonfinite deformed pose")
    return {"max_coordinate_error_m": coordinate_error, "max_vertex_distance_m": distance_error,
        "gltf_bounds_m": {"min": points.min(axis=0).tolist(), "max": points.max(axis=0).tolist()}}


def partition_audit(nif, skin, source_influences, bone_lookup):
    if skin["partition"] < 0:
        return {"present": False, "runtime_equivalence": "not_verified"}
    partition = nif.block(skin["partition"])
    rows, changed, maximum, maximum_l1, covered = 0, 0, 0., 0., set()
    for part in partition["partitions"]:
        require(len(part["vertices"]) == len(part["weights"]) == len(part["indices"]), "Incomplete original skin partition")
        for vertex, weights, indices in zip(part["vertices"], part["weights"], part["indices"]):
            expected = dict(source_influences[vertex])
            actual = {}
            for palette_index, weight in zip(indices, weights):
                if weight:
                    joint = bone_lookup[skin["bones"][part["bones"][palette_index]]]
                    actual[joint] = actual.get(joint, 0.) + weight
            errors = [abs(actual.get(joint, 0.)-expected.get(joint, 0.)) for joint in actual.keys() | expected.keys()]
            difference = max(errors, default=0.)
            maximum = max(maximum, difference)
            maximum_l1 = max(maximum_l1, sum(errors))
            changed += difference > 1e-6
            rows += 1
            covered.add(vertex)
    return {"present": True, "partitions": len(partition["partitions"]), "vertex_rows_compared": rows,
        "unique_vertices_compared": len(covered), "rows_different_above_1e_minus_6": changed,
        "max_absolute_weight_difference": maximum, "max_l1_weight_difference": maximum_l1,
        "comparison": "Original partition weights versus raw NiSkinData weights, before glTF normalization",
        "gltf_weight_source": "NiSkinData normalized per vertex", "runtime_equivalence": "not_verified"}


def compare_skin(nif, doc, blob):
    bones, parents = source_skeleton(nif)
    count = len(bones)
    require(count == 36 and len(doc["skins"]) == len(doc["meshes"]) == 1, "Unexpected candidate skin structure")
    require(doc["skins"][0]["joints"] == list(range(count)) and doc["skins"][0]["skeleton"] == 0, "Joint order changed")
    require(doc["scenes"][0]["nodes"] == [0, count] and len(doc["nodes"]) == count+1, "Unexpected wrapper/global transform")
    require(np.array_equal(gltf_matrix(doc["nodes"][count]), np.eye(4)), "Skinned mesh has an extra transform")
    require(doc["nodes"][count]["skin"] == doc["nodes"][count]["mesh"] == 0, "Mesh/skin reference changed")
    lookup = {block: i for i, (block, _) in enumerate(bones)}
    rest_errors = []
    for i, (block, original) in enumerate(bones):
        target = doc["nodes"][i]
        require(target["name"] == original["name"] and target["extras"]["originalNifBlock"] == block, "Bone identity changed")
        require(sorted(target.get("children", [])) == [j for j, parent in enumerate(parents) if parent == i], "Bone hierarchy changed")
        error = float(np.max(np.abs(gltf_matrix(target) - C @ nif_matrix(original) @ C.T)))
        rotation = np.asarray(original["rotation"]).reshape(3, 3)
        rest_errors.append({"bone": i, "name": original["name"], "source_nif_block": block,
            "max_local_matrix_difference": error, "original_rotation_determinant": float(np.linalg.det(rotation)),
            "original_rotation_orthogonality_error": float(np.max(np.abs(rotation.T @ rotation-np.eye(3))))})
    inverse_binds = [value.reshape(4, 4).T.astype(float) for value in accessor(doc, blob, doc["skins"][0]["inverseBindMatrices"])]
    nif_parents = {}
    for i, kind in enumerate(nif.types):
        if kind == "NiNode":
            for child in nif.block(i)["children"]:
                require(child not in nif_parents, "NIF node has multiple parents")
                nif_parents[child] = i
    def geometry_world(index, visited=()):
        require(index not in visited, "Cycle in original geometry hierarchy")
        parent = nif_parents.get(index)
        before = np.eye(4) if parent is None else geometry_world(parent, (*visited, index))
        return before @ nif_matrix(nif.block(index))
    original_shapes = [(i, nif.block(i)) for i, kind in enumerate(nif.types)
        if kind in ("NiTriShape", "NiTriStrips") and nif.block(i)["skin"] >= 0]
    require(len(original_shapes) == len(doc["meshes"][0]["primitives"]), "Skinned shape count changed")
    shapes, reports = [], []
    for (index, shape), primitive in zip(original_shapes, doc["meshes"][0]["primitives"]):
        geometry = nif.block(shape["data"])
        skin = nif.block(shape["skin"])
        data = nif.block(skin["data"])
        vertices = np.asarray(geometry["vertices"])
        homogeneous = np.c_[vertices, np.ones(len(vertices))]
        positions = accessor(doc, blob, primitive["attributes"]["POSITION"])
        require(np.array_equal(positions, (C @ homogeneous.T).T[:, :3].astype(np.float32)), "Original vertices differ")
        require(np.array_equal(accessor(doc, blob, primitive["indices"]).ravel(), np.asarray(geometry["triangles"]).ravel()), "Original triangles differ")
        cancellation_error = float(np.max(np.abs(geometry_world(index) @ nif_matrix(data["transform"])-np.eye(4))))
        require(cancellation_error < 1e-5, "Original geometry/overall skin transform does not cancel")
        influences, binds = [[] for _ in vertices], {}
        for palette, bone in enumerate(data["bones"]):
            joint = lookup[skin["bones"][palette]]
            binds[joint] = nif_matrix(bone)
            require(np.array_equal(inverse_binds[joint], C @ binds[joint] @ C.T), "Original inverse bind changed")
            for vertex, weight in bone["weights"]:
                if weight:
                    influences[vertex].append((joint, weight))
        expected_joints, expected_weights, totals = [], [], []
        for row in influences:
            total = sum(weight for _, weight in row)
            require(0 < len(row) <= 4 and total > 0, "Invalid original weighted vertex")
            expected_joints.append([joint for joint, _ in row] + [0]*(4-len(row)))
            expected_weights.append([weight/total for _, weight in row] + [0.]*(4-len(row)))
            totals.append(total)
        joints = accessor(doc, blob, primitive["attributes"]["JOINTS_0"]).astype(int)
        weights = accessor(doc, blob, primitive["attributes"]["WEIGHTS_0"])
        require(np.array_equal(joints[weights > 0], np.asarray(expected_joints)[weights > 0]), "Weighted joint mapping changed")
        require(np.array_equal(weights, np.asarray(expected_weights, dtype=np.float32)), "Normalized source weights changed")
        shapes.append({"source_vertices_h": homogeneous, "source_binds": binds, "source_influences": influences,
            "positions": positions, "joints": joints, "weights": weights})
        reports.append({"source_shape_block": index, "vertices": len(vertices), "triangles": len(geometry["triangles"]),
            "positions_float32_exact": True, "triangles_exact": True, "weighted_joint_indices_exact": True,
            "inverse_bind_matrices_exact": True, "normalized_weights_float32_exact": True,
            "source_weight_sum_range": [min(totals), max(totals)],
            "max_normalized_weight_rounding_error": float(np.max(np.abs(weights-np.asarray(expected_weights)))),
            "geometry_overall_skin_cancellation_max_error": cancellation_error,
            "skin_partition_comparison": partition_audit(nif, skin, influences, lookup)})
    source_worlds = accumulated([nif_matrix(node) for _, node in bones], parents)
    target_worlds = accumulated([gltf_matrix(node) for node in doc["nodes"][:count]], parents)
    rest = compare_deformation(shapes, source_worlds, target_worlds, inverse_binds)
    rest.update(classification="measured_unanimated_rest_comparison",
        local_matrix_max_difference=max(row["max_local_matrix_difference"] for row in rest_errors),
        bones=rest_errors, local_matrices_within_1e_minus_6=all(row["max_local_matrix_difference"] < 1e-6 for row in rest_errors))
    return bones, parents, shapes, inverse_binds, {"bones": count, "shapes": reports, "rest_pose": rest}


def animation_accessors(doc, blob, animation):
    result = {}
    for channel in animation["channels"]:
        key = (channel["target"]["node"], channel["target"]["path"])
        require(key not in result and key[1] in ("rotation", "translation"), "Unexpected duplicate or extra animation channel")
        sampler = animation["samplers"][channel["sampler"]]
        require(sampler["interpolation"] == "LINEAR", "Unexpected interpolation declaration")
        result[key] = (accessor(doc, blob, sampler["input"]).ravel(), accessor(doc, blob, sampler["output"]))
    return result


def compare_track_keys(keys, converted, kind):
    times, values = converted
    source = np.asarray([value for _, value in keys])
    expected = np.empty_like(source)
    expected[:, :3] = source[:, :3] @ C[:3, :3].T
    if kind == "rotation":
        expected[:, 3] = source[:, 3]
    expected = expected.astype(np.float32)
    require(np.array_equal(times, np.asarray([time for time, _ in keys], dtype=np.float32)), "Animation key times changed")
    require(np.array_equal(values, expected), "Animation key values changed")
    require(np.isfinite(values).all() and np.isfinite(times).all() and np.all(np.diff(times) > 0), "Nonfinite/unordered animation keys")
    return len(times)


def compare_clip(clip, source_tracks, idle_reference, bones, parents, shapes, inverse_binds, doc, blob, animation):
    require(animation["name"] == clip["name"].replace("\\", "_"), "GLB clip identity changed")
    require(animation["extras"]["source_duration_seconds"] == clip["duration"], "GLB clip duration changed")
    root, arrow = source_tracks[1]["translation"], source_tracks[35]["translation"]
    moving = clip["role"] in ("walk", "run", "sprint")
    travel = arrow if arrow else [[time, [value[0], value[1], 0.]] for time, value in root] if moving else []
    reference_root = root[0][1] if clip["role"] == "ambient_idle" else idle_reference
    require(clip["root_reference"]["translation"] == reference_root and clip["root_motion"]["translation"] == travel,
        "Declared rebasing/trajectory differs from its original source curves")
    initial = np.asarray(travel[0][1]) if travel else np.zeros(3)
    converted = animation_accessors(doc, blob, animation)
    expected_keys, adapted_tracks, key_count = set(), [], 0
    for index, track in enumerate(source_tracks):
        adapted = {}
        for kind in ("rotation", "translation"):
            keys = track[kind]
            if kind == "translation" and index == 35:
                keys = []  # ARROW displacement is metadata, never applied twice.
            elif kind == "translation" and index == 1:
                times = sorted({time for time, _ in root+travel})
                keys = [[time, list(np.asarray(bones[1][1]["translation"])+linear_sample(root, time)-reference_root
                    -(linear_sample(travel, time) if travel else 0)+initial)] for time in times]
            adapted[kind] = keys
            if keys:
                key = (index, kind)
                require(key in converted, "Original animation channel missing")
                require(keys[0][0] == 0 and 0 <= keys[-1][0] <= clip["duration"]+1e-6, "Source key outside duration or initial sample unavailable")
                key_count += compare_track_keys(keys, converted[key], kind)
                expected_keys.add(key)
        adapted_tracks.append(adapted)
    require(set(converted) == expected_keys, "Unexpected GLB animation channel")
    original_local, target_local = [], []
    for index, (_, bone) in enumerate(bones):
        original, target = nif_matrix(bone), gltf_matrix(doc["nodes"][index])
        for kind in ("rotation", "translation"):
            if adapted_tracks[index][kind]:
                source_value = adapted_tracks[index][kind][0][1]
                target_value = converted[(index, kind)][1][0]
                if kind == "rotation":
                    original[:3, :3] = quaternion_matrix(source_value) * bone["scale"]
                    target[:3, :3] = quaternion_matrix(target_value) * bone["scale"]
                else:
                    original[:3, 3], target[:3, 3] = source_value, target_value
        original_local.append(original)
        target_local.append(target)
    sample = compare_deformation(shapes, accumulated(original_local, parents), accumulated(target_local, parents), inverse_binds)
    require(sample["max_vertex_distance_m"] <= POSE_TOLERANCE_M, "Initial animated skin pose exceeds numeric tolerance")
    return {"name": clip["name"], "role": clip["role"], "duration_seconds": clip["duration"],
        "all_key_values_and_times_float32_exact": True, "key_samples_compared": key_count,
        "channels_compared": len(converted), "max_key_float32_error": 0.,
        "pose_samples": [{"time_seconds": 0., **sample}], "sample_count": 1,
        "root_reference_translation_game_m": reference_root,
        "root_policy_scope": "Validate declared converter rebasing against original curves; original game rebasing semantics remain unproven"}


def validate(manifest_path, output, priority):
    require(not output.exists(), "Output report must be new")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest["ready_for_unreal"] is False and len(manifest["npcs"]) == 6, "Expected six pending NPC candidates")
    require(sorted(candidate["ped_id"] for candidate in manifest["npcs"]) == sorted(NEW_PED_IDS), "Candidate identity set changed")
    cache, hxd_cache, reports = {}, {}, []
    for candidate in manifest["npcs"]:
        require(candidate["ready_for_unreal"] is False, "Candidate readiness must remain pending")
        glb_path, _ = verify(candidate["source_glb"], cache)
        nif_path, _ = verify(candidate["original_model"], cache)
        verify(candidate["original_texture"], cache)
        export_path, export_bytes = verify(candidate["export_report"], cache)
        export_report = json.loads(export_bytes)
        skin_path, _ = verify(export_report["original_skin"], cache)
        verify(export_report["original_cat_evidence"], cache)
        require(export_report["ped_id"] == candidate["ped_id"] and export_report["model"] == candidate["model"], "Candidate identity changed")
        merge_path = glb_path.parent / "merge-report.json"
        merge_report = json.loads(merge_path.read_text(encoding="utf-8"))
        payload_path, payload_bytes = verify({"path": merge_report["animation_payload"], "sha256": merge_report["animation_payload_sha256"]}, cache)
        require(merge_report["output_sha256"] == candidate["source_glb"]["sha256"], "Merge report targets another GLB")
        payload = json.loads(payload_bytes)
        require(payload["ped"]["id"] == candidate["ped_id"] and len(payload["clips"]) == 5, "Payload ped/clip count changed")
        doc, blob = glb(glb_path)
        require(len(doc["animations"]) == 5, "Expected five candidate GLB animations")
        bones, parents, shapes, inverse_binds, skin_result = compare_skin(CharacterNif(nif_path), doc, blob)
        decoded, sources = [], {}
        for clip in payload["clips"]:
            agr_path, agr_bytes = verify(clip["provenance"]["agr"], cache)
            hxd_path, _ = verify(clip["provenance"]["dictionary"], cache)
            if hxd_path not in hxd_cache:
                hxd_cache[hxd_path] = read_hxd(hxd_path)
            hxd = hxd_cache[hxd_path]
            matches = [record for record in hxd["animations"] if record["name"] == clip["name"]]
            require(len(matches) == 1 and matches[0]["duration"] == clip["duration"]
                and matches[0]["name_hash"] == clip["hxd_record"]["name_hash"]
                and hxd["groups"][matches[0]["group"]]["name"] == clip["provenance"]["group"], "Original HXD identity/duration changed")
            start, size = clip["agr_offset"], clip["agr_size"]
            require(0 <= start <= len(agr_bytes)-size, "AGR clip extent outside source")
            raw = agr_bytes[start:start+size]
            require(hashlib.sha256(raw).hexdigest() == clip["agr_sha256"], "Original AGR clip bytes changed")
            original = decode_animation(raw, clip["duration"])
            require(original["tracks"] == clip["source_channels"] and len(original["tracks"]) == 36, "Payload curves differ from original AGR")
            decoded.append(original["tracks"])
            sources[clip["name"]] = {"agr": reference(agr_path), "hxd": reference(hxd_path),
                "agr_offset": start, "agr_bytes": size, "agr_clip_sha256": clip["agr_sha256"]}
        require(payload["clips"][0]["role"] == "idle", "Expected original idle as declared root reference")
        idle_reference = decoded[0][1]["translation"][0][1]
        clips = [dict(compare_clip(clip, tracks, idle_reference, bones, parents, shapes, inverse_binds, doc, blob, animation),
            source=sources[clip["name"]]) for clip, tracks, animation in zip(payload["clips"], decoded, doc["animations"])]
        reports.append({"ped_id": candidate["ped_id"], "model": candidate["model"], "ready_for_unreal": False,
            "sources": {"nif": reference(nif_path), "glb": reference(glb_path), "export_report": reference(export_path),
                "skin_report": reference(skin_path), "animation_payload": reference(payload_path), "merge_report": reference(merge_path)},
            "skin": skin_result, "clips": clips})
    rest = [item["skin"]["rest_pose"] for item in reports]
    samples = [sample for model in reports for clip in model["clips"] for sample in clip["pose_samples"]]
    result = {"schema": "rea-bully-npc-candidate-math-validation-v1", "status": "numeric_checks_passed_with_documented_rest_pose_loss",
        "passed": True, "ready_for_unreal": False, "runtime_validation": "pending", "visual_validation": "pending",
        "process_priority": priority, "numpy_version": np.__version__, "blas_thread_limit": 1,
        "candidate_manifest": reference(manifest_path),
        "validator_sources": [reference(ROOT / "tools" / name) for name in
            ("validate_npc_candidates.py", "export_character.py", "export_nif.py", "bully_agr.py", "validate_player_actions.py")],
        "scope": {"original_binary_decoders": "Existing shared NIF/AGR codecs; this is not an independent binary-format implementation",
            "mathematical_evaluation": "Independent NumPy matrix/hierarchy and sparse-source versus GLB linear blend skinning; no exporter transform/skinning functions called",
            "curve_coverage": "Every exported animation key and time compared at float32 precision",
            "pose_sampling": "Only t=0 for each of 30 clips, plus six unanimated rest poses",
            "interpolation_validated": False, "native_root_policy_validated": False, "capsule_or_ground_offset_derived": False},
        "tolerances": {"animated_sample_max_vertex_distance_m": POSE_TOLERANCE_M,
            "rest_pose_loss": "Measured and retained without declaring exact rest preservation; not silently repaired"},
        "summary": {"skins": len(reports), "clips": sum(len(item["clips"]) for item in reports),
            "animated_pose_samples": len(samples), "sample_times_seconds": [0.], "rest_pose_samples": len(rest),
            "animation_key_samples_compared": sum(clip["key_samples_compared"] for item in reports for clip in item["clips"]),
            "max_key_float32_error": 0., "max_animated_coordinate_error_m": max(s["max_coordinate_error_m"] for s in samples),
            "max_animated_vertex_distance_m": max(s["max_vertex_distance_m"] for s in samples),
            "max_rest_coordinate_error_m": max(s["max_coordinate_error_m"] for s in rest),
            "max_rest_vertex_distance_m": max(s["max_vertex_distance_m"] for s in rest),
            "models_with_rest_matrix_loss_above_1e_minus_6": [item["ped_id"] for item in reports if not item["skin"]["rest_pose"]["local_matrices_within_1e_minus_6"]]},
        "models": reports,
        "limitations": ["Passing sampled numeric checks does not validate the full deformed clip, native playback, engine import or visual fidelity.",
            "Root rebasing and in-place extraction are checked as declared conversion policies, not established as original game behavior.",
            "Five NPC rest skeletons contain slightly nonorthogonal source matrices. Quaternion conversion changes their unanimated skin by millimetres; original inverse binds remain exact.",
            "NiSkinData and NiSkinPartition differ for some NPCs. This report measures their weights; original CPU/GPU runtime selection and visual equivalence remain unresolved.",
            "Sampled bounds are diagnostic, not validated capsule dimensions or ground offsets."]}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    return result


if __name__ == "__main__":
    priority = lower_priority()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "evidence/npc-candidate-manifest-v05.json")
    parser.add_argument("--report", type=Path, default=ROOT / "evidence/npc-candidate-math-validation-v05.json")
    args = parser.parse_args()
    print(json.dumps(validate(args.manifest, args.report, priority)["summary"], indent=2))

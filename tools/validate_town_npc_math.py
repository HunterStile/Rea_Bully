"""Outdoor-cast mathematical validation with source-group identity and original global translations."""
import hashlib
import json
from pathlib import Path

from validate_npc_candidates import (ROOT, NEW_PED_IDS, POSE_TOLERANCE_M, np, require, verify, reference, glb,
    CharacterNif, read_hxd, decode_animation)
from town_npc_transforms import compare_town_skin as compare_skin
from validate_recovered_npcs import recovered_clip as compare_clip

def validate(manifest_path, output, priority, expected_ids=NEW_PED_IDS):
    require(not output.exists(), "Output report must be new")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest["ready_for_unreal"] is False and len(manifest["npcs"]) == len(expected_ids), "Expected exact pending NPC candidate count")
    require(sorted(candidate["ped_id"] for candidate in manifest["npcs"]) == sorted(expected_ids), "Candidate identity set changed")
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
            matches = [record for record in hxd["animations"] if record["name"] == clip["name"] and hxd["groups"][record["group"]]["name"] == clip["provenance"]["group"]]
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
            ("validate_town_npc_math.py", "town_npc_transforms.py", "validate_recovered_npcs.py", "recover_transformed_npcs.py", "validate_npc_candidates.py", "export_character.py", "export_nif.py", "bully_agr.py", "validate_player_actions.py")],
        "scope": {"original_binary_decoders": "Existing shared NIF/AGR codecs; this is not an independent binary-format implementation",
            "mathematical_evaluation": "Independent NumPy matrix/hierarchy and sparse-source versus GLB linear blend skinning; no exporter transform/skinning functions called",
            "curve_coverage": "Every exported animation key and time compared at float32 precision",
            "pose_sampling": f"Only t=0 for each of {sum(len(m['clips']) for m in reports)} clips, plus {len(reports)} unanimated rest poses",
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
        "limitations": ["Civilian duplicate idle-name resolution uses the first IDE-declared animation bank by project policy; native precedence remains unresolved.",
            "Common global translations are checked exactly and factored from both evaluations; reported bounds include them.",
            "Passing sampled numeric checks does not validate the full deformed clip, native playback, engine import or visual fidelity.",
            "Root rebasing and in-place extraction are checked as declared conversion policies, not established as original game behavior.",
            "Five NPC rest skeletons contain slightly nonorthogonal source matrices. Quaternion conversion changes their unanimated skin by millimetres; original inverse binds remain exact.",
            "NiSkinData and NiSkinPartition differ for some NPCs. This report measures their weights; original CPU/GPU runtime selection and visual equivalence remain unresolved.",
            "Sampled bounds are diagnostic, not validated capsule dimensions or ground offsets."]}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    return result

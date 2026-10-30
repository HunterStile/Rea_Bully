# Roadmap

The immediate goal is a local explorable reconstruction covering every recoverable world zone, with the character animations and ambient activities needed for exploration. Missions are outside this phase. This page reflects the v07 developer milestone of 30 October 2026.

| Work | Current state | Evidence required for the next milestone |
|---|---|---|
| Windows setup | Local developer workflow works; 165 synthetic checks pass from this public source snapshot; clean-clone bootstrap incomplete | Successful reconstruction on a fresh machine from its own installed copy, with documented dependencies and configurable tool paths |
| World coverage | 85 saved zones across 47 areas; all 85 sampled checkpoints passed streaming, recovered floor contact and short keyboard walks | Longer continuous routes, difficult collision edges and condition-dependent placements checked against source evidence |
| Characters | Jimmy and 31 distinct NPCs imported and placed; all 31 IDs observed in passive runtime samples | Per-character appearance, clips and routes checked visibly; original autonomous behavior implemented only with supported bindings and schedules |
| Ambient activities | 533 skinned prop placements; two garage interactions and six carnival gestures verified through real F input; selected audio, patrols and doors | More original prop activities, contextual triggers and autonomous schedules, with complete gesture-to-idle behavior and no arbitrary animation activation |
| Physics and travel | 1,132 original collision instances, including a verified helper, and 160 door placements; 47-entry area menu with real keyboard checks | Wider door-route and collision coverage, including unsupported original primitive cases and continuous travel |
| Presentation | Nine final views and the integrated rendered/audio probe accepted for a developer preview | Better shader/material and original lighting fidelity; investigation of foliage cards, hard shadows and apparently mirrored signs against source UVs and viewing sides |
| Verification | 350 local Python checks pass; runtime proofs bind the tested DLL, maps and frozen package set | Repeatable reconstruction and verification on a separate installation; broader gameplay and visual samples with measured stability on documented hardware |

The next work prioritizes shader and lighting fidelity, then source-supported prop activities and autonomous population behavior, alongside a reproducible clean-clone bootstrap. The preview rendering profile improves readability; it is not a recreation of the original area lighting. The cause of the apparent sign-orientation defects remains unproven.

Original population scheduling, full native AI, vehicles, combat, saves, minigames and the Lua native API bridge remain incomplete. The broader ambition includes more of Bully, but this page does not promise a complete campaign or a delivery date. Imported characters and animation clips alone do not establish completed activities or original AI.

A useful public demonstration would be a short, uncut route through several areas on Windows, showing NPC activities and working transitions, accompanied by exact setup instructions. That is a proposed delivery, not a video or clean setup already provided. Fixed-step test results are not interactive FPS benchmarks.

The [project comparison](PROJECT_COMPARISON.md) remains a historical comparison researched on 28 October 2026; it is not a new survey of other projects as of this milestone.

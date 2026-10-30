# Verification scope — v07 development milestone

Local checks completed on 30 October 2026. The final integrated render and nine-image tour are accepted for a developer preview with the limits below. The v07 local release record passed, binding the tested runtime, 31,802 generated package/configuration files and ten reviewed images to their recorded hashes.

## Results and boundaries

| Evidence stage | Verified result | What it does not establish |
|---|---|---|
| Saved world | All 85 source zones, covering 47 areas, assembled through bounded batches; 6,984 visible static placements | Every original object, condition-dependent placement or gameplay system |
| Full traversal probe | 85/85 sampled source checkpoints passed streaming, recovered floor contact and a short keyboard walk | Exhaustive traversal, every route or collision edge, or continuous travel between all checkpoints |
| Characters | 31 distinct imported and placed NPCs; passive full-tour samples contained all 31 IDs | Complete per-NPC behavior, every clip or route, original AI or autonomous scheduling |
| Props and doors | 533 skinned prop placements, 160 door placements, 1,132 original collision instances including one helper | All props interactive, all door routes individually input-tested, or original object physics in every case |
| Garage probe | 2/2 source placements passed actual F open/close, original clip endpoints, stable poses and no automatic travel | Bicycle storage/gameplay or collision verification for the garage interaction |
| Carnival probe | 6/6 source placements passed two held-F gestures each, one-shot completion and original idle resumption | Original activity scheduling, conversation or speech |
| Navigation menu | 47 entries derived from successful source checkpoints; real Tab/Esc and selection controls, two distinct arrivals verified | Keyboard selection of every entry independently tested |
| Integrated rendered probe | Player movement, sampled NPC/floor behavior, doors, interior entry/return and selected audio checks passed | All NPC behavior or all 207 samples individually playback-tested |
| Python suite | 350/350 checks passed, zero failures/errors/skips, unchanged tool/test SHA inventory | A clean-clone recreation of the asset-dependent world or whole-game compatibility |
| Public source-only checks | 165/165 synthetic checks passed without reading the owner's generated assets or writing to the workspace | Asset reconstruction, engine execution or the 350-test asset-dependent scope |
| Visual tour | 9/9 screenshots inspected and accepted for a developer preview; no loading or engine-warning overlay in these images | Visual coverage of all 85 zones, every NPC appearance, original lighting fidelity or finished visual quality |

Manual F interactions, the area menu, streaming policy and ambient selection are reconstructed exploration behavior. Original coordinate/clip provenance does not turn that control policy into the original scheduler.

## Local evidence references

The developer workspace retains immutable reports including:

- `world-scene-build-v07-r2.json` — final assembled scene and placement counts.
- `world-tour-runtime-v07.json` — the complete 85-checkpoint traversal probe.
- `prop-interactions-runtime-v07-release.json` and `prop-gestures-runtime-v07-release.json` — two garage and six carnival checks.
- `area-navigation-plan-v07-release.json` and `area-navigation-runtime-v07-release.json` — 47 entries and the real keyboard probe.
- `world-integrated-render-v07-release-r4.json` — the final integrated rendered/audio run using its recorded renderer settings.
- `world-tour-visual-v07-release-r3.json` — the final nine-capture tour under the same profile.
- `world-visual-review-v07-r3.json` and `world-integrated-render-review-v07-r4.json` — actual image reviews, accepted for a developer preview with explicit limits.
- `world-expansion-tests-v07-r7.json` — the 350-test regression report and source inventories.
- `world-release-v07.json` — the passed local release record binding source plans, runtime proofs, renderer profile, generated files and reviewed images.

These reports contain owner-specific paths and derived game metadata and are not distributed in the source repository. The public synthetic-check runner has its own scope and count; the local total must not be presented as a clean-clone result. Historical v06 and intermediate/failed reports remain separate rather than being rewritten.

## Release and performance limits

The launcher points to local `world-release-v07.json` and refuses to start when it is absent, unpassed or no longer matches the DLL and generated packages. No old release is silently substituted. Renderer settings must match the profile actually tested and reviewed before release recording.

The final preview profile uses shadow quality 2, non-Nanite coarse shadow pages disabled, global illumination quality 1 with Lumen diffuse indirect disabled, reflection quality 1, directional shadow LOD bias 2 and skylight intensity multiplier 2. The final tour log contains no recurrence of the earlier VSM marking-queue warning. The profile improves readability but does not recover the original per-area lighting.

Visible follow-up work includes flat or fragmented foliage cards, hard and very dark shadowed surfaces, and some signs that appear mirrored or inverted. Their original UVs and viewing sides have not yet been compared sufficiently to identify the cause. A few views are dominated by a wall or fence. Nine static viewpoints are a limited visual sample of the 85-checkpoint traversal plan, and passive NPC samples do not establish that all 31 NPCs were visible or visually correct.

The completed bounded build does not prove that rendered multi-area play fits any particular memory budget. Rendered checks have shown low host RAM/commit headroom, monitored separately. Fixed-step samples are not interactive frame-rate benchmarks. No hardware-performance guarantee or visual superiority over the original has been established.

A visual fix must be tested with new report/image names. Changing the renderer profile requires fresh rendered evidence for that profile; changing persistent assets or the runtime DLL also invalidates the corresponding snapshot-bound proofs. Source recovery, successful import, saved content, functional checks and visual acceptance remain distinct.

# Bully Decompiled — Unreal Reconstruction

**Decompilation, reverse engineering and reconstruction of Bully: Scholarship Edition in Unreal Engine 5.8.** Windows-focused, built with AI assistance, and developed against an owner-installed PC copy.

**Status: v07 developer preview, locally verified and recorded on 30 October 2026.** The local prototype now assembles 85 source zones covering 47 areas, with 31 placed NPCs, selected original animations and manual exploration interactions. The full game, original AI and complete ambient activities remain in development. This repository contains our code; generated game content and local analysis outputs stay on the owner's machine.

## What “decompiled” means here

The research pipeline examines the original executable with REA/Ghidra, disassembles and decompiles Lua 5.0 scripts locally, and recovers asset formats and animation data. The Unreal runtime is a new implementation informed by those findings. Native decompiler output is analysis evidence, not a complete original C++ source tree or a matching recompilation of Bully.exe.

## Current local milestone

The v07 development milestone of 30 October 2026 separates saved content, runtime checks and visual acceptance:

| Area | Locally verified scope |
|---|---|
| World assembly | 85 saved source zones spanning 47 areas; 6,984 visible static placements from the recovered placement plan |
| Traversal | All 85 sampled source checkpoints passed streaming, floor-contact and short keyboard-walk checks |
| Player | Jimmy's recovered skin and clips with reconstructed walking, running, jumping and camera control |
| Ambient characters | 31 distinct NPCs imported and placed; all 31 IDs observed in passive tour samples, without complete behavior certification |
| Scene objects | 533 skinned prop placements, 160 door placements and 1,132 original collision instances including one separately verified helper |
| Garages | Both original placements passed real F-input open/close and endpoint checks; the exploration control is reconstructed |
| Carnival gestures | Six original placements passed manual one-shot gestures and return to original idle; no autonomous scheduler claim |
| Area menu | 47 entries derived from source checkpoints; actual keyboard focus/cancel and two selected-area arrivals verified |
| Audio | 207 imported samples; selected footsteps and Birds ambience verified in the integrated rendered probe |
| Python regression suite | 350 local checks passed without failures, errors or skips; tool/test hashes stayed unchanged during the run |

The final nine-image GPU tour and integrated audio/rendered probe passed under the same recorded preview profile. All nine tour images were inspected and accepted for a developer preview: the earlier shadow warning is absent and interior readability has improved. Foliage cards, hard shadows and some apparently mirrored signs remain visible limitations; the cause of the sign orientation has not been established. Lighting is an adaptation, not a faithful recreation of the original area lighting. These sampled views do not verify every scene or all 31 NPC appearances. [Detailed verification scope](docs/VERIFICATION.md).

These asset-dependent results require the owner's generated evidence and content. Cloning the source repository alone does not reproduce them. Missions, combat, vehicles, saves, complete original AI and full ambient activities remain incomplete.

## Where we intend to distinguish the project

1. **Reproducible Windows setup.** The prototype runs locally on Windows; a complete clean-clone reconstruction workflow remains a delivery target.
2. **World coverage backed by source evidence.** All 85 recovered source zones are assembled and sampled across 47 areas. Exhaustive traversal and complete original activities remain separate work.
3. **Ambient life with measured fidelity.** Character-specific clips, original placements and collision evidence, followed by visible interactions. Reconstructed behavior is labelled explicitly.
4. **Demonstrations that can be checked.** Same-location footage, measured routes and published limitations, without a finished-game or guaranteed-performance claim.

Unreal is already used by other projects. We do not claim this prototype is more complete or visually superior to them. [Project comparison, researched 28 October 2026](docs/PROJECT_COMPARISON.md) · [Roadmap](docs/ROADMAP.md).

## Getting started

Use Python 3.12 and your own installed PC copy for archive inspection:

```powershell
git clone https://github.com/HunterStile/Rea_Bully.git
cd Rea_Bully
$env:BULLY_INSTALL = 'C:/Path/To/Your/Bully Scholarship Edition'
python tools/inspect_bully.py --game-root "$env:BULLY_INSTALL" --output evidence/my-inventory.json
python tools/bully_archives.py inventory "$env:BULLY_INSTALL" --output evidence/my-archives.json
python tools/bully_archives.py extract "$env:BULLY_INSTALL/Stream/World.dir" --indexed --output local/my-world-extraction
```

For synthetic source-only checks, run `python tools/run_public_checks.py`. This suite passed 165 checks without reading the owner's generated assets; its scope and count are separate from the 350 local asset-dependent checks.

Running the Unreal prototype additionally requires Unreal Engine 5.8.3, a compatible Windows C++ toolchain, locally reconstructed assets, generated maps and provenance reports. The scripts still expect development-workspace staging files and fixed engine locations. **A fully automated setup and a downloadable playable build are not available yet.** Read [setup status](docs/SETUP.md) before attempting to launch.

## Contributing and license

Useful contributions include Windows setup, original-format parsers, animation/physics fidelity, ambient activities and focused regression checks. Describe the source evidence, behavior changed and validation performed. See [CONTRIBUTING.md](CONTRIBUTING.md).

Do not commit game files, extracted assets, decompiled game scripts, executable pseudocode dumps, generated Unreal content, credentials or personal logs. Contributors obtain required game data from their own installation.

Original project code is [MIT licensed](LICENSE). Bully and its proprietary content belong to their respective rights holders and are not included in or licensed by this repository. This is an unofficial project, not affiliated with Rockstar Games or Take-Two Interactive.

Research references include [REA](https://github.com/morluto/rea), [LiquidSnake0/bully-re](https://github.com/LiquidSnake0/bully-re), [Lua 5.0](https://www.lua.org/source/5.0/) and [unluac](https://sourceforge.net/projects/unluac/). External tools are obtained separately under their own licenses. We also compare progress with [oh-ashen-one/bully-remake](https://github.com/oh-ashen-one/bully-remake).

# Bully Decompiled — Unreal Reconstruction

**Decompilation, reverse engineering and reconstruction of Bully: Scholarship Edition in Unreal Engine 5.8.** Windows-focused, built with AI assistance, and developed against an owner-installed PC copy.

**Status: developer preview.** The local prototype supports exploration of the campus, school hallways and boys' dormitory. The full game, original AI and complete ambient activities are still in development. This public repository contains our code; generated game content and local analysis outputs stay on the owner's machine.

## What “decompiled” means here

The research pipeline examines the original executable with REA/Ghidra, disassembles and decompiles Lua 5.0 scripts locally, and recovers asset formats and animation data. The Unreal runtime is a new implementation informed by those findings. Native decompiler output is analysis evidence, not a complete original C++ source tree or a matching recompilation of Bully.exe.

## Current local milestone

The published source snapshot corresponds to the locally tested v06 milestone of 26 October 2026:

| Area | Locally verified scope |
|---|---|
| Exploration | Four streamed source levels: `tGlobal`, `tschool`, `ischool`, `isc_dorm` |
| Player | Jimmy's recovered skin and animations; walking, running, jumping and camera controls |
| Ambient characters | Seven distinct NPCs with their own skins and clips |
| Scene content | 537 static placements, 111 skinned props currently in bind pose, 62 door placements |
| Collision | 63 placed COL3 bodies from 35 models, plus explicitly retained mesh fallback |
| Audio | 207 imported samples; selected footsteps and Birds ambience connected to the runtime |
| Runtime checks | Keyboard movement, source geometry floor contact, door animation, school/dormitory entry and return |

The local milestone passed 214 Python tests and a rendered runtime check. Those asset-dependent checks require the owner's generated evidence and content; they are not reproduced merely by cloning this repository. [Verification scope](docs/VERIFICATION.md).

## Where we intend to distinguish the project

1. **Windows setup that people can actually reproduce.** The developer machine runs the prototype on Windows; a clean-clone reconstruction workflow remains a delivery target.
2. **A broader explorable world.** All 85 recovered source zones are catalogued locally. Import and traversal testing beyond the four-level milestone are in progress.
3. **Ambient life with measured fidelity.** Character-specific animation, original placement and collision evidence, followed by visible activities and interactions. Reconstructed behavior is labelled explicitly.
4. **Demonstrations that can be checked.** Same-location footage, measured routes and published limitations rather than claims of a finished game or guaranteed performance.

Unreal itself is already used by other projects. We do not claim this prototype is more complete or visually superior to them. [Comparison with existing projects](docs/PROJECT_COMPARISON.md) · [Roadmap](docs/ROADMAP.md).

## Getting started

The archive inspection tools can be used from a clean clone with Python 3.12 and your own installed PC copy:

```powershell
git clone https://github.com/HunterStile/Rea_Bully.git
cd Rea_Bully
$env:BULLY_INSTALL = 'C:/Path/To/Your/Bully Scholarship Edition'
python tools/inspect_bully.py --game-root "$env:BULLY_INSTALL" --output evidence/my-inventory.json
python tools/bully_archives.py inventory "$env:BULLY_INSTALL" --output evidence/my-archives.json
python tools/bully_archives.py extract "$env:BULLY_INSTALL/Stream/World.dir" --indexed --output local/my-world-extraction
```

For source-only checks, run `python tools/run_public_checks.py`.

Running the Unreal prototype additionally requires Unreal Engine 5.8.3, a compatible Windows C++ toolchain, locally reconstructed assets, generated maps and provenance reports. The current build/import scripts still expect development-workspace staging files and fixed engine locations. **A fully automated setup and a downloadable playable build are not available yet.** See [setup status](docs/SETUP.md) before attempting to launch the project.

## Contributing

Useful contributions include reproducible Windows setup, original-format parsers, animation/physics fidelity, ambient activities and focused regression checks. Describe the source evidence, the behavior changed and the verification performed. See [CONTRIBUTING.md](CONTRIBUTING.md).

Do not commit game files, extracted assets, decompiled game scripts, executable pseudocode dumps, generated Unreal content, credentials or personal logs. Contributors obtain required game data from their own installation.

## Credits and license

Original project code is [MIT licensed](LICENSE). Bully and its proprietary content belong to their respective rights holders and are not included in or licensed by this repository. This is an unofficial project, not affiliated with Rockstar Games or Take-Two Interactive.

Research references include [REA](https://github.com/morluto/rea), [LiquidSnake0/bully-re](https://github.com/LiquidSnake0/bully-re), [Lua 5.0](https://www.lua.org/source/5.0/) and [unluac](https://sourceforge.net/projects/unluac/). External tools are obtained separately under their own licenses. We also compare progress with [oh-ashen-one/bully-remake](https://github.com/oh-ashen-one/bully-remake).

Development is AI-assisted. Source recovery, local validation and unfinished behavior are described separately so that readers can assess the actual state of the project.

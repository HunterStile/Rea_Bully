# Setup status

The public source snapshot contains Python recovery tools and Unreal C++ source. It does not include the maps, characters, animations, local reports or compiled runtime used by the developer's prototype.

## Available from a clean clone

Use Python 3.12 for synthetic checks and archive inspection against your own installed Bully: Scholarship Edition PC copy. The README provides inventory/extraction commands. Extraction writes to a new directory and records hashes; retain the indexed layout because original archive basenames can repeat.

## Unreal developer workflow

The local milestone used Unreal Engine 5.8.3 on Windows with a C++ toolchain. The project enables PythonScriptPlugin and EditorScriptingUtilities and uses Interchange import. Obtain Unreal through Epic's official distribution and other tools from their upstream projects.

`tools/build_unreal_runtime.py` currently expects a workspace-local MSVC/Windows SDK layout. Several wrappers assume `C:/Program Files/Epic Games/UE_5.8`. Standard-toolchain discovery and configurable engine paths remain portability work.

Converters and scene builders depend on staged manifests under `evidence/` and converted files under `local/`. Archive extraction alone does not create this dependency graph. The builder also requires locally prepared lighting/player maps. The v07 pipeline builds zone levels in bounded fresh-process batches and assembles their soft references separately; this does not remove the staged-input requirement. A single clean-clone bootstrap command has not yet been validated.

`python tools/launch_world.py --check` expects `evidence/world-release-v07.json`, a matching DLL and matching generated package hashes. Renderer settings come from the verified report, with a 60 FPS cap that is not a performance guarantee. The developer workspace has a passed v07 local release record and reviewed developer-preview images. A clean clone lacks this owner-specific report and its generated assets, so the check intentionally refuses to launch until those requirements are satisfied. Do not substitute an old report to make a new installation appear verified.

After local reconstruction and release verification, `python tools/launch_world.py` starts exploration. Controls: WASD/mouse, Space to jump, Shift to run, V for camera view, E for doors, F for selected garage/carnival interactions, Tab for area navigation and R to return to campus. Esc closes an open menu; outside the menu it exits. These controls are reconstruction features.

Run the full local regression suite with `python -m unittest discover -s tests -v` in the environment containing the project dependencies. Do not add `-S`: it disables site packages required by asset-dependent tests. Use the public-check runner for synthetic checks from a clean clone.

## Platform claims

Windows is the developer-tested platform. A fresh Windows installation, Linux and macOS are not yet verified. This is a source release for developers; a reproducible end-user setup remains on the roadmap.

# Setup status

The public snapshot contains Python recovery tools and Unreal C++ source. It does not contain the maps, characters, animation assets, local reports or compiled runtime used in the developer's verified prototype.

## Available from a clean clone

Use Python 3.12 to run the synthetic checks and inspect archives from your own installed Bully: Scholarship Edition PC copy. The README gives read-only inventory and extraction commands. Extraction writes to a new directory and records hashes; retain the indexed layout because original archive basenames can repeat.

## Unreal developer workflow

The verified local milestone used Unreal Engine 5.8.3 on Windows with a C++ toolchain. The project enables PythonScriptPlugin and EditorScriptingUtilities and uses Interchange import. Install Unreal through Epic's official distribution and obtain other tools from their upstream projects.

`tools/build_unreal_runtime.py` currently expects a workspace-local MSVC/Windows SDK layout. Several wrappers assume `C:/Program Files/Epic Games/UE_5.8`. A standard-toolchain discovery path and configurable engine location remain portability work.

Converters and scene builders depend on staged manifests under `evidence/` and converted files under `local/`. The archive extraction example alone does not create that complete dependency graph. The verified scene builder also expects locally prepared lighting/player maps. These inputs are not distributed, and a single clean-clone bootstrap command has not yet been validated.

`tools/launch_world.py --check` intentionally requires a verified local release report, matching DLL and matching generated scene hashes. Do not bypass those checks or substitute an old report to make a new installation appear verified.

## Platform claims

Windows is the developer-tested platform for this project. A fresh Windows installation, Linux and macOS are not yet verified. The current project is a source release for developers, with a reproducible end-user setup still on the roadmap.

# Contributing

Please keep changes focused and state what a player or developer can now do. Include a concrete reproduction, relevant checks, and any remaining fidelity gaps.

Parser and recovery changes should retain source identity, reject unsupported input explicitly and distinguish recovered values from reconstruction policy. Synthetic fixtures should be created specifically for tests. Asset-dependent checks belong in the local reconstruction workflow; do not upload their game-data fixtures.

Before submitting, run `python tools/run_public_checks.py`. Unreal changes also need a local build and an in-engine check where possible; state clearly when those checks have not run.

Never include credentials, personal paths, installed game content, generated maps/materials/meshes, decompiled game scripts, native executable dumps or proprietary dependencies. Keep them in ignored local directories.

Original contributions are submitted under this project's MIT license. Respect the licenses of external projects; linking to format research does not grant permission to copy another runtime.

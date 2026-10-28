# Existing projects and our direction

Research date: 28 October 2026. This comparison uses authors' documentation and source code. We did not build or play these external projects during this review. Author-reported checks are not independent verification by Rea Bully.

| Project | Observed scope | Implication for our work |
|---|---|---|
| [oh-ashen-one/bully-remake](https://github.com/oh-ashen-one/bully-remake) | Unreal 5.8 reconstruction, documented on Apple Silicon Macs. The newer handoff describes early Chapter 1, campus systems and 28 pedestrians. | Unreal, AI-assisted development and campus NPCs already exist elsewhere. These alone do not distinguish us. |
| [LiquidSnake0/bully-re](https://github.com/LiquidSnake0/bully-re) | C++ engine reconstruction, a software-rendered exploration viewer and original Lua script execution tools. | This pursues engine fidelity through a different runtime. We should compare actual behavior, not labels. |
| [oh-ashen-one/bully-decompilation](https://github.com/oh-ashen-one/bully-decompilation) | Separate executable-research project; its examined checkpoint reports no recovered game functions or playable reconstruction. | A decompilation research repository and a playable Unreal reconstruction represent different kinds of progress. |
| Rea Bully | Windows developer-tested Unreal prototype: campus, hallways and dormitory across four source levels, Jimmy and seven ambient NPCs. | A broader world, reproducible installation and ambient activities remain work to deliver. |

## Source details

For `bully-remake`, the [26 October handoff at 23977e5](https://github.com/oh-ashen-one/bully-remake/blob/23977e590c3b86ab5fd6c8fb6c1aaeea0da1b1b9/HANDOFF.md) is newer than the README's milestone. It describes additional story and campus functionality, remaining approximations, and pending human testing. Its [launcher](https://github.com/oh-ashen-one/bully-remake/blob/23977e590c3b86ab5fd6c8fb6c1aaeea0da1b1b9/tools/play_story.sh) uses Mac paths; the [README](https://github.com/oh-ashen-one/bully-remake/blob/23977e590c3b86ab5fd6c8fb6c1aaeea0da1b1b9/README.md) does not verify Windows/Linux setup. This suggests a Windows usability opportunity, not that a port is impossible. Its original code is MIT licensed.

For `bully-re`, the [27 October README at 7c4af2a](https://github.com/LiquidSnake0/bully-re/blob/7c4af2afe865421e7c5759e2c06e8b4a6da409c7/README.md) distinguishes exploration and Lua tooling. Tool-driven completion of scripts is not evidence of a complete manually playable campaign. The [renderer documentation](https://github.com/LiquidSnake0/bully-re/blob/7c4af2afe865421e7c5759e2c06e8b4a6da409c7/docs/rendu-temps-reel.md) describes software rendering and SDL2. Its license is GPL-3.0; we use it as an attributed research reference, not a runtime dependency.

The separate [`bully-decompilation` checkpoint at 23b95ce](https://github.com/oh-ashen-one/bully-decompilation/blob/23b95ce5de1d212e04679a101f930ff811585fc2/README.md) explicitly limits its claims. Repositories such as [Bully-SE-scripts](https://github.com/Razcoina/Bully-SE-scripts) concern script research and should not be counted as complete playable remakes.

## Proposed distinction

Our strongest direction is **a reproducible Windows free-roam reconstruction**, supported by broader area coverage, contextual activities, and clear source-to-runtime evidence. These are proposed delivery priorities, not advantages already established over every project above.

The next persuasive public milestone is a clean-machine setup plus an uncut, real-time exploration demonstration: campus, interiors, NPC activity and return, with commit and hardware recorded. [Our roadmap](ROADMAP.md) and [current verification scope](VERIFICATION.md) keep that target separate from today's source release. This research does not predict popularity or establish superior graphics.

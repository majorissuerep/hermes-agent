# Shared product builders

Hermes separates locked dependency preparation from compilation and assembly.
The supported application product is the Ink TUI; there is no desktop or web dashboard product.

| Layer | Responsibility | Implementation |
|---|---|---|
| Dependency provider | Prepare locked Python/JavaScript tools and native libraries | PM, `scripts/build/node-deps.mjs`, Nix, Termux |
| Product builder | Compile Ink or assemble the agent from prepared inputs | `scripts/build/tui.mjs`, `scripts/build/agent.py` |
| Distribution adapter | Select products and package for its native target | `scripts/bundles/stage.py`, Dockerfile, Nix, Termux |

From a clean checkout, prepare the complete workspace union once:

```sh
node scripts/build/node-deps.mjs --source /work/source --workspace ui-tui --workspace apps/shared
node scripts/build/tui.mjs --source /work/source --out /work/products/tui
python scripts/bundles/stage.py --ref HEAD --tui /work/products/tui --out /work/agent
```

Use PM-provisioned tools and a build-owned Hermes home/runtime. Never mutate a Hermes
Python environment with raw pip or uv. Dependency preparation may access registries;
product builders consume prepared inputs and refuse missing inputs. Nix and Termux
supply their native dependency environments rather than borrowing another target's environment.

Linux build hosts need Clang for the current PM Python's native-extension compiler,
plus CMake, pkg-config and the platform development headers. Install the browser
runtime libraries listed in the [Dockerfile runtime base](../../Dockerfile), including
Cua's X11, Xi and xkbcommon dependencies, before staging the complete tool set.
PM verifies native tools by executing their probes during assembly.

The stage adapter accepts a source ref and prepared TUI. Its output is a terminal
agent payload, not a signed GUI installer. Docker, Nix and Termux retain their own
native layouts. Parser/helper tests do not prove a native build or successful runtime;
qualify the exact source and artifacts on the target platform with isolated state.

Release packaging emits a JSON receipt with the complete archive SHA-256 and an
ordered parts list. Archives over 1.9 GB are streamed into numbered parts, keeping
each file below [GitHub's release asset limit](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases#storage-and-bandwidth-quotas).
Concatenate those parts in receipt order into the listed archive filename, verify
the complete SHA-256, then extract it. Smaller products retain one tar.gz file.
The receipt also records each part's size and SHA-256.

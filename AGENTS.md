# Reload Maker

Reload Maker is the standalone authoring application for physical weapon reloads that DIAMOND plays in game. Before modifying the application, read `skills/reload-maker-portable/SKILL.md` and the `build-diamond-reloads` skill, which holds the reload contract shared with DIAMOND. This file holds only Maker's own rules.

## Build and run

- Machine locations live in the git-ignored `CMakeUserPresets.json`: a `custom-fast` configure and build preset that inherits `fast` and sets `NIFSKOPE_SOURCE_DIR` (the nifskope-cli checkout), `ROCK_SOURCE_DIR` (the ROCK checkout whose collision and grip code Maker compiles), `QT_BIN` (the MSYS2 UCRT64 `bin` folder with Qt 6) and, for the Ammunition suite, `RELOADMAKER_GLOCK_PACKAGE`. The source-policy MCP suite reads DIAMOND's reference testbeds from the `DIAMOND_TESTBEDS_DIR` environment variable.
- `cmake --preset custom-fast`, then `cmake --build --preset custom-fast`, capped at two compiler processes. Launch with `ReloadMaker.cmd`.
- Settings stay under `local/settings/ReloadMakerV2/` and saved projects keep the `reload-maker-v2-*` kinds: these identifiers are saved data, not the product name.
- All settings, caches, temporary output, authoring libraries, screenshots and staged exports stay in the selected project workspace. Never reintroduce AppData or registry storage.
- Scratch scripts, probes and captures go in the git-excluded `scratchbook/`.

## Architecture

- `StudioWindow` owns the native viewport and model directly. Keep the NifSkope application window, entry point, generic CLI and unrelated editor tools out of the build. The native model, material, skinning, rendering and branch-transfer code is a read-only external dependency; patch only its isolated build staging directory. Preserve dependency licenses and provenance.
- Offset Maker, source mod packages, game Data, MO2 profiles, ROCK, PAPER, FRIK and framework checkouts are references or inputs. Do not modify or deploy them during ordinary Maker work.
- GUI and MCP share one controller. Keep independent physical hands, and keep source/native pose, finger preview, part-grip presentation, mechanism simulation and reload-driven grips distinct. Drafts survive workspace changes, but a saved draft is not automatically the active pose authority. Failed or cancelled source replacements keep completed authoring data.
- Hand-pose, part-grip, mechanism and reload-recipe JSON keep their domain contracts. Their saved `revision` fields are separate from MCP `documentRevision`. Validate imports, preserve original transform values and reject conflicting IDs.
- Archive export writes to the selected local output; Export to MO2 delivers complete source-plugin groups to the selected named mod folder without changing activation or profile settings.

## Authoring workflow

- Weapons are authored by a person by hand, by a person with an AI, or entirely by an AI through MCP; automatic detection never covers weapons well on its own. Both the manual UI and MCP must be good and intuitive for their user, and an AI should finish a weapon in a few minutes. MCP friction met while authoring (a missing command, an unclear refusal, a step needing a workaround) is a defect to record and fix.
- Every weapon supplied for authoring goes on the dev bench: copy its mod folder from MO2 (read only) into `local/devbench/<mod folder>` and save its project from that copy. Copies stay out of `workspace/`, which test workers mirror. Use captured testbed data only for the affected behavior; roster counts are not validation gates.

## Game files and MO2

- A development source (package or testbed mode) resolves masters beside its plugin, then in the game's Data folder, then in `workspace/dependencies`.
- MO2 load order and profile apply only when Maker runs inside MO2. A package is one plugin, mod folder or source pack and may sit in MO2's mods folder; package mode reads only those files, Data and `workspace/dependencies`, never MO2's other mods or profile. Never write to MO2 or the game's Data folder.
- Data holds the base game and DLCs. When a source needs another master, copy that master and its `<name> - *.ba2` archives from its MO2 mod into the git-ignored `workspace/dependencies`, which is mirrored to test workers. Ask before changing where Maker looks for game files or launching Maker through MO2.

## Library protection

- `workspace/library` holds the saved ESP projects, is git-ignored and is irreplaceable. Inspect any affected destructive file path and exercise changes on an isolated copy. Tests never delete or overwrite the author's saved library; real saves use the editor's Save. Keep this protection in `tools/editor_session.py` and the relevant `EditorHarnessRestore` cases.
- Capture workers operate in isolated workspaces through the same native controller; only the initiating editor commits its saved ESP project. A worker must not change the visible editor's selection, camera, hand or drafts. Complete paired assets before changing either assignment, and preserve cancellation cleanup.
- MK18 and MK18 Empire are not testbeds; their projects stay on disk and are re-added only on request.

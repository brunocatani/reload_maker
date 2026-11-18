---
name: reload-maker-portable
description: Author and inspect physical Fallout 4 reloads using the installed Reload Maker application's MCP tools, or build its finished magazine-object packs. Use for source selection, reload motion, hands, operating points, sound and collision authoring in Maker. Use the repository development workflow when changing native game plugins.
---

# Reload Maker

## Magazine release policy

For magazine release, manual extraction, grab accessibility or release-template selection, read the [owner-approved magazine release and contact-acquisition contract](build-diamond-reloads skill: references/magazine-release-policy.md). It defines all 75 weapon assignments and touch + fresh grab + deliberate pull, with no trigger or separate paddle step for manual removal. Acquisition uses magazine-body contact; the saved fit controls presentation after acquisition. Keep magazine release independent of bolt release. Body-click acquisition works in Operate. `grab_magazine` acquires a seated magazine; `move_magazine(distance=...)` drives withdrawal; `release_magazine` releases the hold. Button templates require `drop_magazine` to unlock first; manual templates do not.

Use the user's installed Maker and selected workspace. An external AI client drives the same controller as the manual UI. The application owns project state and validation; this skill does not supply another implementation of its file formats. Durable rules and technical references belong in the skill packages. Prior notes from the old Docs tree are historical input; do not recreate it. No hash-based input verification, invalidation or skip gates are permitted.

## Lean validation includes tests

The preset is the only required authoring. Apply the same anchor/fallback contract to UI, MCP, save, capture, export, tests and validation scripts. Correct or remove obsolete refusal assertions and their scaffolding; do not restore product restrictions to make old tests pass. Missing optional fingers, grabs, spring settings, fire clips or review stamps are not failures. No hash checks or full test runs. Use the smallest relevant behavior checks, protecting actual motion, source/ownership safety and saved work. Check fresh save results: a failed save may leave an older capture intact.

The maintained developer contract is in the `build-diamond-reloads` skill's `references/project-and-pack-workflow.md`. Convert owned private data before removing superseded readers/tests; this does not discard released provider contracts or justify guessed source-time conversions.

## Shared insertion assistance

All authored reloads use the [shared insertion-assist contract](build-diamond-reloads skill: references/insertion-assistance.md): wider finite-rail proximity plus loading-object contact, followed by a deliberate inward push. Maker previews surface proximity; DIAMOND queries the live weapon collision proxy using the solved held object. Existing packs receive the default without mass re-export.

## Connect and establish authority

Start the optional stdio adapter with `ReloadMakerMcp.cmd --workspace "<workspace>"`. The bundled runtime is sufficient. Native format adapters use the same launcher with `--adapter nif`, `--adapter archive` or `--adapter plugin` when that capability is required. Do not locate a developer's personal tools or install separate MCP servers.

Read `get_state(fields=...)` for the selected source and the inventory actually needed, plus `get_installation` when tools/source location matter. Use `get_status()` for the compact session, revision, busy flag, selected records and job ID; mutations can request `response="summary"` through the native client. `mcp/client.py::AuthoringSession` carries the revision without silently retrying edits. Obtain the current revision before a mutation. The endpoint belongs to one workspace and one process session; reconnecting MO2 changes the session. After a stale-session error, read state and re-evaluate the selection before editing. Never replay an old edit blindly.

MO2 metadata alone does not prove access to its effective files. `connect_profile` saves the open project and requests a verified MO2 launch. Wait for the replacement session and completed loading. Package mode and an attached MO2 library are different source contexts. Do not claim that an offline package contains every winning mod override.

## Author the source behavior

Inspect the selected weapon, attachment choices, graph actions and native scene before assigning roles. The weapon label is not evidence that its bolt, handle, barrel, sights or cartridges share an animation owner. Use the actual rendered part and its reference transform.

Keep three things distinct:

- Physical controller input: a finite straight pull/push, possibly angled in weapon space. A gentle curve is the normal maximum; more complex gestures need a specific interaction requirement and recorded justification.
- Visible mechanism: the full required source/authored motion, including handle folding, rotations, barrel tilt, firing pin, follower parts and feeding cartridges.
- Visible hand: keep the holding hand coherently attached/posed on the animated part. Its rendered motion can be curved while the physical controller moves straight; retain the existing magazine-fit and operating-finger assignment policies.

The restriction is on the controller's trajectory, not the visible hand or weapon animation. Controller displacement drives mechanism progress; it must not require tracing a sampled HKX hand/contact curve. Preserve the full visual movement at that progress. The G36 image is a rejected controller-input path, not permission to remove its folding/rotating animation. Ordinary pulls must not require sharp hooks, zigzags, reversals or exact source wrist rotations. A special gesture is an exception only when the interaction actually requires it, not merely because the source animation contains it. Check the editor's input guide and the game's actual mapping separately; neither a valid curve nor a successful preview proves comfortable VR control.

Use source animation for visual movement when it contains what is required. A static firing bolt or handle needs an explicit measured derivation or authored motion. Do not quietly reuse a generic travel distance or simplify the complete visible mechanism into one translated mesh.

### AR-15 charging handles: required M16-style adaptation

For every AR-15-type weapon being authored, provide an independently operable charging handle wherever the geometry permits it. Retain an already-correct independent handle. When movement is missing or the handle is grouped into the receiver/bolt, use the M16-style repair: inspect the real handle geometry, split/reparent existing separable components without changing their detail/materials, and drive the independent part from that weapon's measured mechanical stroke. Put required model changes in a local override and keep source mods unchanged. Empty helper bones, a shared NIF and missing animation do not exempt the weapon. Genuine exceptions are absent handle geometry or geometry that cannot be separated into a usable part without destroying it or inventing a replacement; record the inspected limitation. An unavailable authoring/export operation is a tool deficiency to resolve/report, not physical impossibility.

Keep AR nonreciprocating handle motion independent of firing-bolt cycling and empty catch/release. Pick the actual handle surface, give controller input a straight or gently curved guide, and retain full visual motion and contact for the part and visible hand. Carry the source binding, model override, movement and grab points together when publishing. Reuse the M16 repair method, never its particular node names, mesh-component indices or travel distance. This rule applies to existing AR configurations when revisited as well as new ones; recording it does not mean their packs have already been repaired.

For magazines, select the actual variant and physical hand, then use `configure_magazine` and a recognizable library pose. Move/rotate the hand around the authoritative magazine. Save the selected magazine's fit by replacement; use the weapon-default scope only when requested. Confirm all fifteen joints and the actual wrist-to-carrier placement. Do not confuse an attachment root with the animated magazine anchor.

For operating parts, authored grabs control fingers only on either permitted hand. Preserve the live wrist position and approach angle. Grab locations are picked surface points in the moving part's local frame; they follow the part at closed, intermediate and open positions. A saved wrist or mesh center never replaces a picked point.

For controller-input inspection, read `get_simulation_state().controllerInput.strokeInWeapon` and use `simulate_weapon(operation="controller_pull", position=[x,y,z])` with weapon-local displacement since acquisition. Release before starting a new acquisition. The old `pull(progress=...)` directly sets normalized visual progress and does not verify controller usability. Compare real rendered part motion and full endpoints separately.

Check full, empty and pre-feed magazine presentations independently. Exclude animation-only spare copies. Validate the magazine both in free carry and along the insertion rail, from multiple camera angles. Fingers should contact the surface; a small intersection is preferable to visible hovering.

Sound cues belong to physical events and directional crossings, not a continuously playing reload clip. Exercise holds, reversals, catch/release, dry fire and cancellation so a cue does not repeat every frame.

## Build and inspect

Save projects and authored records before switching sources. Use the tools' actual returned IDs, paths and revisions. Poll long operations only until they complete or fail, and preserve the returned job identity when checking a pack build.

The collision workshop's convex hulls are editable preparation data. A distributable world NIF needs validated Fallout 4 `bhkNPCollisionObject` / `bhkPhysicsSystem` output from the configured Creation Kit cooker. Missing Elric does not block pose work or building source-plugin ESLs from already cooked assets.

Complete export creates one ESL-flagged ESP per source plugin, with matching magazine-mesh folders. The two manual actions are **Export to MO2** and **Export ZIP / 7z**. Keep the creator allocation ledger so each group's compact IDs remain stable. Updating one source group retains its previously published sibling weapons; another source group's files and MO2 activation remain unchanged.

`pack_job(operation="export", selections=[{"plugin": ..., "save": ...}], mo2=..., expected_revision=...)` exports committed versions. Omit `save` for latest and selections for the open ESP; `scope="everything"` selects saved weapons present in the loaded source. `archive` chooses ZIP / 7z instead of `mo2`. `open`/`close` control the export window. Follow the returned ID with `wait_job(job_id, timeout_seconds=30)` or `pack_job(operation="state", job_id=...)`; timeout leaves it running. Explicit `cancel` names the same ID. Do not restart it after a transport timeout. Use the current CLI `--help` for low-level pack import/build operations.

Keep ESL-flagged ESPs, their per-group object bindings, behavior and required models together. Magazine models are under `Meshes/<ESL basename>/`. Source weapon overrides retain required native paths and are declared explicitly in pack-project `modelOverrides`. Do not copy original source mods or change MO2 activation/load order. VR needs an ESL loader such as Daytripper.

## Current compatibility boundary

Complete exports use the DIAMOND runtime and bundled validator supporting source-plugin light ESPs (`sourcePluginLightESP` capability). Groups activate from the game's loaded-plugin list at GameDataReady. An enabled source ESL supersedes legacy monolithic content for the same weapon. Legacy spawned magazine references are not remapped into new ESL identities. Keep the creator's saved export identity workspace for subsequent updates.

Report separately what was source-derived, mathematically checked, visually inspected, built, and actually tested in the headset. An offline preview or successful plugin readback does not establish in-game acceptance.

## Complete authored-pack publication

Save the ESP project, then choose Export ESP reloads or Export everything. Only committed versions are exported; unsaved changes are reported. Complete publication freezes behavior, hands, operating points, sounds and cooked objects and uses DIAMOND's native catalog parser. One source-plugin group preserves its previously published siblings and permanent object IDs. Current library coverage is not game acceptance.

Runtime projection shares exact immutable numeric curves and finger poses while keeping selector, binding and behavior identity. Authoring/history remain lossless. Behavior-only edits reuse cooked assets and generated ESPs; a missing installed file is restored. No hash, review certificate or full-roster test is a prerequisite.

## Source review and collision cooking

Use `checklist_state` and the loaded source identities to inspect the current configuration. The earlier twelve-step authoring_roadmap/fingerprint workflow is retired. Inspect actual missing parts, changed behavior and source availability; never invalidate saved work or demand reconfirmation because a hash differs.

The collision workshop exports enabled hulls in the selected magazine root with `configure_collisions(changes={"operation":"export-hulls", "path":...}, expected_revision=...)`. The profile must have a current source root. Existing saved source-local hulls are not final Havok data. `cook_world_object(job, output, expected_revision)` and `ReloadMakerTools.cmd --workspace <workspace> cook-object --job <job.json> --output <new.nif>` run the same native cooker. The frozen job contains `geometry` and `collision` path objects and format `reload-maker-world-object-job`, version 1. Geometry must already be a prepared rigid full/empty magazine in the same root frame, without existing collision; do not select a whole receiver or source animated spare as a shortcut. The cooker preserves source inputs, validates finished physics and compares render geometry/transforms/material bindings. Inspect its receipt and test actual physics in game. Whole live-model extraction into that prepared input remains a distinct authoring step.

## Prepare geometry inside Maker

Reload builder > Magazines (workspace 7) lists only the captured loose magazine world object, the only capture a pack accepts; the whole weapon and installed parts are in the separate Weapon collision tool (MENU, workspace 9), which nothing consumes yet. In the collision workshop select the actual magazine/part reference and the intended paused source frame. `capture_prepared_part(output, expected_revision)` freezes the native viewport's rigid surface attributes, material paths and part-local transforms, then creates a prepared NIF through the bundled native importer. The visible source must have complete UVs/normals/tangents, supported vertex counts and full detail. Unsupported deforming/incomplete geometry is rejected instead of flattened silently. Native readback checks counts, frame matrices, bounds and surface attributes. Continue with the same root-space hulls and the cooker.

Use the WORLD MODEL PIECES list, or `configure_collisions(changes={"operation":"piece", "index":..., "included":false}, expected_revision=...)`, to select the intended body and payload. Re-generate hulls after re-including pieces. Selections persist in collision profiles and apply to geometry capture and collision generation/export. Inspect names, source paths and material names from get_collision_state.sources. Timberwolf/L118 source containers include alternative projectiles and spare magazines: capturing every source piece is not proof of the correct full/empty presentation. Do not replace the existing accepted runtime meshes until the selected pieces and root frame are verified for that magazine. No automatic weapon-specific name filter is implied by the capture tool.

## Capture and save a live configured reload

Use `checklist(operation="preset", id=...)` with an ID from `checklist_state.presets`. The bundled presets may share a mechanism; retain the exact selected ID. Prepare and reset Operate. `export_authored_record(kind="runtime_configuration", ...)` captures the selected receiver/magazine/operating source, complete curves and the resolved magazine fits, optional finger-only operating poses, sounds and grab points. Missing hand refinements or grab points use the anchor, existing ROCK pose and whole-part acquisition; they do not reject capture.

The manual FULL WORLD MODEL and EMPTY WORLD MODEL buttons call `capture_magazine_world(empty=..., expected_revision=...)`. This freezes the same pre-feed/empty state shown by Operate in the actual magazine carrier frame, excluding hands, receiver and parked spares. The collision workshop remains on that presentation. Review WORLD MODEL PIECES, especially overlapping projectile alternatives; select the intended materials before preparing and cooking. For the current Timberwolf/L118 FMJ source, the alternative 338copperprojectileinc.bgsm, 338brassprojectile.bgsm and 338armorprojectile.bgsm pieces are excluded. Do not generalize this into a filter for unrelated weapons or ammunition.

Then use `capture_prepared_part`, export enabled hulls, and `cook_world_object` as above. Keep each finished NIF beside its `.receipt.json`. Carrier-space cooking requires a matching preparation receipt; geometry and collision must share the exact weapon, magazine state, reference frame and selected piece list. Reject a concrete geometry/reference mismatch; provenance fields are not admission gates.

`save_project(expected_revision, note="", wait_seconds=0, refresh_current=False)` is the editor's deliberate ESP Save. `get_save_plan()` explains what needs capture. Normal Save refreshes changed parts and failed selectors; `refresh_current=True` explicitly refreshes the selected weapon. The parent freezes authoring, a separate native worker evaluates configurations, and the parent commits one dated version. The visible weapon, hand and camera stay in place. Failed captures identify retained previous data; an old export is not proof of fresh capture success.

`prepare_magazine_pair(expected_revision, output="")` runs full/empty capture, native preparation, hull export and Elric cooking, then assigns both only after success and only to the initiating authoring. Cancellation or failure keeps previous assignments. The lower-level operations remain available for manual piece/collision correction. `review_magazine_hands(expected_revision, output="")` renders both physical hands from side/top/iso and opens a nonmodal review; inspect contact and the returned fit/mode metadata. Existing independent fits win; this review never authors a guessed pose. To harvest a fit, select the verified source clip/seconds with `control_playback`, choose its actual hand/anchor with `save_hand_pose`, then use `start_part_grip` and the placement tools.

## Verify pack inputs before publishing

Inspect-project reports the prepared weapon/configuration coverage. Validate actual source identities, complete curves, hands, point sets, native profiles and full/empty model assignments. Update a changed configuration in its existing pack project; do not rebuild unrelated configurations or assets. Models/poses for different source editions remain separate. New logical world-object identities distinguish different weapon/fit owners, even when they reuse an OMOD. Keep the installation ledger for stable IDs. Source hashes or recipe IDs are not validity gates.

## Operating roles and source movements

Use get_operating_setup before configure_operating_setup. Settings belong to the operating assembly; recipeId is provenance, not a validity gate. Source anchors rebind the saved setup onto another reload. Select the actual operating, bolt and magazine carrier tracks and complete follower list. Source modes are preset (verified current behavior), reload (current capture), clip (explicit native animation and baseline), and manual (existing named part-mechanism records). Native version-2 windows use openSeconds/closedSeconds in the selected source clip, independent of magazine frame counts; -1 for both selects automatic detection. Endpoints may run in either source direction. Manual Move/Rotate keeps its own existing movement records. Never guess a legacy frame window's source grid; preserve readable old records until a conversion is grounded in their actual source. Manual spring movement is a closed-to-open path; a manual bolt needs the complete opening-and-closing sequence. Linked rigid followers and independent movements must not control the same part. For a manual bolt, ejectSeconds=-1 selects full opening. Do not create fake source annotations.

Save and preview, then test the stroke, release/catch, firing and ejection. Check source evidence, save/reopen, and export runtime_configuration. Exports preserve native hierarchy separately from model-relative source paths. Rebind actual operating/bolt anchors, skip absent optional followers and use the detected magazine when needed; preserve all applicable present follower motion. A changed recipe ID does not require reconfirmation. Restore source defaults removes only that assembly's operating override.

## Magazine input direction

Use get_magazine_guide and configure_magazine_guide to inspect/save/reset the selected assembly's straight input path. Start from returned settings, retaining recipeId. outwardInConnection is a unit vector pointing away from the seated magazine in its connection-local axes. travel, captureRadius and releaseMargin use game units. A zero direction is invalid; recipe changes alone do not invalidate the guide. The manual equivalent is Operate > Configure insertion guide.

Changing the guide retains the whole visible magazine movement, including an original straight path. It does not rotate or translate the authored insertion animation. Test entry at intermediate positions, pull-out and complete seating. A guide edit must not change either magazine hand fit or the weapon's independent operating curves. Restore source guide removes only this assembly's override.

## Magazine body and payload authoring

Use get_magazine_presentation and configure_magazine_presentation from the loaded Operate configuration. The full carrier is selected in Operating parts; this panel chooses the empty native body (or the same carrier for a shared body), source movement mode and payload samples. Native insertion keeps rotation from entrySeconds through the seated frame. Sampled payload uses explicit pre-feed full and empty follower frames. This is separate from editing the magazine hand and from configuring the straight input guide.

Review every returned native piece path. Roles are body, cartridge (hide when empty), and spare (exclude from physical presentation). Native paths must remain unique; parts outside the full/empty carriers must remain spares. Separate carriers cannot be parent/child branches. Save rolls back if it leaves no body or an invalid source presentation. Preview both full and empty states, recapture and cook both world NIFs, then save the configured reload. Cook receipts describe the physical presentation. Recook when actual geometry/payload/reference changes need new assets, not because recipe IDs or track lists differ. Records using the same model may share cooked assets. Inside-carrier exclusions are also exported to DIAMOND's native extraction preview, with source visibility restored when it releases control.

## Authored workspace coverage

`get_pack_coverage(refresh=True)` reads current ESP library coverage and the loaded source's available weapons. `get_esp_project()` shows saved versions, unsaved weapons and publication history. Counts distinguish saved/exported work; they are not compatibility or game certificates. For a point/fit/guide change, edit the owned part, Save the ESP and export that source group; unchanged world models need no recook.

`checklist_state` derives progress from authoring and fallback resolution. `checklist(operation="preset", id=..., scope="weapon"|"assembly"|"feed")` sets a weapon default or an override for the selected moving assembly, optionally its selected feed. Empty ID clears a scoped override. Resolution is feed, assembly, weapon default. Do not choose a wrong mechanism to bypass missing behavior. A template change recaptures its actual dependents; it does not relabel unrelated saved assemblies.

`inspect_reload()` returns source provenance/inheritance, controller guide, current part writer and runtime paths, and invokes the actual DIAMOND source binder against the editor scene. The UI offers Inspect reload sources and movement in Operating parts. Its result is static editor evidence, not in-game verification. Use `inspect_pose` for transforms and existing connection markers for frame placement.

## Explicit magazine references and authored movement

`get_magazine_reference` lists owner-qualified eligible reference frames. Save through `configure_magazine_reference` with the returned settings and anchorKey. The selected frame is magazine authority for wrist/finger fits; the native mount, full carrier pivot, or exact custom carrier-local connection determines the guide origin/orientation. Never select a wrist as the grab/connection authority. Changed references preserve saved fits and use the resolved frame/anchor fallback. Refuse only a concrete unsafe or incompatible physical binding, not a missing review stamp. Check both physical hands and the detached carrier in native renders.

`get_magazine_presentation` lists saved movements authored for the exact full carrier. Set presentation motion to `manual` and movementId to a saved Move/Rotate definition. Its staged extraction is the visible path; insertion traverses it in reverse. The user's straight/angled guide remains independent. The complete body must clear the weapon at the endpoint; do not bypass the clearance error by removing geometry. Runtime capture freezes the sampled path and original movement definition, so DIAMOND does not need the creator's library to play it. When a source has no usable extraction, get_magazine_guide exposes an explicit manual draft after the operating roles are resolved. Choose and save its outward direction before configuring the visible magazine movement; geometry still determines complete clearance. For a pistol whose slide and magazine share one source model, select their independent native branches explicitly in Operating parts. The magazine subtree then owns visibility and its carrier provides the starting connection; do not select a receiver/operating ancestor as a magazine. Configure payload and guide separately, and verify detached geometry before authoring the hands.

Finished-pack builds run the complete DIAMOND runtime catalog parser over an isolated Data view before selecting the output generation. Preserve real runtime record/API capacities without silent truncation; arbitrary file, pack and aggregate byte ceilings are retired. An object-only manifest is incomplete. `packs validate` checks frozen inputs; full catalog/ESL/bindings acceptance belongs to `packs build`. An unchanged or refreshed hash never substitutes for valid behavior, readable source identities and matching full/empty object assignments.

Normal saves belong to the originating ESP library. Per-weapon project JSON is explicit import/export interchange, useful for preserving a draft; do not invent a Save As target. Source context remains part of the saved source descriptor. Switching weapons keeps independent drafts. Cosmetic source-byte changes do not invalidate authoring or demand review.

## Source compatibility and separate editions

Use get_source_compatibility/configure_source_compatibility for the current weapon/source-context key. The rules cover an add-on marker that must be present or absent, blocked conversions and nonphysical magazine collection records. Bundled defaults distinguish MK18 base/Empire and nonphysical magazine collections. Auxiliary SREP M203/M26 selectors do not block the host reload; regular reload authoring ignores their secondary ammunition and animation overrides. Custom edits are explicit and apply to every configuration of this weapon when saved into its pack; they do not change curves or hands. Reset restores the current source defaults.

Saved ESP versions retain their source and explicit compatibility policy. Legacy per-weapon/source-context projects can be imported as drafts. A default compatibility policy must match an existing pack: do not silently mix base and overhaul editions. A deliberate change requires reviewing and saving the compatibility rules explicitly. Apply policy changes to the affected source selection without invalidating unrelated hand/collision work or demanding a publication-review stamp.

## Output and activation

Finished-pack builds return the generated output folder and native catalog counts. Keep one allocation ledger per installation so IDs remain stable; failed or cancelled builds do not replace the last successful output. Authored packs and the source ESL-flagged ESPs and their bindings are ordinary mod files. The user controls MO2 activation and load order. Do not add cutover/rollback plans or edit profiles as part of building or deploying folders.

For repository integration, the build-diamond-reloads skill holds the shared Maker/DIAMOND reload contract: fallbacks instead of gates, template and variation authority, testbed completion, per-weapon review, default magazine insertion and per-source ESL delivery. It also maintains the roster, observed future-family evidence, the M16 geometry-adaptation method, the project/pack workflow and runtime ownership. Keep detailed implementation conclusions there and portable authoring commands here. Do not use archived older notes as a second active instruction set.

## Optional authoring, anchors and allowed gates
The behaviour preset is the only required authoring choice. Preserve an existing assigned preset; in an authorized authoring task select the correct supported behavior from actual source evidence without adding a separate confirmation gate. Every other setting is an optional refinement with a fallback, so do not stop a weapon because a refinement is missing.

- Author parts, not combinations. Magazine settings (fit, guide, reference, presentation, models, collision) belong to the magazine; operating setup, grab points and fingers belong to the receiver/operating assembly. A magazine works with every receiver.
- The first authored example of each kind is the anchor: the first magazine fit for a hand serves every magazine (the other hand receives its mirror), and the first configured receiver/operating part serves same-model siblings. `checklist_state.planSources`, `operating_setup_state.source` and `grab_locations_state.own` identify the part's own value, anchor or sibling fallback. Resolution is own part, weapon anchor, another authored part of that kind, then same-ESP sibling. Author a part individually only when its reused anchor looks wrong.
- Fastest path for a new weapon: assign the preset, author one magazine fit, configure one receiver/operating part, save. Refine other parts only where the result is wrong.
- Allowed refusals are only: no magazine, a genuinely different magazine or moving-part model, and memory/node/file safety. Ammunition or fire-mode uncertainty, missing fire clips, changed magazine pieces, missing fingers or grab points, and one weapon's export problem are warnings with fallbacks. Never add a blocked-mod policy for joke or comical parts; they use the anchors too.

## Available: deploy folding handles on grab

Use the accepted G36 as the reference when a folding/stowed tab must come out before it can follow a manual pull. Maker and DIAMOND support **Deploy on grab: source seconds** in the reload-source Operating setup (`grabDeploySeconds`, -1 disabled). Acquisition deploys the handle at the current bolt position; held motion follows bolt distance immediately instead of scrubbing through the clip's pauses. Save/export emits `grabPoses`; the runtime DLL and newly exported data are both needed. This is reusable behavior, not a G36 allowlist. Inspect each weapon's own node ownership and deployment pose; never copy G36's 2.7-second sample or travel blindly. Read the maintained [folding-handle reference](build-diamond-reloads skill: references/folding-operating-handles.md) before applying it to a future similar request.

## Window placement

Launch and reopen with the application's saved monitor preference, maximized. The current user preference is monitor 2; do not silently restore the older primary-monitor rule. The application owns placement through `StudioWindow::showOnPreferredScreen`. Preserve pending authoring before a restart. Never apply `--unattended` to the user's interactive workspace; isolated workers own their own input-free window and workspace.

## Explicit magazine rail length

An authored magazine rail length limits both controller travel and the visible guided insertion. Shortening it clips the source approach at the new entrance, retaining the near-seat rotations and fixed seated pose; it must not compress the entire old approach into shorter controller travel. This is specific to magazine insertion, not permission to truncate operating handle/bolt motion. Guide editing retains the original source curve in memory so shortening and lengthening are reversible. Export the clipped curve for DIAMOND; unchanged older exported curves do not acquire the new behavior until saved and exported.

## MO2-launched development sources

Preserve the user’s launch context when testing replacers. A development source opened through MO2 still sees MO2’s winning NIFs and textures; restarting Maker outside MO2 is not equivalent. Confirm the running process has usvfs when relevant, keep the active profile, and use the actual executable path with MO2 run when restarting. Cosmetic root names alone must not invalidate compatible moving parts or the selected magazine’s saved fit. See build-diamond-reloads references/frames-hands-payload.md for the root-relative binding and carrier-ancestry behavior.

Ask the user before opening Reload Maker through Mod Organizer; the established CLI route is not permission to launch it autonomously. Preserve MO2 VFS context once the user opens it. Authoring a supplied testbed does not need MO2: copy it into Maker's `local/devbench` development library and use standalone Maker (closing, force-closing and reopening Maker is allowed after saving pending authoring).

## Bottom-loaded tube shotguns

The implemented self-loading tube reference is [Benelli M2](build-diamond-reloads skill: references/benelli-tube-feed.md). Use `shotgun.self-loading-tube.v1`: every shell enters the bottom, a manual bolt pull ejects and latches open, and another pull/release closes and chambers one shell. Never use the native first-shell ejection-port insertion. `magazine_setup` accepts the bottom-loading `entrySeconds`, `seatSeconds` and `loadingFollowers`; physical supply is one counted shell. Keep both hand fits, fixed tube capacity and separate chamber state. Pump weapons need their own pump action; this template does not implement one.

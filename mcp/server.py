"""Local MCP adapter for Reload Maker. The interactive application owns state."""
import argparse
import json
import os
import sys
from pathlib import Path
from mcp.server.fastmcp import FastMCP, Image

# The bundled runtime starts isolated, so the adapter's own modules are named here.
sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(os.environ.get('RELOAD_MAKER_WORKSPACE', Path(__file__).resolve().parents[1])).resolve()
os.environ['TEMP'] = os.environ['TMP'] = str(ROOT/'local/tmp')
mcp = FastMCP('Reload Maker', instructions=(
    'The manually launched native editor must be running. Read get_state before editing and use '
    'its revision. Source import and reload capture are asynchronous: inspect state and continue '
    'when busy is false. Use the configured action list, native meshes, independent physical hands '
    'and actual multi-view renders. Author functional, immersive contacts; shallow clipping is '
    'preferable to hovering. Preserve source packages and keep exports local. Native and '
    'weapon-fixed motion are distinct. Offline preview is not full Havok behavior emulation '
    'or proof of game integration. Authoring belongs to the originating ESP: save_project commits '
    'every weapon of that ESP as a new dated version; nothing else saves.'
))

_session: str | None = None

def command(method: str, params: dict | None = None, revision: int | None = None, *, response: str = 'full', expected_session: str | None = None) -> dict:
    global _session
    endpoint_path=ROOT/'local/settings/endpoint.json'
    try:
        endpoint=json.loads(endpoint_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f'Open Reload Maker with workspace {ROOT} before connecting') from exc
    if endpoint.get('version')!=1 or not str(endpoint.get('pipe','')).startswith('ReloadMaker.') or not endpoint.get('session'):
        raise RuntimeError('Invalid editor endpoint; reopen this workspace')
    if Path(endpoint.get('workspace','')).resolve()!=ROOT:
        raise RuntimeError('The endpoint belongs to another workspace')
    if expected_session is not None and expected_session!=endpoint['session']:
        raise RuntimeError('Editor restarted; establish a new authoring session')
    if method in ('state','revision'): _session=endpoint['session']
    elif _session is not None and _session!=endpoint['session']:
        raise RuntimeError('Editor session changed; read state before editing')
    request = {'method':method, 'params':params or {}, 'response':response}
    request['expectedSession']=endpoint['session']
    if revision is not None: request['expectedRevision']=revision
    with open('\\\\.\\pipe\\'+endpoint['pipe'],'r+b',buffering=0) as pipe:
        pipe.write(json.dumps(request,allow_nan=False).encode()+b'\n')
        data=bytearray()
        while not data.endswith(b'\n'):
            chunk=pipe.read(65536)
            if not chunk: raise RuntimeError('Editor disconnected before completing the request')
            data.extend(chunk)
            if len(data)>64*1024*1024: raise RuntimeError('Editor response exceeds the supported size')
    response=json.loads(data)
    if not response.get('ok'): raise RuntimeError(response.get('error','Editor rejected the request'))
    return response

@mcp.tool()
def get_state(fields: list[str] | None = None) -> dict:
    """Read selected state fields, or the whole inventory when fields are omitted.
    Use get_status for waiting; inventory is unnecessary between ordinary edits."""
    return command('state', {'fields': fields or []})

@mcp.tool()
def get_status() -> dict:
    """Cheap current selection, physical hand, busy/job and document revision."""
    return command('revision')

@mcp.tool()
def wait_job(job_id: str, timeout_seconds: float = 30) -> dict:
    """Wait at most 60 seconds for this exact job; a timeout leaves it running.
    A completed earlier job can still be retrieved after another job starts."""
    import time
    end = time.monotonic() + min(60, max(0, timeout_seconds))
    while True:
        result = command('pack_job', {'operation':'state', 'id':job_id})
        if result.get('phase') in ('complete','failed','cancelled') or time.monotonic() >= end:
            return result
        time.sleep(.2)

@mcp.tool()
def get_scope_state() -> dict:
    """Read installed optic targets, rigid shape names and current authored lens profile."""
    return command('scope_state')

@mcp.tool()
def configure_scope(operation: str, expected_revision: int, values: dict | None = None) -> dict:
    """Scope operations: open, select(index), pick(x,y viewport pixels), edit(profile), save, import(path), remove, export. Exports remain local."""
    return command('scope_profile', dict(values or {}, operation=operation), expected_revision)

@mcp.tool()
def get_collision_state() -> dict:
    """Read target indices, generated hulls, warnings and asynchronous generation status."""
    return command('collision_state')

@mcp.tool()
def configure_collisions(changes: dict, expected_revision: int) -> dict:
    """Workspace 7 (Reload builder > Magazines: the captured loose magazine world object) or 9 (Weapon collision: the whole weapon and its installed parts): select target index; defaults/generate (gapGame, supportErrorGame, targetPoints, algorithm rock|concavity, concavityGame); worker-test regenerates as one job per source on a worker thread polled each simulated frame (framePeriodMs, supersedeAfterMs) and reports workerTest; state quality reports build time, surface missed and hull bulge per source; open saved profile; hull index/enabled; piece index/included controls prepared-world geometry and associated collision; view overlay/compare/isolated; save replaces this weapon/loadout/target profile; export to a local path; export-hulls converts enabled points into the selected magazine root for cooking; cancel worker. Saved profiles use source-local game units, not cooked Havok shapes."""
    return command('collisions', changes, expected_revision)

@mcp.tool()
def get_diagnostics() -> dict:
    """Read current source/pose diagnostics without changing the project."""
    return command('diagnostics')

@mcp.tool()
def inspect_reload() -> dict:
    """Inspect the selected source, inherited authoring, controller guide and
    visual tracks. Replay source paths through DIAMOND's actual binder.
    This checks the editor scene, not live game collision or provider leases."""
    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
    from product_backend import Backends
    value=command('inspect_reload')
    scene=value.pop('bindingScene')
    value['staticBinding']=Backends(Path(value['application']),ROOT).source_bindings(scene)
    value['staticBinding']['frameConvention']=scene['frameConvention']
    value['staticBinding']['nameTranslations']=[{k:row[k] for k in ('key','editorName','name')}
        for row in scene['sources'] if row['editorName']!=row['name']]
    return value

@mcp.tool()
def describe_weapon(hands: bool = True) -> dict:
    """Measure the open weapon configuration instead of looking at it: start here.
    One reply holds what an image would have to tell you. reload.movingTracks:
    every part that moves in the reload, how far, along which weapon-local
    direction, how much it turns and when. meshes: each drawn shape with its
    size, centre and the tracks it rides (worked out from how its vertices
    move), so you know which shape is the handle, the bolt or the magazine.
    roles: the operating, bolt and magazine tracks with the suggested open and
    closed seconds. hands: per physical hand, when its palm is at the magazine
    or operating part in the native clip, and tightestWrap.seconds, the moment
    to harvest a magazine hold with control_playback and save_hand_pose.
    grabPoints.candidates: exposed surface points of the operating part, ready
    for configure_grab_location(operation='add', values={'pointInSource': ...}).
    Also the preset proposal, checklist gaps, clips with events, magazine rail
    and slots. Scrubs the native clip and restores the view; leaves the editor
    in Operate. hands=False skips the hand timeline."""
    import perception
    return perception.describe(command, hands)

@mcp.tool()
def measure_hand_contact(hand: str = '') -> dict:
    """Numbers in place of a review render: how a physical hand sits on the
    magazine it holds. hand='left' or 'right' takes the seated magazine with
    that hand in Operate, pulls it clear, measures and lets go: use this to
    check a saved fit. With no hand, the hand already shown holding the magazine,
    or the fit being placed in configure_magazine, is measured; a hand holding
    nothing is refused. Per finger and palm: the gap between hand surface and
    magazine surface in game units (negative is inside), with touching, hovering
    and buried lists and a verdict. An accepted hand-authored fit reads all five
    fingers touching with at most about 0.5 units of sinking; a fit that sinks
    0.7 or more needs its own correction."""
    import perception
    return perception.measure_contact(command, hand)

@mcp.tool()
def author_magazine_fit(hand: str = '', seconds: float | None = None) -> dict:
    """Give the selected magazine its hand fit in one call, without a viewport.
    Finds the physical hand that carries the magazine in the weapon's own reload
    and the moment its fingers are closed on it (or uses the hand and seconds you
    give), saves that exact native pose as this magazine's fit, mirrors it to the
    other hand, then holds the magazine with each hand in Operate and returns the
    measured contact of both. Select the magazine with configure_weapon first.
    Nothing is written to the library until save_project."""
    import perception
    return perception.author_magazine_fit(command, hand, seconds)

@mcp.tool()
def build_weapon_atlas(output: str, plugin: str = '', limit: int = 10) -> dict:
    """Describe every weapon of one plugin of the open source into a folder, a
    few per call (each takes several seconds): <output>/<plugin>/<form id>.json
    per weapon and index.json with one row each (preset, reload clip, operating
    part travel, magazine timing). plugin defaults to the open weapon's. Call
    again until remaining is empty. Weapons that share a reload clip share
    behavior, so the index shows which ones one authoring pattern covers.
    Changes the selected weapon; refuses while there are unsaved edits."""
    import perception
    return perception.atlas(command, output, plugin, limit)

@mcp.tool()
def assign_model_override(model: str, path: str, expected_revision: int, operation: str = 'set') -> dict:
    """Set or clear a weapon's replacement NIF at its game-relative Meshes path. Previews through the native source overlay; Save copies it into the ESP project and later exports include it. Source files and MO2 activation are unchanged. For clear, path may be empty."""
    return command('model_override',dict(operation=operation,model=model,path=path),expected_revision)

@mcp.tool()
def inspect_pose() -> dict:
    """Read current local/world bone frames and owner-qualified rendered part frames, including active hand and mechanism composition."""
    return command('pose')

@mcp.tool()
def open_source(path: str, game_data: str, expected_revision: int) -> dict:
    """Open a read-only mod folder or ZIP; state reports asynchronous loading. The open weapon's unsaved edits are discarded; save_project first."""
    return command('open_source',{'path':path,'gameData':game_data},expected_revision)

@mcp.tool()
def open_installed_library(instance: str, expected_revision: int) -> dict:
    """Read the configured MO2 library; requires the editor to be launched in its virtual context."""
    return command('open_library',{'instance':instance},expected_revision)

@mcp.tool()
def select_weapon(index: int, expected_revision: int) -> dict:
    """Choose a weapon index from the current state and assemble its configuration. Leaving the open weapon discards its unsaved edits; save_project first."""
    return command('select_weapon',{'index':index},expected_revision)

@mcp.tool()
def select_testbed(name: str, expected_revision: int) -> dict:
    """Switch to a saved testbed, discarding the outgoing project's unsaved edits (save_project first): the corpus (glock/ru556/m1a/timberwolf/l118a2/thanatos/srep-*) or a dev bench weapon (source_browser developmentSources with bench=true). State becomes busy while the source loads."""
    return command('testbed',{'name':name},expected_revision)

@mcp.tool()
def configure_weapon(changes: dict, expected_revision: int) -> dict:
    """Change body male/female, or slot and mod decimal-string IDs. Animation sets always resolve from equipped weapon/OMOD keywords. reloadNoExtender plays the animation set's optional NoExtender reload when it has one (QuickHands sets ship it for the unextended magazine). Set it separately from slots. Configuration changes rebuild the native interaction source."""
    return command('configure',changes,expected_revision)

@mcp.tool()
def annotations() -> dict:
    """Pins: notes pointed at parts (open or resolved), with part, anchor shape, author,
    whether the part is installed in the current configuration (placed), its viewport
    position (screen, logical pixels) and its current point in weapon space (position)."""
    return command('annotations')

@mcp.tool()
def annotation(operation: str, expected_revision: int, id: str = '', text: str = '',
               x: float | None = None, y: float | None = None, reference: str = '') -> dict:
    """Pin operations: add(x, y viewport pixels on a part, text), edit(id, text),
    resolve(id, reference: what resolved it, for example the property you changed),
    reopen(id), remove(id). Pins you add are recorded as written by the AI."""
    params={'operation':operation,'id':id,'text':text,'reference':reference}
    if x is not None: params['x']=x
    if y is not None: params['y']=y
    return command('annotation',params,expected_revision)

@mcp.tool()
def part_at(x: float, y: float) -> dict:
    """Which installed part a click at viewport pixel (x, y) would select: slot key, slot label and part name, or slot null. Also returns the viewport width and height in the same logical pixels."""
    return command('part_at',{'x':x,'y':y})

@mcp.tool()
def focus_slot(slot: str, expected_revision: int) -> dict:
    """Focus a loadout slot by its decimal-string key from get_state, as clicking its part on the model does. The state reports it as focusedSlot."""
    return command('focus_slot',{'slot':slot},expected_revision)

@mcp.tool()
def select_action(index: int, expected_revision: int) -> dict:
    """Select a configured animation action from get_state."""
    return command('action',{'index':index},expected_revision)

@mcp.tool()
def control_playback(changes: dict, expected_revision: int) -> dict:
    """Set seconds, playing, loop or mode (0 weapon-fixed, 1 full native)."""
    return command('playback',changes,expected_revision)

@mcp.tool()
def set_workspace(index: int, expected_revision: int, hands_page: int | None = None) -> dict:
    """Choose 2 hand editor (hands_page 0 Poses library, 1 Part grips, 2 Mechanisms, 3 Grab points), 4 Reload builder > Reload, 5 Operate, 6 Sounds, 7 Reload builder > Magazines, 8 Scopes or 9 Weapon collision."""
    args={'index':index}
    if hands_page is not None: args['handsPage']=hands_page
    return command('workspace',args,expected_revision)

@mcp.tool()
def render_view(view: str = 'current', fit: bool = False, zoom: float | None = None, preserve_camera: bool = False) -> Image:
    """Return a fresh native viewport image: current/front/back/side/top/iso. Fit always references the assembled weapon. preserve_camera restores the user's camera after taking the requested inspection view."""
    args={'view':view,'fit':fit,'preserveCamera':preserve_camera}
    if zoom is not None: args['zoom']=zoom
    result=command('render',args)
    return Image(path=result['path'])

@mcp.tool()
def capture_window() -> Image:
    """Return an image of the whole editor window as the user sees it: panels, controls and the native viewport."""
    return Image(path=command('window_capture')['path'])

@mcp.tool()
def edit_finger_joints(hand: str, joints: list[dict], expected_revision: int) -> dict:
    """Edit native parent-local joints: index 0-14, rotation [x,y,z,w] unit quaternion."""
    return command('edit_fingers',{'hand':hand,'joints':joints},expected_revision)

@mcp.tool()
def restore_hand_animation(hand: str, expected_revision: int) -> dict:
    """Remove this hand's unsaved finger override and restore native animation; saved records remain intact."""
    return command('restore_hand',{'hand':hand},expected_revision)

@mcp.tool()
def save_hand_pose(hand: str, name: str, expected_revision: int, notes: str = '', anchor: int | None = None) -> dict:
    """Capture the current exact hand and reference placement as a new library pose."""
    params={'hand':hand,'name':name,'notes':notes}
    if anchor is not None: params['anchor']=anchor
    return command('save_hand_pose',params,expected_revision)

@mcp.tool()
def author_reload_grips(expected_revision: int) -> dict:
    """Pause a prepared reload at its current moment and open hand authoring with the weapon fixed and moving magazine selected. Then scrub, choose either hand, and save a pose."""
    return command('author_reload_grips',revision=expected_revision)

@mcp.tool()
def use_for_magazine_grab(expected_revision: int, scope: str = 'magazine') -> dict:
    """Replace this magazine's active fit and save the project. scope=weapon_default sets the current weapon's default, preserving other custom fits. Drums are supported. Old snapshots remain in explicit history; reload_bundle embeds concrete resolved fits."""
    return command('use_magazine_grip',{'scope':scope},expected_revision)

@mcp.tool()
def configure_magazine(expected_revision: int, grip_id: str = '') -> dict:
    """Open the selected magazine in the existing Move/Rotate hand editor. Load its draft/current fit or optionally a specific saved fit from its history. An unassigned magazine offers named Finger poses. Magazine geometry stays authoritative."""
    return command('configure_magazine',{'gripId':grip_id},expected_revision)

@mcp.tool()
def apply_magazine_pose(pose_id: str, expected_revision: int) -> dict:
    """Apply a named Finger pose's exact joints AND saved wrist rotation to the configured magazine. Preserve the current hand position for fine adjustment."""
    return command('apply_magazine_pose',{'poseId':pose_id},expected_revision)

@mcp.tool()
def use_default_magazine_grip(expected_revision: int) -> dict:
    """Remove this magazine's custom assignment, use the weapon default, and save the project."""
    return command('use_default_magazine_grip',revision=expected_revision)

@mcp.tool()
def start_part_grip(pose_id: str, anchor: int, expected_revision: int, captured: bool = True) -> dict:
    """Place a saved hand at a current reference part; choose captured or current placement."""
    return command('start_grip',{'poseId':pose_id,'anchor':anchor,'captured':captured},expected_revision)

@mcp.tool()
def set_part_grip(transform: dict, name: str, expected_revision: int) -> dict:
    """Set hand-in-part transform: rotate[9] native row-major, translate[3], uniform scale."""
    return command('set_grip',{'transform':transform,'name':name},expected_revision)

@mcp.tool()
def save_part_grip(expected_revision: int) -> dict:
    """Save the current validated grip in the local library."""
    return command('save_grip',revision=expected_revision)

@mcp.tool()
def select_mechanism_part(anchor: int, expected_revision: int) -> dict:
    """Select a concrete part to teach or edit its independent mechanism."""
    return command('select_part',{'anchor':anchor},expected_revision)

@mcp.tool()
def control_mechanism(changes: dict, expected_revision: int) -> dict:
    """Read/edit a mechanism; operation read/set_stage/preview/seek/release/stop/save. set_stage takes stage index, axis 0-2, rotation, from/to, pivot, release hold/start/rest and returnRate."""
    return command('mechanism',changes,expected_revision)

@mcp.tool()
def learn_reload(expected_revision: int) -> dict:
    """Capture the configured native reload; inspect busy state before using it."""
    return command('learn_reload',revision=expected_revision)

@mcp.tool()
def get_reload_recipe() -> dict:
    """Read the complete current captured source curves."""
    return command('reload')

@mcp.tool()
def save_project(expected_revision: int, note: str = '', wait_seconds: float = 0, refresh_current: bool = False) -> dict:
    """Deliberately save the open ESP project: every weapon of the originating
    plugin with unsaved edits, together, as a new dated version that becomes the
    latest. Changed part dependencies select the necessary captures, so the saved version exports what was
    authored; a configuration that cannot be captured keeps its earlier behavior
    and is reported. Nothing else saves: changing parts, weapons, sources or
    exporting never writes. refresh_current explicitly refreshes the selected
    weapon's saved reload, useful after an exporter repair; ordinary saves stay
    incremental. Returns its job ID immediately; wait_job observes completion.
    An optional bounded wait does not cancel the save on timeout."""
    job=command('save_project',{'note':note,'refreshCurrent':refresh_current},expected_revision)
    return wait_job(job['id'], wait_seconds) if wait_seconds > 0 else job

@mcp.tool()
def get_esp_project() -> dict:
    """The open ESP project: originating plugin, the version being edited (base),
    the latest version, every saved version (id, UTC savedAt, weapons with
    exportable/configuration counts), unsaved weapons and export history."""
    return command('esp_project')

@mcp.tool()
def get_save_plan() -> dict:
    """Read captures/reuse and reasons for the selected weapon without saving."""
    return command('save_plan')

@mcp.tool()
def esp_version(operation: str, save: str, expected_revision: int) -> dict:
    """load or delete exactly one saved version of the open ESP project.
    load replaces the working project for this session (unsaved edits are
    discarded in an unattended editor; save first); the next save creates a new
    latest version and keeps this one. delete removes only that version; the next
    newest becomes latest and export object IDs are never reset."""
    return command('esp_version',{'operation':operation,'save':save},expected_revision)

@mcp.tool()
def import_project(path: str, expected_revision: int) -> dict:
    """Explicitly import an earlier per-weapon project file as unsaved edits of
    its weapon in that weapon's ESP project. Opens the project's own source only
    when the current source lacks the weapon. Save to keep it as a new version."""
    return command('import_project',{'path':path},expected_revision)

@mcp.tool()
def assign_magazine_models(full: str, empty: str, expected_revision: int, magazine: str = '', clear: bool = False) -> dict:
    """Assign finished, Maker-cooked full and empty world models (with their
    .receipt.json) to a magazine of the current weapon; magazine defaults to the
    one prepared in Operate. The assignment is authoring: save_project copies the
    models into the ESP project and uses them for that magazine's configurations."""
    params={'magazine':magazine}
    if clear: params['operation']='clear'
    else: params.update(full=full,empty=empty)
    return command('assign_magazine_models',params,expected_revision)

@mcp.tool()
def export_authored_record(kind: str, expected_revision: int, path: str = '') -> dict:
    """Export project/hand/grip/mechanism/reload/simulation, slide, sounds, grab_locations, scope, runtime_configuration or reload_bundle locally. project writes a copy of the open weapon's working authoring to path (or exports/), for import_project; it is not a save. runtime_configuration captures the complete selected runtime motion, magazine fits, fingers, cues, grab policy and source evidence for pack authoring. sounds retains original descriptors. slide retains every closing-stroke sample/native rig path. reload_bundle embeds the recipe and assigned grips; unresolved references reject export. Never deploys."""
    params={'kind':kind}
    if path: params['path']=path
    return command('export',params,expected_revision)

@mcp.tool()
def get_simulation_state() -> dict:
    """Read direct-operation state, source curves, magazine assembly and current part frames. controllerInput.strokeInWeapon is the straight controller displacement for one complete stroke; the visible parts retain their complete animation."""
    return command('simulation_state')

@mcp.tool()
def simulate_weapon(operation: str, expected_revision: int, progress: float | None = None,
                    cancel: bool = False, enabled: bool = True,
                    position: list[float] | None = None, distance: float | None = None,
                    seconds: float | None = None) -> dict:
    """Operate: controller_pull(position=[x,y,z] is controller displacement since acquisition in weapon-local game units; read controllerInput.strokeInWeapon, release before reacquisition), fire/reload/drop_magazine/reset/slide_release, bolt_release or slap (locking-handle presets: full straight pull latches; release then slap closes and feeds), pull (direct normalized authoring progress), release(cancel), spring(enabled), ghost(enabled: draw parts whose authored motion differs at the source clip's pose), mag_out/mag_in/grab_magazine/release_magazine (cancel_magazine alias). grab_magazine acquires a seated magazine without moving it; manual templates unlock after a small outward move_magazine distance, button templates require drop_magazine before withdrawal, mechanism templates require their lever. release_magazine before withdrawal leaves it seated; after withdrawal it drops. AR-15 uses an independent charging handle (pull 0..1) and bolt catch; trigger_down/up controls held automatic fire. C14 Timberwolf uses pull progress 0..1 to lift/open, then 1..0 to close/lock; release holds position. Firing leaves a spent case until the source ejection threshold; only a full rear-and-forward cycle feeds. Commands never run the live clock: an action starts and holds until step(seconds 0..30) advances simulated time deterministically, so each call acts on the state the previous one returned. move_magazine takes weapon-local offset position[3] while free or outward distance on its straight, weapon-angled input rail. Linear progress drives any sampled magazine rotation/translation; never trace the visual motion path. Fresh insertion does not chamber until the bolt/slide closes. For unitLoading tube feeds, grab_magazine draws one shell; seating it increments only the tube and returns to waiting for another. The tube stays installed. Pull the held-open bolt again and release to close and feed one shell."""
    params={'operation':operation,'cancel':cancel,'enabled':enabled}
    if progress is not None: params['progress']=progress
    if position is not None: params['position']=position
    if distance is not None: params['distance']=distance
    if seconds is not None: params['seconds']=seconds
    return command('simulate',params,expected_revision)

@mcp.tool()
def set_interaction_hand(hand: str, expected_revision: int) -> dict:
    """Choose the physical left or right operating/reload hand, including pistols. Finish the current grab first."""
    return command('interaction_hand',{'hand':hand},expected_revision)

@mcp.tool()
def get_grab_locations() -> dict:
    """Read source-local operating grab points, radii and allowed physical hands."""
    return command('grab_locations_state')

@mcp.tool()
def configure_grab_location(operation: str, expected_revision: int, values: dict | None = None) -> dict:
    """open/add/pick/edit/remove/select/use_palm/save exact operating grab locations; every argument goes in values. add takes pointInSource[3] (the operating part's own frame, as describe_weapon's grabPoints.candidates give it) with optional name, radiusGame (the candidate's suggestedRadius) and handMask, and needs no viewport; remove and select take id; pick takes viewport x/y and optional replace; edit takes id, name, pointInSource[3], radiusGame and handMask (left=1/right=2/both=3). Exports use kind=grab_locations. These are game acquisition locations, independent of wrist/finger poses."""
    return command('grab_locations',dict(values or {},operation=operation),expected_revision)

@mcp.tool()
def get_sound_state() -> dict:
    """Read imported physical cues, original descriptor identities, playback counters and audio errors."""
    return command('sound_state')

@mcp.tool()
def configure_sound(operation: str, expected_revision: int, id: str = '', record: str | None = None,
                    trigger: str | None = None, progress: float | None = None, gain: float | None = None,
                    enabled: bool | None = None) -> dict:
    """Edit, play, stop, save or mute original reload sounds. Movement progress is 0..1; gain 0..2. Save persists this weapon/reload set.
    play with id 'fire' auditions the weapon's own fire sound (get_sound_state 'fire'), which the game plays and packs never include."""
    params={'operation':operation,'id':id}
    for key,value in {'record':record,'trigger':trigger,'progress':progress,'gain':gain,'enabled':enabled}.items():
        if value is not None:params[key]=value
    return command('sound',params,expected_revision)

@mcp.tool()
def mirror_magazine_grip(expected_revision: int) -> dict:
    """Create an editable opposite-hand magazine fit using the actual skeleton bases. Operating grabs use dynamic contact for either hand."""
    return command('mirror_magazine_grip',{},expected_revision)

@mcp.tool()
def cancel_operation() -> dict:
    """Cancel the current source load or reload capture."""
    return command('cancel')

@mcp.tool()
def import_authored_records(paths: list[str], expected_revision: int) -> dict:
    """Validate and import old or V2 pose/grip/mechanism/recipe JSON. Preserves originals and rejects conflicting IDs."""
    return command('import_records',{'paths':paths},expected_revision)



@mcp.tool()
def use_operating_fingers(id: str, expected_revision: int) -> dict:
    """Assign saved finger shape to the current operating hand/group; wrist stays live. The unassigned other hand uses a skeleton-aware mirror."""
    return command('use_operating_fingers', {'id': id}, expected_revision)


@mcp.tool()
def capture_prepared_part(output: str, expected_revision: int) -> dict:
    """In Magazines or Weapon collision, freeze the selected target's currently visible rigid geometry,
    UVs, normals, tangents, colors, materials and exact root frame into a prepared
    world NIF. Pick the correct full/empty presentation first; hidden shapes are
    excluded. Uses bundled native import, leaves source assets untouched, and
    rejects unsupported/incomplete geometry. Poll pack_job(state) for completion.
    """
    return command('capture_prepared_part',dict(output=output),expected_revision)

@mcp.tool()
def cook_world_object(job: str, output: str, expected_revision: int) -> dict:
    """Cook a frozen prepared-magazine job through bundled NIF tools and configured
    Creation Kit. Input is a rigid render NIF plus bounded hull JSON in its root
    frame. Finished output is new/immutable; source models stay untouched. Poll
    pack_job(state) for completion and its validated output/receipt.
    """
    return command('pack_job', dict(operation='cook',job=job,output=output), expected_revision)

@mcp.tool()
def get_operating_setup() -> dict:
    """Read current native track roles, follower choices, source clips and saved movements."""
    return command('operating_setup_state')

@mcp.tool()
def prepare_magazine_pair(expected_revision: int, output: str = '') -> dict:
    """Prepare/cook full and empty models in an isolated native editor. Assign
    both together only if this authoring is still current. Returns a job ID;
    wait_job retrieves files and assignment status."""
    params=dict(operation='prepare_pair')
    if output:params['output']=output
    return command('pack_job',params,expected_revision)

@mcp.tool()
def review_magazine_hands(expected_revision: int, output: str = '') -> dict:
    """Render both saved physical-hand fits from side/top/isometric views in an
    isolated native editor. Preserves the user's view, hand and drafts. This is
    contact review, not a gameplay test. Returns a job ID."""
    params=dict(operation='review_magazine')
    if output:params['output']=output
    return command('pack_job',params,expected_revision)

@mcp.tool()
def get_magazine_guide() -> dict:
    """Inspect this assembly's finite input guide and independent visible magazine path."""
    return command('magazine_guide_state')

@mcp.tool()
def get_magazine_reference() -> dict:
    """List exact eligible magazine hand-reference frames and connection settings."""
    return command('magazine_reference_state')


@mcp.tool()
def get_source_compatibility() -> dict:
    """Read this weapon/source context's add-on, blocked-mod and magazine-group rules."""
    return command('source_policy_state')


@mcp.tool()
def configure_source_compatibility(operation: str, key: str, expected_revision: int, policy: dict | None = None) -> dict:
    """Save/reset explicit rules; use key from get_source_compatibility.

    Rules are sourceOverride {marker: plugin:local-id, present: bool}, blockedMods
    and magazineCollections lists. Explicit edits apply to every configuration
    of this weapon when saved to its pack. Reset restores source defaults.
    """
    return command('source_policy',dict(operation=operation,key=key,policy=policy or {}),expected_revision)


@mcp.tool()
def configure_magazine_reference(operation: str, expected_revision: int, settings: dict | None = None) -> dict:
    """Save/reset the selected assembly's hand reference and seated connection.

    Start with get_magazine_reference.settings and an anchorKey from anchors.
    source uses the native mount; carrier uses the full-body pivot; custom uses
    connectionInCarrier, an exact position/rotation/scale frame in that body.
    Hand fits stay saved but must match the newly chosen reference before reuse.
    """
    return command('magazine_reference',dict(operation=operation,settings=settings or {}),expected_revision)


@mcp.tool()
def get_magazine_presentation() -> dict:
    """Read this magazine's native bodies, payload samples and individual piece roles."""
    return command('magazine_setup_state')

@mcp.tool()
def configure_magazine_presentation(operation: str, expected_revision: int, settings: dict | None = None) -> dict:
    """Save/reset the selected magazine's full/empty presentation.

    Start from get_magazine_presentation.settings. Select an emptyTrack from
    its native body candidates, sampledPayload and source loadedFrame/emptyFrame.
    Motion is preset, linear or native (entrySeconds). Use entrySeconds and seatSeconds for a loading segment that ends before the carrier closes or the inserted round is culled. This covers open cell trays and bottom-loaded tubes; optional loadingFollowers track indices drive a tube lifter/gate. For individual parallel chambers, chambers is a list of {track, seatSeconds} sockets, each sampled at its own seated source time. Every piece path keeps
    an explicit body/cartridge/spare role. After changes, recapture and cook both
    world models; a model built with another presentation cannot be reused.
    """
    return command('magazine_setup',dict(operation=operation,settings=settings or {}),expected_revision)

@mcp.tool()
def get_magazine_rounds() -> dict:
    """Read the selected magazine's pieces: round (hidden when empty) or body, and what Maker detected."""
    return command('magazine_rounds_state')

@mcp.tool()
def configure_magazine_rounds(operation: str, expected_revision: int, rounds: dict | None = None) -> dict:
    """Save or reset which pieces of the selected magazine are rounds, hidden when it is empty.

    save: rounds maps piece paths from get_magazine_rounds to true (round) or
    false (body); pieces left out keep their current role. Only choices that
    differ from the detected role are kept. reset returns to the detected roles.
    The choice belongs to the magazine and applies to every configuration that
    uses it. At least one piece must stay a body. Afterwards, recapture and cook
    both world models. A configuration with a saved presentation setup decides
    its pieces there instead.
    """
    return command('magazine_rounds',dict(operation=operation,rounds=rounds or {}),expected_revision)

@mcp.tool()
def configure_magazine_guide(operation: str, expected_revision: int, settings: dict | None = None) -> dict:
    """Save or reset the magazine guide without flattening the visible animation.

    Start from get_magazine_guide.settings. Direction is a unit vector outward
    in the connection's local frame. Travel, pickup radius and release margin
    are in game units. Edits replace the selected assembly's guide and save.
    """
    return command('magazine_guide',dict(operation=operation,settings=settings or {}),expected_revision)

@mcp.tool()
def configure_operating_setup(operation: str, expected_revision: int, settings: dict | None = None) -> dict:
    """Save/reset this captured assembly's operating roles and movement source.
    Start from get_operating_setup.settings. Modes: preset, reload, clip (path and
    baseline), manual (saved movement IDs per track). Native settings use version
    2 with openSeconds/closedSeconds in the source clip; the endpoint order may
    reverse a native path. Manual settings retain version 1 openFrame/closedFrame
    and saved movement IDs. Manual spring parts use an opening path;
    manual bolts use a full cycle. Both -1 endpoints request stroke detection.
    Native reload/clip modes support grabDeploySeconds for folding handles.
    Set grabDeployIndependent=true when that pose has the handle forward while
    the bolt is already open; followers must contain only its rigid handle assembly.
    slapClose enables free-palm closure at saved operating grab points; normal
    manual closure remains available. magazineEjectionSpeed is a launch speed in
    game units/second (0 keeps ordinary dropping), preserving the cell's charge.
    Fire mode is preset/native/stroke. Saved roles follow the reload's nodes, so
    other parts keep them; no wrist orientation is imposed by this setup. Test
    Operate before runtime export.
    """
    return command('operating_setup',dict(operation=operation,settings=settings or {}),expected_revision)

@mcp.tool()
def grip_tool(tool: str, expected_revision: int) -> dict:
    """Switch the viewport handles of the grip being placed: 'move' (arrows and the
    screen-plane square) or 'rotate' (rings)."""
    return command('grip_tool',dict(tool=tool),expected_revision)

@mcp.tool()
def capture_magazine_world(empty: bool, expected_revision: int) -> dict:
    """Capture the current full/empty loose-magazine presentation from Operate.
    Uses its actual carrier frame, pre-feed payload and native visibility; excludes
    hands, receiver and parked spares. Opens Reload builder > Magazines and starts bounded hull
    generation. Review pieces, then capture_prepared_part and cook_world_object.
    """
    return command('capture_magazine_world', dict(empty=empty), expected_revision)

@mcp.tool()
def load_timings(since: int = 0) -> dict:
    """Wall time of each loading stage after sequence number `since`: source
    load stages (archives, plugins, masters, names, reference catalogs),
    install, project read/save/restore, weapon rebuild stages, reload learning
    and Operate preparation. Pass the returned `next` to read only newer ones.
    """
    return command('load_timings', dict(since=since))

@mcp.tool()
def checklist_state() -> dict:
    """The weapon's checklist for the current configuration: items from its
    behavior preset (a manual bolt gets its cycle between shots, a bolt driven
    by a charging handle only follows it), each with state done (its data
    exists, authored or read from the weapon's files and reload), missing,
    not-applicable, or not-checked while the weapon is still loading, plus its
    value and evidence. Nothing needs confirming. summary.missing counts the
    required items still to do; summary.progress is not-started, draft or
    complete. evaluated is false until the weapon, its learned reload and its
    operating parts are available. presets lists the supported behavior
    presets; weapon.preset is done once one is assigned (presetAssigned);
    otherwise preset is the one Maker proposes from the records, or empty when
    the records do not tell; assign one with checklist operation preset.
    """
    return command('checklist_state')

@mcp.tool()
def checklist(operation: str, id: str, expected_revision: int, scope: str = 'weapon') -> dict:
    """Assign a behavior preset (operation preset, id = preset id), or mark a
    missing optional item not applicable (skip) and undo that (unskip). Skips
    save with the project.
    """
    return command('checklist', dict(operation=operation, id=id,scope=scope), expected_revision)

@mcp.tool()
def weapon_properties_state() -> dict:
    """What Maker read from the open configuration, which the editor uses in
    place of weapon names. clips lists the reload, fire and cycle clips with
    their events (sound plays, ejection, weaponfire, culling).
    preset is the assigned preset and the one proposed from the records, with
    the evidence or the reason there is no proposal.
    magazineEvents is the magazine entry and removal Maker read from the
    reload clip's sound names, with any ambiguity. insertion is how the
    magazine goes in: straight, or native-curve along the clip; source says
    whether the weapon's own choice or its preset decided.
    magazine (once Operate is prepared) is how the magazine sits: installed
    (a part attached inside the carrier bone), supplies-carrier (the magazine
    model brings its own carrier bone) or built-in (the receiver holds it),
    with evidence; pieces lists each magazine shape with its proposed role
    (body, cartridge from round/casing/ammo names, or parked spare) and why.
    A saved magazine setup overrides the proposed roles.
    """
    return command('weapon_properties_state')

@mcp.tool()
def weapon_property(name: str, value: str | None, expected_revision: int) -> dict:
    """Set one property for the whole weapon, overriding its preset, or clear
    it with value null. name insertion takes straight or native-curve. Operate
    is prepared again and the project saved.
    """
    return command('weapon_property', dict(name=name, value=value), expected_revision)

@mcp.tool()
def get_installation() -> dict:
    """Inspect game/Creation Kit roots, MO2 profiles, capabilities and active VFS status."""
    return command('installation')

@mcp.tool()
def home(role: str = '') -> dict:
    """Inspect first-run capabilities and the recent project. An optional creator
    or player role saves the workspace's launch preference without opening a
    window or changing its scene. Player pack building does not require CK or
    source-scene setup. New collision cooking still requires the configured CK.
    """
    return command('home',dict(role=role) if role else {})

@mcp.tool()
def load_base_game(expected_revision: int) -> dict:
    """Load base game plugins and a frozen explicit archive/loose Data view.
    Requires a configured game folder and an ordinary, non-MO2 process. Inside
    MO2 use the connected library's Fallout4.esm filter instead. Saves no game
    files and makes no claim about a manual installation's enabled mod list.
    Wait for get_state.busy to clear, then choose a weapon and explicit template.
    """
    return command('base_game',{},expected_revision)

@mcp.tool()
def get_pack_coverage(refresh: bool = False) -> dict:
    """Show the library's ESP projects and their latest versions' weapons
    (configurations, magazines, exportable), with the loaded source's weapons.
    Saved coverage does not establish current source compatibility or in-game
    enablement.
    """
    return command('pack_coverage',dict(refresh=refresh))

@mcp.tool()
def source_browser(operation: str = 'state', plugin: str = '', mode: str = '',
                   index: int = -1, expected_revision: int | None = None) -> dict:
    """Read the actual Plugin -> Weapon pickers, or choose mode/plugin/weapon.
    Modes are dynamic (configured MO2 view) and development (saved testbeds).
    Plugin order follows the current source; master roles identify dependencies.
    In MO2 mode, plugin='*' selects Load all weapons and labels each weapon
    with its originating plugin. Specific plugins keep their scoped list.
    Development plugin selection may load its saved source asynchronously.
    """
    if operation != 'state' and expected_revision is None:
        raise ValueError('Read the document revision before changing source selection')
    return command('source_browser',dict(operation=operation,plugin=plugin,mode=mode,index=index),expected_revision)

@mcp.tool()
def pack_job(operation: str = 'state', selections: list[dict] | None = None,
             job: str = '', output: str = '', archive: str = '', mo2: str = '',
             scope: str = '', expected_revision: int | None = None, job_id: str = '') -> dict:
    """Export reload mods from saved ESP projects: one ESL-flagged ESP per source
    plugin with its magazine meshes. export takes selections=[{plugin, save}]
    (save omitted = latest; no selections = the open ESP) and archive (.zip/.7z)
    or mo2 (one named mod folder). scope='everything' instead exports the latest
    version of every saved ESP project with weapons in the loaded source: their
    reloads (weapons the source lacks are skipped and listed) and the True Scopes
    profiles of those weapons, in the same bundle. Only committed versions are exported; unsaved
    edits are reported in unsavedNotIncluded, never saved. Object IDs belong to
    each ESP project and never change between versions. MO2 export leaves
    identical files in place and never changes activation, load order or source
    plugins. open (with scope 'esp' or 'everything' for the header windows)/close
    show or close the export window; state/cancel observe
    or cancel the job (including a running save). cook and prepare run the
    world-model tools on job, writing output. Poll state to complete/failed.
    """
    if operation not in ('state', 'cancel') and expected_revision is None:
        raise ValueError('Read the current document revision before starting a pack job')
    return command('pack_job', dict(operation=operation, id=job_id, selections=selections or [], job=job,
                                   output=output, archive=archive, mo2=mo2, scope=scope), expected_revision)

@mcp.tool()
def configure_installation(game_root: str, creation_kit_root: str, same_tools_root: bool,
                           mo2_instance: str, profile: str, expected_revision: int) -> dict:
    """Save setup without changing the active source. Collision cooking remains optional."""
    return command('configure_installation', {'version': 1, 'gameRoot': game_root,
        'creationKitRoot': creation_kit_root, 'sameToolsRoot': same_tools_root,
        'mo2Instance': mo2_instance, 'profile': profile}, expected_revision)

@mcp.tool()
def connect_profile(expected_revision: int) -> dict:
    """Connect saved setup only through the matching active MO2 virtual profile."""
    return command('connect_profile', {}, expected_revision)


@mcp.tool()
def holding_grips(operation: str, expected_revision: int, values: dict | None = None) -> dict:
    """Physical firing grips: preview(enabled, hand, support, replay), import-capture(path), edit, save, reset, export(path). Reload-hand selection stays independent. Export writes optional ROCK holding-grip records for the current weapon; it does not enable mods. Runtime captures include actual controller calibration and final hands."""
    return command('holding_grips', dict(values or {}, operation=operation), expected_revision)

@mcp.tool()
def get_holding_grips() -> dict:
    """Read holding preview, independent firing fits, capture metadata and available comparison errors."""
    return command('holding_grips_state')

@mcp.tool()
def get_holding_geometry() -> dict:
    """Read visible deformed surfaces in weapon space for contact diagnosis, including skin-bone names and attachment frames. This explicit snapshot includes vertex/triangle arrays; it does not change authoring."""
    return command('holding_geometry')

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--command',help='One JSON request to the local application')
    parser.add_argument('--call',help='Call one tool of this adapter by name and print its reply; --list names them')
    parser.add_argument('--arguments',default='{}',help='The tool arguments as one JSON object')
    parser.add_argument('--list',action='store_true',help='Print every tool with its description')
    args=parser.parse_args()
    if args.list or args.call:
        import asyncio
        tools={tool.name:tool for tool in asyncio.run(mcp.list_tools())}
        if args.list:
            for name,tool in sorted(tools.items()):print(name+'\n    '+' '.join((tool.description or '').split())+'\n')
        elif args.call not in tools:
            raise SystemExit('Unknown tool '+args.call+'; use --list')
        else:
            arguments=json.loads(args.arguments)
            # An edit takes the revision it was written against; outside a client that tracks it, use the current one.
            if 'expected_revision' in tools[args.call].inputSchema.get('properties',{}) and 'expected_revision' not in arguments:
                arguments['expected_revision']=command('revision')['revision']
            reply=globals()[args.call](**arguments)
            print(reply.path if isinstance(reply,Image) else json.dumps(reply,indent=1))
    elif args.command:
        request=json.loads(args.command)
        print(json.dumps(command(request['method'],request.get('params'),request.get('expectedRevision'))))
    else: mcp.run()

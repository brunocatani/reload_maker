"""A weapon authored one decided step at a time.

next_step reads what the weapon still lacks and names the single tool call that
supplies it, so the caller never plans, orders or skips work. The choices it is
asked to make are closed: a number on a picture, or nothing at all. finish is
the gate: it measures the result, saves, and reports what the save accepted.
What cannot be decided this way is handed back with stop, never guessed.
"""
import json
import math
import time
from pathlib import Path

import fitting
import perception
from perception import Editor, NativeHands, bounds

ATTEMPTS = 2              # fit searches per hand before the weapon is handed back


def stop(reason):
    return {'stop': True, 'reason': reason,
            'tell': 'Do nothing else on this weapon. Report this reason, word for word, to whoever gave you the task.'}


def step(item, why, tool, arguments=None, then='Then call next_step again.'):
    return {'item': item, 'why': why, 'do': {'tool': tool, 'arguments': arguments or {}}, 'then': then}


def progress(store, weapon):
    path = Path(store) / 'progress.json'
    record = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    return path, record, record.setdefault(weapon, {})


def count(store, weapon, key):
    """One more attempt at `key` on this weapon."""
    path, record, mine = progress(store, weapon)
    mine[key] = mine.get(key, 0) + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=1), encoding='utf-8')
    return mine[key]


def tries(store, weapon, key):
    return progress(store, weapon)[2].get(key, 0)


# --- grab points ------------------------------------------------------------------------------------

def grab_sheet(command, store):
    """The exposed points of the operating part as numbered dots on the weapon."""
    dossier = perception.describe(command, hands=False)
    if not dossier['grabPoints']['candidates']:
        raise RuntimeError(dossier['grabPoints'].get('note') or 'This weapon has no operating part to grab')
    # Dots that would sit on top of each other in the picture are one choice.
    candidates = []
    for candidate in dossier['grabPoints']['candidates']:
        if all(math.dist(candidate['pointInWeapon'], other['pointInWeapon']) >= 1.8 for other in candidates):
            candidates.append(candidate)
    editor = Editor(command)
    editor.edit('simulate', {'operation': 'reset'})
    points = [c['pointInWeapon'] for c in candidates]
    low, high = bounds(points)
    focus = [[x, y, z] for x in (low[0] - 7, high[0] + 7) for y in (low[1] - 7, high[1] + 7) for z in (low[2] - 7, high[2] + 7)]
    marks = [{'point': c['pointInWeapon'], 'color': '#ffd371', 'label': str(number), 'size': 1.2} for number, c in enumerate(candidates, start=1)]
    paths = [editor.read('render', {'view': view, 'focus': focus, 'marks': marks, 'preserveCamera': True,
                                    'caption': 'Grab points   %s view' % view})['path'] for view in fitting.VIEWS]
    whole = editor.read('render', {'view': 'side', 'fit': True, 'marks': marks, 'caption': 'The whole weapon', 'preserveCamera': True})['path']
    panels = [{'path': path, 'label': view} for path, view in zip(paths, fitting.VIEWS)] + [{'path': whole, 'label': 'whole weapon'}]
    sheet = editor.read('picture_sheet', {'panels': panels, 'columns': 2, 'cellWidth': 640,
                                          'title': 'Numbered dots: places on the part a hand can take hold of'})['path']
    path = Path(store) / 'grab-candidates.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'weapon': dossier['weapon']['record'], 'candidates': candidates}), encoding='utf-8')
    return {'sheet': sheet, 'part': dossier['roles']['trackNames'].get(str(dossier['roles']['operatingTrack'])),
            'candidates': [{'number': n, 'clearance': c['clearance']} for n, c in enumerate(candidates, start=1)], 'recommended': 1,
            'saved': len(dossier['grabPoints']['saved']),
            'next': 'Call choose_grab_point with the number of the dot that sits on the handle or knob a person would pull. '
                    'Dot 1 stands most clear of the weapon; choose it unless it is plainly not on the handle.'}


def choose_grab(command, store, number):
    path = Path(store) / 'grab-candidates.json'
    record = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None
    editor = Editor(command)
    status = editor.idle()
    if not record or record['weapon'] != status['weapon']:
        raise RuntimeError('No grab point candidates for this weapon; call show_grab_points first')
    if not 1 <= number <= len(record['candidates']):
        raise RuntimeError('Dot numbers are 1 to %d' % len(record['candidates']))
    candidate = record['candidates'][number - 1]
    editor.edit('grab_locations', {'operation': 'add', 'pointInSource': candidate['pointInSource'], 'radiusGame': candidate['suggestedRadius']})
    editor.edit('grab_locations', {'operation': 'save'})
    points = editor.read('grab_locations_state').get('profile', {}).get('points', [])
    return {'added': number, 'pointsNow': len(points), 'next': 'Call next_step.'}


# --- which part a hand works ------------------------------------------------------------------------------

def operating_part(command, part, moves_with=(), bolt='', magazine='', unfolded_seconds=None, pulled_seconds=None, returned_seconds=None):
    """Make a named part the one a hand works, and show it pulled.

    For a weapon whose charging handle is its own part: `moves_with` names the
    parts that travel rigidly with the handle (never the bolt of an independent
    handle). `bolt` and `magazine` name those parts where the preset has not
    found them. The three moments are seconds of the reload clip. `unfolded_seconds`
    is for a handle that swings out before it is pulled: the moment it is fully
    out and not yet pulled; Maker then deploys it when grabbed. The bolt's own
    position at that moment tells whether the handle is independent of it.
    """
    editor = Editor(command)
    setup = editor.read('operating_setup_state')
    settings = dict(setup['settings'])
    names = [anchor['name'] for anchor in settings['trackAnchors']]
    missing = [name for name in (part, bolt, magazine) + tuple(moves_with) if name and name not in names]
    if missing:
        raise RuntimeError('This weapon has no moving part named %s. Its moving parts are: %s' % (', '.join(missing), ', '.join(names)))
    for role, name in (('bolt', bolt), ('magazine', magazine)):
        if name:
            settings[role] = names.index(name)
        elif settings.get(role, -1) < 0:
            raise RuntimeError('Maker has not found the %s of this weapon; name it with the "%s" argument' % (role, role))
    reload_clip = next((clip for clip in setup['clips'] if clip['name'] == 'Reload'), None)
    if reload_clip is None:
        raise RuntimeError('This weapon has no reload clip to take the movement from')
    report = {'part': part, 'movesWith': list(moves_with)}
    action = next(a['index'] for a in editor.read('state', {'fields': ['actions']})['actions'] if a['path'].lower() == reload_clip['path'].lower())
    editor.edit('workspace', {'index': 2, 'handsPage': 0})
    editor.edit('action', {'index': action})

    def where(name, seconds):
        editor.edit('playback', {'seconds': seconds, 'playing': False})
        return fitting.weapon_local(editor.read('pose'), name)[0]

    if pulled_seconds is not None and returned_seconds is None:
        # The stroke ends where the part first comes back to rest; a part that settles afterwards
        # would make the stroke run backwards.
        rest, seconds = where(part, 0.0), pulled_seconds
        away = math.dist(where(part, pulled_seconds), rest)
        while seconds < setup['duration'] and math.dist(where(part, seconds), rest) > 0.05 * away:
            seconds += 0.025
        returned_seconds = report['returnedSeconds'] = round(seconds, 3)
    settings.update(version=2, mode='clip', clip=reload_clip['path'], baseline=reload_clip['baseline'], operating=names.index(part),
                    followers=[names.index(name) for name in (part,) + tuple(moves_with)],
                    openSeconds=-1 if pulled_seconds is None else pulled_seconds,
                    closedSeconds=-1 if returned_seconds is None else returned_seconds)
    if unfolded_seconds is not None:
        bolt = report['bolt'] = names[settings['bolt']]
        report['boltWhenUnfolded'] = round(math.dist(where(bolt, 0.0), where(bolt, unfolded_seconds)), 2)
        # A bolt already back while the handle is still forward does not carry the handle.
        settings.update(grabDeploySeconds=unfolded_seconds, grabDeployIndependent=report['boltWhenUnfolded'] > 2.0 and bolt != part)
        report['independentOfBolt'] = settings['grabDeployIndependent']
    editor.edit('operating_setup', {'operation': 'save', 'settings': settings})
    saved = editor.read('operating_setup_state')
    if saved.get('error'):
        raise RuntimeError('Maker did not accept this operating setup: ' + saved['error'])
    # Shown as a hand would feel it: taken hold of, then pulled along the straight stroke.
    editor.edit('workspace', {'index': 5})
    editor.edit('simulate', {'operation': 'reset'})
    stroke = editor.read('simulation_state').get('controllerInput', {}).get('strokeInWeapon') or [0.0, 0.0, 0.0]
    report['pullLength'] = round(math.hypot(*stroke), 2)
    # Framed on the shapes that ride these parts; a part's own origin can lie far from what it draws.
    shapes = [mesh for mesh in perception.describe(command, hands=False)['meshes'] if mesh['rides'] in (part,) + tuple(moves_with)]
    if not shapes:
        raise RuntimeError('No drawn shape rides %s, so there is nothing for a hand to take hold of' % part)
    low, high = bounds([[mesh['centre'][i] + side * mesh['size'][i] / 2 for i in range(3)] for mesh in shapes for side in (-1, 1)])
    report['shapes'] = [mesh['name'] for mesh in shapes]
    focus = [[x, y, z] for x in (low[0] - 5, high[0] + 5) for y in (low[1] - 5 - report['pullLength'], high[1] + 5) for z in (low[2] - 5, high[2] + 5)]
    panels = []
    for share, label in ((None, 'at rest, nobody holding it'), (0.0, 'taken hold of, not pulled'), (0.5, 'pulled half way'),
                         (1.0, 'pulled all the way')):
        if share is not None:
            editor.edit('simulate', {'operation': 'controller_pull', 'position': [value * share for value in stroke]})
        for view in ('left', 'top'):
            path = editor.read('render', {'view': view, 'focus': focus, 'preserveCamera': True, 'caption': '%s   %s view' % (label, view)})['path']
            panels.append({'path': path, 'label': '%s  %s' % (label, view)})
    editor.edit('simulate', {'operation': 'release'})
    editor.edit('simulate', {'operation': 'reset'})
    report['sheet'] = editor.read('picture_sheet', {'panels': panels, 'columns': 2, 'cellWidth': 640,
                                                    'title': 'The %s taken hold of and pulled %.1f units' % (part, report['pullLength'])})['path']
    report['look'] = ['Row 1 is the weapon at rest; rows 2 to 4 are a hand holding the part, at three amounts of pull. '
                      'Each is seen from the left side and from above.',
                      'In row 2 the part must be in its row 1 place, or folded out from it; in rows 3 and 4 it must move back with the pull.',
                      'A folding part is folded in row 1: in the game it folds out as a hand comes near.',
                      'A part that jumps, stays folded in row 2, or leaves the weapon is a fault: report it.']
    report['next'] = 'Call next_step.'
    return report


# --- operating fingers ---------------------------------------------------------------------------------

def operating_fingers(command):
    """The finger shape of the hand that works the operating part in the weapon's own reload, for both hands."""
    dossier = perception.describe(command)
    hands = dossier['hands']
    if 'problem' in hands:
        raise RuntimeError(hands['problem'])
    carrier = hands.get('carriedBy', {}).get('operatingPart', {}).get('hand')
    sides = [side for side in ('left', 'right') if hands.get(side, {}).get('operatingPart', {}).get('tightestWrap')]
    side = carrier if carrier in sides else (sides[0] if sides else None)
    if side is None:
        raise RuntimeError('No hand works the operating part in this weapon\'s reload, so there is no finger shape to take')
    seconds = hands[side]['operatingPart']['tightestWrap']['seconds']
    editor = Editor(command)
    state = editor.read('state', {'fields': ['anchors', 'actions', 'weapons', 'weaponIndex']})
    part = dossier['roles']['trackNames'][str(dossier['roles']['operatingTrack'])]
    anchor = next((a['index'] for a in state['anchors'] if a['name'] == part), 0)
    clip = dossier['reload']['animation'].lower()
    action = next(a['index'] for a in state['actions'] if a['path'].lower() == clip)
    name = '%s %s fingers %.2fs' % (state['weapons'][state['weaponIndex']]['name'], part, seconds)
    with NativeHands(editor):
        editor.edit('workspace', {'index': 2, 'handsPage': 0})
        editor.edit('action', {'index': action})
        editor.edit('playback', {'seconds': seconds, 'playing': False})
        editor.edit('save_hand_pose', {'hand': side, 'name': name, 'anchor': anchor})
    pose = [p for p in editor.read('state', {'fields': ['handPoses']})['handPoses'] if p['name'] == name][-1]
    editor.edit('workspace', {'index': 5})
    for hand in ('left', 'right'):
        editor.edit('simulate', {'operation': 'reset'})
        editor.edit('interaction_hand', {'hand': hand})
        editor.edit('use_operating_fingers', {'id': pose['id']})
    return {'fingersFrom': '%s hand at %.2f s of the reload' % (side, seconds), 'pose': name, 'next': 'Call next_step.'}


# --- the magazine's world models --------------------------------------------------------------------------

def magazine_models(command, store, magazine=''):
    """Prepare and cook the full and empty models of a magazine and assign them; waits for the job.

    `magazine` is the record of one of the weapon's magazines, which is selected first; without it
    the selected magazine is prepared.
    """
    editor = Editor(command)
    weapon = editor.idle()['weapon']
    if magazine:
        slots = editor.read('state', {'fields': ['slots']})['slots']
        found = next(((slot, choice) for slot in slots for choice in slot['choices'] if choice['record'].lower() == magazine.lower()), None)
        if found is None:
            raise RuntimeError('This weapon has no magazine ' + magazine)
        editor.edit('configure', {'slot': found[0]['key'], 'mod': found[1]['id']})
        editor.idle()
    path, record, mine = progress(store, weapon)
    mine['todo'] = [entry for entry in mine.get('todo', []) if entry.lower() != magazine.lower()]
    mine['models'] = mine.get('models', 0) + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=1), encoding='utf-8')
    job = editor.edit('pack_job', {'operation': 'prepare_pair'})
    deadline = time.monotonic() + 1500
    while True:
        state = editor.read('pack_job', {'operation': 'state', 'id': job['id']})
        if state.get('phase') in ('complete', 'failed', 'cancelled'):
            break
        if time.monotonic() > deadline:
            raise RuntimeError('Preparing the magazine models is still running after 25 minutes')
        time.sleep(0.5)
    if state.get('phase') != 'complete':
        raise RuntimeError('The magazine models could not be prepared: ' + str(state.get('error')))
    result = state.get('result') or {}
    return {'full': result.get('full'), 'empty': result.get('empty'), 'assigned': bool(result.get('assigned')), 'next': 'Call next_step.'}


# --- the sequence ---------------------------------------------------------------------------------------

def next_step(command, store):
    editor = Editor(command)
    status = editor.idle()
    if not status.get('weapon'):
        return stop('No weapon is open in Reload Maker.')
    if editor.read('state', {'fields': ['workspace']})['workspace'] != 5:
        editor.edit('workspace', {'index': 5})
        editor.idle()
    checklist = editor.read('checklist_state')
    weapon = status['weapon']
    if not checklist.get('preset'):
        return stop('Maker cannot tell which behavior preset this weapon needs: ' + str(checklist.get('problem')))
    if not checklist.get('presetAssigned'):
        return step('weapon.preset', 'Maker proposes the preset "%s" from the weapon\'s records.' % checklist.get('presetLabel'),
                    'checklist', {'operation': 'preset', 'id': checklist['preset']})
    if not checklist.get('evaluated'):
        return stop('The weapon has a preset but no usable learned reload or operating parts: ' +
                    '; '.join('%s: %s' % (i['id'], i['message']) for i in checklist['items'] if i['state'] == 'missing'))
    missing = [i for i in checklist['items'] if i['state'] == 'missing' and i.get('mandatory')]
    for item in missing:
        key = item['id']
        if key == 'weapon.reload':
            return step(key, 'The reload has not been learned from the weapon\'s animation.', 'learn_reload')
        if key.startswith('action.') and key.endswith('.grab'):
            return step(key, 'The operating part has no place for a hand to take it.', 'show_grab_points',
                        then='Then call choose_grab_point with the number you pick from the picture.')
        if key == 'action.fingers':
            return step(key, 'The hand working the operating part has no finger shape.', 'author_operating_fingers')
        if key == 'feed.fits' or (key.startswith('feed.') and key.endswith('.fit')):
            hand = 'right' if 'right' in item['message'].lower() else 'left'
            if tries(store, weapon, 'fit-' + hand) >= ATTEMPTS:
                return stop('The %s hand still has no magazine fit after %d searches.' % (hand, ATTEMPTS))
            return step(key, 'The %s hand has no fit on the magazine.' % hand, 'solve_magazine_fit', {'hand': hand},
                        then='Then call choose_magazine_fit with the same hand and the number you pick from the picture.')
        if key == 'sounds.reload':
            return step(key, 'The reload\'s sounds have been read but not saved.', 'configure_sound', {'operation': 'save'})
        return stop('"%s" is missing and has no automatic step: %s' % (item['title'], item['message']))
    todo = progress(store, weapon)[2].get('todo', [])
    if todo:
        return step('feed.world', 'The save found another magazine of this weapon without world models.', 'prepare_magazine_models',
                    {'magazine': todo[0]})
    world = next((i for i in checklist['items'] if i['id'] == 'feed.world' and i['state'] == 'missing'), None)
    if world and not tries(store, weapon, 'models'):
        # The save needs a new magazine's full and empty world models even though the checklist lets them
        # wait. The checklist item itself follows the collision drafts, so the step is offered once.
        return step('feed.world', 'The magazine has no full and empty world models yet; the save needs them.', 'prepare_magazine_models')
    held = fitting.show(command)
    for hand in ('left', 'right'):
        report = held.get(hand, {})
        if report.get('verdict') != 'holds':
            if tries(store, weapon, 'fit-' + hand) >= ATTEMPTS:
                return stop('The %s hand does not hold the magazine after %d searches: %s.' % (hand, ATTEMPTS, json.dumps(report)))
            return step('feed.fits', 'Held in Operate, the %s hand reads "%s" on the magazine.' % (hand, report.get('verdict')),
                        'solve_magazine_fit', {'hand': hand},
                        then='Then call choose_magazine_fit with the same hand and the number you pick from the picture.')
    return {'item': 'finish', 'why': 'Nothing is missing and both hands hold the magazine.', 'do': {'tool': 'finish_weapon', 'arguments': {}},
            'then': 'finish_weapon saves the weapon and reports the result.', 'sheet': held.get('sheet')}


def finish(command, store):
    """The gate: nothing missing, both hands measured holding, then the real Save and what it accepted."""
    editor = Editor(command)
    status = editor.idle()
    if editor.read('state', {'fields': ['workspace']})['workspace'] != 5:
        editor.edit('workspace', {'index': 5})
        editor.idle()
    checklist = editor.read('checklist_state')
    failing = [{'check': i['id'], 'detail': i['message'], 'do': 'next_step'} for i in checklist.get('items', [])
               if i['state'] == 'missing' and i.get('mandatory')]
    if not checklist.get('presetAssigned'):
        failing.append({'check': 'weapon.preset', 'detail': 'No behavior preset is assigned.', 'do': 'next_step'})
    held = fitting.show(command) if not failing else {}
    for hand in ('left', 'right'):
        if held and held.get(hand, {}).get('verdict') != 'holds':
            failing.append({'check': 'magazine fit, %s hand' % hand, 'detail': json.dumps(held.get(hand)), 'do': 'next_step'})
    if failing:
        return {'finished': False, 'failing': failing, 'next': 'Call next_step; it names the one call that fixes the first of these.'}
    job = editor.edit('save_project', {'note': 'guided authoring', 'refreshCurrent': True})
    deadline = time.monotonic() + 1500
    while True:
        state = editor.read('pack_job', {'operation': 'state', 'id': job['id']})
        if state.get('phase') in ('complete', 'failed', 'cancelled'):
            break
        if time.monotonic() > deadline:
            return {'finished': False, 'failing': [{'check': 'save', 'detail': 'The save is still running after 25 minutes.', 'do': 'wait_job'}]}
        time.sleep(0.5)
    result = state.get('result') or {}
    rows = (result.get('packReports') or {}).get(status['weapon'], {})
    rejected = [row for row in rows.get('rows', []) if row.get('status') != 'saved']
    unmodelled = [row['selection'].get('magazine') for row in rejected if 'cooked models' in str(row.get('error')) and row.get('selection')]
    path, record, mine = progress(store, status['weapon'])
    if state.get('phase') == 'complete' and unmodelled and len(unmodelled) == len(rejected) and not mine.get('asked'):
        # Each magazine with its own model needs its own world models; the save names them, once.
        mine['todo'], mine['asked'] = sorted(set(unmodelled)), True
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=1), encoding='utf-8')
        return {'finished': False, 'configurationsSaved': rows.get('saved'),
                'failing': [{'check': 'world models of ' + magazine, 'detail': 'This magazine has no full and empty world models.',
                             'do': 'next_step'} for magazine in mine['todo']],
                'next': 'Call next_step; it prepares each of these magazines, then finish_weapon again.'}
    if state.get('phase') != 'complete' or rejected or rows.get('exceptions'):
        reason = state.get('error') or '; '.join(str(row.get('error')) for row in rejected) or json.dumps(rows.get('exceptions'))
        return {'finished': False, 'failing': [{'check': 'save', 'detail': reason, 'do': 'stop'}], **stop('The save rejected this weapon: ' + reason)}
    return {'finished': True, 'saved': (result.get('save') or {}).get('id'), 'configurationsSaved': rows.get('saved'),
            'sheet': held.get('sheet'), 'note': 'Saved and accepted by the export check. Behaviour in the game is not verified by this.'}

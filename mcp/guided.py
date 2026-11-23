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

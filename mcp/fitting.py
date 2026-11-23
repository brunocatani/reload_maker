"""Magazine hand fits worked out by measurement instead of by eye.

A hand whose fingers are fixed is rigid once its wrist is placed. One snapshot
of the displayed hand therefore lets any number of wrist placements be scored
without the editor: the hand's skin points are kept in the wrist's own frame and
tested against a distance field of the magazine. Starting hands come from the
fit already assigned, from the weapon's own reload, and from fits that hold on
other weapons whose magazine has a similar cross-section (the fit library).

Every candidate is then shown in the editor and measured for real; the numbers
reported are those measurements, never the search's own estimate.
"""
import json
import math
from pathlib import Path

from perception import (FINGERS, HOLD_REACH, Editor, NativeClip, Reload, Surface, apply, bounds, contact, hand_groups, hold_score,
                        hold_verdict, part_state, rotate, transform, unapply)

FIELD_CELL = 0.4          # spacing of the magazine's distance field
FIELD_REACH = 3.0         # the field is flat beyond this distance from the magazine
REST_GAP = -0.25          # where a finger of a saved fit typically sits: a quarter unit into the surface
SINK = 0.8                # skin may sink this deep before it counts against the fit
SEARCH_TRAVEL = 3.0       # how far the search may move a starting hand, game units
SEARCH_TURN = 25.0        # and how far it may turn it, degrees
FREE_ABOVE = 1.0          # a finger that stood this clear on a borrowed fit is not pulled to the surface
# The wrist of a saved fit sits near the magazine's outer end: 95% lie no more than 0.38 of the
# magazine's length toward the weapon. A wrist farther in holds the end that goes into the weapon.
WRONG_END = -0.4
STICKS_OUT = 1.5          # a finger on the magazine whose end is this far off it points away from it
# How deep the hand may enter the weapon when the magazine is seated: 95% of the saved fits stay within 1.1.
WEAPON_SINK = 1.1
WEAPON_PAD = 7.0          # the weapon is measured this far around the seated magazine
PER_SEGMENT = 24          # skin points kept per finger segment
PER_PALM = 90
VIEWS = ('side', 'left', 'front')
STATE_COLOUR = {'touching': '#35c46a', 'hovering': '#f2c230', 'away': '#f2c230', 'buried': '#e5484d'}
LETTER = {'thumb': 'T', 'index': 'I', 'middle': 'M', 'ring': 'R', 'little': 'L'}


# --- transforms: (translation[3], quaternion[x, y, z, w], scale[3]) ---------------------------

def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz]


def compose(a, b):
    return apply(a, b[0]), qmul(a[1], b[1]), [a[2][i] * b[2][i] for i in range(3)]


def inverse(t):
    """Of a transform whose scale is the same on every axis."""
    q = [-t[1][0], -t[1][1], -t[1][2], t[1][3]]
    moved = rotate(q, [-t[0][i] for i in range(3)])
    return [moved[i] / t[2][i] for i in range(3)], q, [1 / s for s in t[2]]


def turn(axis, degrees):
    half = math.radians(degrees) / 2
    s = math.sin(half)
    return [axis[0] * s, axis[1] * s, axis[2] * s, math.cos(half)]


def quaternion(x, y, z):
    """The rotation whose columns are the unit axes x, y and z."""
    trace = x[0] + y[1] + z[2]
    if trace > 0:
        s = math.sqrt(trace + 1) * 2
        return [(y[2] - z[1]) / s, (z[0] - x[2]) / s, (x[1] - y[0]) / s, s / 4]
    if x[0] > y[1] and x[0] > z[2]:
        s = math.sqrt(1 + x[0] - y[1] - z[2]) * 2
        return [s / 4, (y[0] + x[1]) / s, (z[0] + x[2]) / s, (y[2] - z[1]) / s]
    if y[1] > z[2]:
        s = math.sqrt(1 + y[1] - x[0] - z[2]) * 2
        return [(y[0] + x[1]) / s, s / 4, (z[1] + y[2]) / s, (z[0] - x[2]) / s]
    s = math.sqrt(1 + z[2] - x[0] - y[1]) * 2
    return [(z[0] + x[2]) / s, (z[1] + y[2]) / s, s / 4, (x[1] - y[0]) / s]


def unit(v):
    length = math.hypot(*v)
    return [c / length for c in v]


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def pose_json(t):
    """The saved-pose layout the editor reads: a row-major rotation matrix, translate and one scale."""
    x, y, z = rotate(t[1], [1, 0, 0]), rotate(t[1], [0, 1, 0]), rotate(t[1], [0, 0, 1])
    return {'rotate': [x[0], y[0], z[0], x[1], y[1], z[1], x[2], y[2], z[2]], 'translate': list(t[0]), 'scale': t[2][0]}


def packed(t):
    return [round(c, 5) for c in t[0]] + [round(c, 6) for c in t[1]] + [round(t[2][0], 5)]


def unpacked(row):
    return list(row[0:3]), list(row[3:7]), [row[7]] * 3


# --- the scene being fitted ------------------------------------------------------------------------

def side_prefix(side):
    return 'LArm_' if side == 'left' else 'RArm_'


def weapon_local(pose, name):
    bones = {bone['name']: bone for bone in pose['bones']}
    return compose(inverse(transform(bones['Weapon']['world'])), transform(bones[name]['world']))


def finger_locals(pose, side):
    """The fifteen finger joints in their parents' frames, thumb to little finger, base to tip."""
    bones = {bone['name']: transform(bone['local']) for bone in pose['bones']}
    return [bones['%sFinger%d%d' % (side_prefix(side), finger, segment)] for finger in range(1, 6) for segment in range(1, 4)]


def magazine_box(body_meshes, outward):
    """A frame every magazine has: x leaves the weapon, y is the weapon's forward, z is across.

    Its origin is the middle of the magazine's outer end, so hands taken from
    magazines of other lengths land on the end a hand holds.
    """
    x = unit(outward)
    forward = [0.0, 1.0, 0.0]
    slack = sum(forward[i] * x[i] for i in range(3))
    y = unit([forward[i] - slack * x[i] for i in range(3)])
    z = cross(x, y)
    spans = []
    for axis in (x, y, z):
        along = [sum(v[i] * axis[i] for i in range(3)) for mesh in body_meshes for v in mesh['vertices']]
        spans.append((min(along), max(along)))
    middle = [spans[0][1], (spans[1][0] + spans[1][1]) / 2, (spans[2][0] + spans[2][1]) / 2]
    origin = [middle[0] * x[i] + middle[1] * y[i] + middle[2] * z[i] for i in range(3)]
    return (origin, quaternion(x, y, z), [1.0, 1.0, 1.0]), [round(high - low, 2) for low, high in spans]


class Field:
    """Signed distance to a surface on a grid filled as it is asked for."""

    def __init__(self, meshes, region=None):
        self.surface = Surface(meshes, region=region)
        self.nodes = {}

    def node(self, key):
        value = self.nodes.get(key)
        if value is None:
            d = self.surface.distance([key[0] * FIELD_CELL, key[1] * FIELD_CELL, key[2] * FIELD_CELL], reach=FIELD_REACH)
            value = self.nodes[key] = FIELD_REACH if d is None else d
        return value

    def at(self, p):
        x, y, z = p[0] / FIELD_CELL, p[1] / FIELD_CELL, p[2] / FIELD_CELL
        i, j, k = math.floor(x), math.floor(y), math.floor(z)
        u, v, w = x - i, y - j, z - k
        node = self.node
        low = (node((i, j, k)) * (1 - u) + node((i + 1, j, k)) * u) * (1 - v) + (node((i, j + 1, k)) * (1 - u) + node((i + 1, j + 1, k)) * u) * v
        high = (node((i, j, k + 1)) * (1 - u) + node((i + 1, j, k + 1)) * u) * (1 - v) + \
               (node((i, j + 1, k + 1)) * (1 - u) + node((i + 1, j + 1, k + 1)) * u) * v
        return low * (1 - w) + high * w


class Scene:
    """The magazine fit editor's view: the magazine shown clear of the weapon and one hand on it."""

    def __init__(self, editor, side, seated=None):
        """`seated` is the magazine's reference frame while it sits in the weapon."""
        self.editor, self.side, self.seated = editor, side, seated
        simulation = editor.read('simulation_state')
        self.shapes = simulation.get('magazineShapes', [])
        self.rail = simulation.get('magazineRail') or {}
        if not self.shapes:
            raise RuntimeError('This configuration has no magazine pieces assigned; assign them in the Magazines workspace first')
        self.field = None
        self.weapon = None

    def body(self, view, anchor):
        """The weapon around the seated magazine, and the transform that takes a point beside the
        magazine as shown now (reference frame `anchor`) to where it is once the magazine is in."""
        if self.seated is None:
            return None
        to_seat = compose(self.seated, inverse(anchor))
        if self.weapon is None:
            corners = [apply(to_seat, [x, y, z]) for x in (self.low[0], self.high[0]) for y in (self.low[1], self.high[1])
                       for z in (self.low[2], self.high[2])]
            low, high = bounds(corners)
            region = [c - WEAPON_PAD for c in low], [c + WEAPON_PAD for c in high]
            blocks = {shape['block'] for shape in self.shapes}
            self.weapon = Field([m for m in view['geometry']['meshes'] if not m['skinBones'] and m['block'] not in blocks], region=region)
        return self.weapon, to_seat

    def clearance(self, view, anchor):
        """How deep the shown hand would be inside the weapon with the magazine seated (negative is inside)."""
        body = self.body(view, anchor)
        if body is None:
            return None
        deepest = FIELD_REACH
        for name, group in view['groups'].items():
            for v in (group[::3] if name != 'Hand' else [p for p in group if math.dist(p, view['hand'][0]) < 6.0][::3]):
                d = body[0].surface.distance(apply(body[1], v), reach=FIELD_REACH)
                if d is not None and d < deepest:
                    deepest = d
        return round(deepest, 2)

    def look(self):
        """What is displayed now: the hand's frame, its finger joints, its skin and the magazine."""
        geometry = self.editor.read('holding_geometry')
        pose = self.editor.read('pose')
        blocks = {shape['block'] for shape in self.shapes}
        magazine = [m for m in geometry['meshes'] if m['block'] in blocks]
        if not magazine:
            raise RuntimeError('The magazine is not drawn in the fit editor')
        if self.field is None:
            self.field = Field(magazine)
            body = [m for m in magazine if not next(s for s in self.shapes if s['block'] == m['block']).get('cartridge')] or magazine
            self.box, self.size = magazine_box(body, self.rail.get('outwardInWeapon') or [0.0, 0.0, -1.0])
            self.low, self.high = bounds([v for m in magazine for v in m['vertices']])
        hand = weapon_local(pose, side_prefix(self.side) + 'Hand')
        return {'geometry': geometry, 'pose': pose, 'magazine': magazine, 'hand': hand, 'fingers': finger_locals(pose, self.side),
                'groups': hand_groups(geometry, pose, self.side)}

    def anchor(self, pose, index):
        part = next((p for p in pose['parts'] if p['index'] == index), None)
        if part is None:
            raise RuntimeError('The magazine\'s reference part has no frame')
        weapon = next(transform(bone['world']) for bone in pose['bones'] if bone['name'] == 'Weapon')
        return compose(inverse(weapon), transform(part['world']))


def skin_points(view):
    """A spread of the hand's skin points in the wrist's frame, by finger and palm."""
    hand = view['hand']
    groups = view['groups']
    toward_fingers = unit(unapply(hand, sum_points([groups.get('Finger31', []), groups.get('Finger21', [])]) or apply(hand, [1.0, 0.0, 0.0])))
    points = {}
    for name, vertices in groups.items():
        local = [unapply(hand, v) for v in vertices]
        if name == 'Hand':
            # The wrist joint also collects the forearm's skin; only the palm side takes part.
            local = [p for p in local if sum(p[i] * toward_fingers[i] for i in range(3)) > -1.0]
        limit = PER_PALM if name == 'Hand' else PER_SEGMENT
        step = max(1, len(local) // limit)
        points[name] = local[::step][:limit]
    return points


def sum_points(groups):
    every = [v for group in groups for v in group]
    if not every:
        return None
    return [sum(v[i] for v in every) / len(every) for i in range(3)]


# --- the search ------------------------------------------------------------------------------------

def refine(points, start, field, targets=None, body=None, travel=SEARCH_TRAVEL, turn_limit=SEARCH_TURN):
    """The wrist placement near `start` whose skin sits on the magazine as intended.

    Without `targets` every finger and the palm are drawn onto the surface.
    With them (a borrowed fit's own closest approach per finger and palm) each
    part is drawn to where it sat on the fit's own magazine, and a part that
    stood clear there is left free, so a pinch stays a pinch. Nothing may sink
    deep into the magazine. `body` is (field of the weapon, transform to the
    seated magazine): the hand is kept out of the weapon as it will be when the
    magazine is in. The placement stays within `travel` units and `turn_limit`
    degrees of `start`, so the hand keeps the side and direction it had.
    """
    placed = {name: [apply(start, p) for p in group] for name, group in points.items()}
    palm = placed.get('Hand') or [start[0]]
    pivot = [sum(p[i] for p in palm) / len(palm) for i in range(3)]
    fingers = [[name for name in ('Finger%d1' % n, 'Finger%d2' % n, 'Finger%d3' % n) if name in placed] for n in range(1, 6)]
    # On its own a hand is drawn onto the surface by its fingers; a saved fit's palm rarely touches a magazine.
    wanted = [REST_GAP] * 5 + [None]
    if targets:
        # A part that stood more than a unit clear on the borrowed fit's magazine has no place to be.
        wanted = [None if value is None or value > FREE_ABOVE else max(value, -SINK) for value in targets]
    seat = body and body[1]

    def rotation(x):
        return qmul(turn([0, 0, 1], x[5]), qmul(turn([0, 1, 0], x[4]), turn([1, 0, 0], x[3])))

    def cost(x):
        qx, qy, qz, qw = rotation(x)
        inside_weapon = [FIELD_REACH]

        def nearest(group):
            best = FIELD_REACH
            for p in group:
                vx, vy, vz = p[0] - pivot[0], p[1] - pivot[1], p[2] - pivot[2]
                tx, ty, tz = 2 * (qy * vz - qz * vy), 2 * (qz * vx - qx * vz), 2 * (qx * vy - qy * vx)
                moved = (vx + qw * tx + (qy * tz - qz * ty) + pivot[0] + x[0], vy + qw * ty + (qz * tx - qx * tz) + pivot[1] + x[1],
                         vz + qw * tz + (qx * ty - qy * tx) + pivot[2] + x[2])
                d = field.at(moved)
                if d < best:
                    best = d
                if seat:
                    w = body[0].at(apply(seat, moved))
                    if w < inside_weapon[0]:
                        inside_weapon[0] = w
            return best

        total = 0.02 * (x[0] ** 2 + x[1] ** 2 + x[2] ** 2) + 0.0004 * (x[3] ** 2 + x[4] ** 2 + x[5] ** 2)
        for number, names in enumerate(fingers):
            segments = [nearest(placed[name]) for name in names]
            if not segments:
                continue
            whole = min(segments)
            total += 6 * max(0.0, -whole - SINK) ** 2
            if wanted[number] is not None:
                total += (whole - wanted[number]) ** 2 if targets else max(0.0, whole - wanted[number]) ** 2 + 0.5 * max(0.0, segments[-1] - REST_GAP) ** 2
        if 'Hand' in placed:
            d = nearest(placed['Hand'])
            total += 6 * max(0.0, -d - SINK) ** 2
            if wanted[5] is not None:
                total += (d - wanted[5]) ** 2 if targets else max(0.0, d - wanted[5]) ** 2
        return total + 4 * max(0.0, -inside_weapon[0] - WEAPON_SINK) ** 2

    x = [0.0] * 6
    steps = [0.8, 0.8, 0.8, 8.0, 8.0, 8.0]
    limits = [travel] * 3 + [turn_limit] * 3
    best = first = cost(x)
    while steps[0] > 0.04:
        improved = False
        for i in range(6):
            for sign in (1, -1):
                trial = list(x)
                trial[i] += sign * steps[i]
                if abs(trial[i]) > limits[i]:
                    continue
                value = cost(trial)
                if value < best - 1e-6:
                    x, best, improved = trial, value, True
                    break
        if not improved:
            steps = [s / 2 for s in steps]
    q = rotation(x)
    arm = rotate(q, [start[0][i] - pivot[i] for i in range(3)])
    placement = [arm[i] + pivot[i] + x[i] for i in range(3)], qmul(q, start[1]), list(start[2])
    return placement, {'costBefore': round(first, 3), 'costAfter': round(best, 3), 'moved': round(math.hypot(*x[0:3]), 2),
                       'turned': round(math.hypot(*x[3:6]), 1)}


# --- the fit library: hands that hold on other weapons ------------------------------------------------

def library_path(store):
    return Path(store) / 'fits.json'


def read_library(store):
    path = library_path(store)
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'fits': {}}


class Held:
    """Operate with the seated magazine taken by one hand and pulled clear of the weapon."""

    def __init__(self, editor, side):
        self.editor, self.side = editor, side

    def __enter__(self):
        editor = self.editor
        editor.edit('workspace', {'index': 5})
        editor.edit('simulate', {'operation': 'reset'})
        editor.edit('interaction_hand', {'hand': self.side})
        self.anchor = (editor.read('state', {'fields': ['magazineEditor']})['magazineEditor'] or {}).get('anchor')
        self.seated = seated_frame(editor)
        editor.edit('simulate', {'operation': 'grab_magazine'})
        rail = editor.read('simulation_state').get('magazineRail') or {}
        editor.edit('simulate', {'operation': 'move_magazine', 'distance': rail.get('travel', 0) + rail.get('releaseMargin', 0) + 1})
        self.grip = editor.read('simulation_state').get('magazineGrip', {})
        return self

    def __exit__(self, *exception):
        self.editor.edit('simulate', {'operation': 'release_magazine'})
        self.editor.edit('simulate', {'operation': 'reset'})
        return False


def remember_fits(command, store):
    """Add the open weapon's magazine fits to the fit library, one per hand that holds.

    The hand is recorded in the magazine's own box frame with its finger joints
    and its measured contact, so a later weapon can start from it.
    """
    editor = Editor(command)
    status = editor.idle()
    library = read_library(store)
    listed = editor.read('state', {'fields': ['weapons', 'weaponIndex']})
    weapon_name = listed['weapons'][listed['weaponIndex']]['name']
    kept = {}
    for side in ('left', 'right'):
        try:
            with Held(editor, side) as held:
                if not held.grip.get('applied'):
                    kept[side] = 'no fit applied'
                    continue
                scene = Scene(editor, side, held.seated)
                view = scene.look()
                report = contact(view['geometry'], view['pose'], view['magazine'], side, scene.field.surface)
                fit = editor.read('state', {'fields': ['magazineEditor']})['magazineEditor']
                in_weapon = scene.clearance(view, scene.anchor(view['pose'], held.anchor))
        except RuntimeError as error:
            kept[side] = str(error)
            continue
        key = '%s|%s|%s' % (status['weapon'], fit.get('ownerRecord', ''), side)
        numbers = {name: [part['whole'].get('gap'), part['whole'].get('deepest')] for name, part in report['fingers'].items()}
        numbers['palm'] = [report['palm'].get('gap'), report['palm'].get('deepest')]
        numbers['tips'] = [report['fingers'][finger]['tip'].get('deepest') for finger in FINGERS]
        # Every saved fit is kept with its measured gaps; which of them count as holding is judged when they are used.
        library['fits'][key] = {'weapon': status['weapon'], 'name': weapon_name, 'magazine': fit.get('magazine'), 'hand': side,
                                'fit': held.grip.get('name'),
                                'box': scene.size, 'handInBox': packed(compose(inverse(scene.box), view['hand'])),
                                'fingers': [packed(joint) for joint in view['fingers']], 'contact': numbers, 'inWeaponSeated': in_weapon}
        kept[side] = '%s %s seated %s' % (report['verdict'], json.dumps({name: numbers[name][1] for name in FINGERS + ('palm',)}), in_weapon)
    path = library_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(library, indent=1), encoding='utf-8')
    return kept


def holds(numbers):
    """The verdict of contact() from a fit's stored closest approaches."""
    states = [part_state(numbers[part][1]) for part in FINGERS + ('palm',)]
    return hold_verdict(states[:5].count('touching'), 'buried' in states) == 'holds'


def library_starts(store, size, side, weapon, count=5):
    """Fits from other weapons whose magazine is closest in cross-section, one per weapon."""
    rows = [row for row in read_library(store)['fits'].values() if row['hand'] == side and row['weapon'] != weapon and holds(row['contact'])
            and row['handInBox'][0] / max(row['box'][0], 0.1) >= WRONG_END]
    rows.sort(key=lambda row: abs(row['box'][1] - size[1]) + abs(row['box'][2] - size[2]) + 0.15 * abs(row['box'][0] - size[0]))
    chosen, seen = [], set()
    for row in rows:
        # Sibling weapons share one fit; each distinct hand is offered once.
        same = tuple(round(value, 1) for value in row['handInBox'][:7])
        if same in seen:
            continue
        seen.add(same)
        chosen.append(row)
        if len(chosen) == count:
            break
    return chosen


# --- candidates --------------------------------------------------------------------------------------

def open_fit_editor(editor, side):
    """Operate, then the magazine fit editor for this hand: the magazine shown clear of the weapon."""
    editor.edit('workspace', {'index': 5})
    editor.edit('simulate', {'operation': 'reset'})
    editor.edit('interaction_hand', {'hand': side})
    seated = seated_frame(editor)
    editor.edit('configure_magazine', {})
    state = editor.read('state', {'fields': ['magazineEditor', 'handPoses', 'weapons', 'weaponIndex', 'actions']})
    fit = state['magazineEditor']
    if not fit:
        raise RuntimeError('No magazine is selected on this weapon')
    fit['seated'] = seated
    if not fit.get('editing'):
        # Nothing is assigned yet: a draft has to start from a saved hand of this side. Which one does
        # not matter, since every candidate sets the wrist and all finger joints itself.
        pose = next((p for p in state['handPoses'] if p['hand'] == side), None)
        if pose is None:
            # An empty pose library: the hand as it rests on the weapon becomes its first entry.
            name = '%s %s hand at rest' % (state['weapons'][state['weaponIndex']]['name'], side)
            editor.edit('workspace', {'index': 2, 'handsPage': 0})
            editor.edit('save_hand_pose', {'hand': side, 'name': name, 'anchor': fit['anchor']})
            pose = [p for p in editor.read('state', {'fields': ['handPoses']})['handPoses'] if p['name'] == name][-1]
            editor.edit('workspace', {'index': 5})
            editor.edit('simulate', {'operation': 'reset'})
            editor.edit('interaction_hand', {'hand': side})
            editor.edit('configure_magazine', {})
        editor.edit('start_grip', {'poseId': pose['id'], 'anchor': fit['anchor'], 'captured': True})
        fit = editor.read('state', {'fields': ['magazineEditor']})['magazineEditor']
        if not fit.get('editing'):
            raise RuntimeError('The fit editor did not open a draft: ' + str(fit.get('error') or 'unknown reason'))
        fit['assignment'] = 'none'
        fit['seated'] = seated
    return state, fit


def seated_frame(editor):
    """The selected magazine's reference frame in weapon space, read in Operate while it sits in the weapon."""
    fit = editor.read('state', {'fields': ['magazineEditor']})['magazineEditor']
    if not fit:
        return None
    pose = editor.read('pose')
    part = next((p for p in pose['parts'] if p['index'] == fit['anchor']), None)
    if part is None:
        return None
    weapon = next(transform(bone['world']) for bone in pose['bones'] if bone['name'] == 'Weapon')
    return compose(inverse(weapon), transform(part['world']))


def place(editor, scene, anchor, hand, fingers, name):
    """Show a hand given in weapon space; returns what is then displayed."""
    editor.edit('set_grip', {'transform': pose_json(compose(inverse(anchor), hand)), 'fingers': [pose_json(joint) for joint in fingers],
                             'name': name})
    view = scene.look()
    off = math.dist(view['hand'][0], hand[0])
    if off > 0.05:
        raise RuntimeError('The arm does not reach this placement (the hand stopped %.2f units short)' % off)
    return view


def native_start(editor, side, anchor_index):
    """The reload's own hand at the moment its fingers are closest around the magazine, if this hand carries it."""
    recipe = editor.read('reload')
    simulation = editor.read('simulation_state')
    track = simulation.get('setup', {}).get('magazineTrack', -1)
    blocks = {shape['block'] for shape in simulation.get('magazineShapes', [])}
    if track < 0 or not recipe.get('tracks'):
        return None
    reload = Reload(recipe)
    best = None
    with NativeClip(editor, reload) as clip:
        for frame in range(0, reload.frames, max(2, round(reload.frames / 45))):
            if reload.travel(track, frame) <= 3.0 or min(abs(s) for s in reload.poses[track][frame][2]) <= 0.5:
                continue
            clip.at(frame)
            pose = editor.read('pose')
            hand = weapon_local(pose, side_prefix(side) + 'Hand')
            palm_to_part = math.dist(hand[0], reload.poses[track][frame][0])
            if palm_to_part < HOLD_REACH * 2 and (best is None or palm_to_part < best[0]):
                best = (palm_to_part, frame)
        if best is None:
            return None
        # Around the closest approach, the frame whose skin sits best on the magazine.
        tried = []
        for frame in sorted({max(0, best[1] + offset) for offset in (-6, -3, 0, 3, 6)}):
            if frame >= reload.frames or reload.travel(track, frame) <= 3.0:
                continue
            clip.at(frame)
            geometry = editor.read('holding_geometry')
            meshes = [m for m in geometry['meshes'] if m['block'] in blocks]
            if not meshes:
                continue
            pose = editor.read('pose')
            report = contact(geometry, pose, meshes, side)
            part = next((p for p in pose['parts'] if p['index'] == anchor_index), None)
            if part is None:
                continue
            weapon = next(transform(bone['world']) for bone in pose['bones'] if bone['name'] == 'Weapon')
            hand_in_anchor = compose(inverse(transform(part['world'])), compose(weapon, weapon_local(pose, side_prefix(side) + 'Hand')))
            tried.append((hold_score(report), reload.seconds(frame), hand_in_anchor, finger_locals(pose, side)))
    if not tried:
        return None
    score, seconds, hand_in_anchor, fingers = max(tried, key=lambda row: row[0])
    return {'source': 'this weapon\'s own reload at %.2f s' % seconds, 'handInAnchor': hand_in_anchor, 'fingers': fingers}


def marks(view, report):
    """A coloured dot on each finger end: green touching, yellow gap, red sunk. The palm has none,
    since the palm of a good magazine hold usually stands clear."""
    result = []
    for number, finger in enumerate(FINGERS, start=1):
        end = sum_points([view['groups'].get('Finger%d3' % number, [])])
        if end:
            result.append({'point': end, 'color': STATE_COLOUR[report['fingers'][finger]['whole']['state']], 'label': LETTER[finger]})
    return result


def pictures(editor, scene, view, report, caption):
    """The displayed hand on the magazine from three sides, framed on the hand, with contact dots."""
    hand_points = [v for name, group in view['groups'].items() if name != 'Hand' for v in group[::7]] + [view['hand'][0]]
    low, high = bounds(hand_points + [scene.low, scene.high])
    focus = [[x, y, z] for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])]
    dots = marks(view, report)
    return [editor.read('render', {'view': name, 'focus': focus, 'marks': dots, 'caption': '%s   %s view' % (caption, name),
                                   'preserveCamera': True})['path'] for name in VIEWS]


def brief(report):
    return {k: report[k] for k in ('verdict', 'touching', 'hovering', 'buried')}


def judged(report, scene, hand_frame, in_weapon, proposed=True):
    """A measured hand as a candidate: the contact, where it sits along the magazine, what is wrong and a score.

    Contact alone cannot tell a hand on the magazine's base from one closed on
    the end that goes into the weapon, or a finger lying on the magazine from
    one that only touches it at the knuckle and points away. Those two readings
    rank and mark a proposed hand only: a saved fit keeps its contact verdict,
    since one in twenty accepted fits has its wrist toward the weapon.
    """
    along = round(unapply(scene.box, hand_frame[0])[0] / max(scene.size[0], 0.1), 2)
    sticking = [finger for finger in FINGERS if report['fingers'][finger]['whole']['state'] == 'touching' and
                (report['fingers'][finger]['tip'].get('deepest') is None or report['fingers'][finger]['tip']['deepest'] > STICKS_OUT)]
    verdict = report['verdict']
    if proposed and along < WRONG_END:
        verdict = 'holds the end that goes into the weapon'
    elif proposed and verdict == 'holds' and len(report['touching']) - len(sticking) < 3:
        verdict = 'fingers point away from the magazine'
    score = hold_score(report) - len(sticking) - (4 if along < WRONG_END else 0)
    if in_weapon is not None:
        score -= 2 * max(0.0, -in_weapon - WEAPON_SINK)
    return {'verdict': verdict, 'touching': report['touching'], 'hovering': report['hovering'], 'buried': report['buried'],
            'pointingAway': sticking, 'alongMagazine': along, 'inWeaponSeated': in_weapon, 'score': round(score, 2)}


def solve(command, store, hand, count=4, native=True):
    """Work out hand fits for the selected magazine and show them side by side.

    Leaves the fit editor open on the best candidate, unsaved. The candidates are
    written to <store>/candidates.json for choose().
    """
    if hand not in ('left', 'right'):
        raise ValueError('hand is left or right')
    editor = Editor(command)
    status = editor.idle()
    own = None
    if native:
        # The reload is scrubbed before the fit editor opens, since scrubbing leaves it.
        editor.edit('workspace', {'index': 5})
        editor.edit('simulate', {'operation': 'reset'})
        editor.edit('interaction_hand', {'hand': hand})
        selected = editor.read('state', {'fields': ['magazineEditor']})['magazineEditor']
        if selected:
            own = native_start(editor, hand, selected['anchor'])
    state, fit = open_fit_editor(editor, hand)
    scene = Scene(editor, hand, fit['seated'])
    view = scene.look()
    anchor = scene.anchor(view['pose'], fit['anchor'])
    weapon_name = state['weapons'][state['weaponIndex']]['name']
    starts = []
    if fit.get('assignment') != 'none':
        starts.append({'source': 'the fit assigned now (%s)' % (fit.get('name') or fit.get('assignment')), 'hand': view['hand'],
                       'fingers': view['fingers'], 'assigned': True})
    for row in library_starts(store, scene.size, hand, status['weapon']):
        starts.append({'source': 'the fit of %s (%s)' % (row.get('name') or row['weapon'], row['weapon']),
                       'hand': compose(scene.box, unpacked(row['handInBox'])), 'fingers': [unpacked(joint) for joint in row['fingers']],
                       'targets': [row['contact'][part][1] for part in FINGERS + ('palm',)]})
    if own:
        starts.append({'source': own['source'], 'hand': compose(anchor, own['handInAnchor']), 'fingers': own['fingers']})
    if not starts:
        raise RuntimeError('Nothing to start from: no fit is assigned, the fit library has no %s hand and the reload does not show this '
                           'hand carrying the magazine. Build the fit library from the authored weapons first.' % hand)
    candidates = []
    for start in starts:
        try:
            shown = place(editor, scene, anchor, start['hand'], start['fingers'], 'Fit search')
            if start.get('assigned'):
                # The fit in use is shown as it is, so the others can be compared with it.
                placement, search = start['hand'], {'unchanged': True}
            else:
                placement, search = refine(skin_points(shown), start['hand'], scene.field, start.get('targets'), scene.body(shown, anchor))
                shown = place(editor, scene, anchor, placement, start['fingers'], 'Fit search')
        except RuntimeError as error:
            candidates.append({'source': start['source'], 'problem': str(error)})
            continue
        report = contact(shown['geometry'], shown['pose'], shown['magazine'], hand, scene.field.surface)
        candidates.append({'source': start['source'], **judged(report, scene, shown['hand'], scene.clearance(shown, anchor)), 'search': search,
                           'handInAnchor': packed(compose(inverse(anchor), placement)), 'fingers': [packed(j) for j in start['fingers']]})
    usable = sorted((c for c in candidates if 'problem' not in c), key=lambda c: -c['score'])[:count]
    if not usable:
        raise RuntimeError('No starting hand could be placed: ' + '; '.join(c['problem'] for c in candidates))
    panels = []
    for number, candidate in enumerate(usable, start=1):
        candidate['number'] = number
        shown = place(editor, scene, anchor, compose(anchor, unpacked(candidate['handInAnchor'])),
                      [unpacked(j) for j in candidate['fingers']], 'Fit search')
        report = contact(shown['geometry'], shown['pose'], shown['magazine'], hand, scene.field.surface)
        for path, name in zip(pictures(editor, scene, shown, report, 'Candidate %d: %s' % (number, candidate['verdict'])), VIEWS):
            panels.append({'path': path, 'label': '%d  %s' % (number, name)})
    sheet = editor.read('picture_sheet', {'panels': panels, 'columns': len(VIEWS), 'cellWidth': 520,
                                          'title': '%s  %s  %s hand   green = touching, yellow = gap, red = sunk into the magazine'
                                                   % (weapon_name, fit.get('magazine', ''), hand)})
    best = usable[0]
    place(editor, scene, anchor, compose(anchor, unpacked(best['handInAnchor'])), [unpacked(j) for j in best['fingers']], 'Fit search')
    record = {'weapon': status['weapon'], 'magazine': fit.get('ownerRecord'), 'hand': hand, 'anchor': fit['anchor'], 'candidates': usable}
    path = Path(store) / 'candidates.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding='utf-8')
    return {'weapon': weapon_name, 'magazine': fit.get('magazine'), 'hand': hand, 'sheet': sheet['path'],
            'candidates': [{k: c[k] for k in ('number', 'source', 'verdict', 'touching', 'hovering', 'buried', 'pointingAway',
                                              'alongMagazine', 'inWeaponSeated', 'score')} for c in usable],
            'recommended': 1,
            'next': 'Look at the sheet: one row per candidate, three views each. Call choose_magazine_fit with the number of the row '
                    'whose hand wraps the magazine with no red dot. If the numbers already say "holds" for row 1, choose 1.',
            'notPlaced': [c for c in candidates if 'problem' in c]}


def sheet(editor, rows, title):
    """Rows of three views, one row per labelled state, as one picture."""
    panels = [{'path': path, 'label': '%s  %s' % (label, name)} for label, paths in rows for path, name in zip(paths, VIEWS)]
    return editor.read('picture_sheet', {'panels': panels, 'columns': len(VIEWS), 'cellWidth': 520, 'title': title})['path']


def show(command, hands=('left', 'right')):
    """Each hand holding the magazine in Operate, as numbers and as one picture."""
    editor = Editor(command)
    editor.idle()
    rows, result = [], {}
    for side in hands:
        with Held(editor, side) as held:
            if not held.grip.get('applied'):
                result[side] = {'verdict': 'no fit', 'problem': str(held.grip.get('error') or 'no fit is assigned for this hand')}
                continue
            scene = Scene(editor, side, held.seated)
            view = scene.look()
            report = contact(view['geometry'], view['pose'], view['magazine'], side, scene.field.surface)
            rows.append(('%s hand: %s' % (side, report['verdict']), pictures(editor, scene, view, report, '%s hand' % side)))
            result[side] = {**judged(report, scene, view['hand'], scene.clearance(view, scene.anchor(view['pose'], held.anchor)), proposed=False),
                            'fit': held.grip.get('name')}
    if rows:
        result['sheet'] = sheet(editor, rows, 'Magazine held in Operate   green = touching, yellow = gap, red = sunk into the magazine')
    return result


MOVES = {'up': (0, -1), 'down': (0, 1), 'forward': (1, 1), 'back': (1, -1), 'closer': None, 'away': None, 'twist left': (0, 1), 'twist right': (0, -1)}
AMOUNTS = {'small': (0.4, 5.0), 'medium': (1.0, 12.0), 'large': (2.5, 25.0)}


def nudge(command, hand, move, amount='small'):
    """Move the saved fit one step in plain words, measure before and after, and keep it unless it got worse.

    up and down run along the magazine (up is the end with the cartridges),
    forward is toward the muzzle, closer brings the palm onto the magazine,
    twist turns the hand around the magazine's length.
    """
    if move not in MOVES or amount not in AMOUNTS:
        raise ValueError('move is one of %s; amount is one of %s' % (', '.join(MOVES), ', '.join(AMOUNTS)))
    editor = Editor(command)
    editor.idle()
    state, fit = open_fit_editor(editor, hand)
    if fit.get('assignment') == 'none':
        raise RuntimeError('This hand has no fit yet; use solve_magazine_fit and choose_magazine_fit first')
    scene = Scene(editor, hand)
    view = scene.look()
    anchor = scene.anchor(view['pose'], fit['anchor'])
    before = contact(view['geometry'], view['pose'], view['magazine'], hand, scene.field.surface)
    rows = [('before: %s' % before['verdict'], pictures(editor, scene, view, before, 'Before'))]
    axes = [rotate(scene.box[1], axis) for axis in ([1, 0, 0], [0, 1, 0], [0, 0, 1])]
    distance, degrees = AMOUNTS[amount]
    hand_now = view['hand']
    if move.startswith('twist'):
        q = turn(axes[0], degrees * MOVES[move][1])
        # Around the magazine's own length, through the middle of its outer end.
        arm = rotate(q, [hand_now[0][i] - scene.box[0][i] for i in range(3)])
        moved = [arm[i] + scene.box[0][i] for i in range(3)], qmul(q, hand_now[1]), hand_now[2]
    else:
        if MOVES[move] is None:
            palm = sum_points([[v for v in view['groups'].get('Hand', []) if math.dist(v, hand_now[0]) < 5.0]]) or hand_now[0]
            middle = [(scene.low[i] + scene.high[i]) / 2 for i in range(3)]
            offset = [middle[i] - palm[i] for i in range(3)]
            along = sum(offset[i] * axes[0][i] for i in range(3))
            direction = unit([offset[i] - along * axes[0][i] for i in range(3)])
            sign = 1 if move == 'closer' else -1
        else:
            direction, sign = axes[MOVES[move][0]], MOVES[move][1]
        moved = [hand_now[0][i] + sign * distance * direction[i] for i in range(3)], hand_now[1], hand_now[2]
    shown = place(editor, scene, anchor, moved, view['fingers'], fit.get('name') or 'Magazine hold')
    after = contact(shown['geometry'], shown['pose'], shown['magazine'], hand, scene.field.surface)
    rows.append(('after: %s' % after['verdict'], pictures(editor, scene, shown, after, 'After')))
    worse = len(after['buried']) > len(before['buried']) or len(after['touching']) < len(before['touching']) - 1
    if worse:
        place(editor, scene, anchor, hand_now, view['fingers'], fit.get('name') or 'Magazine hold')
    else:
        editor.edit('use_magazine_grip', {'scope': 'magazine'})
    picture = sheet(editor, rows, '%s hand moved %s (%s)   green = touching, yellow = gap, red = sunk' % (hand, move, amount))
    return {'hand': hand, 'move': move, 'amount': amount, 'kept': not worse, 'before': brief(before), 'after': brief(after), 'sheet': picture,
            'next': 'The move was undone because it lost contact or sank a finger; try the opposite direction or a smaller amount.' if worse
                    else 'Saved. Look at the after row; nudge again only if a dot is still yellow or red.'}


def choose(command, store, hand, number):
    """Save one of solve()'s candidates as this magazine's fit for the hand, and measure it held in Operate."""
    path = Path(store) / 'candidates.json'
    record = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None
    editor = Editor(command)
    status = editor.idle()
    if not record or record['weapon'] != status['weapon'] or record['hand'] != hand:
        raise RuntimeError('No candidates for the %s hand of this weapon; call solve_magazine_fit first' % hand)
    candidate = next((c for c in record['candidates'] if c['number'] == number), None)
    if candidate is None:
        raise RuntimeError('Candidate numbers are 1 to %d' % len(record['candidates']))
    state, fit = open_fit_editor(editor, hand)
    if fit.get('ownerRecord') != record['magazine']:
        raise RuntimeError('Another magazine is selected than the one the candidates were worked out for; call solve_magazine_fit again')
    scene = Scene(editor, hand)
    view = scene.look()
    anchor = scene.anchor(view['pose'], fit['anchor'])
    name = '%s %s %s hold' % (state['weapons'][state['weaponIndex']]['name'], fit.get('magazine', 'magazine'), hand)
    place(editor, scene, anchor, compose(anchor, unpacked(candidate['handInAnchor'])), [unpacked(j) for j in candidate['fingers']], name)
    editor.edit('use_magazine_grip', {'scope': 'magazine'})
    with Held(editor, hand) as held:
        if not held.grip.get('applied'):
            raise RuntimeError('The saved fit is not applied when the magazine is held: ' + str(held.grip.get('error')))
        shown = scene.look()
        report = contact(shown['geometry'], shown['pose'], shown['magazine'], hand)
    return {'saved': name, 'hand': hand, 'candidate': number, 'heldInOperate': brief(report),
            'next': 'Call next_step. A hand without a fit of its own takes the mirror of this one; next_step measures it and asks '
                    'for that hand only if the mirror does not hold.'}

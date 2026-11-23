"""Numeric perception of the loaded weapon.

A client that cannot look at the viewport still has to know what the weapon is
made of, what moves during its reload, which hand carries the magazine and
whether a saved hand actually touches the part. Everything here is measured
from data the editor already serves: the learned reload (every animated part's
weapon-local pose at every frame), the visible geometry in weapon space and the
skeleton pose. Nothing is authored and nothing is saved; the calls that scrub
the native clip put the workspace, action and time back afterwards.

Lengths are game units (one unit is about 1.43 cm). Points are weapon-local
unless a name says otherwise.
"""
import math
import time

MOVING_TRAVEL = 0.05      # a part that travels less is at rest
MOVING_TURN = 0.5         # degrees
RIGID_RESIDUAL = 0.05     # a mesh follows a track when the track's motion explains its vertices this well
HOLD_REACH = 6.0          # a palm this close to a part's surface is holding or working it
CARRIED = 3.0             # a part this far from where the clip starts it has left its seat
WITH_PART = 0.15          # seconds a palm must stay at an unseated part to be moving it
TOUCH = 0.3               # a hand surface this close to the part counts as touching
BURIED = -0.7             # a hand surface this far inside the part is clearly sunk into it
FINGERS = ('thumb', 'index', 'middle', 'ring', 'little')
_NEAREST_JOINT = {}       # per hand mesh, the joint each vertex is nearest to


# --- transforms: (translation[3], quaternion[x, y, z, w], scale[3]) ---------------------------

def transform(value):
    """A track row of ten numbers, or a pose object with translate, rotation and scale."""
    if isinstance(value, dict):
        scale = value.get('scale', 1)
        scale = list(scale) if isinstance(scale, (list, tuple)) else [scale] * 3
        return list(value['translate']), list(value['rotation']), scale
    return list(value[0:3]), list(value[3:7]), list(value[7:10])


def rotate(q, v):
    x, y, z, w = q
    tx, ty, tz = 2 * (y * v[2] - z * v[1]), 2 * (z * v[0] - x * v[2]), 2 * (x * v[1] - y * v[0])
    return [v[0] + w * tx + (y * tz - z * ty), v[1] + w * ty + (z * tx - x * tz), v[2] + w * tz + (x * ty - y * tx)]


def apply(t, point):
    r = rotate(t[1], [point[0] * t[2][0], point[1] * t[2][1], point[2] * t[2][2]])
    return [r[0] + t[0][0], r[1] + t[0][1], r[2] + t[0][2]]


def unapply(t, point):
    q = t[1]
    r = rotate([-q[0], -q[1], -q[2], q[3]], [point[0] - t[0][0], point[1] - t[0][1], point[2] - t[0][2]])
    return [r[i] / t[2][i] if abs(t[2][i]) > 1e-9 else 0.0 for i in range(3)]


def turn_degrees(a, b):
    return 2 * math.degrees(math.acos(min(1.0, abs(sum(a[i] * b[i] for i in range(4))))))


def visible(t):
    return min(abs(s) for s in t[2]) > 0.5


def rounded(values, digits=2):
    return [round(v, digits) for v in values]


# --- the learned reload: what moves, when, how far ---------------------------------------------

class Reload:
    def __init__(self, recipe):
        self.duration = recipe['duration']
        self.frames = recipe['frames']
        self.source = recipe.get('source', {})
        self.tracks = recipe['tracks']
        self.poses = [[transform(row) for row in track['poses']] for track in self.tracks]

    def seconds(self, frame):
        return self.duration * frame / (self.frames - 1)

    def frame(self, seconds):
        return max(0, min(self.frames - 1, round(seconds / self.duration * (self.frames - 1))))

    def name(self, track):
        return self.tracks[track]['anchor'].get('name') or self.tracks[track]['label']

    def moved(self, track, frame, point):
        """Where a point that rests at `point` is at `frame`, carried by `track`."""
        return apply(self.poses[track][frame], unapply(self.poses[track][0], point))

    def rested(self, track, frame, point):
        """The rest position of a point seen at `point` at `frame`, carried by `track`."""
        return apply(self.poses[track][0], unapply(self.poses[track][frame], point))

    def travel(self, track, frame):
        return math.dist(self.poses[track][frame][0], self.poses[track][0][0])

    def motion(self, track):
        poses = self.poses[track]
        rest = poses[0]
        travel = [math.dist(p[0], rest[0]) for p in poses]
        turn = [turn_degrees(p[1], rest[1]) for p in poses]
        away = [i for i in range(len(poses)) if travel[i] > MOVING_TRAVEL or turn[i] > MOVING_TURN]
        if not away:
            return None
        spans, start, previous = [], away[0], away[0]
        for i in away[1:]:
            if i != previous + 1:
                spans.append((start, previous))
                start = i
            previous = i
        spans.append((start, previous))
        peak = max(range(len(poses)), key=lambda i: (travel[i], turn[i]))
        path = sum(math.dist(poses[i][0], poses[i - 1][0]) for i in range(1, peak + 1))
        delta = [poses[peak][0][i] - rest[0][i] for i in range(3)]
        length = math.hypot(*delta)
        hidden = [i for i in range(len(poses)) if not visible(poses[i])]
        result = {
            'track': track, 'name': self.name(track), 'label': self.tracks[track]['label'],
            'drawsGeometry': bool(self.tracks[track].get('surface')),
            'travel': round(travel[peak], 2), 'turnDegrees': round(max(turn), 1),
            'peakSeconds': round(self.seconds(peak), 3), 'peakFrame': peak,
            'awaySeconds': [[round(self.seconds(a), 3), round(self.seconds(b), 3)] for a, b in spans],
            'direction': rounded([d / length for d in delta], 3) if length > MOVING_TRAVEL else None,
            'straightness': round(length / path, 2) if path > MOVING_TRAVEL else None,
            'endsAtRest': travel[-1] <= MOVING_TRAVEL and turn[-1] <= MOVING_TURN,
        }
        if hidden:
            result['hiddenSeconds'] = [round(self.seconds(hidden[0]), 3), round(self.seconds(hidden[-1]), 3)]
        return result

    def follows(self, follower, leader, frames):
        """Whether `follower` keeps a fixed pose in `leader`'s frame at these frames."""
        base = unapply(self.poses[leader][0], self.poses[follower][0][0])
        for frame in frames:
            now = unapply(self.poses[leader][frame], self.poses[follower][frame][0])
            relative_turn = abs(turn_degrees(self.poses[follower][frame][1], self.poses[follower][0][1]) -
                                turn_degrees(self.poses[leader][frame][1], self.poses[leader][0][1]))
            if math.dist(now, base) > RIGID_RESIDUAL or relative_turn > MOVING_TURN:
                return False
        return True

    def moving(self):
        rows = [m for m in (self.motion(i) for i in range(len(self.tracks))) if m]
        for row in rows:
            frames = sorted({row['peakFrame'], self.frame((row['awaySeconds'][0][0] + row['peakSeconds']) / 2)})
            leaders = [other['track'] for other in rows if other['track'] != row['track'] and
                       other['travel'] >= row['travel'] - RIGID_RESIDUAL and self.follows(row['track'], other['track'], frames)]
            if leaders:
                row['rigidWith'] = leaders
        return rows


# --- geometry -----------------------------------------------------------------------------------

def bounds(points):
    low = [min(p[i] for p in points) for i in range(3)]
    high = [max(p[i] for p in points) for i in range(3)]
    return low, high


def closest_on_triangle(p, a, b, c):
    ab = [b[i] - a[i] for i in range(3)]
    ac = [c[i] - a[i] for i in range(3)]
    ap = [p[i] - a[i] for i in range(3)]
    d1 = ab[0] * ap[0] + ab[1] * ap[1] + ab[2] * ap[2]
    d2 = ac[0] * ap[0] + ac[1] * ap[1] + ac[2] * ap[2]
    if d1 <= 0 and d2 <= 0:
        return a
    bp = [p[i] - b[i] for i in range(3)]
    d3 = ab[0] * bp[0] + ab[1] * bp[1] + ab[2] * bp[2]
    d4 = ac[0] * bp[0] + ac[1] * bp[1] + ac[2] * bp[2]
    if d3 >= 0 and d4 <= d3:
        return b
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        v = d1 / (d1 - d3)
        return [a[i] + v * ab[i] for i in range(3)]
    cp = [p[i] - c[i] for i in range(3)]
    d5 = ab[0] * cp[0] + ab[1] * cp[1] + ab[2] * cp[2]
    d6 = ac[0] * cp[0] + ac[1] * cp[1] + ac[2] * cp[2]
    if d6 >= 0 and d5 <= d6:
        return c
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        w = d2 / (d2 - d6)
        return [a[i] + w * ac[i] for i in range(3)]
    va = d3 * d6 - d5 * d4
    if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return [b[i] + w * (c[i] - b[i]) for i in range(3)]
    denominator = 1 / (va + vb + vc)
    v, w = vb * denominator, vc * denominator
    return [a[i] + ab[i] * v + ac[i] * w for i in range(3)]


class Surface:
    """Triangles of one or more meshes in a uniform grid, for distance queries."""

    def __init__(self, meshes, cell=1.5):
        self.cell = cell
        self.triangles = []
        volume = 0.0
        # Measured about the meshes' own middle, so an open shell reads the same wherever it is.
        every = [v for mesh in meshes for v in mesh['vertices']]
        middle = [sum(v[i] for v in every) / len(every) for i in range(3)] if every else [0.0, 0.0, 0.0]
        for mesh in meshes:
            vertices = mesh['vertices']
            for i, j, k in mesh['triangles']:
                a, b, c = vertices[i], vertices[j], vertices[k]
                n = [(b[1] - a[1]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[1] - a[1]),
                     (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2]),
                     (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])]
                length = math.hypot(*n)
                if length < 1e-12:
                    continue
                volume += (a[0] - middle[0]) * n[0] + (a[1] - middle[1]) * n[1] + (a[2] - middle[2]) * n[2]
                self.triangles.append((a, b, c, [n[0] / length, n[1] / length, n[2] / length]))
        # Outward normals give a positive enclosed volume; a mesh wound the other way is flipped.
        self.outward = 1.0 if volume >= 0 else -1.0
        self.grid = {}
        for index, (a, b, c, _) in enumerate(self.triangles):
            low = [int(math.floor(min(a[i], b[i], c[i]) / cell)) for i in range(3)]
            high = [int(math.floor(max(a[i], b[i], c[i]) / cell)) for i in range(3)]
            for x in range(low[0], high[0] + 1):
                for y in range(low[1], high[1] + 1):
                    for z in range(low[2], high[2] + 1):
                        self.grid.setdefault((x, y, z), []).append(index)

    def distance(self, point, reach=HOLD_REACH * 2):
        """Signed distance to the surface (negative inside), or None beyond `reach`."""
        if not self.triangles:
            return None
        home = [int(math.floor(point[i] / self.cell)) for i in range(3)]
        best, best_sign, seen = None, 1.0, set()
        for ring in range(int(reach / self.cell) + 2):
            if best is not None and best < (ring - 1) * self.cell:
                break
            for x in range(home[0] - ring, home[0] + ring + 1):
                for y in range(home[1] - ring, home[1] + ring + 1):
                    for z in range(home[2] - ring, home[2] + ring + 1):
                        if max(abs(x - home[0]), abs(y - home[1]), abs(z - home[2])) != ring:
                            continue
                        for index in self.grid.get((x, y, z), ()):
                            if index in seen:
                                continue
                            seen.add(index)
                            a, b, c, n = self.triangles[index]
                            q = closest_on_triangle(point, a, b, c)
                            d = math.dist(point, q)
                            if best is None or d < best:
                                best = d
                                side = (point[0] - q[0]) * n[0] + (point[1] - q[1]) * n[1] + (point[2] - q[2]) * n[2]
                                best_sign = -1.0 if side * self.outward < 0 else 1.0
        if best is None or best > reach:
            return None
        return best * best_sign


def rigid(mesh):
    return not mesh['skinBones']


def hand_meshes(geometry):
    return [m for m in geometry['meshes'] if any('Finger' in bone for bone in m['skinBones'])]


# --- the editor session --------------------------------------------------------------------------

class Editor:
    """Reads and view edits through the editor's command pipe."""

    def __init__(self, command):
        self.command = command

    def read(self, method, params=None):
        return self.command(method, params)

    def idle(self, timeout=180):
        deadline = time.monotonic() + timeout
        while True:
            status = self.command('revision')
            if not status.get('busy'):
                return status
            if time.monotonic() > deadline:
                raise TimeoutError('The editor stayed busy')
            time.sleep(0.1)

    def edit(self, method, params=None):
        return self.command(method, params, self.idle()['revision'], response='summary')


class NativeHands:
    """Shows the clip's own hands: the firing-grip preview pins both hands to the
    weapon at every clip time, so it is switched off and put back afterwards."""

    def __init__(self, editor):
        self.editor = editor

    def __enter__(self):
        self.preview = self.editor.read('holding_grips_state')
        if self.preview.get('enabled'):
            self.editor.edit('holding_grips', {'operation': 'preview', 'enabled': False})
        return self

    def __exit__(self, *exception):
        if self.preview.get('enabled'):
            self.editor.edit('holding_grips', {'operation': 'preview', 'enabled': True, 'hand': self.preview.get('hand', 'right'),
                                               'support': bool(self.preview.get('support')), 'replay': bool(self.preview.get('replay'))})
        return False


class NativeClip:
    """Scrubs the native reload clip with its own hands, then restores the view."""

    def __init__(self, editor, reload):
        self.editor, self.reload = editor, reload
        self.hands = NativeHands(editor)

    def __enter__(self):
        state = self.editor.read('state', {'fields': ['workspace', 'actions', 'animation', 'seconds']})
        self.before = state
        clip = self.reload.source.get('animation', '').lower()
        index = next((a['index'] for a in state['actions'] if a['path'].lower() == clip), None)
        if index is None:
            raise RuntimeError('The learned reload clip is not among this configuration\'s actions')
        self.hands.__enter__()
        self.editor.edit('workspace', {'index': 2, 'handsPage': 0})
        self.editor.edit('action', {'index': index})
        return self

    def at(self, frame):
        self.editor.edit('playback', {'seconds': self.reload.seconds(frame), 'playing': False})

    def __exit__(self, *exception):
        before = self.before
        previous = next((a['index'] for a in before['actions'] if a['path'].lower() == str(before.get('animation', '')).lower()), None)
        if previous is not None:
            self.editor.edit('action', {'index': previous})
            self.editor.edit('playback', {'seconds': before.get('seconds', 0), 'playing': False})
        self.editor.edit('workspace', {'index': before['workspace']})
        self.hands.__exit__()
        self.editor.idle()
        return False


def weapon_local_bones(pose):
    bones = {bone['name']: transform(bone['world']) for bone in pose['bones']}
    weapon = bones['Weapon']
    return {name: unapply(weapon, t[0]) for name, t in bones.items()}


# --- which mesh rides which track ----------------------------------------------------------------

def mesh_owners(reload, rest, snapshots):
    """For every rigid mesh, the tracks whose motion explains how its vertices moved.

    `snapshots` maps a frame to the geometry seen at that frame; `rest` is frame 0.
    A mesh that never moves is fixed to the weapon.
    """
    owners = {}
    for mesh in rest['meshes']:
        if not rigid(mesh):
            continue
        vertices = mesh['vertices']
        picks = sorted({int(i * (len(vertices) - 1) / 15) for i in range(16)}) if vertices else []
        seen, moved, residual = 0, 0.0, [0.0] * len(reload.tracks)
        for frame, geometry in snapshots.items():
            later = next((m for m in geometry['meshes'] if m['block'] == mesh['block']), None)
            if later is None or len(later['vertices']) != len(vertices):
                continue
            seen += 1
            for i in picks:
                moved = max(moved, math.dist(vertices[i], later['vertices'][i]))
                for track in range(len(reload.tracks)):
                    if residual[track] <= RIGID_RESIDUAL * 4:
                        residual[track] = max(residual[track], math.dist(reload.moved(track, frame, vertices[i]), later['vertices'][i]))
        if not seen:
            owners[mesh['block']] = {'tracks': [], 'note': 'hidden while the reload plays'}
        elif moved <= RIGID_RESIDUAL:
            owners[mesh['block']] = {'tracks': [], 'note': 'fixed to the weapon'}
        else:
            tracks = [t for t in range(len(reload.tracks)) if residual[t] <= RIGID_RESIDUAL]
            owners[mesh['block']] = {'tracks': tracks, 'moved': round(moved, 2)} if tracks else \
                {'tracks': [], 'moved': round(moved, 2), 'note': 'moves, but no learned track explains it'}
    return owners


def snapshot_frames(moving, limit=6):
    frames = []
    for row in sorted(moving, key=lambda r: (not r['drawsGeometry'], -r['travel'])):
        frame = row['peakFrame']
        if all(abs(frame - other) > 6 for other in frames):
            frames.append(frame)
    return frames[:limit]


# --- hands over the reload -------------------------------------------------------------------------

def hand_timeline(reload, clip, parts, step):
    """For each hand and part: when the palm is at the part, and the closest finger wrap.

    `parts` maps a part name to (track, Surface in rest space).
    """
    samples = []
    frame = 0
    while frame < reload.frames:
        clip.at(frame)
        bones = weapon_local_bones(clip.editor.read('pose'))
        row = {'frame': frame}
        for side, prefix in (('left', 'LArm_'), ('right', 'RArm_')):
            palm = bones[prefix + 'Hand']
            tips = [bones['%sFinger%d3' % (prefix, finger)] for finger in range(1, 6)]
            for name, (track, surface) in parts.items():
                if not visible(reload.poses[track][frame]):
                    continue
                palm_gap = surface.distance(reload.rested(track, frame, palm), reach=HOLD_REACH * 3)
                gaps = [surface.distance(reload.rested(track, frame, tip), reach=HOLD_REACH * 3) for tip in tips]
                known = [abs(g) for g in gaps if g is not None]
                row[(side, name)] = (palm_gap, sum(known) / len(known) if len(known) == 5 else None)
        row['palms'] = (bones['LArm_Hand'], bones['RArm_Hand'])
        samples.append(row)
        frame += step
    if all(math.dist(row['palms'][i], samples[0]['palms'][i]) < 0.01 for row in samples for i in (0, 1)):
        return {'problem': 'Neither hand moves at any clip time: a preview is posing the hands instead of the native reload.'}
    result = {}
    for side in ('left', 'right'):
        for name, (track, _) in parts.items():
            spans, start, best, carried = [], None, None, 0.0
            for row in samples:
                palm_gap, tip_gap = row.get((side, name), (None, None))
                near = palm_gap is not None and abs(palm_gap) <= HOLD_REACH
                if near and start is None:
                    start = row['frame']
                if not near and start is not None:
                    spans.append((start, last))
                    start = None
                last = row['frame']
                travel = reload.travel(track, row['frame'])
                if near and travel > CARRIED:
                    carried += reload.seconds(step)
                # A wrap worth harvesting: the part is clear of its seat and the fingers are closed on it.
                if near and tip_gap is not None and travel > CARRIED and (best is None or tip_gap < best[0]):
                    best = (tip_gap, row['frame'])
            if start is not None:
                spans.append((start, last))
            if spans:
                # A hand resting beside the seated part, or one the part flies past, spends no time with it unseated.
                entry = {'atPartSeconds': [[round(reload.seconds(a), 2), round(reload.seconds(b), 2)] for a, b in spans],
                         'movesWithPart': carried >= WITH_PART, 'secondsWithUnseatedPart': round(carried, 2)}
                if best:
                    entry['tightestWrap'] = {'seconds': round(reload.seconds(best[1]), 3), 'meanFingertipGap': round(best[0], 2),
                                             'partTravel': round(reload.travel(track, best[1]), 1)}
                result.setdefault(side, {})[name] = entry
    # Both hands can be near a seated part; the one that stays with it longest while it is unseated carries it.
    for name in parts:
        times = {side: result[side][name]['secondsWithUnseatedPart'] for side in ('left', 'right') if name in result.get(side, {})}
        movers = {side: seconds for side, seconds in times.items() if seconds >= WITH_PART}
        if movers:
            side = max(movers, key=movers.get)
            result.setdefault('carriedBy', {})[name] = {
                'hand': side, 'because': 'its palm is at the unseated part for %.2f s' % movers[side] +
                (''.join('; the %s hand for %.2f s' % (other, seconds) for other, seconds in times.items() if other != side))}
    return result


# --- contact of a displayed hand -------------------------------------------------------------------

def contact(geometry, pose, target_meshes, side):
    """How the displayed hand sits on the target: per finger and palm, the gap to its surface."""
    prefix = 'LArm_' if side == 'left' else 'RArm_'
    bones = weapon_local_bones(pose)
    joints = [(name, position) for name, position in bones.items()
              if (name.startswith('LArm_') or name.startswith('RArm_')) and ('Finger' in name or name.endswith('_Hand'))]
    surface = Surface(target_meshes)
    low, high = bounds([v for mesh in target_meshes for v in mesh['vertices']])
    groups = {}
    for mesh in hand_meshes(geometry):
        # Skin never changes which joint a vertex is nearest to, so each hand mesh is sorted once.
        key = (mesh['block'], len(mesh['vertices']))
        if key not in _NEAREST_JOINT:
            _NEAREST_JOINT[key] = [min(joints, key=lambda joint: (v[0] - joint[1][0]) ** 2 + (v[1] - joint[1][1]) ** 2 + (v[2] - joint[1][2]) ** 2)[0]
                                   for v in mesh['vertices']]
        for name, vertex in zip(_NEAREST_JOINT[key], mesh['vertices']):
            if name.startswith(prefix):
                groups.setdefault(name[len(prefix):], []).append(vertex)
    if not groups:
        raise RuntimeError('No hand surface is drawn; show the arms and the hand fit first')

    def measure(vertices):
        near = [v for v in vertices if all(low[i] - HOLD_REACH <= v[i] <= high[i] + HOLD_REACH for i in range(3))]
        gaps = [g for g in (surface.distance(v, reach=HOLD_REACH) for v in near) if g is not None]
        if not gaps:
            return {'gap': None, 'state': 'away'}
        nearest = min(gaps, key=abs)
        deepest = min(gaps)
        state = 'buried' if deepest < BURIED else 'touching' if min(abs(g) for g in gaps) <= TOUCH or deepest < 0 else 'hovering'
        return {'gap': round(nearest, 2), 'deepest': round(deepest, 2), 'touchingVertices': sum(1 for g in gaps if abs(g) <= TOUCH),
                'state': state}

    fingers = {}
    for number, finger in enumerate(FINGERS, start=1):
        tip = measure(groups.get('Finger%d3' % number, []))
        whole = measure([v for segment in (1, 2, 3) for v in groups.get('Finger%d%d' % (number, segment), [])])
        fingers[finger] = {'tip': tip, 'whole': whole}
    palm = measure(groups.get('Hand', []))
    touching = [f for f in FINGERS if fingers[f]['whole']['state'] == 'touching']
    hovering = [f for f in FINGERS if fingers[f]['whole']['state'] in ('hovering', 'away')]
    buried = [f for f in FINGERS if fingers[f]['whole']['state'] == 'buried']
    verdict = 'holds' if len(touching) >= 3 and not buried and palm['state'] != 'away' else \
              'sunk into the part' if buried or palm['state'] == 'buried' else 'not gripping'
    return {'hand': side, 'verdict': verdict, 'touching': touching, 'hovering': hovering, 'buried': buried,
            'palm': palm, 'fingers': fingers, 'units': 'game units; negative is inside the part',
            'thresholds': {'touching': TOUCH, 'buried': BURIED}}


# --- where a hand can take hold of a moving part ----------------------------------------------------

def grab_candidates(part_meshes, other_meshes, rest_pose, count=3):
    """Surface points of the part that stand clear of the rest of the weapon.

    Returns weapon-local points with the same point in the part's own frame
    (`pointInSource`), ordered by how exposed they are.
    """
    cell = 2.0
    grid = {}
    for mesh in other_meshes:
        for v in mesh['vertices']:
            grid.setdefault((int(v[0] // cell), int(v[1] // cell), int(v[2] // cell)), []).append(v)

    def clearance(point, limit=8.0):
        home = (int(point[0] // cell), int(point[1] // cell), int(point[2] // cell))
        best = limit
        reach = int(limit // cell) + 1
        for x in range(home[0] - reach, home[0] + reach + 1):
            for y in range(home[1] - reach, home[1] + reach + 1):
                for z in range(home[2] - reach, home[2] + reach + 1):
                    for v in grid.get((x, y, z), ()):
                        d = math.dist(point, v)
                        if d < best:
                            best = d
        return best

    points = [v for mesh in part_meshes for v in mesh['vertices']]
    if not points:
        return []
    low, high = bounds(points)
    scored = sorted(((clearance(p), p) for p in points), key=lambda item: -item[0])
    chosen = []
    for score, point in scored:
        if all(math.dist(point, other) > 1.0 for _, other in chosen):
            chosen.append((score, point))
        if len(chosen) == count:
            break
    thickness = sorted(high[i] - low[i] for i in range(3))
    radius = round(max(1.2, min(2.5, thickness[1] * 0.9)), 2)
    return [{'pointInWeapon': rounded(p, 3), 'pointInSource': rounded(unapply(rest_pose, p), 4), 'clearance': round(score, 2),
             'suggestedRadius': radius} for score, p in chosen]


# --- the dossier ------------------------------------------------------------------------------------

def mesh_row(mesh, owner, reload):
    low, high = bounds(mesh['vertices'])
    row = {'block': mesh['block'], 'name': mesh['name'], 'size': rounded([high[i] - low[i] for i in range(3)], 1),
           'centre': rounded([(high[i] + low[i]) / 2 for i in range(3)], 1), 'triangles': len(mesh['triangles'])}
    if owner is not None:
        # Tracks that move alike all explain the mesh; the first one names the part.
        row['rides'] = reload.name(owner['tracks'][0]) if owner['tracks'] else owner.get('note')
    return row


def describe(command, hands=True):
    """One compact description of the open weapon configuration, measured, with no image.

    Leaves the editor in Operate (opening it if needed) with the same weapon,
    configuration and authoring.
    """
    editor = Editor(command)
    status = editor.idle()
    if not status.get('weapon'):
        raise RuntimeError('Open a weapon first')
    state = editor.read('state', {'fields': ['workspace', 'slots', 'weapons', 'weaponIndex', 'source', 'magazineEditor']})
    if state['workspace'] != 5:
        editor.edit('workspace', {'index': 5})
        editor.idle()
    simulation = editor.read('simulation_state')
    properties = editor.read('weapon_properties_state')
    checklist = editor.read('checklist_state')
    operating = editor.read('operating_setup_state')
    grabs = editor.read('grab_locations_state')
    recipe = editor.read('reload')
    if not recipe.get('tracks'):
        raise RuntimeError('This configuration has no learned reload: ' + str(simulation.get('error') or 'no base reload clip resolved'))
    reload = Reload(recipe)
    moving = reload.moving()
    setup = simulation.get('setup', {})
    settings = operating.get('settings', {})
    magazine_track = setup.get('magazineTrack', settings.get('magazine', -1))
    operating_track = setup.get('slideTrack', settings.get('operating', -1))
    magazine_blocks = {shape['block'] for shape in simulation.get('magazineShapes', [])}

    with NativeClip(editor, reload) as clip:
        clip.at(0)
        rest = editor.read('holding_geometry')
        snapshots = {}
        for frame in snapshot_frames(moving):
            clip.at(frame)
            snapshots[frame] = editor.read('holding_geometry')
        owners = mesh_owners(reload, rest, snapshots)
        timeline = {}
        if hands:
            parts = {}
            magazine_meshes = [m for m in rest['meshes'] if m['block'] in magazine_blocks]
            if magazine_meshes and magazine_track >= 0:
                parts['magazine'] = (magazine_track, Surface(magazine_meshes))
            operating_meshes = [m for m in rest['meshes'] if rigid(m) and operating_track in owners.get(m['block'], {}).get('tracks', [])
                                and m['block'] not in magazine_blocks]
            if operating_meshes and operating_track >= 0:
                parts['operatingPart'] = (operating_track, Surface(operating_meshes))
            if parts:
                timeline = hand_timeline(reload, clip, parts, max(2, round(reload.frames / 70)))

    weapon = state['weapons'][state['weaponIndex']] if state.get('weapons') else {}
    # Tracks that move as one rigid group are one part: list the first, name the rest.
    listed, grouped = [], set()
    for row in moving:
        if row['track'] in grouped:
            continue
        shown = {k: v for k, v in row.items() if k not in ('peakFrame', 'rigidWith')}
        same = [t for t in row.get('rigidWith', []) if t > row['track']]
        grouped.update(same)
        if same:
            shown['sameMotion'] = [reload.name(t) for t in same]
        listed.append(shown)
    slots = []
    for slot in state.get('slots', []):
        chosen = next((c for c in slot['choices'] if c['id'] == slot['selected']), {})
        slots.append({'slot': slot['name'], 'key': slot['key'], 'choices': len(slot['choices']),
                      'distinctModels': len({c.get('model', '').lower() for c in slot['choices'] if c.get('model')}),
                      'selected': chosen.get('name'), 'selectedRecord': chosen.get('record'), 'selectedModel': chosen.get('model')})
    rigid_meshes = [m for m in rest['meshes'] if rigid(m)]
    meshes = [mesh_row(m, owners.get(m['block']), reload) for m in rigid_meshes]
    for row in meshes:
        if row['block'] in magazine_blocks:
            shape = next(s for s in simulation['magazineShapes'] if s['block'] == row['block'])
            row['magazinePiece'] = 'cartridge' if shape.get('cartridge') else 'body'
    candidates = []
    if operating_track >= 0:
        part = [m for m in rigid_meshes if operating_track in owners.get(m['block'], {}).get('tracks', []) and m['block'] not in magazine_blocks]
        others = [m for m in rigid_meshes if m not in part and m['block'] not in magazine_blocks]
        candidates = grab_candidates(part, others, reload.poses[operating_track][0])
    ammunition = simulation.get('ammunition', {})
    rail = simulation.get('magazineRail') or {}
    items = checklist.get('items', [])
    result = {
        'weapon': {'record': status['weapon'], 'name': weapon.get('name'), 'editorId': weapon.get('editorId'), 'plugin': weapon.get('plugin'),
                   'ammunition': ammunition.get('ammoName'), 'capacity': ammunition.get('capacity'), 'automatic': ammunition.get('automatic'),
                   'openBolt': ammunition.get('openBolt')},
        'preset': {**properties.get('preset', {}), 'supported': [p.get('id') for p in checklist.get('presets', [])]},
        'checklist': {'progress': checklist.get('summary', {}).get('progress'), 'missing': [i['id'] for i in items if i.get('state') == 'missing'],
                      'inherited': {k: v for k, v in checklist.get('planSources', {}).items() if v.get('source') not in ('own', 'none')}},
        'clips': [{'role': c.get('role'), 'animation': c.get('animation'), 'duration': round(c.get('duration', 0), 3),
                   'events': [[round(e['seconds'], 3), e['text']] for e in c.get('events', [])]} for c in properties.get('clips', [])],
        'reload': {'animation': reload.source.get('animation'), 'duration': round(reload.duration, 3), 'frames': reload.frames,
                   'measuredFrom': 'the clip\'s first frame; a part that does not end at rest is left in another state, as a bolt that starts open',
                   'movingTracks': listed,
                   'stillTracks': [reload.name(i) for i in range(len(reload.tracks)) if i not in {row['track'] for row in moving}]},
        'roles': {'magazineTrack': magazine_track, 'operatingTrack': operating_track, 'boltTrack': settings.get('bolt'),
                  'trackNames': {str(t): reload.name(t) for t in {magazine_track, operating_track, settings.get('bolt')}
                                 if isinstance(t, int) and 0 <= t < len(reload.tracks)},
                  'followers': settings.get('followers'), 'mode': settings.get('mode'), 'source': operating.get('source'),
                  'suggestedOpenSeconds': operating.get('suggestedOpenSeconds'), 'suggestedClosedSeconds': operating.get('suggestedClosedSeconds'),
                  'savedOpenSeconds': settings.get('openSeconds'), 'savedClosedSeconds': settings.get('closedSeconds'),
                  'controllerStroke': rounded(simulation.get('controllerInput', {}).get('strokeInWeapon', []), 3)},
        'magazine': {**properties.get('magazine', {}), 'events': properties.get('magazineEvents'), 'insertion': properties.get('insertion'),
                     'release': simulation.get('magazineRelease'),
                     'inputGuide': {'meaning': 'the straight line the player moves the magazine along; the native clip carries it '
                                               'much farther, as reload.movingTracks shows',
                                    'length': rail.get('travel'),
                                    **{k: (rounded(rail[k], 3) if isinstance(rail.get(k), list) else rail.get(k))
                                       for k in ('outwardInWeapon', 'captureRadius', 'clearance', 'connectionName', 'inputPath') if k in rail}},
                     'fit': state.get('magazineEditor', {})},
        'meshes': meshes,
        'hands': timeline,
        'grabPoints': {'saved': grabs.get('profile', {}).get('points', []), 'own': grabs.get('own'), 'candidates': candidates},
        'slots': slots,
        'units': 'game units (about 1.43 cm); points and directions are weapon-local unless named otherwise',
    }
    if operating_track >= 0 and not candidates:
        result['grabPoints']['note'] = 'No drawn mesh rides the operating track: its handle is part of another mesh or has no geometry.'
    return result


def index_row(dossier):
    """The few facts that sort weapons into families, for the atlas index."""
    roles = dossier['roles']

    def part(track):
        # A track folded into a rigid group is found under the group's first track.
        name = roles.get('trackNames', {}).get(str(track))
        return next((row for row in dossier['reload']['movingTracks'] if row['track'] == track or name in row.get('sameMotion', [])), None)

    operating = part(roles.get('operatingTrack'))
    magazine = part(roles.get('magazineTrack'))
    hands = dossier.get('hands', {})
    carrier = hands.get('carriedBy', {}).get('magazine', {}).get('hand')
    return {'record': dossier['weapon']['record'], 'name': dossier['weapon']['name'], 'ammunition': dossier['weapon']['ammunition'],
            'capacity': dossier['weapon']['capacity'], 'preset': dossier['preset'].get('assigned') or dossier['preset'].get('proposed'),
            'presetAssigned': bool(dossier['preset'].get('assigned')), 'checklist': dossier['checklist']['progress'],
            'reloadClip': dossier['reload']['animation'], 'reloadSeconds': dossier['reload']['duration'],
            'operating': operating and {'name': operating['name'], 'travel': operating['travel'], 'turnDegrees': operating['turnDegrees']},
            'magazine': magazine and {'name': magazine['name'], 'leavesSeconds': magazine['awaySeconds'][0]},
            'magazineCarriedBy': carrier, 'handsProblem': hands.get('problem'),
            'grabCandidates': len(dossier['grabPoints']['candidates']),
            'magazineChoices': next((s['choices'] for s in dossier['slots'] if s['selectedRecord'] and
                                     s['selectedRecord'].lower() == str(dossier['magazine'].get('fit', {}).get('ownerRecord', '')).lower()), None)}


def atlas(command, output, plugin='', limit=10):
    """Describe every weapon of one plugin in the open source into `output`, a few per call.

    Each weapon becomes <output>/<plugin>/<form id>.json and one row of
    <output>/<plugin>/index.json. Weapons already described are skipped, so
    repeated calls finish the plugin. The open weapon must have no unsaved
    edits, since choosing another weapon discards them.
    """
    import json
    from pathlib import Path
    editor = Editor(command)
    editor.idle()
    state = editor.read('state', {'fields': ['weapons', 'weaponIndex', 'modified']})
    if state.get('modified'):
        raise RuntimeError('The open weapon has unsaved edits; save the project before building the atlas')
    weapons = state['weapons']
    plugin = (plugin or weapons[state['weaponIndex']]['plugin']).lower()
    folder = Path(output) / plugin
    folder.mkdir(parents=True, exist_ok=True)
    index_path = folder / 'index.json'
    index = json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {'plugin': plugin, 'weapons': {}}
    wanted = [w for w in weapons if w['plugin'].lower() == plugin]
    done, failed = [], []
    for weapon in wanted:
        key = weapon['formId'][-6:].lower()
        if key in index['weapons'] or len(done) + len(failed) >= limit:
            continue
        try:
            editor.edit('select_weapon', {'index': weapon['index']})
            editor.idle()
            dossier = describe(command)
            (folder / (key + '.json')).write_text(json.dumps(dossier, indent=1), encoding='utf-8')
            index['weapons'][key] = index_row(dossier)
            done.append(weapon['name'])
        except Exception as error:  # one weapon's problem never stops the plugin
            index['weapons'][key] = {'record': plugin + ':' + key, 'name': weapon['name'], 'problem': str(error)}
            failed.append(weapon['name'] + ': ' + str(error))
        index_path.write_text(json.dumps(index, indent=1), encoding='utf-8')
    remaining = [w['name'] for w in wanted if w['formId'][-6:].lower() not in index['weapons']]
    return {'plugin': plugin, 'folder': str(folder), 'described': done, 'problems': failed, 'remaining': remaining}


def held_contact(editor, command, side):
    """Operate: take the seated magazine with this hand, pull it clear, measure, let go."""
    editor.edit('workspace', {'index': 5})
    editor.edit('simulate', {'operation': 'reset'})
    editor.edit('interaction_hand', {'hand': side})
    editor.edit('simulate', {'operation': 'grab_magazine'})
    rail = editor.read('simulation_state').get('magazineRail') or {}
    editor.edit('simulate', {'operation': 'move_magazine', 'distance': rail.get('travel', 0) + rail.get('releaseMargin', 0) + 1})
    grip = editor.read('simulation_state').get('magazineGrip', {})
    if not grip.get('applied'):
        raise RuntimeError('The %s hand has no fit applied on this magazine: %s' % (side, grip.get('error') or 'none is assigned'))
    report = measure_contact(command)
    report['fit'] = grip.get('name')
    editor.edit('simulate', {'operation': 'release_magazine'})
    editor.edit('simulate', {'operation': 'reset'})
    return report


def hold_score(report):
    """Higher is a better grip: fingers on the surface count, sunk or floating ones cost."""
    return (len(report['touching']) - 1.5 * len(report['buried']) - 0.5 * len(report['hovering']) +
            (0.5 if report['palm']['state'] == 'touching' else 0.0))


def best_native_hold(editor, track, hand, spans, limit=12):
    """The moment of the native reload at which this hand's skin sits best on the magazine.

    Tries moments spread over the time the hand is at the magazine while it is
    clear of its seat, measuring real hand-to-magazine contact at each.
    """
    reload = Reload(editor.read('reload'))
    blocks = {shape['block'] for shape in editor.read('simulation_state').get('magazineShapes', [])}
    frames = []
    for start, end in spans:
        frame = reload.frame(start)
        while frame <= reload.frame(end):
            if reload.travel(track, frame) > CARRIED and visible(reload.poses[track][frame]):
                frames.append(frame)
            frame += 4
    frames = frames[::max(1, len(frames) // limit)][:limit + 1]
    tried = []
    with NativeClip(editor, reload) as clip:
        for frame in frames:
            clip.at(frame)
            geometry = editor.read('holding_geometry')
            meshes = [m for m in geometry['meshes'] if m['block'] in blocks]
            if not meshes:
                continue
            report = contact(geometry, editor.read('pose'), meshes, hand)
            tried.append({'seconds': round(reload.seconds(frame), 3), 'score': hold_score(report), 'verdict': report['verdict'],
                          'touching': report['touching'], 'hovering': report['hovering'], 'buried': report['buried']})
    if not tried:
        raise RuntimeError('The magazine is never drawn in this hand while clear of its seat; pose a library hand with configure_magazine')
    best = max(tried, key=lambda row: row['score'])
    return {**best, 'considered': [[row['seconds'], row['score'], row['verdict']] for row in tried]}


def author_magazine_fit(command, hand='', seconds=None):
    """Give the selected magazine its hand fit from the weapon's own reload, and measure it.

    Takes the hand that carries the magazine in the native clip at the moment
    its fingers are closed on it (or the given hand and seconds), saves that
    exact pose as the magazine's fit, mirrors it to the other hand and reports
    the contact of both. Nothing is saved to the library.
    """
    editor = Editor(command)
    editor.idle()
    choice = None
    if not hand or seconds is None:
        dossier = describe(command)
        timeline = dossier['hands']
        if 'problem' in timeline:
            raise RuntimeError(timeline['problem'])
        carriers = {side: parts['magazine'] for side, parts in timeline.items()
                    if side in ('left', 'right') and parts.get('magazine', {}).get('movesWithPart')}
        if hand and hand not in carriers:
            raise RuntimeError('The %s hand never carries the magazine in the native reload; give seconds yourself' % hand)
        if not carriers:
            raise RuntimeError('No hand carries the magazine in the native reload; pose a library hand with configure_magazine instead')
        hand = hand or timeline['carriedBy']['magazine']['hand']
        if seconds is None:
            choice = best_native_hold(editor, dossier['roles']['magazineTrack'], hand, carriers[hand]['atPartSeconds'])
            seconds = choice['seconds']
    editor.edit('workspace', {'index': 5})
    editor.edit('simulate', {'operation': 'reset'})
    editor.edit('interaction_hand', {'hand': hand})
    state = editor.read('state', {'fields': ['magazineEditor', 'actions', 'weapons', 'weaponIndex']})
    fit = state['magazineEditor']
    clip = editor.read('reload')['source']['animation'].lower()
    action = next(a['index'] for a in state['actions'] if a['path'].lower() == clip)
    name = '%s %s native hold %.2fs' % (state['weapons'][state['weaponIndex']]['name'], fit.get('magazine', 'magazine'), seconds)
    with NativeHands(editor):
        editor.edit('workspace', {'index': 2, 'handsPage': 0})
        editor.edit('action', {'index': action})
        editor.edit('playback', {'seconds': seconds, 'playing': False})
        editor.edit('save_hand_pose', {'hand': hand, 'name': name, 'anchor': fit['anchor']})
    pose = [p for p in editor.read('state', {'fields': ['handPoses']})['handPoses'] if p['name'] == name][-1]
    editor.edit('start_grip', {'poseId': pose['id'], 'anchor': fit['anchor'], 'captured': True})
    editor.edit('use_magazine_grip', {'scope': 'magazine'})
    editor.edit('workspace', {'index': 5})
    editor.edit('mirror_magazine_grip', {})
    other = 'right' if hand == 'left' else 'left'
    contacts = {hand: held_contact(editor, command, hand), other: held_contact(editor, command, other)}
    brief = {side: {k: report[k] for k in ('verdict', 'touching', 'hovering', 'buried')} for side, report in contacts.items()}
    return {'magazine': fit.get('magazine'), 'harvestedHand': hand, 'harvestedSeconds': seconds, 'poseId': pose['id'], 'poseName': name,
            'mirroredTo': other, 'moment': choice, 'contact': brief,
            'next': 'A hand that does not read "holds" needs its own correction with configure_magazine and set_part_grip, '
                    'then measure_hand_contact again. save_project keeps the result.'}


def measure_contact(command, hand=''):
    """A physical hand against the magazine it holds, as numbers.

    With a hand, Operate takes the seated magazine with it, pulls it clear,
    measures and lets go. Without one, the hand already shown holding or
    fitting the magazine is measured; a hand that holds nothing is refused,
    since its numbers would describe an idle pose.
    """
    editor = Editor(command)
    if hand:
        if hand not in ('left', 'right'):
            raise ValueError('hand is left or right')
        return held_contact(editor, command, hand)
    status = editor.idle()
    simulation = editor.read('simulation_state')
    fitting = editor.read('state', {'fields': ['magazineEditor']}).get('magazineEditor', {})
    if not simulation.get('magazineGrip', {}).get('applied') and not fitting.get('editing') and not fitting.get('preview'):
        raise RuntimeError('No hand is holding the magazine. Pass hand="left" or "right" to have it held and measured, '
                           'or open configure_magazine and measure the fit being placed.')
    geometry = editor.read('holding_geometry')
    pose = editor.read('pose')
    blocks = {shape['block'] for shape in simulation.get('magazineShapes', [])}
    meshes = [m for m in geometry['meshes'] if m['block'] in blocks]
    if not meshes:
        raise RuntimeError('The magazine is not drawn; prepare Operate and hold or configure the magazine first')
    return contact(geometry, pose, meshes, status.get('hand', 'left'))

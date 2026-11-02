"""Object/interface assembly compiler ("puzzle pieces").

A robot is compiled from *objects* (catalog component types, each modeled in
its own frame) joined at named *interfaces*. Object poses are derived from the
joins, never authored, so parts cannot be placed inside each other and hole
patterns must line up.

Frames
------
Every interface has a frame ``F`` in its object frame: ``origin``, ``z`` (the
outward normal of a seat face, or the shaft/bore axis pointing out of the part)
and ``x`` (the clocking reference); ``y = z × x``. A join mates ``b`` onto ``a``::

    pose_b = pose_a · F_a · M · F_b⁻¹
    M      = Rz(clock + q_rev) · Tz(offset + q_slide) · R_ball(q_ball) · Rx(π)

``Rx(π)`` flips ``b`` so the two z axes oppose and the x axes align; ``clock``
rotates ``b`` about ``a``'s z, and ``offset`` moves it along ``a``'s z (a
positive offset leaves a gap). In ``a``'s interface frame a point ``(x, y, 0)``
of ``b``'s interface plane therefore lands at ``Rz(clock) · (x, −y, 0)``.

Moving joins (revolute, slider, ball) are evaluated at ``q = 0`` for the
compiled pose; :meth:`AssemblyResult.poses_at` evaluates any ``q``.

Interface ``params`` by kind (lengths SI or unit strings):

* ``face_mount`` / ``hole_pattern``: ``holes: [[x, y, d, clearance|tapped|thread, depth]]``
  in the interface frame (``depth`` is the plate thickness for clearance holes,
  the thread depth for tapped holes and the stud length for threads),
  ``thickness`` (default hole depth), ``min_engagement_factor`` (default 1.0 × d).
* ``shaft``: ``d``, ``tol`` (ISO 286 shaft grade, e.g. ``g6``), ``length``, ``flat_offset``.
* ``bore``: ``d``, ``tol`` (ISO 286 hole grade, e.g. ``H7``), ``depth``, ``flat_offset``.
* ``spline``: ``teeth``, ``d``, ``gender: male|female``.
* ``header`` / ``socket``: ``pitch``, ``rows``, ``cols``, ``pin1: [x, y]`` (grid
  centered on the origin, columns along x, rows along y).
* ``ball_stud`` / ``ball_socket``: ``d``.
* ``strap_seat`` / ``strap_slot``: ``width``.
* ``adhesive_pad``: ``area``.
* ``connector_body``: ``type`` (catalog connector id).
* any kind: ``capacity`` (joins that may share the interface, default 1).
"""

from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable

import numpy as np

from .diagnostics import Diagnostic, Severity
from .graph import rpy_matrix
from .spec import Join, Joint, Pose, RobotSpec
from .units import to_si

if TYPE_CHECKING:
    from r3_catalog.schema import Catalog, Interface

# ----------------------------------------------------------------------------- frames


def homogeneous(rotation: np.ndarray, translation: Iterable[float]) -> np.ndarray:
    matrix = np.eye(4)
    matrix[:3, :3] = rotation
    matrix[:3, 3] = np.asarray(list(translation), dtype=float)
    return matrix


def rot_x(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return homogeneous(np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]]), (0.0, 0.0, 0.0))


def rot_z(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return homogeneous(np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]]), (0.0, 0.0, 0.0))


def trans_z(distance: float) -> np.ndarray:
    return homogeneous(np.eye(3), (0.0, 0.0, distance))


# Rx(π) with exact zeros, so a flip introduces no 1e-16 noise.
FLIP = np.diag([1.0, -1.0, -1.0, 1.0])


def rotation_vector_matrix(vector: Iterable[float]) -> np.ndarray:
    """Rodrigues: R = I + sinθ K + (1 − cosθ) K², θ = |v|, K = [v/θ]×."""
    v = np.asarray(list(vector), dtype=float)
    angle = float(np.linalg.norm(v))
    if angle < 1e-15:
        return np.eye(4)
    k = v / angle
    cross = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
    rotation = np.eye(3) + math.sin(angle) * cross + (1.0 - math.cos(angle)) * cross @ cross
    return homogeneous(rotation, (0.0, 0.0, 0.0))


def rotation_about_axis(axis: Iterable[float], point: Iterable[float], angle: float) -> np.ndarray:
    """Rigid rotation by ``angle`` about the world line through ``point`` along ``axis``."""
    a = np.asarray(list(axis), dtype=float)
    p = np.asarray(list(point), dtype=float)
    rotation = rotation_vector_matrix(a / np.linalg.norm(a) * angle)
    return homogeneous(np.eye(3), p) @ rotation @ homogeneous(np.eye(3), -p)


def frame_matrix(origin: Iterable[float], z: Iterable[float], x: Iterable[float]) -> np.ndarray:
    """Interface frame from origin, z and x (x is orthogonalized against z)."""
    z_axis = np.asarray(list(z), dtype=float)
    z_axis = z_axis / np.linalg.norm(z_axis)
    x_axis = np.asarray(list(x), dtype=float)
    x_axis = x_axis - z_axis * float(x_axis @ z_axis)
    x_axis = x_axis / np.linalg.norm(x_axis)
    y_axis = np.cross(z_axis, x_axis)
    return homogeneous(np.column_stack([x_axis, y_axis, z_axis]), origin)


def invert(matrix: np.ndarray) -> np.ndarray:
    """Exact rigid inverse: [Rᵀ, −Rᵀp]."""
    rotation = matrix[:3, :3].T
    return homogeneous(rotation, -rotation @ matrix[:3, 3])


def mate_matrix(clock: float, offset: float, revolute: float = 0.0, slide: float = 0.0,
                ball: Iterable[float] | None = None) -> np.ndarray:
    """``M``: pose of ``b``'s interface frame in ``a``'s interface frame."""
    matrix = rot_z(clock + revolute) @ trans_z(offset + slide)
    if ball is not None:
        matrix = matrix @ rotation_vector_matrix(ball)
    return matrix @ FLIP


def pose_matrix(xyz: Iterable[float], rpy: Iterable[float]) -> np.ndarray:
    return homogeneous(rpy_matrix(list(rpy)), xyz)


def rpy_from_matrix(rotation: np.ndarray) -> list[float]:
    """Inverse of R = Rz(yaw)·Ry(pitch)·Rx(roll) (the spec convention)."""
    r = rotation[:3, :3]
    sin_pitch = -float(np.clip(r[2, 0], -1.0, 1.0))
    pitch = math.asin(sin_pitch)
    if abs(sin_pitch) > 1.0 - 1e-12:  # gimbal lock: roll and yaw share one axis; put it all in yaw
        return [0.0, pitch, math.atan2(-r[0, 1], r[1, 1])]
    return [math.atan2(r[2, 1], r[2, 2]), pitch, math.atan2(r[1, 0], r[0, 0])]


def rotation_angle(rotation: np.ndarray) -> float:
    """Angle of a rotation matrix: θ = atan2(|R − Rᵀ|_F / (2√2), (tr R − 1) / 2), precise near 0 and π."""
    r = rotation[:3, :3]
    sin_angle = float(np.linalg.norm(r - r.T)) / (2.0 * math.sqrt(2.0))
    cos_angle = (float(np.trace(r)) - 1.0) / 2.0
    return math.atan2(sin_angle, cos_angle)


def wrap_angle(angle: float) -> float:
    """Wrap to [0, 2π)."""
    wrapped = math.fmod(angle, 2.0 * math.pi)
    if wrapped < 0:
        wrapped += 2.0 * math.pi
    return 0.0 if abs(wrapped - 2.0 * math.pi) < 1e-12 else wrapped


def angle_distance(a: float, b: float) -> float:
    delta = wrap_angle(a - b)
    return min(delta, 2.0 * math.pi - delta)


# ----------------------------------------------------------------------------- standards

# ISO 273 clearance holes (mm): nominal -> (fine, medium, coarse).
ISO_273 = {
    1.6: (1.7, 1.8, 2.0), 2.0: (2.2, 2.4, 2.6), 2.5: (2.7, 2.9, 3.1), 3.0: (3.2, 3.4, 3.6),
    4.0: (4.3, 4.5, 4.8), 5.0: (5.3, 5.5, 5.8), 6.0: (6.4, 6.6, 7.0), 8.0: (8.4, 9.0, 10.0),
    10.0: (10.5, 11.0, 12.0), 12.0: (13.0, 13.5, 14.5),
}
# ISO 4032 hex nut height m (mm), for through-bolt length.
ISO_4032_NUT = {1.6: 1.3, 2.0: 1.6, 2.5: 2.0, 3.0: 2.4, 4.0: 3.2, 5.0: 4.7, 6.0: 5.2, 8.0: 6.8, 10.0: 8.4, 12.0: 10.8}

# ISO 286-2 limit deviations (µm) for nominal sizes over a, up to and including b (mm).
_ISO_286_RANGES = ((0.0, 3.0), (3.0, 6.0), (6.0, 10.0), (10.0, 18.0), (18.0, 30.0))
_ISO_286_HOLES = {  # (lower, upper)
    "H7": ((0, 10), (0, 12), (0, 15), (0, 18), (0, 21)),
    "H8": ((0, 14), (0, 18), (0, 22), (0, 27), (0, 33)),
    "H11": ((0, 60), (0, 75), (0, 90), (0, 110), (0, 130)),
}
_ISO_286_SHAFTS = {  # (lower ei, upper es)
    "c11": ((-120, -60), (-145, -70), (-170, -80), (-205, -95), (-240, -110)),
    "f7": ((-16, -6), (-22, -10), (-28, -13), (-34, -16), (-41, -20)),
    "g6": ((-8, -2), (-12, -4), (-14, -5), (-17, -6), (-20, -7)),
    "h6": ((-6, 0), (-8, 0), (-9, 0), (-11, 0), (-13, 0)),
    "k6": ((0, 6), (1, 9), (1, 10), (1, 12), (2, 15)),
    "n6": ((4, 10), (8, 16), (10, 19), (12, 23), (15, 28)),
    "p6": ((6, 12), (12, 20), (15, 24), (18, 29), (22, 35)),
    "s6": ((14, 20), (19, 27), (23, 32), (28, 39), (35, 48)),
}


def _iso_286_row(nominal_mm: float) -> int:
    for index, (low, high) in enumerate(_ISO_286_RANGES):
        if low < nominal_mm <= high:
            return index
    raise ValueError(f"nominal size {nominal_mm:.3f} mm is outside the ISO 286 table (0-30 mm)")


def iso_286_limits(nominal: float, grade: str | None, hole: bool) -> tuple[float, float]:
    """(min, max) diameter in m for a nominal size and ISO 286 grade (None: exact)."""
    if not grade:
        return nominal, nominal
    table = _ISO_286_HOLES if hole else _ISO_286_SHAFTS
    if grade not in table:
        raise ValueError(f"unsupported ISO 286 {'hole' if hole else 'shaft'} grade {grade!r}; known: {', '.join(table)}")
    low, high = table[grade][_iso_286_row(nominal * 1e3)]
    return nominal + low * 1e-6, nominal + high * 1e-6


def running_clearance_max(nominal: float) -> float:
    """Loosest standard running fit, H11/c11 maximum clearance (m)."""
    row = _iso_286_row(nominal * 1e3)
    return (_ISO_286_HOLES["H11"][row][1] - _ISO_286_SHAFTS["c11"][row][0]) * 1e-6


def press_interference_max(nominal: float) -> float:
    """Medium drive fit H7/s6 maximum interference (m): the largest press fit allowed without an explicit class."""
    row = _iso_286_row(nominal * 1e3)
    return (_ISO_286_SHAFTS["s6"][row][1] - _ISO_286_HOLES["H7"][row][0]) * 1e-6


_FASTENER = re.compile(r"^\s*M\s*(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?)")


def parse_fastener(text: str) -> tuple[float, float] | None:
    """``M3x10`` -> (nominal 0.003 m, length under head 0.010 m)."""
    match = _FASTENER.match(text)
    if not match:
        return None
    return float(match.group(1)) * 1e-3, float(match.group(2)) * 1e-3


def _nominal_key(diameter: float) -> float | None:
    mm = round(diameter * 1e3, 3)
    return next((key for key in ISO_273 if abs(key - mm) < 1e-6), None)


# ----------------------------------------------------------------------------- interfaces

PLANAR = {"face_mount", "hole_pattern", "adhesive_pad"}
# Unordered kind pairs that can mate, and the join kinds each pair supports.
_PAIR_JOINS: dict[frozenset[str], set[str]] = {
    frozenset({"shaft", "bore"}): {"fasten", "revolute", "slider", "drive_flat"},
    frozenset({"spline"}): {"fasten"},
    frozenset({"header", "socket"}): {"fasten"},
    frozenset({"ball_stud", "ball_socket"}): {"ball"},
    frozenset({"strap_seat", "strap_slot"}): {"fasten"},
    frozenset({"connector_body"}): {"fasten"},
}
for _a in PLANAR:
    for _b in PLANAR:
        _PAIR_JOINS[frozenset({_a, _b})] = {"fasten"}

JOIN_DOF = {"fasten": 0, "drive_flat": 0, "revolute": 1, "slider": 1, "ball": 3}
MOVING = {"revolute", "slider", "ball"}
POSITION_EPS = 1e-9  # m, numeric slack for exact comparisons


def allowed_join_kinds(kind_a: str, kind_b: str) -> set[str]:
    return _PAIR_JOINS.get(frozenset({kind_a, kind_b}), set())


@dataclass
class Hole:
    x: float
    y: float
    d: float
    kind: str  # clearance | tapped | thread
    depth: float

    @property
    def radius_from_origin(self) -> float:
        return math.hypot(self.x, self.y)


@dataclass
class ResolvedInterface:
    obj: str
    name: str
    kind: str
    frame: np.ndarray  # 4x4 in the object frame
    params: dict[str, Any]
    material: str = ""

    @property
    def ref(self) -> str:
        return f"{self.obj}.{self.name}"

    def quantity(self, key: str, default: float | None = None) -> float | None:
        value = self.params.get(key, default)
        return None if value is None else to_si(value)

    @property
    def holes(self) -> list[Hole]:
        thickness = self.quantity("thickness", 0.0) or 0.0
        holes = []
        for entry in self.params.get("holes", []):
            x, y, d = (to_si(v) for v in entry[:3])
            kind = str(entry[3]) if len(entry) > 3 else "clearance"
            if kind not in ("clearance", "tapped", "thread"):
                raise ValueError(f"{self.ref}: hole kind {kind!r} must be clearance, tapped or thread")
            depth = to_si(entry[4]) if len(entry) > 4 else thickness
            holes.append(Hole(x, y, d, kind, depth))
        return holes

    @property
    def capacity(self) -> int:
        return int(self.params.get("capacity", 1))

    @property
    def clock_free(self) -> bool:
        """Rotationally symmetric about z: a clock angle does not change the fit."""
        if self.kind in ("ball_stud", "ball_socket"):
            return True
        if self.kind in ("shaft", "bore"):
            return self.params.get("flat_offset") is None
        if self.kind in ("hole_pattern", "face_mount"):
            holes = self.holes
            return bool(holes) and all(h.radius_from_origin < POSITION_EPS for h in holes) and not self.params.get("keyed")
        return False


def mirrored_interface(iface: ResolvedInterface, mirror: list[float]) -> ResolvedInterface:
    """The interface of a mirrored instance (``params.mirror``, e.g. [1, -1, 1]):
    origin, z and x are mirrored, the frame stays right-handed (y = z × x), and
    in-plane coordinates (hole and pin positions) are re-expressed in it."""
    m = np.diag(mirror)
    old = iface.frame
    origin, z, x = m @ old[:3, 3], m @ old[:3, 2], m @ old[:3, 0]
    frame = frame_matrix(origin, z, x)

    def remap(px: float, py: float) -> tuple[float, float]:
        local = m @ (old[:3, 0] * px + old[:3, 1] * py)  # in-plane offset, mirrored
        return float(local @ frame[:3, 0]), float(local @ frame[:3, 1])

    params = dict(iface.params)
    if "holes" in params:
        holes = []
        for entry in params["holes"]:
            px, py = remap(to_si(entry[0]), to_si(entry[1]))
            holes.append([px, py, *entry[2:]])
        params["holes"] = holes
    if params.get("pin1") is not None:
        params["pin1"] = list(remap(to_si(params["pin1"][0]), to_si(params["pin1"][1])))
    return ResolvedInterface(iface.obj, iface.name, iface.kind, frame, params, iface.material)


def b_in_a_plane(x: float, y: float, clock: float) -> np.ndarray:
    """A point of b's interface plane expressed in a's interface frame: Rz(clock)·(x, −y)."""
    c, s = math.cos(clock), math.sin(clock)
    return np.array([c * x + s * y, s * x - c * y])


# ----------------------------------------------------------------------------- pattern matching


@dataclass
class PatternMatch:
    clock: float
    pairs: list[tuple[int, int, float]]  # (a index, b index, center offset m)
    unmatched_a: list[int]
    unmatched_b: list[int]
    allowances: list[float]
    required: int

    @property
    def max_offset(self) -> float:
        return max((offset for _, _, offset in self.pairs), default=0.0)

    @property
    def max_excess(self) -> float:
        return max((offset - allow for (_, _, offset), allow in zip(self.pairs, self.allowances)), default=0.0)

    @property
    def ok(self) -> bool:
        return len(self.pairs) >= self.required and all(
            offset <= allow + POSITION_EPS for (_, _, offset), allow in zip(self.pairs, self.allowances))


def hole_allowance(a: Hole, b: Hole, fastener_d: float | None) -> float:
    """Largest center offset between two holes that still passes a straight
    fastener of diameter F (floating/fixed fastener rule, ASME Y14.5 B.4):
    each clearance hole contributes (H − F)/2, a threaded hole locates the
    fastener and contributes 0."""
    threaded = [h.d for h in (a, b) if h.kind in ("tapped", "thread")]
    nominal = fastener_d or (min(threaded) if threaded else min(a.d, b.d))
    return sum(max(0.0, (h.d - nominal) / 2.0) for h in (a, b) if h.kind == "clearance")


def match_holes(holes_a: list[Hole], holes_b: list[Hole], clock: float, fastener_d: float | None,
                required: int | None) -> PatternMatch:
    """Pair holes of b (mapped into a's frame at ``clock``) with holes of a.
    Pairs are mutual nearest neighbours whose circles overlap."""
    points_a = np.array([[h.x, h.y] for h in holes_a]).reshape(-1, 2)
    points_b = np.array([b_in_a_plane(h.x, h.y, clock) for h in holes_b]).reshape(-1, 2)
    distance = np.linalg.norm(points_a[:, None, :] - points_b[None, :, :], axis=2) if len(holes_a) and len(holes_b) \
        else np.zeros((len(holes_a), len(holes_b)))
    pairs: list[tuple[int, int, float]] = []
    allowances: list[float] = []
    for i in range(len(holes_a)):
        if not len(holes_b):
            break
        j = int(np.argmin(distance[i]))
        if int(np.argmin(distance[:, j])) != i:
            continue
        if distance[i, j] > (holes_a[i].d + holes_b[j].d) / 2.0:
            continue
        pairs.append((i, j, float(distance[i, j])))
        allowances.append(hole_allowance(holes_a[i], holes_b[j], fastener_d))
    paired_a = {i for i, _, _ in pairs}
    paired_b = {j for _, j, _ in pairs}
    return PatternMatch(
        clock=clock, pairs=pairs,
        unmatched_a=[i for i in range(len(holes_a)) if i not in paired_a],
        unmatched_b=[j for j in range(len(holes_b)) if j not in paired_b],
        allowances=allowances,
        required=min(len(holes_a), len(holes_b)) if required is None else required,
    )


def candidate_hole_clocks(holes_a: list[Hole], holes_b: list[Hole], tolerance: float) -> list[float]:
    """Clock angles that map some hole of b onto a's farthest-out hole (radius
    test within ``tolerance``). Every valid clock is among them."""
    reference = max(holes_a, key=lambda h: h.radius_from_origin)
    radius = reference.radius_from_origin
    if radius < POSITION_EPS:
        return [0.0]
    phi_a = math.atan2(reference.y, reference.x)
    clocks: list[float] = []
    for hole in holes_b:
        if abs(hole.radius_from_origin - radius) > tolerance:
            continue
        mapped = b_in_a_plane(hole.x, hole.y, 0.0)
        clock = wrap_angle(phi_a - math.atan2(mapped[1], mapped[0]))
        if all(angle_distance(clock, other) > 1e-9 for other in clocks):
            clocks.append(clock)
    return sorted(clocks)


def header_pins(iface: ResolvedInterface) -> tuple[np.ndarray, np.ndarray | None]:
    pitch = iface.quantity("pitch")
    rows, cols = int(iface.params.get("rows", 1)), int(iface.params.get("cols", 1))
    xs = (np.arange(cols) - (cols - 1) / 2.0) * pitch
    ys = (np.arange(rows) - (rows - 1) / 2.0) * pitch
    grid = np.array([[x, y] for y in ys for x in xs])
    pin1 = iface.params.get("pin1")
    return grid, (np.array([to_si(v) for v in pin1]) if pin1 is not None else None)


@dataclass
class HeaderMatch:
    clock: float
    max_pin_offset: float
    pin1_offset: float | None

    def ok(self, tolerance: float) -> bool:
        return self.max_pin_offset <= tolerance and (self.pin1_offset is None or self.pin1_offset <= tolerance)


def match_header(a: ResolvedInterface, b: ResolvedInterface, clock: float) -> HeaderMatch:
    grid_a, pin1_a = header_pins(a)
    grid_b, pin1_b = header_pins(b)
    mapped = np.array([b_in_a_plane(x, y, clock) for x, y in grid_b])
    if len(grid_a) != len(mapped):
        worst = float("inf")
    else:
        distance = np.linalg.norm(grid_a[:, None, :] - mapped[None, :, :], axis=2)
        worst = float(max(distance.min(axis=1).max(), distance.min(axis=0).max()))
    pin1 = None
    if pin1_a is not None and pin1_b is not None:
        pin1 = float(np.linalg.norm(pin1_a - b_in_a_plane(pin1_b[0], pin1_b[1], clock)))
    return HeaderMatch(clock, worst, pin1)


# ----------------------------------------------------------------------------- result


@dataclass
class TreeEdge:
    join: Join
    parent: str
    child: str
    reversed: bool  # True when the parent holds interface b
    frame_a: np.ndarray
    frame_b: np.ndarray
    clock: float

    @property
    def joint_id(self) -> str:
        return self.join.joint or self.join.join_id

    def child_pose(self, parent_pose: np.ndarray, q: Any = None) -> np.ndarray:
        kind = self.join.kind
        mate = mate_matrix(self.clock, self.join.offset,
                           revolute=float(q) if kind == "revolute" and q is not None else 0.0,
                           slide=float(q) if kind == "slider" and q is not None else 0.0,
                           ball=q if kind == "ball" and q is not None else None)
        if not self.reversed:
            return parent_pose @ self.frame_a @ mate @ invert(self.frame_b)
        return parent_pose @ self.frame_b @ invert(mate) @ invert(self.frame_a)


@dataclass
class AssemblyResult:
    root: str
    poses: dict[str, np.ndarray] = field(default_factory=dict)
    tree: list[TreeEdge] = field(default_factory=list)
    loops: list[dict[str, Any]] = field(default_factory=list)
    joins: dict[str, dict[str, Any]] = field(default_factory=dict)
    joints: list[Joint] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    interfaces: dict[str, ResolvedInterface] = field(default_factory=dict)
    root_pose: np.ndarray = field(default_factory=lambda: np.eye(4))
    fastener_frames: dict[str, dict[str, Any]] = field(default_factory=dict)
    matches: dict[str, "PatternMatch"] = field(default_factory=dict)  # join id -> matched hole pairs
    valid: dict[str, tuple[ResolvedInterface, ResolvedInterface]] = field(default_factory=dict)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity is Severity.ERROR]

    def poses_at(self, q: dict[str, Any] | None = None) -> dict[str, np.ndarray]:
        """Object poses with moving joints at ``q`` (keyed by joint id or join
        id; revolute rad, slider m, ball rotation vector in a's interface frame)."""
        q = q or {}
        poses = {self.root: self.root_pose.copy()}
        for edge in self.tree:  # tree edges are stored in BFS order
            value = q.get(edge.joint_id, q.get(edge.join.join_id))
            poses[edge.child] = edge.child_pose(poses[edge.parent], value)
        return poses

    def interface_world(self, ref: str, poses: dict[str, np.ndarray] | None = None) -> np.ndarray:
        iface = self.interfaces[ref]
        return (poses or self.poses)[iface.obj] @ iface.frame

    def to_json(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "poses": {obj: {"xyz": pose[:3, 3].tolist(), "rpy": rpy_from_matrix(pose), "matrix": pose.tolist()}
                      for obj, pose in self.poses.items()},
            "tree": [{"join": e.join.join_id, "parent": e.parent, "child": e.child, "reversed": e.reversed} for e in self.tree],
            "joins": self.joins,
            "fastener_frames": self.fastener_frames,
            "loops": self.loops,
            "joints": [joint.model_dump(mode="json") for joint in self.joints],
            "interfaces": {ref: {"kind": iface.kind, "frame": (self.poses[iface.obj] @ iface.frame).tolist()}
                           for ref, iface in self.interfaces.items() if iface.obj in self.poses},
        }


# ----------------------------------------------------------------------------- compiler


class AssemblyCompiler:
    """Compiles ``spec.assembly`` against the catalog. Pure numpy; never raises
    for design errors (they become diagnostics)."""

    def __init__(self, spec: RobotSpec, catalog: "Catalog") -> None:
        if spec.assembly is None:
            raise ValueError("spec has no assembly section")
        self.spec = spec
        self.catalog = catalog
        self.assembly = spec.assembly
        self.position_tol = float(self.assembly.tolerance.position)
        self.angle_tol = float(self.assembly.tolerance.angle)
        self.result = AssemblyResult(root=self.assembly.root)
        self.valid: dict[str, tuple[ResolvedInterface, ResolvedInterface]] = {}
        self.clocks: dict[str, float] = {}
        self.matches: dict[str, PatternMatch] = {}

    # ------------------------------------------------------------- helpers
    def add(self, diagnostic: Diagnostic) -> None:
        diagnostic.stage = diagnostic.stage or "assembly"
        self.result.diagnostics.append(diagnostic)

    def _interface(self, ref: str, join: Join) -> ResolvedInterface | None:
        obj, _, name = ref.partition(".")
        comp = self.spec.components[obj]
        if comp.type not in self.catalog.components:
            self.add(Diagnostic.error("component_type_unknown", f"{obj}: type {comp.type!r} is not in the catalog",
                                      subjects=[obj, join.join_id], data={"type": comp.type}))
            return None
        if ref in self.result.interfaces:
            return self.result.interfaces[ref]
        ctype = self.catalog.component(comp.type)
        spec_iface: "Interface | None" = ctype.interfaces.get(name)
        if spec_iface is None:
            self.add(Diagnostic.error(
                "interface_unknown", f"join {join.join_id}: {comp.type} has no interface {name!r}",
                subjects=[obj, join.join_id], data={"type": comp.type, "interface": name, "available": sorted(ctype.interfaces)},
                hint="Use one of the available interfaces or add it to the catalog entry."))
            return None
        resolved = ResolvedInterface(obj, name, spec_iface.kind, frame_matrix(spec_iface.origin, spec_iface.z, spec_iface.x),
                                     dict(spec_iface.params))
        mirror = comp.params.get("mirror")
        if mirror is not None:
            resolved = mirrored_interface(resolved, [float(v) for v in mirror])
        resolved.material = ctype.mechanical.material or ""
        if resolved.params.get("pattern_confirmed") is False:
            holes = resolved.holes
            span = [max((getattr(h, axis) for h in holes), default=0)
                    - min((getattr(h, axis) for h in holes), default=0) for axis in ("x", "y")]
            self.add(Diagnostic.warning(
                "mount_pattern_estimated", f"{obj}.{name}: mounting pattern is unconfirmed; "
                f"{len(holes)} holes, diameters {[round(h.d * 1e3, 3) for h in holes]} mm; "
                f"hole centres span {span[0] * 1e3:g} × {span[1] * 1e3:g} mm; "
                "measure the owned part before releasing its mount",
                subjects=[obj, join.join_id],
                data={"interface": resolved.ref, "holes": [[h.x, h.y, h.d] for h in holes],
                      "source": resolved.params.get("pattern_source", ""), "units": "m"}))
        self.result.interfaces[ref] = resolved
        return resolved

    # ------------------------------------------------------------- pipeline
    def compile(self) -> AssemblyResult:
        root = self.assembly.root
        root_comp = self.spec.components[root]
        self.result.root_pose = pose_matrix(root_comp.pose.xyz, root_comp.pose.rpy)
        for join in self.assembly.joins:
            a = self._interface(join.a, join)
            b = self._interface(join.b, join)
            if a is None or b is None:
                continue
            if a.obj == b.obj:
                self.add(Diagnostic.error("join_self", f"join {join.join_id} joins {a.obj} to itself",
                                          subjects=[a.obj, join.join_id], data={"a": join.a, "b": join.b}))
                continue
            if self._check_kinds(join, a, b):
                self.valid[join.join_id] = (a, b)
                self.clocks[join.join_id] = self._check_compatibility(join, a, b)
        self._check_occupancy()
        self._build_tree()
        self._check_loops()
        self._derive_joints()
        self._record_joins()
        self.result.matches = self.matches
        self.result.valid = self.valid
        return self.result

    # ------------------------------------------------------------- kinds
    def _check_kinds(self, join: Join, a: ResolvedInterface, b: ResolvedInterface) -> bool:
        allowed = allowed_join_kinds(a.kind, b.kind)
        if not allowed:
            partners = sorted({k for pair in _PAIR_JOINS if a.kind in pair for k in (pair - {a.kind} or pair)})
            self.add(Diagnostic.error(
                "interface_kind_mismatch", f"join {join.join_id}: {a.kind} ({join.a}) cannot mate with {b.kind} ({join.b})",
                subjects=[a.obj, b.obj, join.join_id],
                data={"a_kind": a.kind, "b_kind": b.kind, "a_partners": partners},
                hint="Join interfaces whose kinds mate (shaft↔bore, header↔socket, planar seat↔planar seat, ...)."))
            return False
        if join.kind not in allowed:
            self.add(Diagnostic.error(
                "join_kind_invalid", f"join {join.join_id}: a {join.kind} join is not possible between {a.kind} and {b.kind}",
                subjects=[a.obj, b.obj, join.join_id], data={"join_kind": join.kind, "allowed": sorted(allowed)}))
            return False
        return True

    # ------------------------------------------------------------- compatibility
    def _check_compatibility(self, join: Join, a: ResolvedInterface, b: ResolvedInterface) -> float:
        """Run the per-kind checks and return the clock angle to compile with."""
        kinds = {a.kind, b.kind}
        if kinds <= PLANAR:
            return self._check_holes(join, a, b)
        if kinds == {"shaft", "bore"}:
            return self._check_shaft_bore(join, a, b)
        if kinds == {"header", "socket"}:
            return self._check_header(join, a, b)
        clock = join.clock or 0.0
        if kinds == {"spline"}:
            self._check_spline(join, a, b)
        elif kinds == {"ball_stud", "ball_socket"}:
            self._check_ball(join, a, b)
        elif kinds == {"strap_seat", "strap_slot"}:
            seat, slot = (a, b) if a.kind == "strap_seat" else (b, a)
            width_seat, width_slot = seat.quantity("width", 0.0), slot.quantity("width", 0.0)
            if width_slot + POSITION_EPS < width_seat:
                self.add(Diagnostic.error(
                    "strap_mismatch", f"join {join.join_id}: strap slot {width_slot * 1e3:.1f} mm is narrower than seat {width_seat * 1e3:.1f} mm",
                    subjects=[a.obj, b.obj, join.join_id], data={"slot_width": width_slot, "seat_width": width_seat}))
        elif kinds == {"connector_body"}:
            self._check_connector(join, a, b)
        return clock

    def _check_holes(self, join: Join, a: ResolvedInterface, b: ResolvedInterface) -> float:
        holes_a, holes_b = a.holes, b.holes
        fastener = parse_fastener(join.fasteners.type) if join.fasteners else None
        if join.fasteners and fastener is None:
            self.add(Diagnostic.warning("fastener_type_invalid", f"join {join.join_id}: cannot parse fastener {join.fasteners.type!r}",
                                        subjects=[join.join_id], data={"type": join.fasteners.type},
                                        hint="Write metric fasteners as M<d>x<length>, e.g. M3x10."))
        fastener_d = fastener[0] if fastener else None
        required = join.fasteners.count if join.fasteners and join.fasteners.count is not None else None
        if not holes_a or not holes_b:
            if join.fasteners and (holes_a or holes_b or required):
                self._hole_mismatch(join, a, b, match_holes(holes_a, holes_b, join.clock or 0.0, fastener_d, required or 1),
                                    holes_a, holes_b, [])
            return join.clock or 0.0
        if join.clock is not None:
            clock = join.clock
            match = match_holes(holes_a, holes_b, clock, fastener_d, required)
            if not match.ok:
                self._hole_mismatch(join, a, b, match, holes_a, holes_b, self._matching_clocks(holes_a, holes_b, fastener_d, required))
        elif a.clock_free and b.clock_free:
            clock = 0.0
            match = match_holes(holes_a, holes_b, clock, fastener_d, required)
            if not match.ok:
                self._hole_mismatch(join, a, b, match, holes_a, holes_b, [])
        else:
            matches = self._clock_matches(holes_a, holes_b, fastener_d, required)
            good = [m for m in matches if m.ok]
            if len(good) == 1:
                clock, match = good[0].clock, good[0]
            elif len(good) > 1:
                clock, match = good[0].clock, good[0]
                self.add(Diagnostic.error(
                    "clock_ambiguous",
                    f"join {join.join_id}: hole patterns match at {len(good)} clock angles "
                    f"({', '.join(f'{math.degrees(m.clock):.1f}' for m in good)} deg); set an explicit clock",
                    subjects=[a.obj, b.obj, join.join_id],
                    data={"candidates": [m.clock for m in good], "candidates_deg": [math.degrees(m.clock) for m in good]},
                    hint="Add `clock: <angle>` to the join to choose the orientation."))
            else:
                match = min(matches, key=lambda m: (-len(m.pairs), m.max_excess)) if matches else \
                    match_holes(holes_a, holes_b, 0.0, fastener_d, required)
                clock = match.clock
                self._hole_mismatch(join, a, b, match, holes_a, holes_b, [])
        self.matches[join.join_id] = match
        self._check_hole_sizes(join, a, b, match, holes_a, holes_b, fastener_d)
        if fastener or any(h.kind == "thread" for h in holes_a + holes_b):
            self._check_fastener_length(join, a, b, match, holes_a, holes_b, fastener)
        return clock

    def _clock_matches(self, holes_a: list[Hole], holes_b: list[Hole], fastener_d: float | None,
                       required: int | None) -> list[PatternMatch]:
        # The radius test tolerance is the largest allowance any hole pair can have.
        slack = max((hole_allowance(ha, hb, fastener_d) for ha in holes_a for hb in holes_b), default=0.0) + POSITION_EPS
        return [match_holes(holes_a, holes_b, clock, fastener_d, required) for clock in candidate_hole_clocks(holes_a, holes_b, slack)]

    def _matching_clocks(self, holes_a: list[Hole], holes_b: list[Hole], fastener_d: float | None, required: int | None) -> list[float]:
        return [m.clock for m in self._clock_matches(holes_a, holes_b, fastener_d, required) if m.ok]

    def _hole_mismatch(self, join: Join, a: ResolvedInterface, b: ResolvedInterface, match: PatternMatch,
                       holes_a: list[Hole], holes_b: list[Hole], matching_clocks: list[float]) -> None:
        over = [{"a_hole": i, "b_hole": j, "offset": offset, "allowance": allow}
                for (i, j, offset), allow in zip(match.pairs, match.allowances) if offset > allow + POSITION_EPS]
        data = {
            "clock": match.clock, "clock_deg": math.degrees(match.clock),
            "max_offset": match.max_offset, "allowance": min(match.allowances, default=0.0),
            "matched": len(match.pairs), "required": match.required,
            "over_limit": over,
            "unmatched_a": [{"index": i, "xy": [holes_a[i].x, holes_a[i].y]} for i in match.unmatched_a],
            "unmatched_b": [{"index": j, "xy": b_in_a_plane(holes_b[j].x, holes_b[j].y, match.clock).tolist()} for j in match.unmatched_b],
        }
        if matching_clocks:
            data["matching_clocks_deg"] = [math.degrees(c) for c in matching_clocks]
        self.add(Diagnostic.error(
            "hole_pattern_mismatch",
            f"join {join.join_id}: hole patterns do not line up at clock {math.degrees(match.clock):.1f} deg: "
            f"{len(match.pairs)}/{match.required} holes matched"
            + (f", max center offset {match.max_offset * 1e3:.3f} mm (allowed {data['allowance'] * 1e3:.3f} mm)" if match.pairs else ""),
            subjects=[a.obj, b.obj, join.join_id], data=data,
            hint="Move the holes in the catalog interface (or choose a matching clock) so every fastener passes straight."))

    def _check_hole_sizes(self, join: Join, a: ResolvedInterface, b: ResolvedInterface, match: PatternMatch,
                          holes_a: list[Hole], holes_b: list[Hole], fastener_d: float | None) -> None:
        violations: list[dict[str, Any]] = []
        for i, j, _ in match.pairs:
            ha, hb = holes_a[i], holes_b[j]
            kinds = sorted((ha.kind, hb.kind))
            pair = {"a_hole": i, "b_hole": j, "a_d": ha.d, "b_d": hb.d, "a_kind": ha.kind, "b_kind": hb.kind}
            if kinds in (["tapped", "tapped"], ["thread", "thread"]):
                violations.append({**pair, "reason": f"{ha.kind} hole cannot join a {hb.kind} hole"})
                continue
            if kinds == ["tapped", "thread"]:
                if abs(ha.d - hb.d) > POSITION_EPS:
                    violations.append({**pair, "reason": "thread sizes differ"})
                continue
            threaded = [h.d for h in (ha, hb) if h.kind != "clearance"]
            nominal = threaded[0] if threaded else fastener_d
            if nominal is None:
                continue
            if fastener_d is not None and abs(fastener_d - nominal) > POSITION_EPS:
                violations.append({**pair, "nominal": nominal, "fastener_d": fastener_d, "reason": "fastener size differs from thread"})
            key = _nominal_key(nominal)
            for hole, iface in ((ha, a), (hb, b)):
                if hole.kind != "clearance":
                    continue
                printed = self.catalog.print_fit(iface.material) if hasattr(self.catalog, "print_fit") else None
                if printed is not None:
                    # Printed holes print undersize: the process table, not ISO 273, sets the clearance.
                    lo, hi = (to_si(v) for v in printed.hole_clearance)
                    if not nominal + lo - POSITION_EPS <= hole.d <= nominal + hi + POSITION_EPS:
                        violations.append({**pair, "nominal": nominal, "hole_d": hole.d, "print_min": nominal + lo,
                                           "print_max": nominal + hi, "process": printed.process,
                                           "reason": f"printed ({printed.process}) clearance hole outside the print-fit table"})
                    continue
                if key is None:
                    if hole.d <= nominal:
                        violations.append({**pair, "nominal": nominal, "reason": "clearance hole not larger than fastener"})
                    continue
                fine, _, coarse = ISO_273[key]
                if not fine * 1e-3 - POSITION_EPS <= hole.d <= coarse * 1e-3 + POSITION_EPS:
                    violations.append({**pair, "nominal": nominal, "hole_d": hole.d, "iso273_min": fine * 1e-3,
                                       "iso273_max": coarse * 1e-3, "reason": "clearance hole outside ISO 273 fine..coarse"})
        if violations:
            first = violations[0]
            self.add(Diagnostic.error(
                "hole_size_mismatch", f"join {join.join_id}: {len(violations)} hole pair(s) incompatible: {first['reason']}",
                subjects=[a.obj, b.obj, join.join_id], data={"violations": violations, **{k: v for k, v in first.items() if k != "reason"}},
                hint="Pair each threaded hole with an ISO 273 clearance hole for the same nominal size."))

    def _check_fastener_length(self, join: Join, a: ResolvedInterface, b: ResolvedInterface, match: PatternMatch,
                               holes_a: list[Hole], holes_b: list[Hole], fastener: tuple[float, float] | None) -> None:
        """Screw length from the stack: grip = clearance plate thickness(es) +
        offset; minimum = grip + engagement (factor × d into a tapped hole, or
        an ISO 4032 nut for through bolts); maximum = grip + thread depth."""
        problems: list[dict[str, Any]] = []
        for i, j, _ in match.pairs:
            ha, hb = holes_a[i], holes_b[j]
            if ha.kind == hb.kind and ha.kind != "clearance":
                continue  # no fastener can join two threaded holes (hole_size_mismatch)
            holes = {h.kind: (h, iface) for h, iface in ((ha, a), (hb, b))}
            clearance = [h for h in (ha, hb) if h.kind == "clearance"]
            grip = sum(h.depth for h in clearance) + join.offset
            if "thread" in holes:
                stud, _ = holes["thread"]
                length, d = stud.depth, stud.d
                if "tapped" in holes:
                    tapped, iface = holes["tapped"]
                    factor = iface.quantity("min_engagement_factor", 1.0)
                    # A nut (through thread) may be passed by the stud; a blind tapped hole bottoms out.
                    low = grip + factor * d
                    high = math.inf if iface.params.get("through_thread") else grip + tapped.depth
                else:
                    # A stud through a clearance hole: the female part beyond is joined separately.
                    low, high = grip, math.inf
                source = f"{stud.d * 1e3:g} mm stud"
            elif fastener is not None:
                d, length = fastener
                if "tapped" in holes:
                    tapped, iface = holes["tapped"]
                    factor = iface.quantity("min_engagement_factor", 1.0)
                    low, high = grip + factor * d, grip + tapped.depth
                else:
                    nut = ISO_4032_NUT.get(_nominal_key(d) or -1.0, 0.8 * d * 1e3) * 1e-3
                    low, high = grip + nut, math.inf
                source = join.fasteners.type if join.fasteners else ""
            else:
                continue
            if length < low - POSITION_EPS or length > high + POSITION_EPS:
                problems.append({"a_hole": i, "b_hole": j, "fastener": source, "length": length, "grip": grip,
                                 "min_length": low, "max_length": None if math.isinf(high) else high})
        if problems:
            worst = problems[0]
            limit = (f"needs >= {worst['min_length'] * 1e3:.2f} mm" if worst["length"] < worst["min_length"]
                     else f"bottoms out above {worst['max_length'] * 1e3:.2f} mm")
            self.add(Diagnostic.error(
                "fastener_length_invalid",
                f"join {join.join_id}: {worst['fastener']} length {worst['length'] * 1e3:.2f} mm {limit} (grip {worst['grip'] * 1e3:.2f} mm)",
                subjects=[a.obj, b.obj, join.join_id], data={**worst, "pairs": problems},
                hint="Choose a fastener length between min_length and max_length."))

    def _check_shaft_bore(self, join: Join, a: ResolvedInterface, b: ResolvedInterface) -> float:
        shaft, bore = (a, b) if a.kind == "shaft" else (b, a)
        clock = join.clock if join.clock is not None else 0.0
        subjects = [a.obj, b.obj, join.join_id]
        shaft_d, bore_d = shaft.quantity("d"), bore.quantity("d")
        shaft_tol, bore_tol = shaft.params.get("tol"), bore.params.get("tol")
        try:
            shaft_min, shaft_max = iso_286_limits(shaft_d, shaft_tol, hole=False)
            bore_min, bore_max = iso_286_limits(bore_d, bore_tol, hole=True)
            loose, press = running_clearance_max(shaft_d), press_interference_max(shaft_d)
        except ValueError as error:
            self.add(Diagnostic.error("fit_out_of_range", f"join {join.join_id}: {error}", subjects=subjects,
                                      data={"shaft_d": shaft_d, "bore_d": bore_d, "shaft_tol": shaft_tol, "bore_tol": bore_tol}))
            return clock
        clearance_min, clearance_max = bore_min - shaft_max, bore_max - shaft_min
        actual = "clearance" if clearance_min >= 0 else "interference" if clearance_max <= 0 else "transition"
        required = join.fit or ("clearance" if join.kind in ("revolute", "slider") else "any")
        limits = {"clearance": (0.0, loose), "transition": (-press, loose), "interference": (-press, 0.0), "any": (-press, loose)}[required]
        printed = next((fit for fit in (self._print_fit(bore), self._print_fit(shaft)) if fit is not None), None)
        if printed is not None:
            # Printed parts: the process table replaces ISO 286 (holes shrink, pins grow).
            slide = [to_si(v) for v in printed.sliding_clearance]
            interference = [to_si(v) for v in printed.press_interference]
            limits = {"clearance": (slide[0], slide[1]), "transition": (-interference[1], slide[1]),
                      "interference": (-interference[1], -interference[0]), "any": (-interference[1], slide[1])}[required]
        class_ok = required == "any" or actual == required
        if not class_ok or clearance_min < limits[0] - POSITION_EPS * 1e-3 or clearance_max > limits[1] + POSITION_EPS * 1e-3:
            self.add(Diagnostic.error(
                "fit_out_of_range",
                f"join {join.join_id}: shaft {shaft_d * 1e3:.3f}{shaft_tol or ''} in bore {bore_d * 1e3:.3f}{bore_tol or ''} gives "
                f"diametral clearance {clearance_min * 1e6:+.0f}..{clearance_max * 1e6:+.0f} um ({actual}); "
                f"{required} fit requires {limits[0] * 1e6:+.0f}..{limits[1] * 1e6:+.0f} um",
                subjects=subjects,
                data={"shaft_d": shaft_d, "bore_d": bore_d, "shaft_tol": shaft_tol, "bore_tol": bore_tol,
                      "clearance_min": clearance_min, "clearance_max": clearance_max, "actual_fit": actual,
                      "required_fit": required, "limit_min": limits[0], "limit_max": limits[1],
                      "print_process": printed.process if printed is not None else None},
                hint="Change the bore or shaft diameter / ISO 286 grade so the clearance range lies within the limits."))
        flat_shaft, flat_bore = shaft.quantity("flat_offset"), bore.quantity("flat_offset")
        if join.kind == "drive_flat":
            if flat_shaft is None or flat_bore is None:
                self.add(Diagnostic.error("drive_flat_invalid", f"join {join.join_id}: drive_flat needs a flat on both shaft and bore",
                                          subjects=subjects, data={"shaft_flat_offset": flat_shaft, "bore_flat_offset": flat_bore}))
            else:
                if flat_shaft > flat_bore + POSITION_EPS:
                    self.add(Diagnostic.error(
                        "fit_out_of_range", f"join {join.join_id}: shaft flat at {flat_shaft * 1e3:.3f} mm does not enter bore flat at {flat_bore * 1e3:.3f} mm",
                        subjects=subjects, data={"shaft_flat_offset": flat_shaft, "bore_flat_offset": flat_bore,
                                                 "interference": flat_shaft - flat_bore}))
                if angle_distance(clock, 0.0) > self.angle_tol:
                    self.add(Diagnostic.error(
                        "drive_flat_invalid", f"join {join.join_id}: flats are misaligned by {math.degrees(angle_distance(clock, 0.0)):.2f} deg",
                        subjects=subjects, data={"clock": clock, "misalignment": angle_distance(clock, 0.0)},
                        hint="Flats sit on each interface's +x side; drive_flat joins need clock 0."))
        shaft_length, bore_depth = shaft.quantity("length"), bore.quantity("depth")
        # Rolling bearings, bonded inserts and bushed journals are not plain 1 x d journals.
        exempt = bool(shaft.params.get("short_engagement_ok") or bore.params.get("short_engagement_ok"))
        if shaft_length is not None and bore_depth is not None and not exempt:
            engaged = min(shaft_length - join.offset, bore_depth)
            if engaged < shaft_d - POSITION_EPS:
                # Rule of thumb: a bearing/hub length below 1 x d tilts and wears quickly.
                self.add(Diagnostic.warning(
                    "shaft_engagement_short", f"join {join.join_id}: engaged length {engaged * 1e3:.2f} mm < shaft diameter {shaft_d * 1e3:.2f} mm",
                    subjects=subjects, data={"engaged_length": engaged, "shaft_d": shaft_d, "shaft_length": shaft_length,
                                             "bore_depth": bore_depth, "offset": join.offset}))
        return clock

    def _print_fit(self, iface: ResolvedInterface):
        """Print-fit table entry when the interface's object is 3D printed (else None)."""
        finder = getattr(self.catalog, "print_fit", None)
        return finder(iface.material) if finder is not None and iface.material else None

    def _check_header(self, join: Join, a: ResolvedInterface, b: ResolvedInterface) -> float:
        header, socket = (a, b) if a.kind == "header" else (b, a)
        subjects = [a.obj, b.obj, join.join_id]
        geometry = {
            "header_pitch": header.quantity("pitch"), "socket_pitch": socket.quantity("pitch"),
            "header_rows": int(header.params.get("rows", 1)), "header_cols": int(header.params.get("cols", 1)),
            "socket_rows": int(socket.params.get("rows", 1)), "socket_cols": int(socket.params.get("cols", 1)),
        }
        if join.clock is not None:
            matches = [match_header(a, b, join.clock)]
        else:
            matches = [match_header(a, b, k * math.pi / 2.0) for k in range(4)]
            good = [m for m in matches if m.ok(self.position_tol)]
            if len(good) > 1:
                self.add(Diagnostic.error(
                    "clock_ambiguous", f"join {join.join_id}: header fits at {len(good)} clock angles; add pin1 or an explicit clock",
                    subjects=subjects, data={"candidates": [m.clock for m in good], "candidates_deg": [math.degrees(m.clock) for m in good]}))
                return good[0].clock
            if good:
                return good[0].clock
        best = min(matches, key=lambda m: (m.max_pin_offset, m.pin1_offset or 0.0))
        if not best.ok(self.position_tol):
            self.add(Diagnostic.error(
                "header_mismatch",
                f"join {join.join_id}: header {geometry['header_rows']}x{geometry['header_cols']} @ {geometry['header_pitch'] * 1e3:.2f} mm "
                f"vs socket {geometry['socket_rows']}x{geometry['socket_cols']} @ {geometry['socket_pitch'] * 1e3:.2f} mm: "
                f"max pin offset {best.max_pin_offset * 1e3:.3f} mm, pin-1 offset "
                f"{'n/a' if best.pin1_offset is None else f'{best.pin1_offset * 1e3:.3f} mm'}",
                subjects=subjects,
                data={**geometry, "clock": best.clock, "clock_deg": math.degrees(best.clock),
                      "max_pin_offset": best.max_pin_offset, "pin1_offset": best.pin1_offset},
                hint="Match pitch, rows and columns, and put pin 1 of the socket over pin 1 of the header."))
        return best.clock

    def _check_spline(self, join: Join, a: ResolvedInterface, b: ResolvedInterface) -> None:
        genders = {a.params.get("gender"), b.params.get("gender")}
        teeth = (int(a.params.get("teeth", 0)), int(b.params.get("teeth", 0)))
        diameters = (a.quantity("d", 0.0), b.quantity("d", 0.0))
        if genders != {"male", "female"} or teeth[0] != teeth[1] or abs(diameters[0] - diameters[1]) > 5e-5:
            self.add(Diagnostic.error(
                "spline_mismatch", f"join {join.join_id}: splines {teeth[0]}T/{diameters[0] * 1e3:.2f} mm and {teeth[1]}T/{diameters[1] * 1e3:.2f} mm do not mesh",
                subjects=[a.obj, b.obj, join.join_id],
                data={"a_teeth": teeth[0], "b_teeth": teeth[1], "a_d": diameters[0], "b_d": diameters[1], "genders": sorted(map(str, genders))}))

    def _check_ball(self, join: Join, a: ResolvedInterface, b: ResolvedInterface) -> None:
        stud, socket = (a, b) if a.kind == "ball_stud" else (b, a)
        clearance = socket.quantity("d", 0.0) - stud.quantity("d", 0.0)
        # Molded ball links snap on with 0..0.2 mm diametral clearance.
        if not -POSITION_EPS <= clearance <= 2e-4 + POSITION_EPS:
            self.add(Diagnostic.error(
                "fit_out_of_range", f"join {join.join_id}: ball socket clearance {clearance * 1e3:+.3f} mm outside 0..0.2 mm",
                subjects=[a.obj, b.obj, join.join_id],
                data={"stud_d": stud.quantity("d", 0.0), "socket_d": socket.quantity("d", 0.0), "clearance_min": clearance,
                      "clearance_max": clearance, "limit_min": 0.0, "limit_max": 2e-4}))

    def _check_connector(self, join: Join, a: ResolvedInterface, b: ResolvedInterface) -> None:
        type_a, type_b = a.params.get("type"), b.params.get("type")
        connector = self.catalog.connectors.get(type_a) if type_a else None
        if connector is not None and type_b not in connector.mates_with:
            self.add(Diagnostic.error(
                "connector_mismatch", f"join {join.join_id}: connector {type_a} does not mate with {type_b}",
                subjects=[a.obj, b.obj, join.join_id], data={"a_type": type_a, "b_type": type_b, "mates_with": connector.mates_with}))

    # ------------------------------------------------------------- structure
    def _check_occupancy(self) -> None:
        uses: dict[str, list[str]] = {}
        for join_id, (a, b) in self.valid.items():
            uses.setdefault(a.ref, []).append(join_id)
            uses.setdefault(b.ref, []).append(join_id)
        for ref, joins in uses.items():
            capacity = self.result.interfaces[ref].capacity
            if len(joins) > capacity:
                self.add(Diagnostic.error(
                    "interface_occupied", f"interface {ref} is used by {len(joins)} joins (capacity {capacity}): {', '.join(joins)}",
                    subjects=[ref.split(".")[0], *joins], data={"interface": ref, "uses": len(joins), "capacity": capacity, "joins": joins},
                    hint="Each seat takes one part; add another interface or set params.capacity where sharing is real."))

    def _build_tree(self) -> None:
        adjacency: dict[str, list[str]] = {}
        for join_id, (a, b) in self.valid.items():
            adjacency.setdefault(a.obj, []).append(join_id)
            adjacency.setdefault(b.obj, []).append(join_id)
        root = self.assembly.root
        poses = {root: self.result.root_pose}
        parent_join: dict[str, str] = {}
        queue = deque([root])
        joins = {join.join_id: join for join in self.assembly.joins}
        while queue:
            obj = queue.popleft()
            for join_id in adjacency.get(obj, []):
                a, b = self.valid[join_id]
                other = b.obj if a.obj == obj else a.obj
                if other in poses:
                    continue
                edge = TreeEdge(joins[join_id], obj, other, reversed=a.obj != obj, frame_a=a.frame, frame_b=b.frame,
                                clock=self.clocks[join_id])
                poses[other] = edge.child_pose(poses[obj])
                parent_join[other] = join_id
                self.result.tree.append(edge)
                queue.append(other)
        self.result.poses = poses
        self.parent_join = parent_join

        joined = {obj for pair in self.valid.values() for obj in (pair[0].obj, pair[1].obj)}
        for cid, comp in self.spec.components.items():
            if cid in poses:
                continue
            authored = "pose" in comp.model_fields_set
            if cid in joined:
                island = sorted(self._island(cid, adjacency))
                self.add(Diagnostic.error(
                    "object_not_joined", f"{cid} is joined only to {', '.join(o for o in island if o != cid) or 'nothing'}, "
                    f"which never reaches the root {root}",
                    subjects=[cid], data={"island": island, "root": root},
                    hint="Join this group of objects to an object that is connected to the root."))
            elif authored:
                self.add(Diagnostic.warning(
                    "object_not_joined", f"{cid} has no joins; its authored pose is kept until it is joined",
                    subjects=[cid], data={"root": root, "authored_xyz": list(comp.pose.xyz)},
                    hint="Join one of its interfaces to an object in the assembly so its pose is derived."))
            else:
                self.add(Diagnostic.error(
                    "object_not_joined", f"{cid} is not joined to the assembly and has no pose",
                    subjects=[cid], data={"root": root, "joins": 0},
                    hint="Every object must be joined: add a join from one of its interfaces to an object in the assembly."))

    def _island(self, start: str, adjacency: dict[str, list[str]]) -> set[str]:
        seen, stack = {start}, [start]
        while stack:
            obj = stack.pop()
            for join_id in adjacency.get(obj, []):
                for other in (self.valid[join_id][0].obj, self.valid[join_id][1].obj):
                    if other not in seen:
                        seen.add(other)
                        stack.append(other)
        return seen

    def _tree_path(self, obj: str) -> list[str]:
        """Join ids from ``obj`` up to the root."""
        path = []
        while obj in self.parent_join:
            join_id = self.parent_join[obj]
            path.append(join_id)
            edge = next(e for e in self.result.tree if e.join.join_id == join_id)
            obj = edge.parent
        return path

    def _check_loops(self) -> None:
        tree_ids = {edge.join.join_id for edge in self.result.tree}
        joins = {join.join_id: join for join in self.assembly.joins}
        poses = self.result.poses
        for join_id, (a, b) in self.valid.items():
            if join_id in tree_ids or a.obj not in poses or b.obj not in poses:
                continue
            join = joins[join_id]
            expected = poses[a.obj] @ a.frame @ mate_matrix(self.clocks[join_id], join.offset)
            actual = poses[b.obj] @ b.frame
            error = expected[:3, 3] - actual[:3, 3]
            axis_a = expected[:3, 2]  # b's z at the expected pose (= -a's z)
            if join.kind == "slider":
                error = error - axis_a * float(error @ axis_a)  # sliding along the axis is free
            relative = actual[:3, :3].T @ expected[:3, :3]
            if join.kind == "ball":
                angle = 0.0
            elif join.kind == "revolute" or (a.clock_free and b.clock_free):
                # Spin about the interface axis is free: only the tilt between z axes counts.
                angle = math.atan2(float(np.linalg.norm(np.cross(actual[:3, 2], expected[:3, 2]))),
                                   float(actual[:3, 2] @ expected[:3, 2]))
            else:
                angle = rotation_angle(relative)
            position_error = float(np.linalg.norm(error))
            cycle = self._cycle(a.obj, b.obj) + [join_id]
            dof = sum(JOIN_DOF[joins[j].kind] for j in cycle)
            moving = [j for j in cycle if joins[j].kind in MOVING]
            loop = {"join": join_id, "cycle": cycle, "position_error": position_error, "angle_error": angle,
                    "angle_error_deg": math.degrees(angle), "expected": expected[:3, 3].tolist(), "actual": actual[:3, 3].tolist(),
                    "error_vector": error.tolist(), "mobility": dof - 6, "moving_joins": moving,
                    "ok": position_error <= self.position_tol and angle <= self.angle_tol}
            self.result.loops.append(loop)
            if not loop["ok"]:
                self.add(Diagnostic.error(
                    "assembly_misfit",
                    f"join {join_id} does not close: position error {position_error * 1e3:.3f} mm "
                    f"(tolerance {self.position_tol * 1e3:.3f} mm), angle error {math.degrees(angle):.3f} deg "
                    f"(tolerance {math.degrees(self.angle_tol):.3f} deg)",
                    subjects=[a.obj, b.obj, join_id],
                    data={"join": join_id, "position_error": position_error, "angle_error": angle,
                          "angle_error_deg": math.degrees(angle), "expected": loop["expected"], "actual": loop["actual"],
                          "error_vector": loop["error_vector"], "position_tolerance": self.position_tol,
                          "angle_tolerance": self.angle_tol, "cycle": cycle},
                    hint="A part in this loop has the wrong length or hole position; fix the catalog dimension so the loop closes."))
            if len(moving) == 1:
                # One 1-DOF (or ball) joint in a loop of rigid joins can never move (Kutzbach: M = Σf − 6 < 1).
                self.add(Diagnostic.error(
                    "object_overconstrained_conflict",
                    f"moving join {moving[0]} is locked: loop {' -> '.join(cycle)} is otherwise rigid (mobility {dof - 6})",
                    subjects=[a.obj, b.obj, *cycle], data={"locked_join": moving[0], "cycle": cycle, "mobility": dof - 6},
                    hint="Remove the rigid join that closes the loop or make it a moving join."))
            elif moving and dof - 6 < 1:
                self.add(Diagnostic.warning(
                    "loop_mobility_low",
                    f"loop {' -> '.join(cycle)} has Kutzbach mobility {dof - 6}; it moves only with special geometry (e.g. a planar linkage)",
                    subjects=cycle, data={"cycle": cycle, "mobility": dof - 6, "moving_joins": moving}))

    def _cycle(self, obj_a: str, obj_b: str) -> list[str]:
        path_a, path_b = self._tree_path(obj_a), self._tree_path(obj_b)
        shared = set(path_a) & set(path_b)
        return [j for j in path_a if j not in shared] + [j for j in path_b if j not in shared]

    # ------------------------------------------------------------- joints
    def _derive_joints(self) -> None:
        explicit = {joint.id: joint for joint in self.spec.mechanical.joints}
        tree_ids = {edge.join.join_id for edge in self.result.tree}
        for join in self.assembly.joins:
            if join.kind not in ("revolute", "slider") or join.join_id not in self.valid:
                continue
            if join.join_id not in tree_ids:
                a, b = self.valid[join.join_id]
                if join.joint and a.obj in self.result.poses and b.obj in self.result.poses:
                    self.add(Diagnostic.warning(
                        "joint_in_loop", f"join {join.join_id} closes a loop; joint {join.joint} is not derived from it",
                        subjects=[join.join_id], data={"joint": join.joint}))
                continue
            edge = next(e for e in self.result.tree if e.join.join_id == join.join_id)
            a, _ = self.valid[join.join_id]
            frame = self.result.poses[a.obj] @ a.frame
            axis = frame[:3, 2] * (-1.0 if edge.reversed else 1.0)
            derived = Joint(
                id=edge.joint_id,
                type=("revolute" if join.limits else "continuous") if join.kind == "revolute" else "prismatic",
                parent=edge.parent, child=edge.child, origin=frame[:3, 3].tolist(), axis=axis.tolist(),
                limits=join.limits)
            self.result.joints.append(derived)
            if derived.id in explicit:
                self._compare_joint(join, derived, explicit[derived.id])

    def _compare_joint(self, join: Join, derived: Joint, authored: Joint) -> None:
        problems: dict[str, Any] = {}
        compatible = {"revolute": {"revolute", "continuous"}, "slider": {"prismatic"}}[join.kind]
        if authored.type not in compatible:
            problems["type"] = {"authored": authored.type, "derived": derived.type}
        if (authored.parent, authored.child) != (derived.parent, derived.child):
            problems["bodies"] = {"authored": [authored.parent, authored.child], "derived": [derived.parent, derived.child]}
        axis_d = np.asarray(derived.axis) / np.linalg.norm(derived.axis)
        axis_a = np.asarray(authored.axis) / np.linalg.norm(authored.axis)
        axis_angle = math.atan2(float(np.linalg.norm(np.cross(axis_a, axis_d))), float(axis_a @ axis_d))
        if axis_angle > self.angle_tol:
            problems["axis"] = {"authored": list(authored.axis), "derived": list(derived.axis), "angle": axis_angle}
        if join.kind == "revolute":
            # Any point on the axis line is a valid origin: compare the line, not the point.
            offset = np.asarray(authored.origin) - np.asarray(derived.origin)
            distance = float(np.linalg.norm(np.cross(offset, axis_d)))
            if distance > self.position_tol:
                problems["origin"] = {"authored": list(authored.origin), "derived": list(derived.origin), "axis_distance": distance}
        if problems:
            self.add(Diagnostic.error(
                "joint_conflict", f"mechanical joint {authored.id} disagrees with join {join.join_id}: {', '.join(problems)}",
                subjects=[authored.id, join.join_id], data=problems,
                hint="Delete the explicit joint (it is derived from the join) or make it agree."))

    def _record_joins(self) -> None:
        tree_ids = {edge.join.join_id for edge in self.result.tree}
        loops = {loop["join"]: loop for loop in self.result.loops}
        for join in self.assembly.joins:
            entry: dict[str, Any] = {"a": join.a, "b": join.b, "kind": join.kind, "valid": join.join_id in self.valid}
            if join.join_id in self.valid:
                entry.update({"clock": self.clocks[join.join_id], "clock_deg": math.degrees(self.clocks[join.join_id]),
                              "offset": join.offset, "role": "tree" if join.join_id in tree_ids else "loop"})
                if join.join_id in loops:
                    entry["residual"] = {k: loops[join.join_id][k] for k in ("position_error", "angle_error", "ok")}
            self.result.joins[join.join_id] = entry


def compile_assembly(spec: RobotSpec, catalog: "Catalog") -> AssemblyResult:
    return AssemblyCompiler(spec, catalog).compile()


def apply_to_spec(spec: RobotSpec, result: AssemblyResult) -> list[Diagnostic]:
    """Back-compat: write compiled poses into ``spec.components[*].pose`` (and
    ``mount`` / derived ``mechanical.joints``) so downstream stages work
    unchanged. Authored poses on joined objects are compared first."""
    diagnostics: list[Diagnostic] = []
    assert spec.assembly is not None
    position_tol, angle_tol = float(spec.assembly.tolerance.position), float(spec.assembly.tolerance.angle)
    parents = {edge.child: edge.parent for edge in result.tree}
    for cid, pose in result.poses.items():
        comp = spec.components[cid]
        if cid != result.root and "pose" in comp.model_fields_set:
            authored = pose_matrix(comp.pose.xyz, comp.pose.rpy)
            position_error = float(np.linalg.norm(authored[:3, 3] - pose[:3, 3]))
            angle_error = rotation_angle(authored[:3, :3].T @ pose[:3, :3])
            data = {"authored_xyz": list(comp.pose.xyz), "authored_rpy": list(comp.pose.rpy),
                    "compiled_xyz": pose[:3, 3].tolist(), "compiled_rpy": rpy_from_matrix(pose),
                    "position_error": position_error, "angle_error": angle_error}
            if position_error > position_tol or angle_error > angle_tol:
                diagnostics.append(Diagnostic.error(
                    "pose_conflict", f"{cid}: authored pose differs from the compiled pose by {position_error * 1e3:.3f} mm / "
                    f"{math.degrees(angle_error):.3f} deg", subjects=[cid], data=data, stage="assembly",
                    hint="Delete the authored pose; joined objects get their pose from the joins."))
            else:
                diagnostics.append(Diagnostic.warning(
                    "authored_pose_ignored", f"{cid}: authored pose matches the compiled pose and is ignored",
                    subjects=[cid], data=data, stage="assembly", hint="Delete the redundant authored pose."))
        if cid != result.root:
            comp.pose = Pose(xyz=pose[:3, 3].tolist(), rpy=rpy_from_matrix(pose))
        if cid in parents and "mount" not in comp.model_fields_set:
            comp.mount = parents[cid]
    existing = {joint.id for joint in spec.mechanical.joints}
    spec.mechanical.joints.extend(joint for joint in result.joints if joint.id not in existing)
    return diagnostics

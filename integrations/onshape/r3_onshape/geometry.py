"""Part plans: the robot's CAD as an ordered list of standard modeling operations.

A :class:`PartPlan` is what a person would do in the Onshape UI to make one part:
sketch a profile on a plane and extrude it, revolve a profile about an axis,
drill a hole. :mod:`r3_onshape.direct` turns every operation into standard
Onshape features (no FeatureScript); :func:`plan_manifold` turns the same plan
into a local manifold3d solid so a design can be pre-checked offline.

Conventions (robot frame, meters):

* Every sketch plane is a standard plane (Top, Front or Right) or an offset of
  one, so sketches are axis-aligned. In-plane geometry may be arbitrary.
* Sketch coordinates: Top (x, y), normal +z; Front (x, z), normal -y;
  Right (y, z), normal +x (Onshape's default plane frames).
* Revolve axes and hole axes must be parallel to a robot axis.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal, Sequence

import numpy as np

Base = Literal["TOP", "FRONT", "RIGHT"]
Op = Literal["new", "add", "remove"]

# Default-plane deterministic ids in every Part Studio.
PLANE_IDS: dict[str, str] = {"TOP": "JDC", "FRONT": "JCC", "RIGHT": "JEC"}
_NORMALS: dict[str, np.ndarray] = {"TOP": np.array([0.0, 0.0, 1.0]), "FRONT": np.array([0.0, -1.0, 0.0]),
                                   "RIGHT": np.array([1.0, 0.0, 0.0])}
# Sketch axes (u, v) of each default plane in the robot frame.
_AXES: dict[str, tuple[np.ndarray, np.ndarray]] = {
    "TOP": (np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])),
    "FRONT": (np.array([1.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0])),
    "RIGHT": (np.array([0.0, 1.0, 0.0]), np.array([0.0, 0.0, 1.0])),
}
ANGLE_TOLERANCE = 1e-6
ARC_SEGMENTS = 96  # local preview tessellation of full circles
THROUGH_ALL = 1.0  # m; local stand-in for "through all"


def unit(vector: Sequence[float]) -> np.ndarray:
    v = np.asarray(vector, dtype=float)
    n = float(np.linalg.norm(v))
    if n == 0.0:
        raise ValueError("zero vector")
    return v / n


def axis_base(direction: Sequence[float]) -> tuple[Base, float]:
    """Default plane whose normal is parallel to ``direction`` and the sign of
    ``direction`` relative to that normal. Raises for non axis-aligned axes."""
    d = unit(direction)
    for base, normal in _NORMALS.items():
        dot = float(d @ normal)
        if abs(abs(dot) - 1.0) < ANGLE_TOLERANCE:
            return base, math.copysign(1.0, dot)  # type: ignore[return-value]
    raise ValueError(f"direction {d.tolist()} is not parallel to a robot axis (direct CAD sketches on default planes)")


@dataclass(frozen=True)
class Plane:
    """A default plane or an offset of it. ``offset`` is measured along the
    plane normal (Front's normal is -y, so Front offset d is the plane y = -d)."""

    base: Base
    offset: float = 0.0

    @property
    def normal(self) -> np.ndarray:
        return _NORMALS[self.base]

    @property
    def origin(self) -> np.ndarray:
        return self.normal * self.offset

    def to3d(self, uv: Sequence[float]) -> np.ndarray:
        u, v = _AXES[self.base]
        return self.origin + uv[0] * u + uv[1] * v

    def to2d(self, point: Sequence[float]) -> np.ndarray:
        u, v = _AXES[self.base]
        p = np.asarray(point, dtype=float)
        return np.array([p @ u, p @ v])

    @staticmethod
    def through(point: Sequence[float], normal: Sequence[float]) -> "Plane":
        base, _ = axis_base(normal)
        return Plane(base, round(float(np.asarray(point, dtype=float) @ _NORMALS[base]), 9))

    @staticmethod
    def containing(point: Sequence[float], axis: Sequence[float], radial: Sequence[float]) -> "Plane":
        """The default-plane offset that contains the line ``point + t axis`` and ``radial``."""
        normal = np.cross(unit(axis), unit(radial))
        return Plane.through(point, normal)


@dataclass(frozen=True)
class Segment:
    """One edge of a closed 2D loop: a line ``p0 -> p1`` or an arc about
    ``center`` from angle ``a0`` to ``a1`` (radians; ``a1 < a0`` runs clockwise)."""

    kind: Literal["line", "arc"]
    p0: tuple[float, float] = (0.0, 0.0)
    p1: tuple[float, float] = (0.0, 0.0)
    center: tuple[float, float] = (0.0, 0.0)
    radius: float = 0.0
    a0: float = 0.0
    a1: float = 0.0

    @property
    def start(self) -> np.ndarray:
        if self.kind == "line":
            return np.asarray(self.p0)
        return np.asarray(self.center) + self.radius * np.array([math.cos(self.a0), math.sin(self.a0)])

    @property
    def end(self) -> np.ndarray:
        if self.kind == "line":
            return np.asarray(self.p1)
        return np.asarray(self.center) + self.radius * np.array([math.cos(self.a1), math.sin(self.a1)])

    def points(self) -> list[np.ndarray]:
        """Discretized points excluding the end point."""
        if self.kind == "line":
            return [np.asarray(self.p0)]
        sweep = self.a1 - self.a0
        n = max(2, int(math.ceil(ARC_SEGMENTS * abs(sweep) / (2 * math.pi))))
        return [np.asarray(self.center) + self.radius * np.array([math.cos(self.a0 + sweep * i / n),
                                                                  math.sin(self.a0 + sweep * i / n)]) for i in range(n)]


@dataclass(frozen=True)
class Loop:
    """A closed 2D loop: a full circle, or a chain of segments end-to-start."""

    segments: tuple[Segment, ...] = ()
    circle_center: tuple[float, float] | None = None
    circle_radius: float = 0.0

    @staticmethod
    def circle(center: Sequence[float], radius: float) -> "Loop":
        return Loop(circle_center=(float(center[0]), float(center[1])), circle_radius=float(radius))

    @staticmethod
    def polygon(points: Sequence[Sequence[float]]) -> "Loop":
        pts = [(float(p[0]), float(p[1])) for p in points]
        return Loop(segments=tuple(Segment("line", pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))))

    @staticmethod
    def rectangle(lo: Sequence[float], hi: Sequence[float]) -> "Loop":
        return Loop.polygon([(lo[0], lo[1]), (hi[0], lo[1]), (hi[0], hi[1]), (lo[0], hi[1])])

    @staticmethod
    def d_shape(center: Sequence[float], radius: float, flat_offset: float, flat_angle: float) -> "Loop":
        """Circle of ``radius`` with a flat chord at ``flat_offset`` from the
        center; the flat's outward normal points at ``flat_angle`` (radians)."""
        half = math.acos(flat_offset / radius)
        c = (float(center[0]), float(center[1]))
        arc = Segment("arc", center=c, radius=radius, a0=flat_angle + half, a1=flat_angle + 2 * math.pi - half)
        return Loop(segments=(arc, Segment("line", tuple(arc.end), tuple(arc.start))))

    @staticmethod
    def obround(center: Sequence[float], direction_angle: float, length: float, radius: float) -> "Loop":
        """Slot of width 2 ``radius``, arc centers ``length`` apart along ``direction_angle``."""
        d = np.array([math.cos(direction_angle), math.sin(direction_angle)])
        c = np.asarray(center, dtype=float)
        c0, c1 = c - d * length / 2, c + d * length / 2
        a = direction_angle
        arc1 = Segment("arc", center=tuple(c1), radius=radius, a0=a - math.pi / 2, a1=a + math.pi / 2)
        arc0 = Segment("arc", center=tuple(c0), radius=radius, a0=a + math.pi / 2, a1=a + 3 * math.pi / 2)
        return Loop(segments=(arc1, Segment("line", tuple(arc1.end), tuple(arc0.start)), arc0,
                              Segment("line", tuple(arc0.end), tuple(arc1.start))))

    @staticmethod
    def half_disc(center: Sequence[float], radius: float) -> "Loop":
        """Right half of a circle (x >= center x) closed by the diameter: the
        revolve profile of a sphere about the vertical sketch axis."""
        c = (float(center[0]), float(center[1]))
        arc = Segment("arc", center=c, radius=radius, a0=-math.pi / 2, a1=math.pi / 2)
        return Loop(segments=(arc, Segment("line", tuple(arc.end), tuple(arc.start))))

    def points(self) -> np.ndarray:
        if self.circle_center is not None:
            t = np.linspace(0.0, 2 * math.pi, ARC_SEGMENTS, endpoint=False)
            return np.column_stack([self.circle_center[0] + self.circle_radius * np.cos(t),
                                    self.circle_center[1] + self.circle_radius * np.sin(t)])
        return np.array([p for seg in self.segments for p in seg.points()])

    def transformed(self, matrix: np.ndarray, offset: Sequence[float]) -> "Loop":
        """Apply ``p -> matrix @ p + offset`` with an orthogonal 2x2 ``matrix``
        (rotation, possibly with a reflection, which reverses arc sense)."""
        m = np.asarray(matrix, dtype=float)
        t = np.asarray(offset, dtype=float)

        def fn(p: Sequence[float]) -> tuple[float, float]:
            q = m @ np.asarray(p, dtype=float) + t
            return float(q[0]), float(q[1])

        if self.circle_center is not None:
            return Loop.circle(fn(self.circle_center), self.circle_radius)
        theta = math.atan2(m[1, 0], m[0, 0])
        mirrored = float(np.linalg.det(m)) < 0
        out = []
        for seg in self.segments:
            if seg.kind == "line":
                out.append(Segment("line", fn(seg.p0), fn(seg.p1)))
            elif mirrored:
                out.append(Segment("arc", center=fn(seg.center), radius=seg.radius, a0=theta - seg.a0, a1=theta - seg.a1))
            else:
                out.append(Segment("arc", center=fn(seg.center), radius=seg.radius, a0=seg.a0 + theta, a1=seg.a1 + theta))
        return Loop(segments=tuple(out))


@dataclass
class Prism:
    """Sketch ``loops`` on ``plane`` and extrude ``depth`` along ``direction``
    (+1 = plane normal, -1 = opposite). Inner loops of a sketch become holes."""

    plane: Plane
    loops: list[Loop]
    depth: float | None  # None = through all
    direction: float = 1.0
    op: Op = "add"
    name: str = ""


@dataclass
class Revolve:
    """Revolve a profile ``[(r, t), ...]`` (radius from the axis, distance along
    ``axis`` from ``point``) 360 degrees. ``profile_loop`` may be given instead
    of a polygon (e.g. arcs for spheres), in (r, t) coordinates."""

    point: np.ndarray
    axis: np.ndarray
    profile: list[tuple[float, float]] | None = None
    profile_loop: Loop | None = None
    op: Op = "add"
    name: str = ""
    radial: np.ndarray | None = None

    def radial_dir(self) -> np.ndarray:
        if self.radial is not None:
            return unit(self.radial)
        a = unit(self.axis)
        for candidate in (np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])):
            if abs(float(candidate @ a)) < 0.5:
                return candidate
        raise ValueError("no radial direction")

    def loop_rt(self) -> Loop:
        if self.profile_loop is not None:
            return self.profile_loop
        assert self.profile is not None
        return Loop.polygon(self.profile)


@dataclass
class Hole:
    """A drilled hole made with Onshape's Hole feature. ``point`` is on the entry
    face, ``axis`` points into the material. ``depth`` None = through all.
    ``style`` tapped holes are cut at ``diameter`` = tap drill and carry
    ``thread`` (e.g. M3 x 0.5) and ``tapped_depth``."""

    point: np.ndarray
    axis: np.ndarray
    diameter: float
    depth: float | None = None
    style: Literal["clearance", "tapped", "drilled"] = "drilled"
    thread: str = ""
    pitch: float = 0.0
    tapped_depth: float | None = None
    fit: str = "Normal"
    name: str = ""


Operation = Prism | Revolve | Hole


@dataclass
class PartPlan:
    """One named Onshape part, built in place in the robot frame."""

    name: str
    kind: Literal["purchased", "fabricated", "fastener"]
    material: str
    density: float
    mass: float | None = None  # catalog mass override for purchased parts
    color: str = "#888888"
    ops: list[Operation] = field(default_factory=list)
    owner: str = ""  # component a fastener's mass is lumped into
    mass_source: str = ""  # "catalog" | "geometry"
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------- preview
def _cross_section(loops: list[Loop]):
    import manifold3d as m3d

    return m3d.CrossSection([np.asarray(loop.points(), dtype=float) for loop in loops], m3d.FillRule.EvenOdd)


def _frame_matrix(u: np.ndarray, v: np.ndarray, w: np.ndarray, origin: np.ndarray) -> np.ndarray:
    matrix = np.zeros((3, 4))
    matrix[:, 0], matrix[:, 1], matrix[:, 2], matrix[:, 3] = u, v, w, origin
    return matrix


def operation_manifold(op: Operation):
    """Local manifold3d solid of one operation (robot frame)."""
    import manifold3d as m3d

    if isinstance(op, Prism):
        u, v = _AXES[op.plane.base]
        w = op.plane.normal * op.direction
        depth = op.depth if op.depth is not None else THROUGH_ALL
        if op.op == "remove":
            # Start cuts 10 um outside a coplanar face so mesh booleans open them (Onshape does natively).
            solid = _cross_section(op.loops).extrude(depth + 2e-5).translate([0.0, 0.0, -1e-5])
        else:
            solid = _cross_section(op.loops).extrude(depth)
        if op.direction < 0:
            # Extruding along -normal mirrors the in-plane frame; keep it right-handed.
            solid = solid.mirror([0.0, 0.0, 1.0]).translate([0.0, 0.0, 0.0])
            return solid.transform(_frame_matrix(u, v, op.plane.normal, op.plane.origin))
        return solid.transform(_frame_matrix(u, v, w, op.plane.origin))
    if isinstance(op, Revolve):
        a = unit(op.axis)
        r = op.radial_dir()
        r = unit(r - (r @ a) * a)
        b = np.cross(a, r)
        solid = m3d.Manifold.revolve(_cross_section([op.loop_rt()]), ARC_SEGMENTS)
        return solid.transform(_frame_matrix(r, b, a, np.asarray(op.point, dtype=float)))
    if isinstance(op, Hole):
        a = unit(op.axis)
        r = np.cross(a, [1.0, 0.0, 0.0]) if abs(a[0]) < 0.9 else np.cross(a, [0.0, 1.0, 0.0])
        r = unit(r)
        depth = op.depth if op.depth is not None else 1.0
        radius = op.diameter / 2
        profile = [(0.0, -1e-4), (radius, -1e-4), (radius, depth)]
        if op.depth is not None:
            profile.append((0.0, depth + radius / math.tan(math.radians(59))))  # 118 deg drill point
        else:
            profile.append((0.0, depth))
        solid = m3d.Manifold.revolve(_cross_section([Loop.polygon(profile)]), ARC_SEGMENTS)
        return solid.transform(_frame_matrix(r, np.cross(a, r), a, np.asarray(op.point, dtype=float)))
    raise TypeError(op)


def plan_manifold(plan: PartPlan):
    """Boolean the plan's operations in order into one local solid."""
    solid = None
    for op in plan.ops:
        piece = operation_manifold(op)
        is_remove = isinstance(op, Hole) or getattr(op, "op", "add") == "remove"
        if solid is None:
            if is_remove:
                raise ValueError(f"{plan.name}: first operation removes material")
            solid = piece
        elif is_remove:
            solid = solid - piece
        else:
            solid = solid + piece
    if solid is None:
        raise ValueError(f"{plan.name}: no operations")
    return solid

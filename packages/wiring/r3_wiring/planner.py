"""Curvature-constrained harness planning: a constrained elastic band solved by SciPy.

The voxel router (:mod:`r3_wiring.router`) finds the topology and a collision-free
corridor, but its paths are polylines whose corners are filleted afterwards, so
bend radius is only *checked*, never guaranteed. :func:`plan_bundle` turns a
routed bundle graph into a buildable one: every bundle segment becomes a chain
of points that SciPy's SLSQP moves to minimise length (plus a term that keeps
the bundle within tie reach of structure) subject to

* **curvature**: the circle through every three consecutive points has radius
  ``>= R_min`` (the bundle's catalog factor x OD). With edges ``a = |u|``,
  ``b = |v|`` and chord ``c = |u + v|`` the circumradius is
  ``R = a b c / (2 |u x v|)``, so the constraint is ``a b c - 2 R_min |u x v| >= 0``
  (plus ``u . v >= 0``, which rules out reversals where ``|u x v| -> 0``);
* **tangency at junctions**: every junction (fan-out breakout, splice sleeve,
  breakout, pass-through, floating mated pair, cable support) has one axis;
  segments leave it along ``+axis`` or ``-axis`` (wires that pass through a
  junction keep a continuous tangent, so a breakout leaves its trunk
  tangentially). A circular arc of radius ``R_min`` tangent to the axis reaches
  the first point at a chord angle of ``asin(l / 2 R_min)``, which bounds the
  first edge's angle to the axis;
* **clearance**: the router's static distance field (trilinear, 3 mm voxels)
  ``>= r + margin`` and the moving-parts swept field ``>= r +
  requirements.min_wire_clearance_moving``. Points inside a connector's
  termination zone may not get closer than the routed path already was;
* **edge length**: edges at most 1.6x the nominal spacing (no sparse chords
  across obstacles).

Decision variables are optimised *jointly*: the interior points, free junction
positions (breakouts; splices within :data:`SPLICE_MOVE` of their authored
position; floating mated pairs within :data:`FLOATING_MOVE`), free junction axes
(splice sleeves, breakouts, vias, mated pairs) and the exit axis of integral
pigtails within :data:`PIGTAIL_CONE` of the authored direction. Harnesses joined
by floating mated pairs are planned as one problem.

If SLSQP cannot satisfy every constraint, the plan keeps the least-violating
geometry and returns *infeasibility constraints*: each violated constraint with
its location, the required and achieved value, the parts that bound it and an
action. Nothing is reported valid that is not.

Pin-level terminations stay fixed: every conductor runs from its pin's conductor
exit along a fan-out (built by the router, tangent to the pin's wire-entry axis)
to a *breakout* where it joins the bundle core. The planner plans the bundle
cores from those breakouts: a breakout is a fixed junction whose axis is the
fan-out's arrival tangent, so the tangency and curvature constraints hold across
the breakout, and the fan-outs are kept unchanged. Floating mated pairs that
carry fan-outs stay where the router placed them (their pins are on them).

Integration: :func:`plan_routing` applies the plans to a
:class:`r3_wiring.router.RoutingResult`; the router calls it at the end of
``HarnessRouter.route`` (``RouterOptions.plan``) and the stage calls it again
with the cable supports as junctions.
"""

from __future__ import annotations

import math
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import sparse
from scipy.optimize import NonlinearConstraint, minimize

from r3_model.diagnostics import Diagnostic

from .geometry import polyline_length, resample_polyline
from .grid import FIELD_CUTOFF, VoxelGrid

SPLICE_MOVE = 0.025  # m: a splice may move this far from robot.yaml (reported as a recommended edit)
FLOATING_MOVE = 0.02  # m: a floating mated pair may move this far from the router's placement
BRANCH_MOVE = 0.03  # m: a breakout may move this far
PIGTAIL_CONE = math.radians(30.0)  # integral pigtails may leave their grommet this far off the authored direction
SUPPORT_TILT = math.radians(15.0)  # bundle axis tilt allowed where it leaves a support strap
STATIC_MARGIN = 0.0012  # m, extra static clearance: trilinear 3 mm field error near thin plates and edges
MOVING_MARGIN = 0.0005  # m, same for the moving-parts field
TERMINATION_SLACK = 0.0  # m: inside a termination zone a point may not lose any of its routed clearance
BEND_MARGIN = 1.03  # planned radius / catalog minimum
EDGE_STRETCH = 1.6
SURFACE_BAND = 0.006  # m: bundle surface farther than this from structure cannot sit on a tie mount
SURFACE_WEIGHT = 4.0  # per metre of bundle at one band of excess distance (vs. 1 per metre of length)
PENALTY_WEIGHTS = (1.0, 10.0, 100.0, 1000.0)
ELASTIC_WEIGHT = 50.0  # objective units (~m) per unit of the largest scaled violation
DENSE_SLSQP_MAX_VARIABLES = 256  # dense QP work grows cubically; larger systems retain sparse Jacobians
FEASIBILITY_TOL = {"curvature": 0.005, "clearance": 0.0002, "tangent": 0.001, "edge": 0.02, "move": 0.0, "cone": 0.002}


# ------------------------------------------------------------------ fields
@dataclass
class ClearanceField:
    """Trilinear distance fields on the router's voxel grid (values and gradients)."""

    grid: VoxelGrid
    static: np.ndarray  # flat, min signed distance to static parts (drillable plates except at holes)
    moving: np.ndarray  # flat, min signed distance to moving swept envelopes
    structure: np.ndarray  # flat, distance to static structure (tie anchors)
    hardware: np.ndarray | None = None  # routed housings / bundles, no blanket termination relaxation

    def sample(self, name: str, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return trilinear(self.grid, getattr(self, name), points)


def trilinear(grid: VoxelGrid, values: np.ndarray, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Trilinear interpolation of a flat voxel field and its exact gradient."""
    f = values.reshape(grid.shape)
    rel = (np.atleast_2d(points) - grid.origin) / grid.voxel
    upper = np.asarray(grid.shape) - 1.000001
    clipped = np.clip(rel, 0.0, upper)
    inside = (rel >= 0.0) & (rel <= upper)
    i0 = np.floor(clipped).astype(int)
    t = clipped - i0
    out = np.zeros(len(rel))
    grad = np.zeros((len(rel), 3))
    for dx in (0, 1):
        wx, gx = (t[:, 0], 1.0) if dx else (1 - t[:, 0], -1.0)
        for dy in (0, 1):
            wy, gy = (t[:, 1], 1.0) if dy else (1 - t[:, 1], -1.0)
            for dz in (0, 1):
                wz, gz = (t[:, 2], 1.0) if dz else (1 - t[:, 2], -1.0)
                v = f[i0[:, 0] + dx, i0[:, 1] + dy, i0[:, 2] + dz]
                out += wx * wy * wz * v
                grad[:, 0] += gx * wy * wz * v
                grad[:, 1] += wx * gy * wz * v
                grad[:, 2] += wx * wy * gz * v
    grad = grad * inside / grid.voxel  # clamped outside the grid: no gradient
    return out, grad


# ------------------------------------------------------------------ problem
@dataclass
class Junction:
    """A point where segments meet, with one axis shared by every incident segment end."""

    key: str
    kind: str  # connector | fanout | floating | splice | free | hole | via | branch | support | unplaced
    position: np.ndarray
    axis: np.ndarray | None = None  # authored / initial axis
    axis_free: bool = False
    cone: float = math.pi  # max deviation of the axis from ``axis`` (free axes only)
    move: float = 0.0  # max distance the position may move
    plane_normal: np.ndarray | None = None  # supports: axis must stay parallel to this face
    exempt: set[str] = field(default_factory=set)
    termination_length: float = 0.0
    lead: float = 0.0  # straight strain-relief length out of a connector exit
    half_length: float = 0.0  # floating pair: exits at position +- axis * half_length
    body_radius: float = 0.0  # floating pair: connector body half width (clearance of the pair body)
    harnesses: set[str] = field(default_factory=set)


@dataclass
class Segment:
    """One bundle segment between two junctions (``points`` run start -> end)."""

    harness: str
    index: int  # position in the harness's ``segments`` list
    start: str
    end: str
    points: np.ndarray
    wires: list[str]
    od: float
    r_min: float
    start_sign: float = 1.0  # side of the start junction's axis this segment leaves along
    end_sign: float = 1.0
    head: np.ndarray | None = None  # routed points from the node exit up to the start breakout (fan-out zone)
    tail: np.ndarray | None = None  # routed points after the end breakout up to the node exit


@dataclass
class Violation:
    """An unsatisfied planning constraint, in engineering terms."""

    kind: str  # curvature | static_clearance | moving_clearance | tangent | edge | move | cone
    harness: str
    position: list[float]
    required: float
    achieved: float
    wires: list[str]
    components: list[str] = field(default_factory=list)
    junction: str = ""
    action: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "harness": self.harness, "position": self.position, "required": self.required,
                "achieved": self.achieved, "wires": self.wires, "components": self.components,
                "junction": self.junction, "action": self.action, "units": "m"}


@dataclass
class BundlePlan:
    harnesses: list[str]
    segments: list[Segment]  # planned points
    junctions: dict[str, Junction]  # planned positions / axes
    feasible: bool
    violations: list[Violation]
    stats: dict[str, Any]
    initial_points: list[np.ndarray] = field(default_factory=list)


class _Problem:
    """Maps the decision vector ``x`` to the point/axis state and evaluates objective
    and constraints with analytic Jacobians (``J_x = J_points @ d points / d x``).

    Every point is ``base + x[var] + x[pos] + scale * axis(x)`` (a free interior point,
    a junction position, or a lead / pair-body point along a junction axis), so the
    whole problem is evaluated with array operations. The constraint rows keep one
    fixed order and labels (built once); the last evaluation is cached because SciPy
    asks for values and Jacobians at the same ``x`` separately."""

    def __init__(self, segments: list[Segment], junctions: dict[str, Junction], fields: ClearanceField,
                 requirement: float, spacing: dict[int, float]) -> None:
        self.segments, self.junctions, self.fields, self.c_req = segments, junctions, fields, requirement
        self.x0: list[float] = []
        self.jpos: dict[str, int | None] = {}  # junction -> offset of its position in x (None: fixed)
        self.jaxis: dict[str, int | None] = {}  # junction -> offset of its unnormalised axis in x
        for key, j in junctions.items():
            self.jpos[key] = self._alloc(j.position) if j.move > 0 else None
            self.jaxis[key] = self._alloc(j.axis) if (j.axis_free and j.axis is not None) else None
        # Point slots: ("var", offset), ("junction", key), ("lead", key, sign, length)
        self.slots: list[list[tuple]] = []
        self.h: list[float] = []
        for k, seg in enumerate(segments):
            slots, h = self._slots(seg, spacing[k])
            self.slots.append(slots)
            self.h.append(h)
        self.n = len(self.x0)
        self.point_offset = np.cumsum([0] + [len(s) for s in self.slots])
        self.n_points = int(self.point_offset[-1])
        self.axis_keys = [k for k, j in junctions.items() if j.axis is not None]
        self.axis_index = {k: i for i, k in enumerate(self.axis_keys)}
        self.ny = 3 * self.n_points + 3 * len(self.axis_keys) + 3 * len(junctions)
        self.junction_keys = list(junctions)
        self.junction_index = {k: i for i, k in enumerate(self.junction_keys)}
        self.x0_array = np.asarray(self.x0, dtype=float)
        self._pair_samples = self._pair_body_slots()
        self._layout()
        self._initial_bounds()
        self._rows()
        self._cache: dict[str, tuple[bytes, Any]] = {}

    # ------------------------------------------------------------ layout
    def _alloc(self, values: np.ndarray) -> int:
        offset = len(self.x0)
        self.x0.extend(float(v) for v in values)
        return offset

    def _slots(self, seg: Segment, spacing: float) -> tuple[list[tuple], float]:
        js, je = self.junctions[seg.start], self.junctions[seg.end]
        points = seg.points
        total = polyline_length(points)
        head = self._anchor(js, seg.start_sign)
        tail = self._anchor(je, seg.end_sign)
        s0 = head[1] if head[0] == "lead" else 0.0
        s1 = tail[1] if tail[0] == "lead" else 0.0
        inner = max(total - s0 - s1, 1e-4)
        n = max(2, int(math.ceil(inner / spacing)))
        h = inner / n
        dense, arc = resample_polyline(points, min(h / 4, 0.0005))
        slots: list[tuple] = [("junction", seg.start)]
        if head[0] == "lead":
            slots.append(("lead", seg.start, seg.start_sign, head[1]))
        for i in range(1, n):
            s = s0 + i * h
            p = np.array([np.interp(s, arc, dense[:, a]) for a in range(3)])
            slots.append(("var", self._alloc(p)))
        if tail[0] == "lead":
            slots.append(("lead", seg.end, seg.end_sign, tail[1]))
        slots.append(("junction", seg.end))
        return slots, h

    @staticmethod
    def _anchor(j: Junction, sign: float) -> tuple[str, float]:
        if j.kind == "floating" and j.half_length > 0:
            return "lead", j.half_length
        if j.lead > 0:
            return "lead", j.lead
        return "point", 0.0

    def _pair_body_slots(self) -> list[tuple[str, float, float]]:
        """Clearance samples along every floating pair body: (junction, offset, radius)."""
        out = []
        for key, j in self.junctions.items():
            if j.kind == "floating" and j.half_length > 0:
                for t in np.linspace(-j.half_length, j.half_length, 7):
                    out.append((key, float(t), j.body_radius))
        return out

    def _layout(self) -> None:
        """Per-point affine map from ``x``: segment points first, then pair-body samples."""
        slots = [s for seg in self.slots for s in seg] + [("lead", key, 1.0, t) for key, t, _ in self._pair_samples]
        m = len(slots)
        self.p_var = np.full(m, -1)
        self.p_pos = np.full(m, -1)
        self.p_axis = np.full(m, -1)
        self.p_scale = np.zeros(m)
        self.p_base = np.zeros((m, 3))
        self.p_kind = []
        for i, slot in enumerate(slots):
            self.p_kind.append(slot[0])
            if slot[0] == "var":
                self.p_var[i] = slot[1]
                continue
            key = slot[1]
            if self.jpos[key] is not None:
                self.p_pos[i] = self.jpos[key]
            else:
                self.p_base[i] = np.asarray(self.junctions[key].position, float)
            if slot[0] == "lead":
                self.p_axis[i] = self.axis_index[key]
                self.p_scale[i] = slot[2] * slot[3]
        k = len(self.axis_keys)
        self.a_off = np.array([self.jaxis[key] if self.jaxis[key] is not None else -1 for key in self.axis_keys], int)
        self.a_fixed = np.zeros((k, 3))
        for i, key in enumerate(self.axis_keys):
            if self.a_off[i] < 0:
                a = np.asarray(self.junctions[key].axis, dtype=float)
                self.a_fixed[i] = a / np.linalg.norm(a)
        self.a_free = np.nonzero(self.a_off >= 0)[0]
        self.m_points = m

    # ------------------------------------------------------------ state
    def _axes(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Unit axes (K, 3) and d(axis)/d(raw axis) (K, 3, 3; zero for fixed axes)."""
        d = self.a_fixed.copy()
        dd = np.zeros((len(self.axis_keys), 3, 3))
        if len(self.a_free):
            idx = self.a_off[self.a_free][:, None] + np.arange(3)
            raw = x[idx]
            norm = np.maximum(np.linalg.norm(raw, axis=1), 1e-9)
            unit = raw / norm[:, None]
            d[self.a_free] = unit
            dd[self.a_free] = (np.eye(3)[None] - unit[:, :, None] * unit[:, None, :]) / norm[:, None, None]
        return d, dd

    def axes(self, x: np.ndarray) -> dict[str, tuple[np.ndarray, np.ndarray | None]]:
        """Unit axis of every junction and d(axis)/d(x block) (None when fixed)."""
        d, dd = self._axes(x)
        return {key: (d[i], dd[i] if self.a_off[i] >= 0 else None) for i, key in enumerate(self.axis_keys)}

    def junction_position(self, key: str, x: np.ndarray) -> np.ndarray:
        offset = self.jpos[key]
        return x[offset:offset + 3].copy() if offset is not None else np.asarray(self.junctions[key].position, float)

    def _all_points(self, x: np.ndarray, d: np.ndarray) -> np.ndarray:
        p = self.p_base.copy()
        var = self.p_var >= 0
        p[var] += x[self.p_var[var][:, None] + np.arange(3)]
        pos = self.p_pos >= 0
        p[pos] += x[self.p_pos[pos][:, None] + np.arange(3)]
        lead = self.p_axis >= 0
        p[lead] += self.p_scale[lead, None] * d[self.p_axis[lead]]
        return p

    def points(self, x: np.ndarray) -> list[np.ndarray]:
        d, _ = self._axes(x)
        p = self._all_points(x, d)
        return [p[self.point_offset[k]:self.point_offset[k + 1]].copy() for k in range(len(self.slots))]

    def point(self, slot: tuple, x: np.ndarray, axes: dict) -> np.ndarray:
        kind = slot[0]
        if kind == "var":
            return x[slot[1]:slot[1] + 3].copy()
        if kind == "junction":
            return self.junction_position(slot[1], x)
        if kind == "lead":
            return self.junction_position(slot[1], x) + slot[2] * slot[3] * axes[slot[1]][0]
        return np.asarray(slot[1], dtype=float)

    def _chain(self, rows: np.ndarray, pidx: np.ndarray, grad: np.ndarray, dd: np.ndarray
               ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """COO entries of d(row)/dx from d(row)/d(point) (``grad`` per (row, point) pair)."""
        out_r, out_c, out_v = [], [], []
        three = np.arange(3)
        for offsets in (self.p_var[pidx], self.p_pos[pidx]):
            mask = offsets >= 0
            if mask.any():
                out_r.append(np.repeat(rows[mask], 3))
                out_c.append((offsets[mask][:, None] + three).ravel())
                out_v.append(grad[mask].ravel())
        axis = self.p_axis[pidx]
        mask = axis >= 0
        if mask.any():
            mask[mask] = self.a_off[axis[mask]] >= 0
        if mask.any():
            a = axis[mask]
            vals = self.p_scale[pidx[mask], None] * np.einsum("rij,rj->ri", dd[a], grad[mask])
            out_r.append(np.repeat(rows[mask], 3))
            out_c.append((self.a_off[a][:, None] + three).ravel())
            out_v.append(vals.ravel())
        return (np.concatenate(out_r) if out_r else np.zeros(0, int), np.concatenate(out_c) if out_c else np.zeros(0, int),
                np.concatenate(out_v) if out_v else np.zeros(0))

    # ------------------------------------------------------------ bounds
    def _initial_bounds(self) -> None:
        """Clearance bound per point: the requirement, relaxed inside termination zones
        to the routed path's own clearance (the part a wire terminates on is exempt)."""
        x = self.x0_array
        pts = self.points(x)
        self.bounds: list[tuple[np.ndarray, np.ndarray]] = []
        self.terminal: list[np.ndarray] = []
        for k, (seg, p) in enumerate(zip(self.segments, pts)):
            r = seg.od / 2
            fs, _ = self.fields.sample("static", p)
            fm, _ = self.fields.sample("moving", p)
            arc = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))])
            static_req = np.full(len(p), r + STATIC_MARGIN)
            moving_req = np.full(len(p), r + self.c_req + MOVING_MARGIN)
            terminal = np.zeros(len(p), dtype=bool)
            for key, from_end in ((seg.start, arc), (seg.end, arc[-1] - arc)):
                j = self.junctions[key]
                zone = j.termination_length if j.exempt else (j.half_length + 2 * seg.od if j.kind == "floating" else 0.0)
                if j.kind == "fanout":
                    zone = j.termination_length
                if j.kind in ("hole",):
                    zone = 0.004
                inside = from_end <= zone + 1e-9
                static_req[inside] = np.minimum(static_req[inside], fs[inside] - TERMINATION_SLACK)
                moving_req[inside] = np.minimum(moving_req[inside], fm[inside] - TERMINATION_SLACK)
                terminal[inside] = True
            self.bounds.append((static_req, moving_req))
            self.terminal.append(terminal)

    # ------------------------------------------------------------ constraint layout
    def _rows(self) -> None:
        """Static row layout of every constraint (indices, parameters, labels), in the
        order: per segment curvature/reversal, edge cap/floor, tangency, clearance;
        then pair bodies; then junction moves, cones and support planes."""
        labels: list[dict[str, Any]] = []
        curv: dict[str, list] = {k: [] for k in ("row", "rev_row", "im", "i0", "ip", "radius", "h")}
        edge: dict[str, list] = {k: [] for k in ("cap_row", "floor_row", "i", "h")}
        tan: dict[str, list] = {k: [] for k in ("row", "ia", "ib", "sign", "radius", "axis")}
        clear: dict[str, list] = {k: [] for k in ("row", "point", "req", "field")}
        for k, (seg, slots) in enumerate(zip(self.segments, self.slots)):
            base, h, radius = int(self.point_offset[k]), self.h[k], BEND_MARGIN * seg.r_min
            n = len(slots)
            for i in range(1, n - 1):
                curv["row"].append(len(labels))
                labels.append({"kind": "curvature", "segment": k, "point": i, "radius": radius})
                curv["rev_row"].append(len(labels))
                labels.append({"kind": "curvature", "segment": k, "point": i, "radius": radius, "reversal": True})
                curv["im"].append(base + i - 1)
                curv["i0"].append(base + i)
                curv["ip"].append(base + i + 1)
                curv["radius"].append(radius)
                curv["h"].append(h)
            for i in range(n - 1):
                if slots[i][0] in ("junction", "lead") and slots[i + 1][0] in ("junction", "lead"):
                    continue
                edge["cap_row"].append(len(labels))
                labels.append({"kind": "edge", "segment": k, "point": i})
                edge["floor_row"].append(len(labels))
                labels.append({"kind": "edge", "segment": k, "point": i})
                edge["i"].append(base + i)
                edge["h"].append(h)
            for end, sign, key in ((0, seg.start_sign, seg.start), (1, seg.end_sign, seg.end)):
                if key not in self.axis_index:
                    continue
                anchor = 1 if slots[0 if end == 0 else -1][0] == "junction" and \
                    slots[1 if end == 0 else -2][0] == "lead" else 0
                ia = anchor if end == 0 else n - 1 - anchor
                ib = ia + 1 if end == 0 else ia - 1
                tan["row"].append(len(labels))
                labels.append({"kind": "tangent", "segment": k, "junction": key, "end": end})
                tan["ia"].append(base + ia)
                tan["ib"].append(base + ib)
                tan["sign"].append(sign)
                tan["radius"].append(radius)
                tan["axis"].append(self.axis_index[key])
            static_req, moving_req = self.bounds[k]
            for i in range(n):
                channels = [("static_clearance", static_req[i], 0), ("moving_clearance", moving_req[i], 1)]
                if self.fields.hardware is not None:
                    channels.append(("hardware_clearance", seg.od / 2 + STATIC_MARGIN, 2))
                for name, req, channel in channels:
                    if req < FIELD_CUTOFF:
                        clear["row"].append(len(labels))
                        labels.append({"kind": name, "segment": k, "point": i, "required": float(req),
                                       "terminal": bool(self.terminal[k][i]) if channel != 2 else False})
                        clear["point"].append(base + i)
                        clear["req"].append(float(req))
                        clear["field"].append(channel)
        for s, (key, _, radius) in enumerate(self._pair_samples):
            point = self.n_points + s
            for name, req, channel in (("static_clearance", radius + STATIC_MARGIN, 0),
                                       ("moving_clearance", radius + self.c_req, 1)):
                clear["row"].append(len(labels))
                labels.append({"kind": name, "junction": key, "required": req})
                clear["point"].append(point)
                clear["req"].append(float(req))
                clear["field"].append(channel)
        self.junction_rows: list[tuple[str, str, int]] = []  # (kind, junction key, row)
        for key, j in self.junctions.items():
            if self.jpos[key] is not None and j.move > 0:
                self.junction_rows.append(("move", key, len(labels)))
                labels.append({"kind": "move", "junction": key, "required": j.move})
            if self.jaxis[key] is not None:
                if j.cone < math.pi - 1e-9:
                    self.junction_rows.append(("cone", key, len(labels)))
                    labels.append({"kind": "cone", "junction": key, "required": j.cone})
                if j.plane_normal is not None:
                    self.junction_rows.append(("plane", key, len(labels)))
                    labels.append({"kind": "cone", "junction": key, "required": SUPPORT_TILT})
        as_int = lambda v: np.asarray(v, dtype=int)  # noqa: E731
        self.curv = {k: (as_int(v) if k not in ("radius", "h") else np.asarray(v, float)) for k, v in curv.items()}
        self.edge = {k: (as_int(v) if k != "h" else np.asarray(v, float)) for k, v in edge.items()}
        self.tan = {k: (as_int(v) if k not in ("sign", "radius") else np.asarray(v, float)) for k, v in tan.items()}
        self.clear = {k: (as_int(v) if k != "req" else np.asarray(v, float)) for k, v in clear.items()}
        self.labels = labels
        self.m = len(labels)
        tol = np.zeros(self.m)
        for r, label in enumerate(labels):
            tol[r] = (FEASIBILITY_TOL["clearance"] * 1e3 if label["kind"].endswith("clearance")
                      else FEASIBILITY_TOL.get(label["kind"], 0.0))
        self.tolerance = tol
        # Objective: every consecutive pair of a segment, and the structure term at every point.
        pairs = [(int(self.point_offset[k]) + i, self.h[k]) for k in range(len(self.slots))
                 for i in range(len(self.slots[k]) - 1)]
        self.o_edge = np.asarray([p for p, _ in pairs], int)
        self.o_edge_h = np.asarray([h for _, h in pairs], float)
        self.o_point_h = np.concatenate([np.full(len(s), self.h[k]) for k, s in enumerate(self.slots)]) \
            if self.slots else np.zeros(0)
        self.o_point_r = np.concatenate([np.full(len(s), self.segments[k].od / 2) for k, s in enumerate(self.slots)]) \
            if self.slots else np.zeros(0)

    def _cached(self, name: str, x: np.ndarray, compute: Any) -> Any:
        key = x.tobytes()
        hit = self._cache.get(name)
        if hit is not None and hit[0] == key:
            return hit[1]
        value = compute(x)
        self._cache[name] = (key, value)
        return value

    # ------------------------------------------------------------ objective
    def objective(self, x: np.ndarray) -> tuple[float, np.ndarray]:
        return self._cached("objective", x, self._objective)

    def _objective(self, x: np.ndarray) -> tuple[float, np.ndarray]:
        d, dd = self._axes(x)
        p = self._all_points(x, d)[: self.n_points]
        gp = np.zeros_like(p)
        e = p[self.o_edge + 1] - p[self.o_edge]
        value = float(np.sum(np.einsum("ij,ij->i", e, e) / self.o_edge_h))
        ge = 2 * e / self.o_edge_h[:, None]
        np.subtract.at(gp, self.o_edge, ge)
        np.add.at(gp, self.o_edge + 1, ge)
        # Stay within tie reach of structure: sum h * s^2, s = excess distance / band.
        fs, gs = self.fields.sample("structure", p)
        excess = np.maximum(0.0, fs - self.o_point_r - SURFACE_BAND) / SURFACE_BAND
        value += SURFACE_WEIGHT * float(np.sum(self.o_point_h * excess ** 2))
        gp += (SURFACE_WEIGHT * self.o_point_h * 2 * excess / SURFACE_BAND)[:, None] * gs
        grad = np.zeros(self.n)
        idx = np.arange(self.n_points)
        rows, cols, vals = self._chain(np.zeros(self.n_points, int), idx, gp, dd)
        np.add.at(grad, cols, vals)
        if len(self.a_free):  # keep unnormalised axes near unit length
            offs = self.a_off[self.a_free][:, None] + np.arange(3)
            a = x[offs]
            m = np.einsum("ij,ij->i", a, a) - 1.0
            value += 0.01 * float(np.sum(m * m))
            np.add.at(grad, offs.ravel(), (0.04 * m[:, None] * a).ravel())
        return value, grad

    # ------------------------------------------------------------ constraints
    def constraints(self, x: np.ndarray) -> tuple[np.ndarray, sparse.csr_matrix, list[dict[str, Any]]]:
        """All inequality constraints g(x) >= 0 (scaled to O(1)), their Jacobian and labels."""
        g, jac = self._cached("constraints", x, self._constraints)
        return g, jac, self.labels

    def jacobian_dense(self, x: np.ndarray) -> np.ndarray:
        return self._cached("dense", x, lambda z: self.constraints(z)[1].toarray())

    def _constraints(self, x: np.ndarray) -> tuple[np.ndarray, sparse.csr_matrix]:
        d, dd = self._axes(x)
        p = self._all_points(x, d)
        g = np.zeros(self.m)
        rows: list[np.ndarray] = []
        pidx: list[np.ndarray] = []
        grads: list[np.ndarray] = []
        extra_r: list[np.ndarray] = []
        extra_c: list[np.ndarray] = []
        extra_v: list[np.ndarray] = []

        def point_grad(row: np.ndarray, point: np.ndarray, grad: np.ndarray) -> None:
            rows.append(row)
            pidx.append(point)
            grads.append(grad)

        def axis_grad(row: np.ndarray, axis: np.ndarray, grad: np.ndarray) -> None:
            free = self.a_off[axis] >= 0
            if not free.any():
                return
            a = axis[free]
            vals = np.einsum("rji,rj->ri", dd[a], grad[free])  # dd^T @ g
            extra_r.append(np.repeat(row[free], 3))
            extra_c.append((self.a_off[a][:, None] + np.arange(3)).ravel())
            extra_v.append(vals.ravel())

        c = self.curv
        if len(c["row"]):
            # Curvature at interior points: circumradius c / (2 sin theta) >= R, i.e.
            # (c - 2 R |u x v| / (a b)) / h >= 0, and u.v / (a b) >= 0 (no reversals).
            u, v = p[c["i0"]] - p[c["im"]], p[c["ip"]] - p[c["i0"]]
            w = u + v
            a = np.maximum(np.linalg.norm(u, axis=1), 1e-12)
            b = np.maximum(np.linalg.norm(v, axis=1), 1e-12)
            cc = np.maximum(np.linalg.norm(w, axis=1), 1e-12)
            cross = np.cross(u, v)
            cn = np.linalg.norm(cross, axis=1)
            ch = np.where((cn > 1e-15)[:, None], cross / np.where(cn > 1e-15, cn, 1.0)[:, None], 0.0)
            ab = a * b
            sin = cn / ab
            radius, h = c["radius"], c["h"]
            g[c["row"]] = (cc - 2 * radius * sin) / h
            dsu = np.cross(v, ch) / ab[:, None] - (sin / (a * a))[:, None] * u
            dsv = np.cross(ch, u) / ab[:, None] - (sin / (b * b))[:, None] * v
            du = (w / cc[:, None] - 2 * radius[:, None] * dsu) / h[:, None]
            dv = (w / cc[:, None] - 2 * radius[:, None] * dsv) / h[:, None]
            point_grad(c["row"], c["im"], -du)
            point_grad(c["row"], c["i0"], du - dv)
            point_grad(c["row"], c["ip"], dv)
            dot = np.einsum("ij,ij->i", u, v)
            g[c["rev_row"]] = dot / ab
            cu = v / ab[:, None] - (dot / (a ** 3 * b))[:, None] * u
            cv = u / ab[:, None] - (dot / (a * b ** 3))[:, None] * v
            point_grad(c["rev_row"], c["im"], -cu)
            point_grad(c["rev_row"], c["i0"], cu - cv)
            point_grad(c["rev_row"], c["ip"], cv)
        e_ = self.edge
        if len(e_["i"]):
            # Edge length between 0.5x and EDGE_STRETCH x the nominal spacing.
            h = e_["h"]
            e = p[e_["i"] + 1] - p[e_["i"]]
            ee = np.einsum("ij,ij->i", e, e)
            g[e_["cap_row"]] = ((EDGE_STRETCH * h) ** 2 - ee) / h ** 2
            g[e_["floor_row"]] = (ee - (0.5 * h) ** 2) / h ** 2
            ge = 2 * e / (h ** 2)[:, None]
            point_grad(e_["cap_row"], e_["i"], ge)
            point_grad(e_["cap_row"], e_["i"] + 1, -ge)
            point_grad(e_["floor_row"], e_["i"], -ge)
            point_grad(e_["floor_row"], e_["i"] + 1, ge)
        t = self.tan
        if len(t["row"]):
            # Tangency at both ends: the first edge leaving the anchor within asin(l / 2R) of +-axis.
            axis, sign, radius = d[t["axis"]], t["sign"], t["radius"]
            e = p[t["ib"]] - p[t["ia"]]
            l = np.maximum(np.linalg.norm(e, axis=1), 1e-12)
            ratio = np.minimum(l / (2 * radius), 1.0)
            limit = np.sqrt(np.maximum(0.0, 1.0 - ratio * ratio))
            de_ = np.einsum("ij,ij->i", axis, e)
            g[t["row"]] = sign * de_ / l - limit
            de = sign[:, None] * (axis / l[:, None] - (de_ / l ** 3)[:, None] * e)
            dlim = np.where((limit > 1e-6)[:, None],
                            (ratio / np.maximum(limit, 1e-6))[:, None] * (e / l[:, None]) / (2 * radius)[:, None], 0.0)
            ge = de + dlim
            point_grad(t["row"], t["ia"], -ge)
            point_grad(t["row"], t["ib"], ge)
            axis_grad(t["row"], t["axis"], sign[:, None] * e / l[:, None])
        cl = self.clear
        if len(cl["row"]):
            # Clearance (scaled per mm).
            q = p[cl["point"]]
            fs, gs = self.fields.sample("static", q)
            fm, gm = self.fields.sample("moving", q)
            moving = cl["field"] == 1
            value = np.where(moving, fm, fs)
            grad = np.where(moving[:, None], gm, gs)
            hardware = cl["field"] == 2
            if hardware.any():
                fh, gh = self.fields.sample("hardware", q[hardware])
                value[hardware], grad[hardware] = fh, gh
            g[cl["row"]] = (value - cl["req"]) * 1e3
            point_grad(cl["row"], cl["point"], grad * 1e3)
        # Junction moves, axis cones, support planes.
        for kind, key, row in self.junction_rows:
            j = self.junctions[key]
            r1 = np.array([row])
            if kind == "move":
                delta = self.junction_position(key, x) - j.position
                g[row] = (j.move ** 2 - float(delta @ delta)) / j.move ** 2
                extra_r.append(np.repeat(r1, 3))
                extra_c.append(self.jpos[key] + np.arange(3))
                extra_v.append(-2 * delta / j.move ** 2)
                continue
            ai = self.axis_index[key]
            if kind == "cone":
                d0 = np.asarray(j.axis, dtype=float) / np.linalg.norm(j.axis)
                g[row] = float(d[ai] @ d0) - math.cos(j.cone)
                axis_grad(r1, np.array([ai]), d0[None])
            else:
                nrm = j.plane_normal
                s = float(d[ai] @ nrm)
                g[row] = math.sin(SUPPORT_TILT) ** 2 - s * s
                axis_grad(r1, np.array([ai]), (-2 * s * nrm)[None])
        jr, jc, jv = self._chain(np.concatenate(rows) if rows else np.zeros(0, int),
                                 np.concatenate(pidx) if pidx else np.zeros(0, int),
                                 np.concatenate(grads) if grads else np.zeros((0, 3)), dd)
        all_r = np.concatenate([jr, *extra_r])
        all_c = np.concatenate([jc, *extra_c])
        all_v = np.concatenate([jv, *extra_v])
        jac = sparse.csr_matrix((all_v, (all_r, all_c)), shape=(self.m, self.n))
        return g, jac

    def max_violation(self, x: np.ndarray) -> float:
        g, _, _ = self.constraints(x)
        return float(max(0.0, np.max(-g - self.tolerance, initial=0.0)))

    def fixed_clearance_lower_bound(self) -> float:
        """Residual that no decision variable can change at a fixed point.

        Inspect the affine point layout, not a zero local gradient: a movable
        point at the centre of an obstacle may also have a zero gradient.
        """
        fixed = (self.p_var < 0) & (self.p_pos < 0)
        axis_points = np.flatnonzero(self.p_axis >= 0)
        fixed[axis_points] &= self.a_off[self.p_axis[axis_points]] < 0
        rows = self.clear["row"][fixed[self.clear["point"]]]
        g, _, _ = self.constraints(self.x0_array)
        return float(max(0.0, np.max(-g[rows] - self.tolerance[rows], initial=0.0)))


# ------------------------------------------------------------------ solve
class _PlannerProgress:
    """Observe solver iterations without changing inputs or termination rules."""

    def __init__(self, phase: str, variables: int) -> None:
        self.phase = phase
        self.started = self.last_report = time.monotonic()
        self.iterations = 0
        self.logger = logging.getLogger(__name__)
        self.logger.info("Wiring planner %s started: %d variables", phase, variables)

    def __call__(self, _x: np.ndarray, state: Any = None) -> None:
        self.iterations += 1
        now = time.monotonic()
        if now - self.last_report < 30:
            return
        self.last_report = now
        # trust-constr already measured this residual. Never run an additional
        # geometry query or use the observation as a solver stopping condition.
        residual = getattr(state, "constr_violation", None)
        self.logger.info("Wiring planner %s: iteration %d, elapsed %.1fs, solver residual %s",
                         self.phase, self.iterations, now - self.started, residual)


def _constrained(fun: Any, x: np.ndarray, jac: Any, bounds: list[tuple[float, float]],
                 cons: Any, cons_jac: Any, max_iterations: int,
                 progress: _PlannerProgress | None = None) -> Any:
    """Unchanged physical inequalities, using SciPy's sparse solver at scale.

    Analytical Jacobians plus finite-difference Hessian products avoid both the
    dense SLSQP QP and dense quasi-Newton Hessian storage. The result still goes
    through the unchanged physical constraint audit below.
    """
    if len(x) <= DENSE_SLSQP_MAX_VARIABLES:
        return minimize(fun, x, jac=jac, method="SLSQP", bounds=bounds, callback=progress,
                        constraints=[{"type": "ineq", "fun": cons, "jac": lambda z: cons_jac(z).toarray()}],
                        options={"maxiter": max_iterations, "ftol": 1e-10})
    # Position, unit-axis and elastic-slack coordinates have different units.
    # Scale columns using the actual analytic derivatives before applying a
    # dimensionless trust region. This changes coordinates, not constraints.
    column_norm = np.asarray(abs(cons_jac(x)).max(axis=0).toarray()).ravel()
    scale = 1.0 / np.maximum(1.0, np.maximum(column_norm, np.abs(jac(x))))
    origin = x.copy()
    physical = lambda y: origin + scale * y
    scaled_jac = lambda y: cons_jac(physical(y)).multiply(scale).tocsr()
    constraint = NonlinearConstraint(lambda y: cons(physical(y)), 0.0, np.inf, jac=scaled_jac, hess="2-point")
    scaled_bounds = [((lo - value) / factor, (hi - value) / factor) for (lo, hi), value, factor in zip(bounds, origin, scale)]
    result = minimize(lambda y: fun(physical(y)), np.zeros_like(x), jac=lambda y: jac(physical(y)) * scale,
                      hess="2-point", method="trust-constr", bounds=scaled_bounds, constraints=[constraint], callback=progress,
                      options={"maxiter": max_iterations, "gtol": 1e-12, "xtol": 1e-12, "barrier_tol": 1e-12,
                               "sparse_jacobian": True, "factorization_method": "AugmentedSystem"})
    result.x = physical(result.x)
    return result


def plan_bundle(segments: list[Segment], junctions: dict[str, Junction], fields: ClearanceField,
                requirement: float, *, max_iterations: int = 300, obstacles: Any = None) -> BundlePlan:
    """Plan one bundle group (harnesses joined by floating mates) as a single
    constrained optimisation. Returns the planned segments and junctions,
    feasibility and the violated constraints (with the parts that cause them)."""
    spacing = {k: float(np.clip(seg.r_min / 3.0, 0.0015, 0.004)) for k, seg in enumerate(segments)}
    problem = _Problem(segments, junctions, fields, requirement, spacing)
    x0 = problem.x0_array.copy()
    # The distance fields are defined only on this routing volume. In an
    # infeasible layout an unbounded elastic solve can otherwise send a point
    # tens of metres away, marginally reduce its largest residual, and win the
    # candidate comparison. Keep positional variables in the modelled domain;
    # include their authored initial position when an exit sits on its edge.
    lower, upper = np.full(problem.n, -np.inf), np.full(problem.n, np.inf)
    low = fields.grid.origin
    high = low + (np.asarray(fields.grid.shape) - 1) * fields.grid.voxel
    positions = {int(i) for i in problem.p_var if i >= 0}
    positions.update(i for i in problem.jpos.values() if i is not None)
    for offset in positions:
        lower[offset:offset + 3] = np.minimum(low, x0[offset:offset + 3])
        upper[offset:offset + 3] = np.maximum(high, x0[offset:offset + 3])
    bounds = list(zip(lower, upper))
    stats: dict[str, Any] = {"variables": problem.n, "points": problem.n_points}
    if problem.n == 0:
        return _result(problem, x0, segments, junctions, stats, obstacles)

    violation = problem.max_violation

    # Phase 1: smooth quadratic-penalty descent (L-BFGS-B) from the routed path, with an
    # escalating penalty weight so the residual violation goes to ~0 where a route exists.
    def penalty(x: np.ndarray, weight: float) -> tuple[float, np.ndarray]:
        f, gf = problem.objective(x)
        g, jac, _ = problem.constraints(x)
        neg = np.minimum(g, 0.0)
        return 1e-3 * f + weight * float(neg @ neg), 1e-3 * gf + 2.0 * weight * (jac.T @ neg)

    v0 = violation(x0)
    x1, iterations1 = x0, 0
    for weight in PENALTY_WEIGHTS:
        step = minimize(penalty, x1, args=(weight,), jac=True, method="L-BFGS-B", bounds=bounds,
                        options={"maxiter": 400}, callback=_PlannerProgress(f"penalty weight={weight:g}", problem.n))
        iterations1 += int(step.nit)
        if violation(step.x) <= violation(x1) + 1e-12 or weight == PENALTY_WEIGHTS[0]:
            x1 = step.x
        if violation(x1) == 0.0:
            break
    if violation(x1) > v0:
        x1 = x0
    stats.update(initial_violation=v0, penalty_iterations=iterations1, penalty_violation=violation(x1))

    # A blocked fixed exit/via is a layout failure. Once the best possible
    # maximum residual is reached, dense elastic/polish iterations cannot make
    # this group feasible. Return the full audit and keep every failure.
    fixed_bound = problem.fixed_clearance_lower_bound()
    if fixed_bound > 0.0 and violation(x1) <= fixed_bound + 1e-10:
        stats.update(best_phase="penalty", fixed_clearance_lower_bound=fixed_bound,
                     slsqp_iterations=0, slsqp_message="Skipped: fixed clearance lower bound reached",
                     final_violation=violation(x1),
                     initial_length=float(sum(polyline_length(p) for p in problem.points(x0))),
                     planned_length=float(sum(polyline_length(p) for p in problem.points(x1))))
        return _result(problem, x1, segments, junctions, stats, obstacles)

    # The routing objective is a buildable cable. Once the bounded penalty
    # solve satisfies every constraint, a dense elastic solve cannot improve
    # feasibility and can spend hundreds of iterations polishing its length.
    # Keep the same final constraint audit before accepting this early result.
    if violation(x1) == 0.0:
        accepted_stats = {**stats, "best_phase": "penalty", "slsqp_iterations": 0,
                          "slsqp_message": "Skipped: penalty solution satisfies all constraints",
                          "final_violation": 0.0,
                          "initial_length": float(sum(polyline_length(p) for p in problem.points(x0))),
                          "planned_length": float(sum(polyline_length(p) for p in problem.points(x1)))}
        accepted = _result(problem, x1, segments, junctions, accepted_stats, obstacles)
        if accepted.feasible:
            return accepted

    # Phase 2: elastic SLSQP. One slack t >= 0 relaxes every constraint (g + t >= 0) and
    # costs ELASTIC_WEIGHT per unit, so SLSQP always has a feasible point and converges to
    # the least-violating geometry when the layout leaves no feasible route.
    def fun(z: np.ndarray) -> float:
        return problem.objective(z[:-1])[0] + ELASTIC_WEIGHT * z[-1]

    def jac(z: np.ndarray) -> np.ndarray:
        return np.concatenate([problem.objective(z[:-1])[1], [ELASTIC_WEIGHT]])

    def cons(z: np.ndarray) -> np.ndarray:
        return np.concatenate([problem.constraints(z[:-1])[0] + z[-1], [z[-1]]])

    def cons_jac(z: np.ndarray) -> sparse.csr_matrix:
        jx = problem.constraints(z[:-1])[1]
        top = sparse.hstack([jx, np.ones((jx.shape[0], 1))])
        bottom = sparse.csr_matrix(([1.0], ([0], [problem.n])), shape=(1, problem.n + 1))
        return sparse.vstack([top, bottom], format="csr")

    g1, _, _ = problem.constraints(x1)
    z1 = np.concatenate([x1, [max(0.0, -float(g1.min())) + 1e-3]])
    try:
        result = _constrained(fun, z1, jac, [*bounds, (0.0, np.inf)], cons, cons_jac, max_iterations, _PlannerProgress("elastic", len(z1)))
        x2, message, iterations = result.x[:-1], str(result.message), int(result.nit)
        stats["elastic_slack"] = float(result.x[-1])
        stats["elastic_solver"] = "SLSQP" if len(z1) <= DENSE_SLSQP_MAX_VARIABLES else "trust-constr"
    except (ValueError, np.linalg.LinAlgError) as exc:  # pragma: no cover - SLSQP numerical failure
        x2, message, iterations = x1, f"Constrained solve failed: {exc}", 0
    # Phase 3: hard-constraint SLSQP polish from the elastic solution (removes the residual slack).
    x3 = x2
    try:
        polish = _constrained(lambda x: problem.objective(x)[0], x2, lambda x: problem.objective(x)[1], bounds,
                              lambda x: problem.constraints(x)[0], lambda x: problem.constraints(x)[1], 2 * max_iterations,
                              _PlannerProgress("polish", len(x2)))
        x3 = polish.x
        stats["polish_solver"] = "SLSQP" if len(x2) <= DENSE_SLSQP_MAX_VARIABLES else "trust-constr"
        stats["polish_message"] = str(polish.message)
        stats["polish_iterations"] = int(polish.nit)
    except (ValueError, np.linalg.LinAlgError):  # pragma: no cover
        pass
    candidates = (x0, x1, x2, x3)
    phase = min(range(4), key=lambda k: (violation(candidates[k]), problem.objective(candidates[k])[0]))
    best = candidates[phase]
    stats["best_phase"] = ("routed", "penalty", "elastic", "polish")[phase]
    stats.update(slsqp_message=message, slsqp_iterations=iterations, final_violation=violation(best),
                 initial_length=float(sum(polyline_length(p) for p in problem.points(x0))),
                 planned_length=float(sum(polyline_length(p) for p in problem.points(best))))
    return _result(problem, best, segments, junctions, stats, obstacles)


def _max_violation(g: np.ndarray, labels: list[dict[str, Any]]) -> float:
    worst = 0.0
    for value, label in zip(g, labels):
        tol = FEASIBILITY_TOL.get(label["kind"].replace("static_", "").replace("moving_", ""), 0.0)
        if label["kind"].endswith("clearance"):
            tol = FEASIBILITY_TOL["clearance"] * 1e3
        worst = max(worst, -float(value) - tol)
    return max(worst, 0.0)


def _result(problem: _Problem, x: np.ndarray, segments: list[Segment], junctions: dict[str, Junction],
            stats: dict[str, Any], obstacles: Any) -> BundlePlan:
    axes = problem.axes(x)
    planned = []
    for seg, pts in zip(segments, problem.points(x)):
        planned.append(Segment(seg.harness, seg.index, seg.start, seg.end, pts, seg.wires, seg.od, seg.r_min,
                               seg.start_sign, seg.end_sign, seg.head, seg.tail))
    new_junctions = {}
    for key, j in junctions.items():
        moved = Junction(**{**j.__dict__})
        moved.position = problem.junction_position(key, x)
        if key in axes:
            moved.axis = axes[key][0]
        new_junctions[key] = moved
    g, _, labels = problem.constraints(x)
    violations = _violations(g, labels, planned, new_junctions, problem, obstacles)
    feasible = not violations and _max_violation(g, labels) <= 0.0
    stats["violations"] = len(violations)
    return BundlePlan(sorted({s.harness for s in segments}), planned, new_junctions, feasible, violations, stats,
                      [s.points for s in segments])


def _violations(g: np.ndarray, labels: list[dict[str, Any]], planned: list[Segment], junctions: dict[str, Junction],
                problem: _Problem, obstacles: Any) -> list[Violation]:
    """Group violated constraints into engineering findings (worst per kind and place)."""
    found: list[Violation] = []
    for value, label in zip(g, labels):
        kind = label["kind"]
        tol = FEASIBILITY_TOL["clearance"] * 1e3 if kind.endswith("clearance") else FEASIBILITY_TOL.get(kind, 0.0)
        if value >= -tol or label.get("terminal"):
            continue  # termination zones are judged by the checker with the exact exemptions
        seg = planned[label["segment"]] if "segment" in label else None
        if seg is not None and "point" in label:
            p = seg.points[min(label["point"], len(seg.points) - 1)]
        else:
            p = junctions[label["junction"]].position
        harness = seg.harness if seg is not None else ",".join(sorted(junctions[label["junction"]].harnesses))
        wires = list(seg.wires) if seg is not None else []
        if kind == "curvature":
            pts = seg.points
            i = label["point"]
            achieved = _circumradius(pts[i - 1], pts[i], pts[i + 1])
            required = label["radius"] / BEND_MARGIN
            if achieved >= required * 0.98:
                continue  # inside the catalog minimum (the planner's margin is a preference)
            room = float(problem.fields.sample("static", p[None])[0][0])
            v = Violation("curvature", harness, p.tolist(), required, achieved, wires, _near(obstacles, p),
                          action=f"bundle of {len(wires)} conductor(s), OD {seg.od * 1e3:.1f} mm, needs a "
                                 f"{required * 1e3:.1f} mm bend radius here but the corridor leaves "
                                 f"{max(room - seg.od / 2, 0) * 1e3:.1f} mm of free space: move or rotate the "
                                 f"connector exit, give the bundle a wider path, or split it")
        elif kind.endswith("clearance"):
            field_name = "hardware" if kind.startswith("hardware") else "static" if kind.startswith("static") else "moving"
            achieved = float(problem.fields.sample(field_name, p[None])[0][0])
            required = label["required"]
            r = seg.od / 2 if seg is not None else 0.0
            near = _near(obstacles, p, moving=kind.startswith("moving"))
            what = "moving part swept envelope" if kind.startswith("moving") else "part"
            v = Violation(kind, harness, p.tolist(), required - r, achieved - r, wires, near,
                          junction=label.get("junction", ""),
                          action=f"the corridor between {', '.join(near) or 'parts'} leaves "
                                 f"{(achieved - r) * 1e3:.1f} mm to the {what}; {(required - r) * 1e3:.1f} mm is "
                                 f"required: move the connector/component or add a routing channel")
        elif kind == "tangent":
            j = junctions[label["junction"]]
            if _tangent_radius(seg, label["end"], j) >= 0.98 * seg.r_min:
                continue
            what = "fan-out breakout" if j.kind == "fanout" else j.kind
            v = Violation("tangent", harness, j.position.tolist(), seg.r_min, _tangent_radius(seg, label["end"], j),
                          wires, _near(obstacles, j.position), label["junction"],
                          action=f"the bundle cannot leave {what} {label['junction']} along its axis with a "
                                 f"{seg.r_min * 1e3:.1f} mm bend radius: rotate the "
                                 f"{'connector' if j.kind == 'fanout' else j.kind} or give it more room")
        elif kind == "edge":
            # A chord longer than the spacing cap: the optimiser stretched the chain to cheat the
            # curvature constraint, i.e. no curve with this radius and clearance exists here.
            v = Violation("curvature", harness, p.tolist(), seg.r_min, 0.0, wires, _near(obstacles, p),
                          action=f"no curve with a {seg.r_min * 1e3:.1f} mm bend radius fits the available space "
                                 f"along this segment (the planned chain had to stretch): give the bundle more room, "
                                 f"move the connectors, or split the bundle")
        else:
            v = Violation(kind, harness, p.tolist(), float(label.get("required", 0.0)), float(value), wires, [],
                          label.get("junction", ""), action="junction placement limit reached")
        if any(o.kind == v.kind and o.harness == v.harness and np.linalg.norm(np.subtract(o.position, v.position)) < 0.01
               for o in found):
            prior = next(o for o in found if o.kind == v.kind and o.harness == v.harness and
                         np.linalg.norm(np.subtract(o.position, v.position)) < 0.01)
            if v.achieved < prior.achieved:
                found[found.index(prior)] = v
            continue
        found.append(v)
    return found


def _tangent_radius(seg: Segment, end: int, j: Junction) -> float:
    """Radius of the arc tangent to the junction axis through the segment's first point."""
    pts = seg.points if end == 0 else seg.points[::-1]
    sign = seg.start_sign if end == 0 else seg.end_sign
    for q in pts[1:]:
        e = q - pts[0]
        l = float(np.linalg.norm(e))
        if l > 1e-9:
            sin = float(np.linalg.norm(np.cross(sign * j.axis, e / l)))
            cos = float(sign * j.axis @ e / l)
            if cos <= 0:
                return 0.0
            return l / (2 * sin) if sin > 1e-12 else math.inf
    return math.inf


def _near(obstacles: Any, p: np.ndarray, moving: bool | None = None, limit: float = 0.012) -> list[str]:
    if obstacles is None:
        return []
    near = []
    for o in obstacles.obstacles:
        if moving is not None and o.moving != moving:
            continue
        shape = o.swept if o.moving else o.shape
        d = float(shape.sdf_bounded(np.asarray(p)[None], 0.05)[0])
        if d < limit:
            near.append((d, o.component))
    return [c for _, c in sorted(near)[:3]]


def _circumradius(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    u, v = b - a, c - b
    cross = float(np.linalg.norm(np.cross(u, v)))
    if cross < 1e-15:
        return math.inf
    return float(np.linalg.norm(u) * np.linalg.norm(v) * np.linalg.norm(u + v) / (2 * cross))


def vertex_radii(points: np.ndarray) -> np.ndarray:
    """Circumradius at every vertex of a polyline (inf at the ends and on straight runs)."""
    out = np.full(len(points), math.inf)
    for i in range(1, len(points) - 1):
        out[i] = _circumradius(points[i - 1], points[i], points[i + 1])
    return out


# ------------------------------------------------------------------ routing integration
@dataclass
class PlanningReport:
    plans: list[BundlePlan]
    diagnostics: list[Diagnostic]
    moved: dict[str, dict[str, Any]]  # junction -> {"from", "to", "kind"} (recommended robot.yaml edits)
    stats: dict[str, Any]
    before: dict[str, np.ndarray]  # wire -> routed path before planning

    def plan_of(self, harness: str) -> BundlePlan | None:
        return next((p for p in self.plans if harness in p.harnesses), None)


def clearance_field(router: Any, harness_holes: set[int]) -> ClearanceField:
    """The router's raw-geometry fields: static (drillable plates count except at the
    group's own holes), moving swept envelopes, and static structure."""
    f = router.fields
    static = f.static_raw.copy()
    hole_ok = router._hole_ok(harness_holes)
    for plate in f.plates_raw:
        np.minimum(static, np.where(hole_ok, FIELD_CUTOFF, plate), out=static)
    return ClearanceField(router.grid, static, f.moving_raw.copy(), f.struct_dist.copy())


CROSSING_GAP = 0.0005  # m between the surfaces of two harnesses that cross or run side by side


def occupancy_field(grid: VoxelGrid, occupied: list[tuple[np.ndarray, float, set[str]]],
                    categories: set[str], *, termination_exits: list[np.ndarray] | None = None) -> np.ndarray:
    """Reserve earlier bundle radii and a crossing gap everywhere, plus EMC
    segregation outside short connector fan-outs. The next harness group must
    keep this field >= its own radius; the separate EMC audit is unchanged."""
    from scipy.spatial import cKDTree

    from .bundles import SEPARATION, TERMINATION_ZONE

    centers = grid.centers()
    out = np.full(grid.size, FIELD_CUTOFF)
    # The EMC audit applies segregation to parallel runs of at least 30 mm.
    # Applying that long-run spacing at a fixed connector's short fan-out can
    # make adjacent, physically clear headers impossible to route. Preserve
    # full physical occupancy and the crossing gap everywhere; only the extra
    # EMC routing margin stops at the existing 12 mm termination boundary.
    termination = np.zeros(grid.size, dtype=bool)
    for point in termination_exits or []:
        termination |= np.linalg.norm(centers - point, axis=1) <= TERMINATION_ZONE
    seen: set[tuple] = set()
    for points, radius, cats in occupied:
        separation = max([SEPARATION.get(frozenset({a, b}), 0.0) for a in categories for b in cats if a != b] + [0.0])
        key = (points.shape, points.tobytes(), radius, separation)
        if key in seen:
            continue
        seen.add(key)
        # Voxels farther than the cutoff from the bundle's bounding box see the cutoff (no query needed).
        near = np.all((centers >= points.min(axis=0) - FIELD_CUTOFF) & (centers <= points.max(axis=0) + FIELD_CUTOFF),
                      axis=1)
        d = np.full(len(centers), FIELD_CUTOFF)
        d[near], _ = cKDTree(points).query(centers[near], distance_upper_bound=FIELD_CUTOFF)
        margin = np.where(termination, 0.0, separation) if separation else 0.0
        np.minimum(out, np.minimum(d, FIELD_CUTOFF) - radius - margin - CROSSING_GAP, out=out)
    return out


def _group_categories(project: Any, harnesses: list[str]) -> set[str]:
    from .bundles import category

    return {category(w.signal, w.awg) for h in project.spec.electrical.harnesses if h.id in harnesses for w in h.wires}


def _groups(project: Any, routing: Any) -> list[list[str]]:
    """Harnesses joined by floating mated pairs form one planning group."""
    parent = {h: h for h in routing.harnesses}

    def find(h: str) -> str:
        while parent[h] != h:
            parent[h] = parent[parent[h]]
            h = parent[h]
        return h

    for pair in routing.floating:
        a, b = pair.a.split(".")[0], pair.b.split(".")[0]
        if a in parent and b in parent:
            parent[find(a)] = find(b)
    groups: dict[str, list[str]] = {}
    for h in routing.harnesses:
        groups.setdefault(find(h), []).append(h)
    order = [h.id for h in project.spec.electrical.harnesses]
    copper = {h.id: sum(float(project.catalog.wire(w.awg).conductor_diameter) ** 2 for w in h.wires)
              for h in project.spec.electrical.harnesses}
    # Heaviest (least flexible) groups claim their corridor first.
    return sorted((sorted(g, key=order.index) for g in groups.values()),
                  key=lambda g: (-sum(copper.get(h, 0.0) for h in g), order.index(g[0])))


def _tangent(points: np.ndarray, from_start: bool, reach: float = 0.004) -> np.ndarray:
    pts = points if from_start else points[::-1]
    arc = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
    k = int(np.searchsorted(arc, min(reach, arc[-1])))
    k = min(max(k, 1), len(pts) - 1)
    d = pts[k] - pts[0]
    n = float(np.linalg.norm(d))
    return d / n if n > 1e-12 else np.array([1.0, 0.0, 0.0])


def build_problem(project: Any, router: Any, routing: Any, harnesses: list[str],
                  supports: dict[str, list[dict[str, Any]]] | None = None) -> tuple[list[Segment], dict[str, Junction]]:
    """Junctions and segments of a harness group from the routed bundle graph.
    ``supports`` (harness -> [{"position", "normal", "arc_wire", ...}]) split segments
    at cable supports, which become fixed junctions whose axis stays on the face."""
    catalog = project.catalog
    junctions: dict[str, Junction] = {}
    segments: list[Segment] = []
    pair_of: dict[str, tuple[str, float]] = {}
    fanned: set[str] = set()  # node refs whose conductors fan out from their pins
    jacket = {h.id: h.jacket for h in project.spec.electrical.harnesses}
    for pair in routing.floating:
        # Other groups must not move this pair after its pin fan-outs were
        # fixed by its own planner pass. Their segments do not constrain it.
        if pair.a.split(".", 1)[0] not in harnesses or pair.b.split(".", 1)[0] not in harnesses:
            continue
        key = f"F:{pair.a}|{pair.b}"
        from .terminations import body_width

        width = max(body_width(catalog, pair.type_a), body_width(catalog, pair.type_b))
        junctions[key] = Junction(key, "floating", np.asarray(pair.center, float), np.asarray(pair.axis, float),
                                  axis_free=not getattr(pair, "native", False), move=0.0 if getattr(pair, "native", False) else FLOATING_MOVE, half_length=pair.length / 2,
                                  body_radius=width / 2,
                                  harnesses={pair.a.split(".")[0], pair.b.split(".")[0]})
        pair_of[pair.a], pair_of[pair.b] = (key, -1.0), (key, 1.0)
    for harness in harnesses:
        route = routing.harnesses[harness]
        nodes = routing.nodes[harness]
        failed = {w for w, r in routing.wires.items() if r.harness == harness and r.failed}
        hole_centers = {f"H:{i}": routing.holes[i] for i in route.holes}
        vias = [np.asarray(v, float) for h in project.spec.electrical.harnesses if h.id == harness
                for points in [h.route.via, *getattr(h.route, "wire_via", {}).values()] for v in points]

        def identify(point: np.ndarray) -> tuple[str, float | None]:
            for node in nodes.values():
                if node.exit is not None and np.linalg.norm(node.exit - point) < 1e-7:
                    if node.ref in pair_of:
                        return pair_of[node.ref]
                    key = f"{harness}/N:{node.name}"
                    if key not in junctions:
                        junctions[key] = _node_junction(key, node, catalog)
                    return key, (1.0 if node.kind == "connector" else None)
            for key, hole in hole_centers.items():
                if np.linalg.norm(hole.center - point) < 1e-7:
                    jkey = f"{key}"
                    if jkey not in junctions:
                        normal = np.zeros(3)
                        normal[hole.axis] = 1.0
                        junctions[jkey] = Junction(jkey, "hole", hole.center.copy(), normal, harnesses={harness},
                                                   termination_length=0.004)
                    junctions[jkey].harnesses.add(harness)
                    return jkey, None
            kind = "via" if any(np.linalg.norm(v - point) <= router.opt.voxel * 1.8 for v in vias) else "branch"
            key = f"{harness}/{kind[0].upper()}:{len([k for k in junctions if k.startswith(harness + '/')])}"
            for existing, j in junctions.items():
                if existing.startswith(harness + "/") and j.kind in ("via", "branch") and \
                        np.linalg.norm(j.position - point) < 1e-7:
                    return existing, None
            junctions[key] = Junction(key, kind, point.copy(), None, axis_free=True,
                                      move=BRANCH_MOVE if kind == "branch" else 0.0, harnesses={harness})
            return key, None

        def fanout(bo: dict[str, Any], index: int) -> str:
            """Fixed junction at a fan-out breakout: position and axis come from the pins' fan-outs."""
            node = nodes[bo["node"]]
            fanned.add(node.ref)
            key = f"{harness}/B:{node.name}@{index}"
            if key not in junctions:
                b = float(bo["arc"])
                # Independently routed lanes carry the diameter and approach of
                # their own pins, while the node retains the complete connector.
                lane_od = float(bo.get("lane_od", node.od))
                lane_lead = float(bo.get("lane_lead_length", node.lead_length))
                if node.kind == "connector":
                    zone = node.termination_length
                    lead = min(lane_lead, max(2 * lane_od, 0.003)) if lane_lead > 0 else 0.0
                else:  # floating pair half: its exit zone extends two bundle diameters
                    zone, lead = 2 * lane_od, 0.0
                junctions[key] = Junction(key, "fanout", np.asarray(bo["position"], float).copy(),
                                          np.asarray(bo["direction"], float).copy(), exempt=set(node.exempt),
                                          termination_length=max(zone - b, 0.0), lead=max(lead - b, 0.0),
                                          harnesses={harness})
            return key

        cuts = (supports or {}).get(harness, [])
        for index, seg in enumerate(route.segments):
            if set(seg["wires"]) <= failed:
                continue
            pts = np.asarray(seg["points"], float)
            if len(pts) < 2 or polyline_length(pts) < 1e-6:
                continue
            bos = seg.get("breakouts") or [None, None]
            head = tail = None
            if bos[0] is not None:
                head, pts = _cut(pts, np.asarray(bos[0]["position"], float))
                start, s_sign = fanout(bos[0], index), 1.0
            else:
                start, s_sign = identify(pts[0])
            if bos[1] is not None:
                tail_rev, rev = _cut(pts[::-1], np.asarray(bos[1]["position"], float))
                pts, tail = rev[::-1], tail_rev[::-1]
                end, e_sign = fanout(bos[1], index), 1.0
            else:
                end, e_sign = identify(pts[-1])
            if len(pts) < 2 or polyline_length(pts) < 1e-6:
                continue
            pieces = _split_at_supports(pts, cuts, set(seg["wires"]), harness, junctions)
            for k, (piece, a_key, b_key) in enumerate(pieces):
                a_key = a_key or start
                b_key = b_key or end
                a_sign = s_sign if a_key == start else None
                b_sign = e_sign if b_key == end else None
                if a_key in pair_of.values() or a_key.startswith("F:"):
                    piece = np.vstack([junctions[a_key].position, piece])
                if b_key.startswith("F:"):
                    piece = np.vstack([piece, junctions[b_key].position])
                awgs = [routing.wires[w].awg for w in seg["wires"] if w in routing.wires]
                radius = bundle_bend_radius(catalog, awgs, float(seg["od"]), jacket.get(harness, "none") != "none")
                segments.append(Segment(harness, index, a_key, b_key, piece, list(seg["wires"]), float(seg["od"]),
                                        radius,
                                        a_sign if a_sign is not None else 0.0, b_sign if b_sign is not None else 0.0,
                                        head if k == 0 else None, tail if k == len(pieces) - 1 else None))
    # A floating mated pair whose conductors fan out from its pins stays where the router put it.
    for key, j in junctions.items():
        if j.kind == "floating" and set(key[2:].split("|")) & fanned:
            j.move, j.axis_free = 0.0, False
    _assign_sides(segments, junctions)
    return segments, junctions


def _cut(points: np.ndarray, breakout: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Split a polyline at (the projection of) a breakout: the points before it, and
    the rest starting exactly at the breakout."""
    from .geometry import project_to_polyline

    arc = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))])
    _, s = project_to_polyline(points, breakout[None])
    return points[arc < s - 1e-9], np.vstack([breakout, points[arc > s + 1e-9]])


def bundle_bend_radius(catalog: Any, awgs: list[int], bundle_od: float, jacketed: bool) -> float:
    """Minimum bend radius of the bundle centreline.

    A jacketed (taped, sleeved, overmoulded) bundle bends as one cable: ``factor x OD``.
    In a loose bundle every conductor bends on its own; SAE AS50881 (formerly MIL-W-5088)
    references the bend radius of wire groups to the OD of the largest *wire*, so the
    innermost conductor (``(OD - od) / 2`` inside the centreline) must keep
    ``factor x od``: ``R = factor x od_max + (OD - od_max) / 2``."""
    gauges = [catalog.wire(a) for a in awgs]
    factor = max(float(g.min_bend_radius_factor) for g in gauges)
    od = max(float(g.outer_diameter) for g in gauges)
    if jacketed or len(gauges) == 1:
        return factor * (bundle_od if jacketed else od)
    return factor * od + max(bundle_od - od, 0.0) / 2


def _node_junction(key: str, node: Any, catalog: Any) -> Junction:
    if node.kind == "connector":
        free = node.connector_type == "integral"
        lead = min(node.lead_length, max(2 * node.od, 0.003)) if node.lead_length > 0 else 0.0
        return Junction(key, "connector", node.exit.copy(), np.asarray(node.direction, float), axis_free=free,
                        cone=PIGTAIL_CONE if free else 0.0, exempt=set(node.exempt),
                        termination_length=node.termination_length, lead=lead, harnesses={node.harness})
    if node.kind == "splice":
        axis = node.direction.copy() if node.direction is not None else None
        return Junction(key, "splice", node.exit.copy(), axis, axis_free=axis is None and not node.native, move=0.0 if node.native else SPLICE_MOVE,
                        termination_length=0.006, harnesses={node.harness})
    return Junction(key, node.kind, node.exit.copy() if node.exit is not None else np.zeros(3), None,
                    axis_free=True, harnesses={node.harness})


def _split_at_supports(points: np.ndarray, cuts: list[dict[str, Any]], wires: set[str], harness: str,
                       junctions: dict[str, Junction]) -> list[tuple[np.ndarray, str | None, str | None]]:
    """Split a segment polyline at the supports that hold it (closest point within 3 mm)."""
    from .geometry import project_to_polyline

    total = polyline_length(points)
    hits = []
    for support in cuts:
        if not set(support.get("wires", [])) & wires:
            continue
        d, s = project_to_polyline(points, np.asarray(support.get("path_position", support["position"]), float)[None])
        if d <= 0.003 and 0.004 < s < total - 0.004:
            hits.append((s, support))
    if not hits:
        return [(points, None, None)]
    hits.sort(key=lambda h: h[0])
    arc = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))])
    # Blend each support's pull (support centre - routed point) into the neighbouring points with a
    # cosine window, so the planner starts from a smooth curve through the support instead of a kink
    # it would have to iron out (the window spans a few pull lengths, at least 15 mm each side).
    points = points.copy()
    for s, support in hits:
        offset = np.asarray(support["position"], float) - _at(points, arc, s)
        window = max(0.015, 4.0 * float(np.linalg.norm(offset)))
        near = np.abs(arc - s) < window
        weight = 0.5 * (1.0 + np.cos(np.pi * np.abs(arc[near] - s) / window))
        points[near] += weight[:, None] * offset[None]
    out: list[tuple[np.ndarray, str | None, str | None]] = []
    prev_s, prev_key = 0.0, None
    for s, support in hits:
        key = f"{harness}/S:{support['id']}"
        if key not in junctions:
            junctions[key] = Junction(key, "support", np.asarray(support["position"], float),
                                      np.asarray(support["axis"], float), axis_free=True,
                                      plane_normal=np.asarray(support["normal"], float), harnesses={harness})
        mask = (arc > prev_s) & (arc < s)
        start = _at(points, arc, prev_s) if prev_key else points[0]
        piece = np.vstack([start, points[mask], junctions[key].position])
        out.append((piece, prev_key, key))
        prev_s, prev_key = s, key
    mask = arc > prev_s
    out.append((np.vstack([junctions[prev_key].position, points[mask]]), prev_key, None))
    return out


def _at(points: np.ndarray, arc: np.ndarray, s: float) -> np.ndarray:
    return np.array([np.interp(s, arc, points[:, a]) for a in range(3)])


def _assign_sides(segments: list[Segment], junctions: dict[str, Junction]) -> None:
    """Side (+-axis) of every segment end at every junction, and the initial axis of
    free junctions. Segments that share a wire through a junction sit on opposite
    sides (continuous tangent); other ends follow the routed tangents. Connector and
    floating ends already carry their side (they leave along the exit direction)."""
    ends: dict[str, list[tuple[int, int, np.ndarray]]] = {}
    for k, seg in enumerate(segments):
        ends.setdefault(seg.start, []).append((k, 0, _tangent(seg.points, True)))
        ends.setdefault(seg.end, []).append((k, 1, _tangent(seg.points, False)))
    for key, items in ends.items():
        j = junctions[key]
        if j.kind in ("connector", "fanout", "floating"):
            for k, e, _ in items:
                if e == 0 and not segments[k].start_sign:
                    segments[k].start_sign = 1.0
                elif e == 1 and not segments[k].end_sign:
                    segments[k].end_sign = 1.0
            continue
        tangents = np.array([t for _, _, t in items])
        signs = _two_color(items, segments) if j.kind in ("branch", "via", "hole", "support") else None
        if signs is None:
            reference = j.axis if j.axis is not None else _principal(tangents)
            signs = [1.0 if t @ reference >= 0 else -1.0 for t in tangents]
        combined = sum(sg * t for sg, t in zip(signs, tangents))
        if j.kind == "hole":
            j.axis = j.axis * (1.0 if combined @ j.axis >= 0 else -1.0)
            signs = [1.0 if t @ j.axis >= 0 else -1.0 for t in tangents]
        elif j.kind == "splice" and not j.axis_free and j.axis is not None:
            pass  # an installed sleeve keeps its authored axis
        elif j.kind == "support":
            n = j.plane_normal
            along = combined - (combined @ n) * n
            j.axis = along / max(float(np.linalg.norm(along)), 1e-9)
            signs = [1.0 if t @ j.axis >= 0 else -1.0 for t in tangents]
        else:
            norm = float(np.linalg.norm(combined))
            j.axis = combined / norm if norm > 1e-9 else tangents[0]
        for (k, e, _), sg in zip(items, signs):
            if e == 0:
                segments[k].start_sign = sg
            else:
                segments[k].end_sign = sg


def _principal(tangents: np.ndarray) -> np.ndarray:
    _, _, vt = np.linalg.svd(tangents - 0.0)
    return vt[0]


def _two_color(items: list[tuple[int, int, np.ndarray]], segments: list[Segment]) -> list[float] | None:
    n = len(items)
    color: list[float | None] = [None] * n
    for start in range(n):
        if color[start] is not None:
            continue
        color[start] = 1.0 if items[start][2] @ items[0][2] >= 0 or start == 0 else -1.0
        stack = [start]
        while stack:
            i = stack.pop()
            for j in range(n):
                if j == i or not set(segments[items[i][0]].wires) & set(segments[items[j][0]].wires):
                    continue
                want = -color[i]
                if color[j] is None:
                    color[j] = want
                    stack.append(j)
                elif color[j] != want:
                    return None
    # Unconstrained ends (no shared wire) follow the geometry of the first.
    return [float(c) for c in color]


def plan_routing(project: Any, router: Any, routing: Any, *, supports: dict[str, list[dict[str, Any]]] | None = None,
                 obstacles: Any = None, max_iterations: int = 300) -> PlanningReport:
    """Plan every harness group and write the planned geometry back into ``routing``
    (segment points, wire paths, bundle radii, bends, splice / floating / pigtail
    placement). Returns plans, ``route_infeasible`` diagnostics and moved junctions."""
    requirement = float(project.spec.requirements.min_wire_clearance_moving)
    before = {w: r.path.copy() for w, r in routing.wires.items()}
    plans, diagnostics, moved = [], [], {}
    for harness in project.spec.electrical.harnesses:
        for wire in harness.wires:
            if wire.id not in routing.wires:
                diagnostics.append(Diagnostic.error(
                    "wire_route_failed", f"{harness.id}: {wire.id} has no routed conductor geometry",
                    subjects=[harness.id, wire.id],
                    hint="Resolve the terminal geometry or routing failure, then route this conductor again."))
    stats: dict[str, Any] = {}
    occupied: list[tuple[np.ndarray, float, set[str]]] = []
    for group in _groups(project, routing):
        holes = {i for h in group for i in routing.harnesses[h].holes}
        fields = clearance_field(router, holes)
        fields.hardware = router.cable_field(routing.nodes, set(group))
        categories = _group_categories(project, group)
        if occupied:
            exits = [node.exit for harness in group for node in routing.nodes[harness].values()
                     if node.kind == "connector" and node.exit is not None]
            occupancy = occupancy_field(router.grid, occupied, categories, termination_exits=exits)
            np.minimum(fields.static, occupancy, out=fields.static)
            np.minimum(fields.hardware, occupancy, out=fields.hardware)
        segments, junctions = build_problem(project, router, routing, group, supports)
        if not segments:
            continue
        if any(h.route.independent_branches for h in project.spec.electrical.harnesses if h.id in group):
            from .branch_lanes import plan_lanes

            plan = plan_lanes(project, router, routing, segments, junctions, fields, requirement,
                              max_iterations=max_iterations, obstacles=obstacles)
        else:
            plan = plan_bundle(segments, junctions, fields, requirement, max_iterations=max_iterations,
                               obstacles=obstacles)
        plans.append(plan)
        stats["+".join(group)] = plan.stats
        _apply(project, routing, plan, moved)
        for harness in group:
            for wire_id in routing.harnesses[harness].wires:
                wire = routing.wires.get(wire_id)
                if wire is None or wire.failed:
                    continue  # Failed/missing paths remain errors, never physical occupancy.
                for pts, radius in router._occupied_runs(wire):
                    occupied.append((pts, radius, categories))
        for v in plan.violations:
            diagnostics.append(Diagnostic.error(
                "route_infeasible",
                f"{v.harness}: {v.kind.replace('_', ' ')} cannot be met at "
                f"({', '.join(f'{c * 1e3:.0f}' for c in v.position)}) mm: required {v.required * 1e3:.1f} mm, "
                f"best {v.achieved * 1e3:.1f} mm",
                subjects=[*v.harness.split(","), *v.wires, *v.components],
                data=v.as_dict(), hint=v.action))
    return PlanningReport(plans, diagnostics, moved, stats, before)


def _apply(project: Any, routing: Any, plan: BundlePlan, moved: dict[str, dict[str, Any]]) -> None:
    from .router import Bend, fan_bends

    junctions = plan.junctions
    for key, j in junctions.items():
        if j.kind in ("splice", "floating"):
            original = None
            for harness in j.harnesses:
                for node in routing.nodes.get(harness, {}).values():
                    if j.kind == "splice" and not node.native and key == f"{harness}/N:{node.name}":
                        original = node.exit.copy()
                        node.exit = j.position.copy()
                        node.lead_end = j.position.copy()
            if j.kind == "floating":
                pair = next(f for f in routing.floating if key == f"F:{f.a}|{f.b}")
                if getattr(pair, "native", False):
                    continue
                original = np.asarray(pair.center, float).copy()
                pair.center, pair.axis = j.position.copy(), j.axis.copy()
                for ref, sign in ((pair.a, -1.0), (pair.b, 1.0)):
                    h, name = ref.split(".", 1)
                    node = routing.nodes[h][name]
                    node.face = j.position.copy()
                    node.direction = sign * j.axis
                    node.exit = j.position + sign * j.axis * j.half_length
                    node.lead_end = node.exit.copy()
            if original is not None and np.linalg.norm(original - j.position) > 1e-4:
                moved[key] = {"kind": j.kind, "from": original.tolist(), "to": j.position.tolist(),
                              "distance": float(np.linalg.norm(original - j.position)), "axis": j.axis.tolist()}
        if j.kind == "connector" and j.axis_free:
            harness, name = key.split("/N:")
            node = routing.nodes[harness][name]
            if float(np.degrees(np.arccos(np.clip(node.direction @ j.axis, -1, 1)))) > 0.5:
                moved[key] = {"kind": "pigtail_exit", "from": node.direction.tolist(), "to": j.axis.tolist(),
                              "angle_deg": float(np.degrees(np.arccos(np.clip(node.direction @ j.axis, -1, 1))))}
            node.direction = j.axis.copy()
            node.lead_end = node.exit + node.direction * max(j.lead, node.lead_length)
    # Segment points (floating pair centres stripped: wires start at the connector exit).
    by_harness: dict[str, list[Segment]] = {}
    for seg in plan.segments:
        pts = seg.points
        if seg.start.startswith("F:"):
            pts = pts[1:]
        if seg.end.startswith("F:"):
            pts = pts[:-1]
        by_harness.setdefault(seg.harness, []).append(Segment(seg.harness, seg.index, seg.start, seg.end, pts,
                                                              seg.wires, seg.od, seg.r_min, head=seg.head,
                                                              tail=seg.tail))
    for harness, segs in by_harness.items():
        route = routing.harnesses[harness]
        merged: dict[int, list[Segment]] = {}
        for seg in segs:
            merged.setdefault(seg.index, []).append(seg)
        for index, parts in merged.items():
            pts = parts[0].points
            for extra in parts[1:]:
                pts = np.vstack([pts, extra.points[1:]])
            # The fan-out zones (node exit -> breakout) stay part of the bundle segment.
            if parts[0].head is not None and len(parts[0].head):
                pts = np.vstack([parts[0].head, pts])
            if parts[-1].tail is not None and len(parts[-1].tail):
                pts = np.vstack([pts, parts[-1].tail])
            route.segments[index]["points"] = pts
        nodes = routing.nodes[harness]
        for wire_id in route.wires:
            wire = routing.wires.get(wire_id)
            if wire is None or wire.failed:
                continue
            head, tail = wire.fanout
            n = len(wire.path)
            start = wire.path[head] if head else nodes[wire.from_node].exit
            end = wire.path[n - tail - 1] if tail else nodes[wire.to_node].exit
            chain = _wire_chain(wire, segs, start, end)
            if chain is None:
                continue
            core, radius, count, rmin = chain
            # Fan-outs from the pins are kept exactly; only the core between the breakouts is replanned.
            fan_head, fan_tail = wire.path[:head], wire.path[n - tail:]
            factor = float(project.catalog.wire(wire.awg).min_bend_radius_factor)
            single = lambda k: (np.full(k, wire.od / 2), np.ones(k), np.full(k, factor * wire.od))  # noqa: E731
            (hr, hc, hm), (tr, tc, tm) = single(head), single(tail)
            path = np.vstack([fan_head, core, fan_tail])
            radius = np.concatenate([hr, radius, tr])
            count = np.concatenate([hc, count, tc])
            rmin = np.concatenate([hm, rmin, tm])
            wire.path, wire.bundle_radius, wire.conductors, wire.core = path, radius, count, core
            radii = vertex_radii(path)
            wire.bends = [Bend(path[i].copy(), float(radii[i]), float(rmin[i]), int(count[i]), float(2 * radius[i]))
                          for i in range(head + 1, len(path) - 1 - tail) if radii[i] < 3 * rmin[i]]
            wire.bends += fan_bends(path, (head, tail), wire.od, factor)
        trunk = max((routing.wires[w] for w in route.wires if w in routing.wires),
                    key=lambda w: float(np.sum(w.conductors)), default=None)
        if trunk is not None:
            route.bundle_path = trunk.core if trunk.core is not None else trunk.path


def _wire_chain(wire: Any, segs: list[Segment], start: np.ndarray, end: np.ndarray
                ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
    """The wire's core through the planned segments that carry it, from ``start`` (its
    first breakout, or its start node's exit) to ``end``."""
    mine = [s for s in segs if wire.wire_id in s.wires]
    if not mine:
        return None
    remaining = list(mine)
    current = start
    pts, rad, cnt, rmin = [current], [mine[0].od / 2], [len(mine[0].wires)], [mine[0].r_min]
    guard = 0
    while remaining and guard < 64:
        guard += 1
        nxt = None
        for seg in remaining:
            if np.linalg.norm(seg.points[0] - current) < 1e-6:
                nxt, body = seg, seg.points
            elif np.linalg.norm(seg.points[-1] - current) < 1e-6:
                nxt, body = seg, seg.points[::-1]
            if nxt is not None:
                break
        if nxt is None:
            return None
        remaining.remove(nxt)
        rad[-1], cnt[-1], rmin[-1] = max(rad[-1], nxt.od / 2), max(cnt[-1], len(nxt.wires)), max(rmin[-1], nxt.r_min)
        for p in body[1:]:
            pts.append(p)
            rad.append(nxt.od / 2)
            cnt.append(len(nxt.wires))
            rmin.append(nxt.r_min)
        current = body[-1]
        if np.linalg.norm(current - end) < 1e-6:
            break
    if np.linalg.norm(current - end) > 1e-6:
        return None
    return np.asarray(pts), np.asarray(rad), np.asarray(cnt), np.asarray(rmin)


__all__ = ["bundle_bend_radius", "BundlePlan", "ClearanceField", "Junction", "PlanningReport", "Segment", "Violation", "build_problem",
           "clearance_field", "plan_bundle", "plan_routing", "trilinear", "vertex_radii"]

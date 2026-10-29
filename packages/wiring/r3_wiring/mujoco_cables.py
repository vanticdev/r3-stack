"""Routed harnesses as MuJoCo elastic cables (``mujoco.elasticity.cable``).

R3 does not integrate cable dynamics itself. Every routed bundle becomes a
``<composite type="cable">`` with MuJoCo's cable plugin inside the MuJoCo world
of the robot, MuJoCo steps it together with the rigid bodies, and this module
only *describes* the cables (MJCF) and reads MuJoCo's own collision results
back as clearances.

Model
-----
* **Bundles.** The router lays every wire of a harness that runs between the
  same two connectors on one bundle centreline (identical paths). Such wires
  form one loose bundle and are one cable: mass ``sum m'_i``, bending stiffness
  ``sum EI_i`` (no composite action between loose conductors, the lower bound)
  and the bundle OD of the optimal circle packing (:func:`bundle_diameter`).
  Each member wire reports the clearance of the bundle surface (conservative).
* **Spans.** A cable is split at its pins (connector ends and cable ties). Each
  span is one composite rooted in the body that carries its first pin
  (``initial="none"``: the first segment is clamped to that body) and welded to
  the body of its last pin. A tie clamps the bundle, so neighbouring spans only
  interact through the tie's body; splitting also keeps each kinematic chain
  short (a serial chain has a dense mass matrix, cost ~ n^3).
* **Stiffness.** The plugin's ``bend`` is Young's modulus with the capsule's
  ``I = pi r^4 / 4``, so ``E = EI / I`` reproduces the catalog EI; ``twist`` is
  ``G = E / (2 (1 + nu))`` (``nu`` estimated). ``flat``: the stress-free shape is
  straight (limp, silicone-insulated stranded wire is not formed to its route).
* **Mass.** Capsule density is chosen so that each segment carries
  ``m' l``; MuJoCo includes the hemispherical caps in the capsule volume, so
  ``rho = m' l / (pi r^2 l + 4/3 pi r^3)``.
* **Slack.** A span's free length is its route length times
  ``(cut length - termination allowances) / routed length``. The extra length
  is laid out as a ``sin^2`` bulge (zero slope at both pins, so the pins clamp
  the route tangent) towards the side with the most room. It then settles
  under gravity before t = 0 (:func:`settle`).
* **Segment length.** The plugin's bending stiffness is explicit in MuJoCo's
  integrator. The shortest bending wave a chain of segments of length ``l``
  carries has ``omega = (pi / l)^2 sqrt(EI / m')`` (Euler-Bernoulli
  dispersion), so a stable step needs ``omega dt < 2``, i.e.
  ``l > pi sqrt(dt sqrt(EI / m') / 2)``: 18 mm for 12 AWG, 10 mm for 28 AWG at
  1 ms. Spans use segments of at least ``max(SEGMENT_LENGTH, bound)``. A span
  too short for three such segments (MuJoCo's minimum) instead gets implicit
  joint damping that stabilises its shortest wave: with ``I = m l^2 / 12`` and
  the implicit update, ``b >= I omega^2 dt / 2 = (pi^4 / 24) EI dt / l``.
* **Damping.** Kelvin-Voigt internal damping of the insulation: joint damping
  ``b = tau EI / l`` (``tau`` estimated), with a floor ``b >= k I_x / dt``. The
  floor keeps the torsion mode of thin segments stable under the plugin's
  explicit stiffness (``omega^2 = GJ / (l I_x)``, ``I_x = m l r^2 / 2``). That
  mode is irrelevant to clearance.
* **Contact.** Cable capsules (contype 4, conaffinity 8) collide with dedicated
  part collision geoms (contype 8, conaffinity 0, massless): CAD meshes split
  into convex pieces (:func:`convex_pieces`), else catalog primitives. Parts never
  collide with each other, the ground or the tires, so the rigid-body dynamics
  only change by the cable mass and the forces the cables really exert. Cables
  do not collide with each other. Contacts with static parts have friction;
  contacts with moving parts are frictionless (geom ``priority``), so moving
  parts push cables but a wire jammed against a spinning tire cannot act as a
  friction drive or brake (in reality the tire abrades the wire; that interference
  is reported as a clearance violation).
* **Clearance.** Geoms of moving parts carry ``margin = 0`` and
  ``gap = CLEARANCE_MARGIN``. MuJoCo's narrow phase then lists every
  cable/moving-part pair closer than ``margin + gap`` in ``mjData.contact``, with
  its signed surface distance ``dist``. Only pairs with ``dist < margin``, i.e.
  real penetrations, reach the constraint solver; the rest are flagged
  ``exclude = 1`` (in gap). :class:`CableMonitor` reads that
  contact data every step to find the close pairs, then measures each distinct
  pair with ``mj_geomDistance``: the convex (MPR) narrow phase reports approximate
  ``dist`` for e.g. capsule/cylinder pairs (several micrometres off at a few mm),
  so the contact list selects pairs and GJK measures them.
  :meth:`CableMonitor.exact_distances` cross-checks the result over all pairs.
"""

from __future__ import annotations

import functools
import hashlib
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from r3_model.diagnostics import Diagnostic
from r3_model.project import Project

from .bodies import ROOT, Kinematics
from .geometry import Box, Cylinder, MeshShape, Shape, bundle_diameter, project_to_polyline
from .obstacles import ObstacleSet, build_obstacles
from .terminations import termination_allowance

CABLE_PLUGIN = "mujoco.elasticity.cable"
#: Contact bitmasks: cables (4) collide only with part collision geoms (8).
CABLE_CONTYPE, CABLE_CONAFFINITY = 4, 8
PART_CONTYPE, PART_CONAFFINITY = 8, 0
SEGMENT_LENGTH = 0.010  # m, target; the cantilever converges as 1 - 2 l / L (tests/test_wiring_sim.py)
BEND_STABILITY = 1.5  # safety factor on the implicit-damping floor of spans shorter than the bound
MIN_SEGMENTS = 3  # MuJoCo's cable composite needs at least three bodies
CLEARANCE_MARGIN = 0.025  # m (3x the RC car requirement); larger clearances are reported as this value
POISSON_RATIO = 0.45  # silicone-insulated stranded copper (estimate)
RETARDATION_TIME = 0.002  # s, Kelvin-Voigt tau of the insulation (estimate)
TWIST_STABILITY = 40.0  # damping floor factor k in b >= k I_x / dt
ANCHOR_SOLREF = "0.002 1"  # weld/clamp time constant: 2 dt, critically damped
CONTACT_SOLREF = "0.002 1"
# Impedance d in [0.99, 0.999] (MuJoCo default 0.9-0.95): a soft constraint settles at
# r = (1 - d) a0 / (d k), with a0 the acceleration the bent cable would have unconstrained.
# For stiff 12 AWG on 1 g segments the default gives ~2 mm at a clamp; this gives ~0.1 mm.
ANCHOR_SOLIMP = CONTACT_SOLIMP = "0.99 0.999 0.001 0.5 2"
# Static supports (plates, boxes) keep MuJoCo's default impedance with the 2 dt time constant:
# measured on the RC car the deepest support penetration is the same (0.55 vs 0.52 mm) and the
# clearances identical, at ~30 % less constraint-solver time.
SUPPORT_SOLIMP = "0.9 0.95 0.001 0.5 2"
CABLE_FRICTION = "0.5 0.005 0.0001"  # silicone on plastic/aluminium (estimate)
SETTLE_TIME = 0.5  # s of heavily damped pre-roll before t = 0
SETTLE_DAMPING_FACTOR = 50.0
CONVEX_TOLERANCE = 0.97  # volume / hull volume above which a piece counts as convex


# --------------------------------------------------------------------- plan
@dataclass
class CableSpan:
    """One composite: a cable span between two pins."""

    prefix: str
    vertices: np.ndarray  # (n + 1, 3), robot frame at the nominal (q = 0) pose
    start_body: str  # kinematic body (``ROOT`` or a joint child component)
    end_body: str
    damping: float
    exclude: list[tuple[int, str]] = field(default_factory=list)  # (segment, kinematic body)
    # How the pins hold the bundle (from the support hardware, r3_wiring.supports):
    # "clamped" = position and direction (rigid root / weld), "pinned" = position only
    # (ball-jointed first segment / connect). Connector ends are clamped by their housing.
    start_motion: str = "clamped"
    end_motion: str = "clamped"

    @property
    def segments(self) -> int:
        return len(self.vertices) - 1

    @property
    def segment_length(self) -> float:
        return float(np.mean(np.linalg.norm(np.diff(self.vertices, axis=0), axis=1)))

    def body_names(self) -> list[str]:
        """MuJoCo's names for the composite bodies (one per segment, in order)."""
        n = self.segments
        return [f"{self.prefix}B_first", *(f"{self.prefix}B_{k}" for k in range(1, n - 1)), f"{self.prefix}B_last"]


@dataclass
class CableBundle:
    """Wires that share one routed path, simulated as one cable."""

    id: str
    harness: str
    wires: list[str]
    colors: list[str]
    od: float
    bending_stiffness: float  # N m^2
    mass_per_m: float  # kg/m
    routed_length: float
    free_length: float
    spans: list[CableSpan]
    end_bodies: tuple[str, str]
    ties: int = 0

    @property
    def radius(self) -> float:
        return self.od / 2.0

    @property
    def young_modulus(self) -> float:
        return self.bending_stiffness / (math.pi * self.radius ** 4 / 4.0)

    @property
    def shear_modulus(self) -> float:
        return self.young_modulus / (2.0 * (1.0 + POISSON_RATIO))

    @property
    def density(self) -> float:
        """Capsule density giving mass_per_m per segment, caps included."""
        length = float(np.mean([s.segment_length for s in self.spans]))
        r = self.radius
        return self.mass_per_m * length / (math.pi * r * r * length + 4.0 / 3.0 * math.pi * r ** 3)


@dataclass
class PartGeom:
    """Massless collision geometry of one component, robot frame, nominal pose."""

    component: str
    body: str  # kinematic body
    moving: bool
    kind: str  # "mesh" | "box" | "cylinder"
    pieces: list[np.ndarray] = field(default_factory=list)  # mesh: convex vertex clouds
    center: np.ndarray | None = None
    rotation: np.ndarray | None = None
    size: tuple[float, ...] = ()  # box half extents | (radius, half length)
    hull_only: int = 0  # pieces that stay non-convex (hull used, conservative)


@dataclass
class CablePlan:
    bundles: list[CableBundle]
    parts: list[PartGeom]
    margin: float = CLEARANCE_MARGIN
    timestep: float = 0.001
    diagnostics: list[Diagnostic] = field(default_factory=list)
    assumptions: list[tuple[str, str]] = field(default_factory=list)

    @property
    def wire_bundle(self) -> dict[str, str]:
        return {wire: b.id for b in self.bundles for wire in b.wires}

    @property
    def total_mass(self) -> float:
        return sum(b.mass_per_m * b.free_length for b in self.bundles)


def core_path(data: dict[str, Any]) -> np.ndarray:
    """Bundle-core part of a routed wire: ``path`` without its pin fan-outs
    (``fanout`` = leading/trailing fan-out vertex counts in ``routes.json``)."""
    path = np.asarray(data["path"], dtype=float)
    head, tail = (data.get("fanout") or [0, 0])[:2]
    core = path[int(head): len(path) - int(tail)]
    return core if len(core) >= 2 else path


def fanout_length(data: dict[str, Any]) -> float:
    path = np.asarray(data["path"], dtype=float)
    head, tail = (data.get("fanout") or [0, 0])[:2]
    length = 0.0
    if head:
        length += float(np.linalg.norm(np.diff(path[: int(head) + 1], axis=0), axis=1).sum())
    if tail:
        length += float(np.linalg.norm(np.diff(path[len(path) - int(tail) - 1:], axis=0), axis=1).sum())
    return length


def _wire_index(project: Project) -> dict[str, tuple[Any, Any]]:
    return {wire.id: (harness, wire) for harness in project.spec.electrical.harnesses for wire in harness.wires}


def _end_info(project: Project, kin: Kinematics, harness: Any, ref: str) -> tuple[str, set[str], str | None]:
    """(kinematic body, components the end terminates on, connector type) of a wire end.
    Splices and harness-to-harness (floating) connectors are fixed to the chassis
    (in practice they are taped or tied to it)."""
    if ref in harness.splices:
        return ROOT, set(), None
    connector = harness.connectors[ref.split(".")[0]]
    endpoint = project.graph.endpoints.get(connector.mates) if connector.mates else None
    if endpoint is not None and endpoint.owner_kind == "component":
        component = connector.mates.split(".")[0]
        return kin.body_of(component), set(kin.mount_chain(component)), connector.type
    return ROOT, set(), connector.type


def _bulged(path: np.ndarray, length: float, direction: np.ndarray, segments: int) -> np.ndarray:
    """``segments + 1`` cable vertices from the first to the last point of ``path`` whose
    segments add up to ``length`` (the free wire length): the path plus a sin^2 bulge along
    ``direction`` (perpendicular to the local tangent; zero offset and slope at both pins,
    so the pins keep the route tangent). The amplitude is solved on the segmented curve,
    because chords of a few long segments cut the corners of the smooth one."""
    seg = np.linalg.norm(np.diff(path, axis=0), axis=1)
    arc = np.concatenate([[0.0], np.cumsum(seg)])
    u = arc / max(arc[-1], 1e-12)
    tangent = np.gradient(path, axis=0)
    tangent /= np.maximum(np.linalg.norm(tangent, axis=1, keepdims=True), 1e-12)
    normal = direction[None, :] - (tangent @ direction)[:, None] * tangent
    norm = np.linalg.norm(normal, axis=1, keepdims=True)
    fallback = np.cross(tangent, np.array([1.0, 0.0, 0.0]) if abs(direction[0]) < 0.9 else np.array([0.0, 1.0, 0.0]))
    normal = np.where(norm > 0.2, normal / np.maximum(norm, 1e-12), fallback / np.maximum(
        np.linalg.norm(fallback, axis=1, keepdims=True), 1e-12))
    offset = (np.sin(math.pi * u) ** 2)[:, None] * normal

    def vertices(amplitude: float) -> np.ndarray:
        return _equal_chords(path + amplitude * offset, segments)

    def chord_length(amplitude: float) -> float:
        return float(np.linalg.norm(np.diff(vertices(amplitude), axis=0), axis=1).sum())

    if chord_length(0.0) >= length - 1e-9:
        return vertices(0.0)
    lo, hi = 0.0, max(length - chord_length(0.0), 1e-3)
    while chord_length(hi) < length and hi < 1.0:
        hi *= 2.0
    for _ in range(60):  # bisection on the bulge amplitude
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if chord_length(mid) < length else (lo, mid)
    return vertices(hi)


def _equal_chords(path: np.ndarray, segments: int) -> np.ndarray:
    """``segments + 1`` vertices on ``path`` with equal chord lengths (equal arc spacing would
    let chords across a tight curl fall below the bending-stability segment length)."""
    dense = _resample(path, max(4 * segments, 200))
    end = dense[-1]

    def walk(chord: float) -> tuple[np.ndarray, float]:
        out, current, k = [dense[0]], dense[0], 0
        for _ in range(segments):
            while k < len(dense) - 1 and np.linalg.norm(dense[k + 1] - current) < chord:
                k += 1
            if k >= len(dense) - 1:
                out.append(end)
                current = end
                continue
            # Point on dense[k] -> dense[k+1] at distance ``chord`` from ``current``.
            a, b = dense[k], dense[k + 1]
            d = b - a
            f = a - current
            qa, qb, qc = d @ d, 2 * f @ d, f @ f - chord * chord
            disc = max(qb * qb - 4 * qa * qc, 0.0)
            t = (-qb + math.sqrt(disc)) / (2 * qa) if qa > 0 else 1.0
            current = a + d * min(max(t, 0.0), 1.0)
            out.append(current)
        return np.asarray(out), float(np.linalg.norm(out[-1] - end))

    total = float(np.linalg.norm(np.diff(dense, axis=0), axis=1).sum())
    lo, hi = 0.0, total / segments * 1.0000001
    for _ in range(50):  # bisection: the last vertex lands on the end of the path
        mid = 0.5 * (lo + hi)
        vertices, miss = walk(mid)
        reached = np.allclose(vertices[-1], end) and miss < 1e-12
        lo, hi = (lo, mid) if reached else (mid, hi)
    vertices, _ = walk(lo)  # the largest chord that does not run past the end; snap the last vertex
    vertices[-1] = end
    return vertices


def _resample(path: np.ndarray, segments: int) -> np.ndarray:
    seg = np.linalg.norm(np.diff(path, axis=0), axis=1)
    arc = np.concatenate([[0.0], np.cumsum(seg)])
    s = np.linspace(0.0, arc[-1], segments + 1)
    return np.column_stack([np.interp(s, arc, path[:, axis]) for axis in range(3)])


def _dense(path: np.ndarray, s0: float, s1: float, step: float = 0.001) -> np.ndarray:
    seg = np.linalg.norm(np.diff(path, axis=0), axis=1)
    arc = np.concatenate([[0.0], np.cumsum(seg)])
    inner = arc[(arc > s0) & (arc < s1)]
    s = np.unique(np.concatenate([[s0], inner, np.linspace(s0, s1, max(2, int(math.ceil((s1 - s0) / step)) + 1)), [s1]]))
    return np.column_stack([np.interp(s, arc, path[:, axis]) for axis in range(3)])


_BULGE_DIRECTIONS = [np.array(v, dtype=float) for v in ((0, 0, 1), (0, 1, 0), (0, -1, 0), (1, 0, 0), (-1, 0, 0), (0, 0, -1))]


def _best_bulge(path: np.ndarray, length: float, segments: int, obstacles: ObstacleSet | None,
                radius: float) -> np.ndarray:
    """Cable vertices bulged towards the side whose middle 60 % keeps the most clearance to
    every part (moving parts by their swept envelope)."""
    straight = _bulged(path, length, _BULGE_DIRECTIONS[0], segments)
    if obstacles is None or np.allclose(straight, _resample(path, segments), atol=1e-6):
        return straight  # no bulge needed: the direction does not matter
    candidates, samples = [], []
    for direction in _BULGE_DIRECTIONS:
        candidate = _bulged(path, length, direction, segments)
        dense = np.vstack([candidate[:-1] + (candidate[1:] - candidate[:-1]) * f for f in (0.0, 0.25, 0.5, 0.75)])
        u = np.linalg.norm(dense - candidate[0], axis=1) / max(np.linalg.norm(candidate[-1] - candidate[0]), 1e-9)
        middle = dense[(u > 0.2) & (u < 0.8)] if np.any((u > 0.2) & (u < 0.8)) else dense
        candidates.append(candidate)
        samples.append(middle[np.unique(np.linspace(0, len(middle) - 1, min(len(middle), 16)).astype(int))])
    points = np.vstack(samples)  # one distance query for all candidate directions
    clearance = np.minimum(obstacles.min_distance(points, obstacles.static, cutoff=0.05)[0],
                           obstacles.min_distance(points, obstacles.moving, swept=True, cutoff=0.05)[0]) - radius
    bounds = np.cumsum([0] + [len(m) for m in samples])
    scores = [float(clearance[a:b].min()) for a, b in zip(bounds[:-1], bounds[1:])]
    best = int(np.argmax(np.asarray(scores) + 1e-4 * (np.arange(len(scores)) == 0)))  # ties prefer up
    return candidates[best]


def stable_segment_length(bending_stiffness: float, mass_per_m: float, timestep: float) -> float:
    """Shortest segment whose bending wave is stable at ``timestep`` (see module docstring)."""
    return math.pi * math.sqrt(timestep * math.sqrt(bending_stiffness / mass_per_m) / 2.0)


def _damping(bundle_ei: float, mass_per_m: float, radius: float, length: float, timestep: float) -> float:
    material = RETARDATION_TIME * bundle_ei / length
    twist_inertia = mass_per_m * length * radius * radius / 2.0
    floor = TWIST_STABILITY * twist_inertia / timestep
    if length < stable_segment_length(bundle_ei, mass_per_m, timestep):
        floor = max(floor, BEND_STABILITY * math.pi ** 4 / 24.0 * bundle_ei * timestep / length)
    return max(material, floor)


def plan_cables(project: Project, routes: dict[str, Any], obstacles: ObstacleSet | None = None, *,
                segment_length: float = SEGMENT_LENGTH, timestep: float | None = None,
                margin: float = CLEARANCE_MARGIN) -> CablePlan:
    """Bundles, spans and part collision geometry for every routed wire."""
    obstacles = obstacles or build_obstacles(project)[0]
    kin = obstacles.kinematics
    catalog = project.catalog
    dt = float(timestep if timestep is not None else project.spec.simulation.timestep)
    wires = _wire_index(project)
    plan = CablePlan([], part_geoms(project, obstacles), margin, dt)

    groups: list[list[str]] = []
    for wire_id, data in routes.get("wires", {}).items():
        if wire_id not in wires or len(data.get("path") or []) < 2:
            continue
        path = core_path(data)
        for group in groups:
            other = routes["wires"][group[0]]
            other_path = core_path(other)
            if other["harness"] == data["harness"] and np.shape(other_path) == path.shape and \
                    np.allclose(other_path, path, atol=1e-9):
                group.append(wire_id)
                break
        else:
            groups.append([wire_id])

    zero_slack = []
    for index, members in enumerate(groups):
        harness, wire = wires[members[0]]
        data = routes["wires"][members[0]]
        path = core_path(data)
        gauges = [catalog.wire(int(routes["wires"][w]["awg"])) for w in members]
        od = bundle_diameter([float(g.outer_diameter) for g in gauges])
        ei = sum(float(g.bending_stiffness) for g in gauges)
        mass_per_m = sum(float(g.mass_per_m) for g in gauges)
        ends = [_end_info(project, kin, harness, ref) for ref in (wire.from_, wire.to)]
        allowance = sum(termination_allowance(catalog, connector) for _, _, connector in ends)
        routed = float(np.linalg.norm(np.diff(path, axis=0), axis=1).sum())
        # The fan-outs from the pins into the bundle are short and held by the housings: not simulated.
        free = float(data["cut_length"]) - allowance - fanout_length(data)
        stretch = max(free, routed) / max(routed, 1e-9)
        if free <= routed + 1e-6:
            zero_slack.append(members[0])

        # Pins: both ends plus every tie of the harness that the path passes through.
        pins: list[tuple[float, str, str]] = [(0.0, ends[0][0], "clamped"), (routed, ends[1][0], "clamped")]
        harness_routes = routes.get("harnesses", {}).get(harness.id, {})
        supports = [(np.asarray(sp["position"], dtype=float), sp.get("permitted_motion", "clamped"))
                    for sp in harness_routes.get("supports", [])]
        tie_count = 0
        for tie in routes.get("harnesses", {}).get(harness.id, {}).get("tie_points", []):
            distance, arc = project_to_polyline(path, np.asarray(tie, dtype=float)[None])
            if distance <= 0.8 * od + 0.0015 and segment_length < arc < routed - segment_length:
                if all(abs(arc - s) > segment_length for s, _, _ in pins):
                    tie_point = np.asarray(tie, dtype=float)
                    motion = next((m for q, m in supports if np.linalg.norm(q - tie_point) < 1e-6), "clamped")
                    pins.append((arc, ROOT, motion))
                    tie_count += 1
        pins.sort(key=lambda item: item[0])

        bundle_id = f"cable{index:02d}"
        target = max(segment_length, stable_segment_length(ei, mass_per_m, dt))
        spans: list[CableSpan] = []
        free_arc = 0.0
        free_total = routed * stretch
        for k, ((s0, body0, motion0), (s1, body1, motion1)) in enumerate(zip(pins[:-1], pins[1:])):
            span_path = _dense(path, s0, s1)
            span_free = (s1 - s0) * stretch
            n = max(MIN_SEGMENTS, int(math.floor(span_free / target + 1e-9)))
            vertices = _best_bulge(span_path, span_free, n, obstacles, od / 2)
            length = float(np.mean(np.linalg.norm(np.diff(vertices, axis=0), axis=1)))
            span = CableSpan(f"{bundle_id}s{k}_", vertices, body0, body1,
                             _damping(ei, mass_per_m, od / 2, length, dt), start_motion=motion0, end_motion=motion1)
            # Termination zones: segments within the zone of a connector end do not
            # contact the body they terminate on (the conductors leave the housing there).
            for seg_index in range(n):
                a = free_arc + seg_index * length
                b = a + length
                for end_index, (body, components, _) in enumerate(ends):
                    zone = 0.012 + 3 * od if components else 0.006
                    inside = a <= zone if end_index == 0 else b >= free_total - zone
                    if inside:
                        span.exclude.append((seg_index, body))
            free_arc += span_free
            spans.append(span)
        bundle = CableBundle(bundle_id, harness.id, list(members),
                             [next(w for w in harness.wires if w.id == m).color for m in members],
                             od, ei, mass_per_m, routed, free_total, spans, (ends[0][0], ends[1][0]), tie_count)
        plan.bundles.append(bundle)

    _exclude_initial_penetration(plan, obstacles)
    plan.assumptions.extend([
        ("wiring", f"Cables are MuJoCo elastic cables (composite type=cable, {CABLE_PLUGIN}) in the robot's MuJoCo "
                   f"world: {len(plan.bundles)} cables for {sum(len(b.wires) for b in plan.bundles)} wires, "
                   f"{sum(len(b.spans) for b in plan.bundles)} spans, {segment_length * 1e3:.0f} mm segments, "
                   f"{plan.total_mass * 1e3:.1f} g of wire."),
        ("wiring", "Wires on one routed path form one loose bundle: mass and EI are summed (no composite action), contact "
                   "radius from the optimal circle packing; every member reports the bundle surface clearance."),
        ("wiring", f"Plugin bend modulus E = EI / (pi r^4 / 4) from the catalog EI (estimates); twist G = E / (2 (1 + "
                   f"{POISSON_RATIO})); stress-free shape straight; Kelvin-Voigt damping tau = {RETARDATION_TIME * 1e3:.0f} ms "
                   f"(estimate) with a torsion-stability floor of {TWIST_STABILITY:.0f} I_x / dt."),
        ("wiring", "Connector ends and cable ties clamp the bundle (span rooted in, or welded to, the pin's body); "
                   "splices and harness-to-harness connectors are fixed to the chassis; route slack is laid out as a "
                   f"bulge and settles under gravity for {SETTLE_TIME:.1f} s before t = 0."),
        ("wiring", "Cables collide with massless part collision geoms (CAD meshes split into convex pieces, else catalog "
                   "primitives), not with each other; friction 0.5 against static parts (estimate), frictionless against "
                   "moving parts (abrasion is not modeled; interference is reported); clearance to moving parts is the "
                   f"signed surface distance from MuJoCo contact data (listed up to {margin * 1e3:.0f} mm, only "
                   "penetrations act)."),
    ])
    if zero_slack:
        plan.diagnostics.append(Diagnostic.warning(
            "cable_no_slack", f"{len(zero_slack)} cable(s) have no free length beyond their route; they are laid taut",
            subjects=zero_slack, data={"cables": len(zero_slack)}))
    return plan


def _exclude_initial_penetration(plan: CablePlan, obstacles: ObstacleSet) -> None:
    """Segments that start inside a static part (forced routes, pass-throughs) do not
    contact that part's body; anything shallower than 1 mm is resolved by the contact."""
    excluded = 0
    shapes = [(o.shape, o.body) for o in obstacles.obstacles if not o.moving]
    for bundle in plan.bundles:
        for span in bundle.spans:
            mids = 0.5 * (span.vertices[:-1] + span.vertices[1:])
            for shape, body in shapes:
                depth = shape.sdf_bounded(mids, 0.05) - bundle.radius
                for seg_index in np.nonzero(depth < -0.001)[0]:
                    if (int(seg_index), body) not in span.exclude:
                        span.exclude.append((int(seg_index), body))
                        excluded += 1
    if excluded:
        plan.assumptions.append(("wiring", f"{excluded} cable segment(s) start more than 1 mm inside a static part "
                                           "(route clearance below the bundle radius) and do not contact that body."))


# ----------------------------------------------------------- part geometry
def _manifold(vertices: np.ndarray, faces: np.ndarray) -> Any:
    import manifold3d

    mesh = manifold3d.Mesh(vert_properties=np.asarray(vertices, np.float32), tri_verts=np.asarray(faces, np.uint32))
    mesh.merge()
    return manifold3d.Manifold(mesh)


def _convexity(piece: Any) -> float:
    hull = piece.hull().volume()
    return piece.volume() / hull if hull > 0 else 1.0


def _split(piece: Any, depth: int, out: list[tuple[np.ndarray, bool]]) -> None:
    for part in piece.decompose():
        if part.volume() <= 1e-12:
            continue
        if _convexity(part) >= CONVEX_TOLERANCE or depth >= 6:
            out.append((part.to_mesh().vert_properties[:, :3].astype(float), _convexity(part) >= CONVEX_TOLERANCE))
            continue
        vertices = part.to_mesh().vert_properties[:, :3].astype(float)
        best = None
        for axis in range(3):
            coords = np.unique(np.round(vertices[:, axis], 6))[1:-1]
            if len(coords) > 24:  # candidate planes at vertex coordinates (quantiles for dense meshes)
                coords = np.unique(np.quantile(coords, np.linspace(0, 1, 24)))
            normal = [0.0, 0.0, 0.0]
            normal[axis] = 1.0
            for c in coords:
                a, b = part.split_by_plane(normal, float(c))
                if a.is_empty() or b.is_empty():
                    continue
                waste = sum(q.hull().volume() - q.volume() for half in (a, b) for q in half.decompose())
                if best is None or waste < best[0]:
                    best = (waste, a, b)
        if best is None:
            out.append((vertices, False))
            continue
        for half in best[1:]:
            _split(half, depth + 1, out)


@functools.lru_cache(maxsize=64)
def _convex_pieces_cached(key: str, vertices_bytes: bytes, faces_bytes: bytes) -> tuple[tuple[np.ndarray, ...], int]:
    vertices = np.frombuffer(vertices_bytes, dtype=float).reshape(-1, 3)
    faces = np.frombuffer(faces_bytes, dtype=np.int64).reshape(-1, 3)
    out: list[tuple[np.ndarray, bool]] = []
    _split(_manifold(vertices, faces), 0, out)
    return tuple(v for v, _ in out), sum(1 for _, convex in out if not convex)


def convex_pieces(vertices: np.ndarray, faces: np.ndarray) -> tuple[list[np.ndarray], int]:
    """Exact convex decomposition of a closed mesh by recursive axis-aligned plane
    splits (manifold3d), choosing the split with the least hull waste, until every
    piece has volume / hull volume >= CONVEX_TOLERANCE. CAD parts here are
    boards with components and plates, which split exactly; pieces that do not
    converge in 6 levels keep their hull (conservative). Returns (vertex clouds,
    number of hull-only pieces)."""
    vertices = np.ascontiguousarray(vertices, dtype=float)
    faces = np.ascontiguousarray(faces, dtype=np.int64)
    key = hashlib.sha1(vertices.tobytes() + faces.tobytes()).hexdigest()
    pieces, hull_only = _convex_pieces_cached(key, vertices.tobytes(), faces.tobytes())
    return [p.copy() for p in pieces], hull_only


def part_geoms(project: Project, obstacles: ObstacleSet) -> list[PartGeom]:
    out = []
    for obstacle in obstacles.obstacles:
        shape: Shape = obstacle.shape
        if isinstance(shape, MeshShape):
            pieces, hull_only = convex_pieces(shape.vertices, shape.faces)
            out.append(PartGeom(obstacle.component, obstacle.body, obstacle.moving, "mesh", pieces, hull_only=hull_only))
        elif isinstance(shape, Box):
            out.append(PartGeom(obstacle.component, obstacle.body, obstacle.moving, "box", center=shape.center,
                                rotation=shape.rotation, size=tuple(float(v) for v in shape.half)))
        elif isinstance(shape, Cylinder):
            out.append(PartGeom(obstacle.component, obstacle.body, obstacle.moving, "cylinder", center=shape.center,
                                rotation=shape.rotation, size=(float(shape.radius), float(shape.half_length))))
    return out


# --------------------------------------------------------------------- MJCF
def _fmt(values: Any) -> str:
    return " ".join(f"{float(v):.9g}" for v in np.ravel(values))


def _quat(rotation: np.ndarray) -> np.ndarray:
    import mujoco

    quat = np.zeros(4)
    mujoco.mju_mat2Quat(quat, np.ascontiguousarray(rotation, dtype=float).reshape(9))
    return quat


_COLORS = {"red": "0.85 0.1 0.1", "black": "0.08 0.08 0.08", "white": "0.92 0.92 0.92", "yellow": "0.95 0.8 0.1",
           "orange": "0.95 0.5 0.1", "brown": "0.45 0.25 0.1", "blue": "0.1 0.3 0.85", "green": "0.1 0.6 0.2"}


def _child(parent: ET.Element, tag: str, first: bool = False) -> ET.Element:
    found = parent.find(tag)
    if found is not None:
        return found
    element = ET.Element(tag)
    if first:
        parent.insert(0, element)
    else:
        parent.append(element)
    return element


def emit_mjcf(mj: ET.Element, plan: CablePlan, body_element: Callable[[str], ET.Element],
              body_origin: Callable[[str], np.ndarray], body_name: Callable[[str], str],
              part_mass: Callable[[str], float] | None = None) -> None:
    """Add part collision geoms, cables, pin welds and contact exclusions to an MJCF
    tree. Body callbacks map kinematic bodies (``ROOT`` / joint children) to the
    MJCF body element, its origin in the robot frame (body frames are aligned with
    the robot frame) and its name. ``part_mass`` gives the part geoms mass
    (standalone shaker); otherwise they are massless."""
    extension = _child(mj, "extension", first=True)
    if not any(p.get("plugin") == CABLE_PLUGIN for p in extension.findall("plugin")):
        ET.SubElement(extension, "plugin", plugin=CABLE_PLUGIN)
    # MuJoCo skips contacts between a body and its parent's weld group. A span's first segment
    # is welded to the anchor body (initial="none"), so its second segment would never touch
    # anything on that body (motor, gearbox, plates). Parts never collide with each other, so
    # disabling the parent filter only affects cables.
    option = mj.find("option")
    if option is None:
        option = ET.Element("option")
        mj.insert(1, option)
    flag = option.find("flag")
    if flag is None:
        flag = ET.SubElement(option, "flag")
    flag.set("filterparent", "disable")
    asset = _child(mj, "asset")

    for part in plan.parts:
        element, origin = body_element(part.body), body_origin(part.body)
        # Part geoms only ever touch cables. Moving parts set the pair's parameters (priority):
        # stiff, because their penetration is the reported clearance. Static supports mix with
        # the cable's values (2 dt time constant, MuJoCo's default impedance).
        common = {"contype": str(PART_CONTYPE), "conaffinity": str(PART_CONAFFINITY), "group": "3",
                  "rgba": "0.9 0.5 0.1 0.25", "solref": CONTACT_SOLREF,
                  "solimp": CONTACT_SOLIMP if part.moving else SUPPORT_SOLIMP, "friction": CABLE_FRICTION}
        if part.moving:
            # MuJoCo 3.x: a contact is active when dist < margin and listed (inactive) up to
            # margin + gap, so margin 0 keeps the physics and gap exposes near misses.
            # Frictionless against cables (the higher-priority geom sets condim): a wire rubbing
            # a spinning part is an abrasion failure the clearance check reports; a jammed wire
            # pinned at its connectors must not drive or brake the drivetrain by friction.
            common.update(margin="0", gap=f"{plan.margin:.6g}", priority="1", condim="1")
        mass = part_mass(part.component) if part_mass else 0.0
        if part.kind == "mesh":
            volumes = [max(_hull_volume(p), 1e-12) for p in part.pieces]
            for k, (vertices, volume) in enumerate(zip(part.pieces, volumes)):
                name = f"col_{part.component}_{k}"
                ET.SubElement(asset, "mesh", name=name, vertex=_fmt(vertices - origin))
                ET.SubElement(element, "geom", name=name, type="mesh", mesh=name,
                              mass=f"{mass * volume / sum(volumes):.9g}", **common)
        elif part.kind == "box":
            ET.SubElement(element, "geom", name=f"col_{part.component}_0", type="box", pos=_fmt(part.center - origin),
                          quat=_fmt(_quat(part.rotation)), size=_fmt(part.size), mass=f"{mass:.9g}", **common)
        else:
            ET.SubElement(element, "geom", name=f"col_{part.component}_0", type="cylinder",
                          pos=_fmt(part.center - origin), quat=_fmt(_quat(part.rotation)), size=_fmt(part.size),
                          mass=f"{mass:.9g}", **common)

    equality = _child(mj, "equality")
    contact = _child(mj, "contact")
    for bundle in plan.bundles:
        rgba = _COLORS.get(bundle.colors[0].lower(), "0.6 0.6 0.6") + " 1"
        for span in bundle.spans:
            parent = body_element(span.start_body)
            local = span.vertices - body_origin(span.start_body)
            composite = ET.SubElement(parent, "composite", type="cable", prefix=span.prefix, vertex=_fmt(local),
                                      initial="ball" if span.start_motion == "pinned" else "none")
            plugin = ET.SubElement(composite, "plugin", plugin=CABLE_PLUGIN)
            for key, value in (("twist", f"{bundle.shear_modulus:.9g}"), ("bend", f"{bundle.young_modulus:.9g}"),
                               ("flat", "true"), ("vmax", "0")):
                ET.SubElement(plugin, "config", key=key, value=value)
            ET.SubElement(composite, "joint", kind="main", damping=f"{span.damping:.9g}")
            ET.SubElement(composite, "geom", type="capsule", size=f"{bundle.radius:.9g}", density=f"{bundle.density:.9g}",
                          contype=str(CABLE_CONTYPE), conaffinity=str(CABLE_CONAFFINITY), condim="3",
                          friction=CABLE_FRICTION, solref=CONTACT_SOLREF, solimp=CONTACT_SOLIMP, rgba=rgba,
                          group="1")
            # The weld acts at the pin itself (anchor relative to body2); MuJoCo's default anchor is
            # body2's origin, whose lever arm makes a tiny segment's rotation a large, stiff residual.
            if span.end_motion == "pinned":
                # Position only: the anchor is the cable tip in the last segment's frame (+x spans it).
                tip = float(np.linalg.norm(span.vertices[-1] - span.vertices[-2]))
                ET.SubElement(equality, "connect", name=f"{span.prefix}pin", body1=f"{span.prefix}B_last",
                              body2=body_name(span.end_body), anchor=_fmt([tip, 0.0, 0.0]),
                              solref=ANCHOR_SOLREF, solimp=ANCHOR_SOLIMP)
            else:
                ET.SubElement(equality, "weld", name=f"{span.prefix}pin", body1=f"{span.prefix}B_last",
                              body2=body_name(span.end_body),
                              anchor=_fmt(span.vertices[-1] - body_origin(span.end_body)),
                              solref=ANCHOR_SOLREF, solimp=ANCHOR_SOLIMP)
            names = span.body_names()
            for seg_index, body in sorted(set(span.exclude)):
                if seg_index == 0 and body == span.start_body:
                    continue  # parent/child contacts are already filtered by MuJoCo
                ET.SubElement(contact, "exclude", body1=names[seg_index], body2=body_name(body))


def _hull_volume(points: np.ndarray) -> float:
    from scipy.spatial import ConvexHull

    try:
        return float(ConvexHull(points).volume)
    except Exception:  # noqa: BLE001 - degenerate (flat) piece
        return 0.0


# ------------------------------------------------------------------ runtime
@dataclass
class ClearanceSample:
    distance: float  # signed surface distance, m (capped at the margin)
    component: str
    bundle: str


class CableMonitor:
    """Reads cable state and clearance back from a compiled MuJoCo model/data."""

    def __init__(self, model: Any, data: Any, plan: CablePlan, root_body: str) -> None:
        import mujoco

        self.model, self.data, self.plan = model, data, plan
        self.margin = plan.margin
        self.root_body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, root_body)
        self.bundle_ids = [b.id for b in plan.bundles]
        self.bundle_index = {b.id: k for k, b in enumerate(plan.bundles)}
        geom_bundle = np.full(model.ngeom, -1, dtype=np.int64)
        self.span_bodies: list[list[np.ndarray]] = []
        self.span_last_length: list[list[float]] = []
        dofs: list[int] = []
        for k, bundle in enumerate(plan.bundles):
            spans, lengths = [], []
            for span in bundle.spans:
                ids = np.array([mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, n) for n in span.body_names()])
                if (ids < 0).any():
                    raise KeyError(f"cable bodies of {span.prefix} not found in the MuJoCo model")
                spans.append(ids)
                lengths.append(float(np.linalg.norm(span.vertices[-1] - span.vertices[-2])))
                for body in ids:
                    geom_bundle[model.body_geomadr[body]:model.body_geomadr[body] + model.body_geomnum[body]] = k
                    for jnt in range(model.body_jntadr[body], model.body_jntadr[body] + model.body_jntnum[body]):
                        dofs.extend(range(model.jnt_dofadr[jnt], model.jnt_dofadr[jnt] + 3))
            self.span_bodies.append(spans)
            self.span_last_length.append(lengths)
        self.cable_dofs = np.asarray(sorted(set(dofs)), dtype=np.int64)
        self.cable_bodies = np.concatenate([ids for spans in self.span_bodies for ids in spans]) \
            if self.span_bodies else np.zeros(0, dtype=np.int64)
        self.geom_bundle = geom_bundle
        self.parts = [p.component for p in plan.parts]
        geom_part = np.full(model.ngeom, -1, dtype=np.int64)
        moving = np.zeros(model.ngeom, dtype=bool)
        for k, part in enumerate(plan.parts):
            for g in range(model.ngeom):
                name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, g) or ""
                if name.startswith(f"col_{part.component}_") and name[len(part.component) + 5:].isdigit():
                    geom_part[g] = k
                    moving[g] = part.moving
        self.geom_part = geom_part
        self.geom_moving = moving
        self.moving_geoms = np.nonzero(moving)[0]
        self.cable_geoms = np.nonzero(geom_bundle >= 0)[0]
        self.wire_bundle = plan.wire_bundle
        self.current = np.full(len(plan.bundles), self.margin)
        self.current_part = np.full(len(plan.bundles), -1, dtype=np.int64)
        self.minimum = np.full(len(plan.bundles), self.margin)
        self.minimum_part = np.full(len(plan.bundles), -1, dtype=np.int64)
        self.minimum_time = np.zeros(len(plan.bundles))
        self._pins = self._pin_arrays(plan)
        # Bodies next to each bundle's end pins: the second segment of the first span (its
        # interaction force is what the start termination holds) and the last segment.
        self.end_bodies = np.array([[spans[0][1] if len(spans[0]) > 1 else spans[0][0], spans[-1][-1]]
                                    for spans in self.span_bodies], dtype=np.int64).reshape(-1, 2)

    def _pin_arrays(self, plan: CablePlan) -> tuple[np.ndarray, ...] | None:
        """(last segment body, pin body, pin anchor in the pin body, last segment length) per span."""
        import mujoco

        last, body2, anchor, length = [], [], [], []
        for eq in range(self.model.neq):
            name = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_EQUALITY, eq) or ""
            kind = int(self.model.eq_type[eq])  # numpy int vs pybind enum: compare as ints
            if kind not in (int(mujoco.mjtEq.mjEQ_WELD), int(mujoco.mjtEq.mjEQ_CONNECT)) or not name.endswith("_pin"):
                continue
            last.append(int(self.model.eq_obj1id[eq]))
            body2.append(int(self.model.eq_obj2id[eq]))
            # Weld: data[0:3] is the anchor in body2. Connect: data[3:6] is the anchor in body2.
            anchor.append(np.array(self.model.eq_data[eq, 3:6] if kind == int(mujoco.mjtEq.mjEQ_CONNECT)
                                   else self.model.eq_data[eq, :3]))
        spans = {s.prefix: s for b in plan.bundles for s in b.spans}
        for body in last:
            prefix = (mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_BODY, body) or "").split("B_")[0]
            span = spans[prefix]
            length.append(float(np.linalg.norm(span.vertices[-1] - span.vertices[-2])))
        if not last:
            return None
        return np.asarray(last), np.asarray(body2), np.asarray(anchor), np.asarray(length)

    # -------------------------------------------------------------- clearance
    def update(self) -> np.ndarray:
        """Per-bundle clearance to moving parts: the pairs this step's contact data lists, measured by GJK."""
        data = self.data
        self.current.fill(self.margin)
        self.current_part.fill(-1)
        n = data.ncon
        if n:
            geoms = data.contact.geom[:n]
            g1, g2 = geoms[:, 0], geoms[:, 1]
            b1, b2 = self.geom_bundle[g1], self.geom_bundle[g2]
            cable_first = (b1 >= 0) & self.geom_moving[g2]
            cable_second = (b2 >= 0) & self.geom_moving[g1]
            relevant = cable_first | cable_second
            if relevant.any():
                import mujoco

                cable_geom = np.where(cable_first, g1, g2)[relevant]
                part_geom = np.where(cable_first, g2, g1)[relevant]
                pairs = np.unique(np.stack([cable_geom, part_geom], axis=1), axis=0)
                fromto = np.zeros(6)
                exact = np.array([mujoco.mj_geomDistance(self.model, data, int(gc), int(gp), self.margin, fromto) for gc, gp in pairs])
                bundle = self.geom_bundle[pairs[:, 0]]
                part = self.geom_part[pairs[:, 1]]
                d = np.minimum(exact, self.margin)
                np.minimum.at(self.current, bundle, d)
                closest = d <= self.current[bundle]
                self.current_part[bundle[closest]] = part[closest]
        better = self.current < self.minimum
        if better.any():
            self.minimum[better] = self.current[better]
            self.minimum_part[better] = self.current_part[better]
            self.minimum_time[better] = data.time
        return self.current

    def end_tension(self) -> np.ndarray:
        """(bundles, 2) magnitude of the cable force at the start and end terminations (N):
        the interaction force between the end segment and its neighbour (``cfrc_int`` from
        ``mj_rnePostConstraint``; the force part of a spatial force is frame-independent)."""
        import mujoco

        if not len(self.end_bodies):
            return np.zeros((0, 2))
        mujoco.mj_rnePostConstraint(self.model, self.data)
        force = self.data.cfrc_int[self.end_bodies.ravel(), 3:6]
        return np.linalg.norm(force, axis=1).reshape(-1, 2)

    def _part(self, index: int) -> str:
        return self.parts[index] if index >= 0 else ""

    def wire_clearance(self) -> dict[str, tuple[float, str]]:
        """{wire: (clearance of its bundle this step, closest moving part)}."""
        return {wire: (float(self.current[self.bundle_index[b]]), self._part(int(self.current_part[self.bundle_index[b]])))
                for wire, b in self.wire_bundle.items()}

    def wire_minimum(self) -> dict[str, tuple[float, str, float]]:
        """{wire: (minimum clearance so far, part, time)}."""
        return {wire: (float(self.minimum[k]), self._part(int(self.minimum_part[k])), float(self.minimum_time[k]))
                for wire, b in self.wire_bundle.items() for k in [self.bundle_index[b]]}

    def exact_distances(self, distmax: float | None = None) -> dict[str, tuple[float, str]]:
        """Per-bundle clearance from ``mj_geomDistance`` over all cable/moving-part
        geom pairs (bounding-sphere pre-filter), independent of contact data."""
        import mujoco

        model, data = self.model, self.data
        distmax = self.margin if distmax is None else distmax
        out = {b: (distmax, "") for b in self.bundle_ids}
        if not len(self.cable_geoms) or not len(self.moving_geoms):
            return out
        fromto = np.zeros(6)
        centers_c = data.geom_xpos[self.cable_geoms]
        centers_m = data.geom_xpos[self.moving_geoms]
        gap = np.linalg.norm(centers_c[:, None, :] - centers_m[None, :, :], axis=2) \
            - model.geom_rbound[self.cable_geoms][:, None] - model.geom_rbound[self.moving_geoms][None, :]
        excluded = set(int(v) for v in model.exclude_signature)
        for i, j in zip(*np.nonzero(gap < distmax)):
            g_cable, g_part = int(self.cable_geoms[i]), int(self.moving_geoms[j])
            if self._filtered(g_cable, g_part, excluded):
                continue
            d = mujoco.mj_geomDistance(model, data, g_cable, g_part, distmax, fromto)
            bundle = self.bundle_ids[self.geom_bundle[g_cable]]
            if d < out[bundle][0]:
                out[bundle] = (float(d), self.parts[self.geom_part[g_part]])
        return out

    def _filtered(self, g1: int, g2: int, excluded: set[int]) -> bool:
        """MuJoCo's own pair filters: same weld group, explicit <exclude>, and (when enabled)
        the parent filter on weld groups."""
        import mujoco

        model = self.model
        b1, b2 = int(model.geom_bodyid[g1]), int(model.geom_bodyid[g2])
        w1, w2 = int(model.body_weldid[b1]), int(model.body_weldid[b2])
        if w1 == w2:
            return True
        lo, hi = min(b1, b2), max(b1, b2)
        if (lo << 16) + hi in excluded:
            return True
        if not model.opt.disableflags & mujoco.mjtDisableBit.mjDSBL_FILTERPARENT:
            p1, p2 = int(model.body_weldid[model.body_parentid[w1]]), int(model.body_weldid[model.body_parentid[w2]])
            if (p1 == w2 and w2 != 0) or (p2 == w1 and w1 != 0):
                return True
        return False

    # ------------------------------------------------------------------ state
    def positions(self, frame: str = "robot") -> dict[str, np.ndarray]:
        """Cable vertex positions per bundle (``frame``: "robot" = chassis frame, or "world")."""
        data = self.data
        rotation = data.xmat[self.root_body].reshape(3, 3)
        origin = data.xpos[self.root_body]
        out = {}
        for bundle, spans, lengths in zip(self.bundle_ids, self.span_bodies, self.span_last_length):
            chunks = []
            for ids, last in zip(spans, lengths):
                tip = data.xpos[ids[-1]] + data.xmat[ids[-1]].reshape(3, 3)[:, 0] * last
                pts = np.vstack([data.xpos[ids], tip])
                chunks.append(pts if not chunks else pts[1:])  # consecutive spans share their tie vertex
            world = np.vstack(chunks) if chunks else np.zeros((0, 3))
            out[bundle] = world if frame == "world" else (world - origin) @ rotation
        return out

    def wire_positions(self, frame: str = "robot") -> dict[str, np.ndarray]:
        bundles = self.positions(frame)
        return {wire: bundles[b] for wire, b in self.wire_bundle.items()}

    def max_speed(self) -> float:
        """Largest linear speed of any cable segment (world frame), m/s."""
        if not len(self.cable_bodies):
            return 0.0
        import mujoco

        speed = 0.0
        vel = np.zeros(6)
        for body in self.cable_bodies:
            mujoco.mj_objectVelocity(self.model, self.data, mujoco.mjtObj.mjOBJ_BODY, int(body), vel, 0)
            speed = max(speed, float(np.linalg.norm(vel[3:])))
        return speed

    def pin_error(self) -> float:
        """Largest distance (m) between a span's cable end and the pin it is welded to:
        how far the soft clamps give under load (segments themselves are inextensible)."""
        if not self._pins:
            return 0.0
        data = self.data
        last, body2, anchor, length = self._pins
        tip = data.xpos[last] + data.xmat[last].reshape(-1, 3, 3)[:, :, 0] * length[:, None]
        pin = data.xpos[body2] + np.einsum("nij,nj->ni", data.xmat[body2].reshape(-1, 3, 3), anchor)
        return float(np.max(np.linalg.norm(tip - pin, axis=1)))


def settle(model: Any, data: Any, monitor: CableMonitor, duration: float = SETTLE_TIME,
           before_step: Callable[[], None] | None = None) -> None:
    """Heavily damped pre-roll: the laid-out slack falls onto its supports under
    gravity. Damping is restored, cable velocities zeroed and time reset to 0."""
    import mujoco

    saved = model.dof_damping[monitor.cable_dofs].copy()
    model.dof_damping[monitor.cable_dofs] = saved * SETTLE_DAMPING_FACTOR
    for _ in range(int(math.ceil(duration / model.opt.timestep))):
        if before_step:
            before_step()
        mujoco.mj_step(model, data)
    model.dof_damping[monitor.cable_dofs] = saved
    data.qvel[monitor.cable_dofs] = 0.0
    data.time = 0.0
    mujoco.mj_forward(model, data)
    monitor.minimum.fill(monitor.margin)
    monitor.minimum_part.fill(-1)
    monitor.minimum_time.fill(0.0)


__all__ = ["CABLE_PLUGIN", "CLEARANCE_MARGIN", "CableBundle", "CableMonitor", "CablePlan", "CableSpan", "PartGeom",
           "convex_pieces", "emit_mjcf", "part_geoms", "plan_cables", "settle"]

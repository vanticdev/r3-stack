"""Cable supports as hardware: catalog, placement on real structure faces, spans.

A support is a purchasable part (``data/supports.yaml``: adhesive tie mounts,
screw-applied tie mounts, cushioned P-clips) with a footprint, a fastener, a
bundle-diameter range, a retention force and a *permitted motion* that becomes
the bundle's constraint in the MuJoCo cable model (``pinned``: a narrow strap
holds the position but lets the bundle pivot, a ball joint plus a ``connect``
equality; ``clamped``: a wide band also holds the direction, a rigid root plus a
``weld``).

Placement (:func:`place_supports`) walks every planned bundle segment and, for
candidate points every :data:`CANDIDATE_STEP`, finds the exact closest point on
static structure (CAD meshes; :func:`r3_wiring.witness.exact_closest`). A
support type fits there when

* the bundle can sit on it: bundle centre at ``face + n (height + r)`` within
  :data:`MAX_PULL` of the planned path (the second planning pass routes the
  bundle through it exactly);
* the face is flat under the whole footprint (footprint corners within
  :data:`FLATNESS` of the surface, footprint aligned with the bundle);
* it can be installed: the finger-press (adhesive) or driver-line (screw)
  envelope above the footprint and, for through-bolted parts, the nut side
  below a drillable plate are free of every other part, moving parts by their
  swept envelope;
* bundle OD within the part's range and retention >= the load it must carry:
  ``SF x m' x L x a_peak`` plus, within :data:`STRAIN_RELIEF_REACH` of a
  connector, the handling pull that the support must take off the terminals.

Supports are placed greedily so that no span exceeds the maximum unsupported
span (:func:`max_unsupported_span`): the length at which a clamped-clamped span
of the bundle deflects :data:`SPAN_DEFLECTION` under its own weight at
:data:`PEAK_ACCEL_G` (``delta = w L^4 / 384 EI``), capped at :data:`SPAN_CAP`.
Spans that cannot be supported are reported with the structure gap that
prevents it (``cable_unsupported_span``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from r3_model.diagnostics import Diagnostic
from r3_model.units import to_si

from .geometry import polyline_length, project_to_polyline, resample_polyline
from .grid import FIELD_CUTOFF
from .obstacles import Obstacle, ObstacleSet
from .witness import exact_closest

DATA = Path(__file__).with_name("data") / "supports.yaml"
CANDIDATE_STEP = 0.003  # m between candidate points along a segment
END_KEEPOUT = 0.008  # m: no support this close to a connector / junction end
MIN_SPACING = 0.015  # m between supports
# First-pass bundle within this of parallel to the face. The second planning pass routes the bundle
# through the support centre parallel to the face (support_constraints), so a candidate where the
# first pass still climbs is fine; a support that makes its route infeasible is dropped (stage retry).
CANDIDATE_TILT = math.radians(35.0)
# m: how far the second planning pass may pull the bundle onto a support (it re-plans the bundle through
# the support centre; a pull that makes the route infeasible gets the support re-placed or dropped).
MAX_PULL = 0.020
_PLACEMENT = yaml.safe_load(DATA.read_text())["placement"]
HINT_MATCH = to_si(_PLACEMENT["requested_match"])
WIRE_CAPTURE_ALLOWANCE = to_si(_PLACEMENT["wire_capture_allowance"])
POST_MAX_DIAMETER = 0.012  # m: posts / standoffs a tie can loop around
FLATNESS = 0.0003  # m: footprint corners must lie this close to the face
STRUCTURE_REACH = 0.022  # m: candidate structure within this of the bundle surface (MAX_PULL + mount height)
SPAN_DEFLECTION = 0.003  # m allowed sag of a span (estimate: well inside the 8 mm moving clearance)
PEAK_ACCEL_G = 4.0  # 1 g + the 3 g bump of the slack drive
SPAN_CAP = 0.10  # m, practical cap for small robots (estimate; SAE AS50881 caps aircraft spans at 24 in)
SAFETY_FACTOR = 3.0
SPAN_PLACEMENT_FRACTION = 0.9  # place for 90 % of the limit: the second planning pass changes span lengths
EXCLUDE_RADIUS = 0.010  # m: a re-placement keeps candidates this far from a support that made its route infeasible
STRAIGHT_WINDOW = 0.015  # m: among the farthest reachable candidates within this window, take the straightest
HANDLING_PULL = 10.0  # N a support next to a connector takes off the terminals (estimate, service handling)
STRAIN_RELIEF_REACH = 0.06  # m from a connector exit
G = 9.80665


@dataclass(frozen=True)
class SupportType:
    id: str
    kind: str
    mpn: str | None
    manufacturer: str
    description: str
    footprint: tuple[float, float]
    height: float
    od_range: tuple[float, float]
    tie: dict[str, Any] | None
    fastener: dict[str, Any] | None
    retention: float
    max_temperature_c: float
    motion: str
    access: tuple[float, float]
    provenance: dict[str, Any]

    @property
    def screwed(self) -> bool:
        return bool(self.fastener)


@lru_cache(maxsize=1)
def load_catalog(path: str = str(DATA)) -> tuple[tuple[SupportType, ...], dict[str, dict[str, Any]]]:
    raw = yaml.safe_load(Path(path).read_text())
    ties = {t["id"]: {**t, "width": to_si(t["width"]), "thickness": to_si(t["thickness"]),
                      "max_bundle": to_si(t["max_bundle"]), "loop_tensile": to_si(t["loop_tensile"])}
            for t in raw.get("ties", [])}
    types = []
    for s in raw["supports"]:
        fastener = s.get("fastener")
        types.append(SupportType(
            s["id"], s["kind"], s.get("mpn"), s.get("manufacturer", ""), s.get("description", ""),
            tuple(to_si(v) for v in s["footprint"]), to_si(s["height"]), tuple(to_si(v) for v in s["bundle_od"]),
            ties.get(s.get("tie")) if s.get("tie") else None, fastener if isinstance(fastener, dict) else None,
            to_si(s["retention"]), float(s.get("max_temperature_c", 85)), s["permitted_motion"],
            tuple(to_si(v) for v in s["access"]), s.get("provenance", {})))
    return tuple(types), ties


def max_unsupported_span(bending_stiffness: float, mass_per_m: float, deflection: float = SPAN_DEFLECTION,
                         accel_g: float = PEAK_ACCEL_G, cap: float = SPAN_CAP) -> float:
    """Longest clamped-clamped span whose sag under ``accel_g`` stays within ``deflection``:
    ``delta = w L^4 / (384 EI)`` with ``w = m' g accel_g`` (Euler-Bernoulli beam)."""
    w = mass_per_m * G * accel_g
    if w <= 0 or bending_stiffness <= 0:
        return cap
    return float(min(cap, (384.0 * bending_stiffness * deflection / w) ** 0.25))


@dataclass
class Support:
    id: str
    harness: str
    type: SupportType
    position: np.ndarray  # bundle centre on the support
    path_position: np.ndarray  # where the planned path passed before the bundle was pulled onto it
    mount: np.ndarray  # footprint centre on the face
    normal: np.ndarray  # face normal (out of the host)
    axis: np.ndarray  # bundle direction along the support
    host: str
    wires: list[str]
    bundle_od: float
    segment: int
    arc: float
    required_retention: float
    pull: float
    checks: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        t = self.type
        return {"id": self.id, "harness": self.harness, "type": t.id, "kind": t.kind, "mpn": t.mpn,
                "manufacturer": t.manufacturer, "description": t.description,
                "position": self.position.tolist(), "path_position": self.path_position.tolist(),
                "mount": self.mount.tolist(), "normal": self.normal.tolist(), "axis": self.axis.tolist(),
                "host": self.host, "wires": self.wires, "bundle_od": self.bundle_od, "footprint": list(t.footprint),
                "height": t.height, "fastener": t.fastener, "tie": t.tie["mpn"] if t.tie else None,
                "retention": t.retention, "required_retention": self.required_retention,
                "permitted_motion": t.motion, "pull": self.pull, "checks": self.checks, "units": "m, N"}


@dataclass
class SupportReport:
    supports: list[Support]
    diagnostics: list[Diagnostic]
    spans: list[dict[str, Any]]
    limits: dict[str, float]  # harness -> max unsupported span
    dropped: list[str] = field(default_factory=list)  # placed, then removed: they made a route infeasible

    def by_harness(self) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for s in self.supports:
            out.setdefault(s.harness, []).append(s.as_dict())
        return out


# ------------------------------------------------------------------ geometry
def _frame(normal: np.ndarray, tangent: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    t = tangent - (tangent @ normal) * normal
    if np.linalg.norm(t) < 1e-9:
        helper = np.array([1.0, 0.0, 0.0]) if abs(normal[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        t = np.cross(normal, helper)
    t = t / np.linalg.norm(t)
    return t, np.cross(normal, t)


def _flat(host: Obstacle, mount: np.ndarray, normal: np.ndarray, t: np.ndarray, b: np.ndarray,
          footprint: tuple[float, float]) -> tuple[bool, float]:
    """Footprint corners and edge midpoints, lifted 0.2 mm, must be 0.2 mm from the face."""
    lift = 0.0002
    probes = [mount + normal * lift + t * u * footprint[0] / 2 + b * v * footprint[1] / 2
              for u in (-1, 0, 1) for v in (-1, 0, 1) if (u, v) != (0, 0)]
    distances = np.array([c.distance for c in exact_closest(host.shape, np.asarray(probes))])
    error = float(np.max(np.abs(distances - lift)))
    return error <= FLATNESS, error


def _envelope(points: np.ndarray, obstacles: ObstacleSet, exclude: set[str], radius: float) -> tuple[bool, str]:
    """Is every point at least ``radius`` from every part (moving parts swept) not in ``exclude``?"""
    for o in obstacles.obstacles:
        if o.component in exclude:
            continue
        shape = o.swept if o.moving else o.shape
        # Only the predicate d < radius is needed here. Clamping at radius
        # preserves it exactly, and avoids querying every remote swept pose
        # out to the general-purpose 50 mm cutoff for each installation probe.
        if float(shape.sdf_bounded(points, min(radius, 0.05)).min()) < radius:
            return False, o.component
    return True, ""


def _access(stype: SupportType, host: Obstacle, mount: np.ndarray, normal: np.ndarray, t: np.ndarray,
            b: np.ndarray, bundle: set[str], obstacles: ObstacleSet, r: float,
            post: Post | None = None) -> tuple[bool, dict[str, Any]]:
    diameter, height = stype.access
    exclude = {host.component} | bundle
    checks: dict[str, Any] = {}
    if stype.screwed:
        # Driver line along the normal through the screw (beside the bundle on a tie mount).
        start = mount + normal * (stype.height + 2 * r + 0.001)
        line = start + normal[None] * np.linspace(0.0, height, 25)[:, None]
        ok, blocker = _envelope(line, obstacles, exclude, diameter / 2)
        checks["driver_line"] = {"ok": ok, "blocked_by": blocker, "length": height, "diameter": diameter}
        if not ok:
            return False, checks
        if host.plate_axis is None:
            checks["drill"] = {"ok": False, "reason": f"{host.component} is not a drillable plate"}
            return False, checks
        # Nut side: below the plate, a 10 mm socket / finger envelope 12 mm deep.
        lo, hi = host.shape.aabb()
        thickness = float(hi[host.plate_axis] - lo[host.plate_axis])
        below = mount - normal * (thickness + 0.0005) - normal[None] * np.linspace(0.0, 0.012, 9)[:, None]
        ok, blocker = _envelope(below, obstacles, exclude, 0.005)
        checks["nut_side"] = {"ok": ok, "blocked_by": blocker, "plate_thickness": thickness}
        return ok, checks
    if stype.kind == "post_tie":
        # The tie loops around the post and the bundle: its path must be free of other parts.
        if post is not None:
            if np.linalg.norm(normal - (normal @ post.axis) * post.axis) < 1e-6:
                return False, {"tie_loop": {"ok": False, "reason": "an end face cannot retain a side-loop tie"}}
            loop = _tie_loop(post, mount, normal, r)
        else:
            loop = np.asarray([mount + normal * r + (normal * math.cos(a) + b * math.sin(a)) * (2 * r + 0.004)
                               for a in np.linspace(0, 2 * math.pi, 16, endpoint=False)])
        ok, blocker = _envelope(loop, obstacles, exclude, 0.0)
        checks["tie_loop"] = {"ok": ok, "blocked_by": blocker}
        return ok, checks
    # Adhesive: finger press over the footprint, and room to thread and cinch the tie.
    rings = []
    for s in np.linspace(stype.height + 2 * r + 0.002, height, 6):
        for angle in np.linspace(0, 2 * math.pi, 8, endpoint=False):
            rings.append(mount + normal * s + (t * math.cos(angle) + b * math.sin(angle)) * diameter / 2)
    ok, blocker = _envelope(np.asarray(rings), obstacles, exclude, 0.0)
    checks["finger_press"] = {"ok": ok, "blocked_by": blocker, "diameter": diameter, "height": height}
    return ok, checks


# ------------------------------------------------------------------ placement
NON_HOST_MATERIALS = ("foam", "hook-and-loop")  # tapes and straps cannot carry a mount


def support_hosts(project: Any, obstacles: ObstacleSet) -> list[Obstacle]:
    """Static structure that can carry a tie mount or P-clip: rigid parts only (no foam
    tape, hook-and-loop straps or zip ties)."""
    hosts = []
    for o in obstacles.obstacles:
        if not o.structure or o.moving:
            continue
        try:
            ctype = project.catalog.component(project.spec.components[o.component].type)
        except KeyError:
            continue
        material = str(getattr(ctype.mechanical, "material", "") or "").lower()
        if any(m in material for m in NON_HOST_MATERIALS) or ctype.id.startswith("zip-tie"):
            continue
        hosts.append(o)
    return hosts


@dataclass(frozen=True)
class Post:
    """A slender cylinder of a host (robot frame) that a cable tie can loop around."""

    component: str
    base: np.ndarray
    axis: np.ndarray
    radius: float
    length: float  # math.inf: the whole part (a cylindrical standoff)

    def holds(self, point: np.ndarray, tolerance: float = 3e-4) -> bool:
        """Is ``point`` (a surface point of the host) on this cylinder's side face?"""
        rel = np.asarray(point, float) - self.base
        t = float(rel @ self.axis)
        radial = float(np.linalg.norm(rel - t * self.axis))
        inside = math.isinf(self.length) or -tolerance <= t <= self.length + tolerance
        # Standoffs (infinite length) are posts over their whole side face, whatever their end chamfers.
        return inside and (math.isinf(self.length) or abs(radial - self.radius) <= tolerance)


def post_hosts(project: Any, hosts: list[Obstacle]) -> dict[str, list[Post]]:
    """Slender cylindrical structure a cable tie can loop around: cylindrical standoffs and
    spacers, and slender cylinder solids of other parts (an antenna-mast tube above its flange)."""
    from r3_model.assembly import pose_matrix

    out: dict[str, list[Post]] = {}
    for o in hosts:
        comp = project.spec.components[o.component]
        mech = project.catalog.component(comp.type).mechanical
        pose = pose_matrix(comp.pose.xyz, comp.pose.rpy)
        if mech.shape == "cylinder" and float(mech.size[0]) <= POST_MAX_DIAMETER:
            local = {"x": [1.0, 0, 0], "y": [0, 1.0, 0], "z": [0, 0, 1.0]}.get(str(mech.axis or "z"), [0, 0, 1.0])
            axis = pose[:3, :3] @ np.asarray(local, float)
            out[o.component] = [Post(o.component, pose[:3, 3].copy(), axis / np.linalg.norm(axis),
                                     float(mech.size[0]) / 2, math.inf)]
            continue
        for solid in getattr(mech, "solids", None) or []:
            diameter = float(solid.diameter or 0.0)
            if solid.kind != "cylinder" or solid.op != "add" or not 0 < diameter <= POST_MAX_DIAMETER:
                continue
            axis = pose[:3, :3] @ np.asarray(solid.axis, float)
            axis = axis / np.linalg.norm(axis)
            base = pose[:3, :3] @ np.asarray(solid.position, float) + pose[:3, 3]
            out.setdefault(o.component, []).append(Post(o.component, base, axis, diameter / 2, float(solid.length)))
    return out


def _post_at(posts: dict[str, list[Post]], component: str, point: np.ndarray) -> Post | None:
    return next((p for p in posts.get(component, []) if p.holds(point)), None)


def _is_post(posts: dict[str, list[Post]], component: str, point: np.ndarray) -> bool:
    return _post_at(posts, component, point) is not None


def _tie_loop(post: Post, mount: np.ndarray, normal: np.ndarray, r: float, thickness: float = 0.001) -> np.ndarray:
    """Centre line of a tie hugging a post and the bundle lying against it: the convex hull of the
    two circles (post radius and bundle radius plus half the tie thickness) in the plane normal to
    the post axis. It runs outside both, so parts inside a hollow post (a coax in a mast) do not block it."""
    axis = post.axis
    n = normal - (normal @ axis) * axis
    if np.linalg.norm(n) < 1e-6:  # the bundle sits on the post's end face: no loop around the side
        return np.asarray([mount + normal * (post.radius + r) * 4])
    n = n / np.linalg.norm(n)
    b = np.cross(axis, n)
    big, small = post.radius + thickness / 2, r + thickness / 2
    c_post, c_bundle = mount - n * post.radius, mount + n * r
    distance = float(np.linalg.norm(c_bundle - c_post))
    tangent = math.acos(float(np.clip((big - small) / max(distance, 1e-9), -1.0, 1.0)))  # external tangent angle
    points = []
    for angle in np.linspace(tangent, 2 * math.pi - tangent, 24):  # far side of the post
        points.append(c_post + big * (math.cos(angle) * n + math.sin(angle) * b))
    for angle in np.linspace(-tangent, tangent, 12):  # far side of the bundle
        points.append(c_bundle + small * (math.cos(angle) * n + math.sin(angle) * b))
    return np.asarray(points)


def place_supports(project: Any, obstacles: ObstacleSet, routing: Any,
                   exclude: list[tuple[str, np.ndarray]] | None = None) -> SupportReport:
    """Supports on the first-pass routes. ``exclude`` holds (harness, position) of supports
    that made their route infeasible in the second pass; candidates near them are skipped,
    so the placement moves to the next feasible spot instead of leaving the span open."""
    catalog = project.catalog
    types, _ = load_catalog()
    structures = support_hosts(project, obstacles)
    posts = post_hosts(project, structures)
    supports: list[Support] = []
    diagnostics: list[Diagnostic] = []
    limits: dict[str, float] = {}
    connector_exits = {h: [n.exit for n in nodes.values() if n.kind == "connector" and n.exit is not None]
                       for h, nodes in routing.nodes.items()}
    requested = {h.id: [np.asarray(p, float) for p in h.route.tie_points]
                 for h in project.spec.electrical.harnesses}
    for harness_id, route in routing.harnesses.items():
        wires = [routing.wires[w] for w in route.wires if w in routing.wires and not routing.wires[w].failed]
        if not wires:
            continue
        # A branch harness can have several segments near a requested anchor. Assign it to
        # the nearest segment once; it must not pin unrelated branches to the same point.
        hints_by_segment: dict[int, list[np.ndarray]] = {}
        for hint in requested.get(harness_id, []):
            choices = [(float(project_to_polyline(np.asarray(s["points"], float), hint[None])[0]), i)
                       for i, s in enumerate(route.segments) if len(s["points"]) >= 2]
            if choices:
                distance, index = min(choices)
                if distance <= MAX_PULL:
                    hints_by_segment.setdefault(index, []).append(hint)
        for index, seg in enumerate(route.segments):
            seg_wires = [routing.wires[w] for w in seg["wires"] if w in routing.wires]
            if not seg_wires:
                continue
            gauges = [catalog.wire(w.awg) for w in seg_wires]
            ei = sum(float(g.bending_stiffness) for g in gauges)
            mass = sum(float(g.mass_per_m) for g in gauges)
            limit = max_unsupported_span(ei, mass)
            limits[harness_id] = min(limits.get(harness_id, math.inf), limit)
            pts = np.asarray(seg["points"], dtype=float)
            total = polyline_length(pts)
            if total < 2 * END_KEEPOUT:
                continue
            r = float(seg["od"]) / 2
            candidates = _candidates(pts, total, r, structures, obstacles, types, float(seg["od"]), mass,
                                     limit, connector_exits.get(harness_id, []), posts,
                                     hints_by_segment.get(index, []))
            banned = [p for h, p in (exclude or []) if h == harness_id]
            candidates = [c for c in candidates
                          if all(np.linalg.norm(c["path"] - p) > EXCLUDE_RADIUS for p in banned)]
            # Ends at a connector, splice or mated pair (fan-out breakouts) hold the bundle; a via or branch
            # junction does not, so the span through it continues into the next segment.
            held = [b is not None for b in (seg.get("breakouts") or [None, None])]
            mandatory = [c for c in candidates if c.get("requested") is not None]
            automatic = _greedy([c for c in candidates if c.get("requested") is None], total,
                                SPAN_PLACEMENT_FRACTION * limit, held[0], held[-1])
            chosen = mandatory + [c for c in automatic
                                  if all(abs(c["arc"] - m["arc"]) >= MIN_SPACING for m in mandatory)]
            for c in sorted(chosen, key=lambda c: c["arc"]):
                support = Support(f"{harness_id}.S{len([s for s in supports if s.harness == harness_id]) + 1}",
                                  harness_id, c["type"], c["position"], c["path"], c["mount"], c["normal"], c["t"],
                                  c["host"], [w.wire_id for w in seg_wires], float(seg["od"]), index, c["arc"],
                                  c["required"], c["pull"], c["checks"])
                supports.append(support)
    return SupportReport(supports, diagnostics, [], limits)


def check_requested_supports(project: Any, report: SupportReport, routing: Any) -> None:
    """An authored tie point requires checked hardware, including after planner retries.

    Never retain a simulation pin when installation access, retention or routing made
    its physical support impossible. The normal span and dynamic checks still apply.
    """
    for harness in project.spec.electrical.harnesses:
        for hint in harness.route.tie_points:
            point = np.asarray(hint, float)
            installed = [s for s in report.supports if s.harness == harness.id]
            distances = [float(np.linalg.norm(s.position - point)) for s in installed]
            holding = [s for s in installed if np.linalg.norm(s.position - point) <= HINT_MATCH and
                       all(w in routing.wires and not routing.wires[w].failed and
                           project_to_polyline(routing.wires[w].path, s.position[None])[0] <= s.bundle_od / 2 + WIRE_CAPTURE_ALLOWANCE
                           for w in s.wires)]
            if not holding:
                report.diagnostics.append(Diagnostic.error(
                    "cable_support_unplaceable", f"{harness.id}: requested tie has no installed support holding its wires",
                    subjects=[harness.id], data={"position": point.tolist(), "required_match": HINT_MATCH,
                                                "nearest_support": min(distances) if distances else None,
                                                "units": "m"},
                    hint="Place a real saddle or tie post within reach and keep its installation access clear."))


def check_spans(project: Any, routing: Any, report: SupportReport) -> None:
    """Span check on the final (support-constrained) geometry; fills ``report.spans`` and
    adds ``cable_unsupported_span`` diagnostics."""
    report.spans = _span_check(routing, report.supports, report.limits, report.diagnostics, project.catalog)


_RAYS: dict[int, Any] = {}
FACE_DIRECTIONS = np.array([[0, 0, 1.0], [0, 0, -1.0], [1.0, 0, 0], [-1.0, 0, 0], [0, 1.0, 0], [0, -1.0, 0]])


def _face_mounts(host: Obstacle, p: np.ndarray, reach: float) -> list[tuple[np.ndarray, np.ndarray]]:
    """Mount points on flat faces of ``host`` facing ``p`` along the robot axes. The nearest point
    of a plate is often its edge (a bundle running beside a tapered plate edge), where no footprint
    is flat; the face straight below or beside the bundle may still carry a mount."""
    vertices = getattr(host.shape, "vertices", None)
    if vertices is None:
        return []
    key = id(host.shape)
    if key not in _RAYS:
        import trimesh

        mesh = trimesh.Trimesh(np.asarray(vertices, float), np.asarray(host.shape.faces, int), process=False)
        _RAYS[key] = (mesh, trimesh.ray.ray_triangle.RayMeshIntersector(mesh))
    mesh, rays = _RAYS[key]
    hits, ray_index, tri = rays.intersects_location(np.repeat(p[None], len(FACE_DIRECTIONS), axis=0), -FACE_DIRECTIONS)
    out = []
    for direction_index, (q, t_index) in sorted(zip(ray_index, zip(hits, tri)), key=lambda x: np.linalg.norm(x[1][0] - p)):
        d = FACE_DIRECTIONS[direction_index]
        distance = float(np.linalg.norm(q - p))
        if distance > reach or any(np.allclose(m[1], d) for m in out):
            continue
        if float(mesh.face_normals[t_index] @ d) > 0.999:  # an outward face looking at the bundle
            out.append((np.asarray(q, float), d.copy()))
    return out


def _candidates(pts: np.ndarray, total: float, r: float, structures: list[Obstacle], obstacles: ObstacleSet,
                types: tuple[SupportType, ...], od: float, mass_per_m: float, limit: float,
                exits: list[np.ndarray], posts: dict[str, list[Post]],
                requested: list[np.ndarray] | None = None) -> list[dict[str, Any]]:
    dense, arc = resample_polyline(pts, CANDIDATE_STEP)
    keep = (arc >= END_KEEPOUT) & (arc <= total - END_KEEPOUT)
    dense, arc = dense[keep], arc[keep]
    out: list[dict[str, Any]] = []
    if not len(dense):
        return out
    probes = [(p, float(s), None) for p, s in zip(dense, arc)]
    for number, hint in enumerate(requested or []):
        distance, s = project_to_polyline(pts, hint[None])
        if distance <= MAX_PULL and END_KEEPOUT <= s <= total - END_KEEPOUT:
            probes.append((hint, float(s), number))
    probe_points = np.asarray([p for p, _, _ in probes])
    near_hosts = []
    for host in structures:
        d = host.shape.sdf_bounded(probe_points, FIELD_CUTOFF) - r
        if float(d.min()) <= STRUCTURE_REACH:
            near_hosts.append((host, d))
    for i, (p, s, requested_index) in enumerate(probes):
        k = min(max(int(np.searchsorted(arc, s)), 1), len(dense) - 1)
        tangent = dense[k] - dense[k - 1]
        tangent = tangent / max(float(np.linalg.norm(tangent)), 1e-12)
        near_connector = any(np.linalg.norm(p - e) <= STRAIN_RELIEF_REACH for e in exits)
        load = SAFETY_FACTOR * mass_per_m * limit * PEAK_ACCEL_G * G + (HANDLING_PULL if near_connector else 0.0)
        # Round posts have no flat side faces. Consider each post in reach even
        # when a plate is closer: the plate may lack installation clearance.
        options: list[tuple[Obstacle, np.ndarray, np.ndarray]] = []
        best = None
        for host, d in near_hosts:
            if d[i] > STRUCTURE_REACH or d[i] < -0.0005:
                continue
            closest = exact_closest(host.shape, p[None])[0]
            if closest.distance <= 0:
                continue
            if _post_at(posts, host.component, closest.point) is not None:
                options.append((host, closest.point, (p - closest.point) / closest.distance))
            if best is None or closest.distance < best[1].distance:
                best = (host, closest)
        if best is None:
            continue
        options.append((best[0], best[1].point, (p - best[1].point) / best[1].distance))
        for host, d in near_hosts:
            if d[i] <= STRUCTURE_REACH and d[i] >= -0.0005:
                options += [(host, q, n) for q, n in _face_mounts(host, p, STRUCTURE_REACH + r)]
        found = None
        for host, mount, normal in options:
            if abs(tangent @ normal) > math.sin(CANDIDATE_TILT):
                continue  # the bundle climbs away from the face here, not along it
            t, b = _frame(normal, tangent)
            post = _post_at(posts, host.component, mount)
            on_post = post is not None
            for stype in types:
                if not stype.od_range[0] <= od <= stype.od_range[1] or stype.retention < load:
                    continue
                if (stype.kind == "post_tie") != on_post:
                    continue  # ties loop around posts; mounts and clips need a face
                centre = mount + normal * (stype.height + r)
                path_point = _point_at(pts, s) if requested_index is not None else p
                pull = float(np.linalg.norm(centre - path_point))
                if pull > MAX_PULL:
                    continue
                if requested_index is not None and np.linalg.norm(centre - p) > HINT_MATCH:
                    continue
                if stype.kind == "post_tie":
                    flat, error = True, 0.0
                else:
                    flat, error = _flat(host, mount, normal, t, b, stype.footprint)
                if not flat:
                    continue
                ok, checks = _access(stype, host, mount, normal, t, b, set(), obstacles, r, post)
                if not ok:
                    continue
                clear, blocker = _envelope(centre[None], obstacles, {host.component}, r)
                if not clear:
                    continue
                checks.update(flatness_error=error, strain_relief=near_connector)
                option = {"arc": float(s), "type": stype, "position": centre, "path": path_point.copy(),
                          "tilt": abs(float(tangent @ normal)), "mount": mount.copy(), "normal": normal, "t": t,
                          "host": host.component, "required": load, "pull": pull, "checks": checks,
                          "requested": requested_index}
                # The support type and mount that move the routed bundle least (saddle heights differ).
                score = float(np.linalg.norm(centre - p)) if requested_index is not None else pull
                if found is None or score < found["score"]:
                    option["score"] = score
                    found = option
        if found:
            out.append(found)
    return out


def _greedy(candidates: list[dict[str, Any]], total: float, limit: float, head_held: bool = True,
            tail_held: bool = True) -> list[dict[str, Any]]:
    """Fewest supports keeping every span <= ``limit``. A held segment end counts as a support;
    an unheld one (a junction the span runs through) leaves only half the limit to this segment,
    so the span across the junction stays within the limit whatever the next segment does."""
    chosen: list[dict[str, Any]] = []
    current = 0.0 if head_held else -limit / 2
    end = total + (0.0 if tail_held else limit / 2)
    while end - current > limit:
        reachable = [c for c in candidates if current + MIN_SPACING <= c["arc"] <= current + limit]
        if not reachable:
            later = [c for c in candidates if c["arc"] > current + limit]
            if not later:
                break
            nxt = min(later, key=lambda c: c["arc"])  # leave a gap, report it in the span check
        else:
            # The farthest reach, but at the straightest spot near it: the second pass routes the bundle
            # through the support parallel to its face, which a first-pass curve may not allow.
            far = max(c["arc"] for c in reachable)
            nxt = min((c for c in reachable if c["arc"] >= far - STRAIGHT_WINDOW), key=lambda c: (c["tilt"], -c["arc"]))
        chosen.append(nxt)
        current = nxt["arc"]
    return chosen


def _span_check(routing: Any, supports: list[Support], limits: dict[str, float], diagnostics: list[Diagnostic],
                catalog: Any) -> list[dict[str, Any]]:
    """Spans along every wire between the points that hold it (connector ends, holes,
    supports); report spans longer than the bundle's limit."""
    from .geometry import project_to_polyline

    spans: list[dict[str, Any]] = []
    worst: dict[str, dict[str, Any]] = {}
    hole_centers = [(h.center, h.harnesses) for h in routing.holes]
    for wire in routing.wires.values():
        if wire.failed:
            continue
        total = polyline_length(wire.path)
        nodes = routing.nodes[wire.harness]
        held = []
        for name, s in ((wire.from_node, 0.0), (wire.to_node, total)):
            if nodes[name].kind == "connector":
                held.append((s, "connector"))
            elif nodes[name].kind in ("splice", "floating"):
                held.append((s, nodes[name].kind))  # in-line with the rest of the bundle / mated pair
        for center, harnesses in hole_centers:
            if wire.harness in harnesses:
                d, s = project_to_polyline(wire.path, center[None])
                if d < 0.004:
                    held.append((s, "grommet"))
        for support in supports:
            if wire.wire_id in support.wires:
                d, s = project_to_polyline(wire.path, support.position[None])
                if d < support.bundle_od / 2 + WIRE_CAPTURE_ALLOWANCE:
                    held.append((s, support.id))
        held.sort()
        gauge = catalog.wire(wire.awg)
        limit = limits.get(wire.harness, SPAN_CAP)
        marks = [(0.0, "free end")] + held + [(total, "free end")] if not held else held
        if held and held[0][0] > 1e-6:
            marks = [(0.0, "free end")] + marks
        if held and held[-1][0] < total - 1e-6:
            marks = marks + [(total, "free end")]
        for (s0, a), (s1, b) in zip(marks[:-1], marks[1:]):
            length = s1 - s0
            entry = {"wire": wire.wire_id, "harness": wire.harness, "from": a, "to": b, "start_arc": s0,
                     "end_arc": s1, "length": length, "limit": limit}
            spans.append(entry)
            if length > limit + 1e-6 and not (a == "free end" and b == "free end"):
                key = f"{wire.harness}:{a}->{b}"
                if key not in worst or length > worst[key]["length"]:
                    mid = _point_at(wire.path, (s0 + s1) / 2)
                    worst[key] = {**entry, "midpoint": mid.tolist(), "wires": set()}
                worst[key]["wires"].add(wire.wire_id)
        del gauge
    for item in worst.values():
        diagnostics.append(Diagnostic.error(
            "cable_unsupported_span",
            f"{item['harness']}: {item['length'] * 1e3:.0f} mm between {item['from']} and {item['to']} with no "
            f"support (limit {item['limit'] * 1e3:.0f} mm)",
            subjects=[item["harness"], *sorted(item["wires"])],
            data={k: v for k, v in item.items() if k != "wires"} | {"units": "m"},
            hint="No flat, reachable structure face within the span: add a bracket or stand-off for a tie mount "
                 "near the midpoint, or reroute along the chassis plate / deck."))
    return spans


def _point_at(points: np.ndarray, s: float) -> np.ndarray:
    from .geometry import point_at_arc

    return point_at_arc(points, s)


def support_constraints(report: SupportReport) -> dict[str, list[dict[str, Any]]]:
    """Planner cut points (second pass): the bundle passes each support's centre parallel to its face."""
    out: dict[str, list[dict[str, Any]]] = {}
    for s in report.supports:
        out.setdefault(s.harness, []).append({"id": s.id.split(".")[-1], "position": s.position,
                                              "path_position": s.path_position, "normal": s.normal,
                                              "axis": s.axis, "wires": s.wires})
    return out


__all__ = ["Support", "SupportReport", "SupportType", "check_spans", "load_catalog", "max_unsupported_span",
           "place_supports",
           "support_constraints"]

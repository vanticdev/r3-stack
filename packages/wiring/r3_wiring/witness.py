"""Credible clearance geometry: exact distances, nearest-point witnesses,
independent cross-checks and CAD mesh audits.

Every static clearance finding (``wire_clearance_violation``,
``wire_intersects_component``, ``connector_clearance_violation``) is re-measured
with three independent representations of the part:

1. **router**: the fast field the router and checker use
   (:meth:`r3_wiring.geometry.MeshShape.sdf`: nearest dense surface sample, sign
   from the generalized winding number);
2. **exact**: exact point-to-triangle distance (``trimesh.proximity``) with the
   winding-number sign, or the analytic closest point of a box/cylinder. It
   yields the *witness pair*: the point on the wire (bundle surface) and the
   point on the part that realise the distance;
3. **mujoco**: MuJoCo's own narrow phase, ``mj_geomDistance`` between a sphere
   of the bundle radius at the wire witness and the part's collision geometry
   (CAD meshes split into convex pieces, :func:`r3_wiring.mujoco_cables.convex_pieces`).

When the representations agree (within :data:`AGREEMENT_TOLERANCE` +
:data:`AGREEMENT_FRACTION` of the distance) the finding stands, with the
witnesses in its data. When they disagree, the finding is replaced by
``geometry_uncertain`` (warning) carrying all three values: a hard violation is
never asserted from a single, possibly broken, representation.

Clearance witnesses must be evaluated in the same world frame as the routed
wire and the component mesh. A point sampled in another frame can produce an
arbitrarily large negative distance, so independent representations are compared.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from r3_model.diagnostics import Diagnostic, Severity
from r3_model.project import Project

from .geometry import Box, Cylinder, MeshShape, Shape, SweptShape, load_stl, resample_polyline
from .geometry_audit import BBOX_TOLERANCE, VOLUME_TOLERANCE, MeshAudit, audit_mesh, audit_routing_mesh
from .obstacles import ROUTING_MESH_CELL, Obstacle, ObstacleSet

#: Representations agree when |a - b| <= AGREEMENT_TOLERANCE + AGREEMENT_FRACTION * |b|.
#: 1 mm covers the router's surface sampling (1.5 mm grid: the nearest sample is at most
#: ~0.9 mm from the true closest point) and MuJoCo's hulls of slightly non-convex pieces.
AGREEMENT_TOLERANCE = 0.001
AGREEMENT_FRACTION = 0.05
CHECKED_CODES = ("wire_clearance_violation", "wire_intersects_component", "connector_clearance_violation")


# ------------------------------------------------------------ exact distance
@dataclass
class Closest:
    """Exact signed distance of a query point to a shape and the witness on the shape."""

    distance: float  # signed, negative inside
    point: np.ndarray  # closest point on the shape surface
    pose: int = 0  # index into a swept shape's poses


def _box_closest(box: Box, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    local = (points - box.center) @ box.rotation
    clamped = np.clip(local, -box.half, box.half)
    inside = np.all(np.abs(local) <= box.half, axis=1)
    if inside.any():
        # Inside: the closest surface point is on the nearest face.
        gap = box.half - np.abs(local[inside])
        axis = np.argmin(gap, axis=1)
        snapped = local[inside].copy()
        rows = np.arange(len(snapped))
        snapped[rows, axis] = np.sign(snapped[rows, axis] + 1e-300) * box.half[axis]
        clamped[inside] = snapped
    world = box.center + clamped @ box.rotation.T
    distance = np.linalg.norm(points - world, axis=1)
    return np.where(inside, -distance, distance), world


def _cylinder_closest(cyl: Cylinder, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    local = (points - cyl.center) @ cyl.rotation
    radial = np.hypot(local[:, 0], local[:, 1])
    unit = np.where(radial[:, None] > 1e-12, local[:, :2] / np.maximum(radial, 1e-12)[:, None], [1.0, 0.0])
    r_in, z_in = radial <= cyl.radius, np.abs(local[:, 2]) <= cyl.half_length
    inside = r_in & z_in
    closest = np.empty_like(local)
    # Outside: clamp radially and axially.
    rc = np.minimum(radial, cyl.radius)
    closest[:, :2] = unit * rc[:, None]
    closest[:, 2] = np.clip(local[:, 2], -cyl.half_length, cyl.half_length)
    if inside.any():
        to_side = cyl.radius - radial[inside]
        to_cap = cyl.half_length - np.abs(local[inside, 2])
        side = to_side <= to_cap
        snap = closest[inside].copy()
        snap[side, :2] = unit[inside][side] * cyl.radius
        snap[side, 2] = local[inside][side, 2]
        snap[~side, :2] = local[inside][~side, :2]
        snap[~side, 2] = np.sign(local[inside][~side, 2] + 1e-300) * cyl.half_length
        closest[inside] = snap
    world = cyl.center + closest @ cyl.rotation.T
    distance = np.linalg.norm(points - world, axis=1)
    return np.where(inside, -distance, distance), world


def closest_on_triangles(p: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Closest point on each triangle (a, b, c) to ``p`` (Ericson, *Real-Time Collision
    Detection*, 5.1.5: Voronoi regions of the vertices, edges and face). Vectorized over
    triangles; ``p`` is one point."""
    ab, ac, ap = b - a, c - a, p - a
    d1, d2 = np.einsum("ij,ij->i", ab, ap), np.einsum("ij,ij->i", ac, ap)
    bp = p - b
    d3, d4 = np.einsum("ij,ij->i", ab, bp), np.einsum("ij,ij->i", ac, bp)
    cp = p - c
    d5, d6 = np.einsum("ij,ij->i", ab, cp), np.einsum("ij,ij->i", ac, cp)
    va, vb, vc = d3 * d6 - d5 * d4, d5 * d2 - d1 * d6, d1 * d4 - d3 * d2
    denom = va + vb + vc
    denom = np.where(np.abs(denom) < 1e-300, 1e-300, denom)
    v, w = vb / denom, vc / denom
    out = a + ab * v[:, None] + ac * w[:, None]  # face region
    with np.errstate(divide="ignore", invalid="ignore"):
        # Edge regions (later assignments take precedence in Ericson's early-exit order,
        # so they are applied in reverse: BC, CA, AB, then vertices C, B, A).
        m = (va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0)
        t = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        out = np.where(m[:, None], b + (c - b) * t[:, None], out)
        m = (vb <= 0) & (d2 >= 0) & (d6 <= 0)
        t = d2 / (d2 - d6)
        out = np.where(m[:, None], a + ac * t[:, None], out)
        m = (vc <= 0) & (d1 >= 0) & (d3 <= 0)
        t = d1 / (d1 - d3)
        out = np.where(m[:, None], a + ab * t[:, None], out)
    out = np.where(((d6 >= 0) & (d5 <= d6))[:, None], c, out)
    out = np.where(((d3 >= 0) & (d4 <= d3))[:, None], b, out)
    out = np.where(((d1 <= 0) & (d2 <= 0))[:, None], a, out)
    return out


def _mesh_closest(mesh: MeshShape, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Exact unsigned distance to the triangles (the nearest surface sample bounds the
    search: only triangles whose bounding sphere is within that distance are tested),
    winding-number sign."""
    tri = mesh.vertices[mesh.faces]
    centers = tri.mean(axis=1)
    radii = np.linalg.norm(tri - centers[:, None, :], axis=2).max(axis=1)
    upper, _ = mesh._tree.query(points)
    distance = np.empty(len(points))
    closest = np.empty((len(points), 3))
    for i, p in enumerate(points):
        near = np.nonzero(np.linalg.norm(centers - p, axis=1) - radii <= upper[i] + 1e-12)[0]
        q = closest_on_triangles(p, tri[near, 0], tri[near, 1], tri[near, 2])
        d = np.linalg.norm(q - p, axis=1)
        k = int(np.argmin(d))
        distance[i], closest[i] = d[k], q[k]
    lo, hi = mesh.aabb()
    candidate = np.all((points >= lo) & (points <= hi), axis=1)
    sign = np.ones(len(points))
    if candidate.any():
        sign[candidate] = np.where(mesh.winding_number(points[candidate]) > 0.5, -1.0, 1.0)
    return sign * distance, closest


def exact_closest(shape: Shape, points: np.ndarray) -> list[Closest]:
    """Exact signed distance and surface witness of every point (swept shapes: the closest pose)."""
    points = np.atleast_2d(np.asarray(points, dtype=float))
    if isinstance(shape, SweptShape):
        best = [Closest(math.inf, np.zeros(3), -1) for _ in points]
        lo, hi = shape.aabb()
        for k, pose in enumerate(shape.shapes):
            plo, phi = pose.aabb()
            # Cheap lower bound from the pose box: skip poses that cannot beat the current best.
            gap = np.linalg.norm(np.maximum(np.maximum(plo - points, points - phi), 0.0), axis=1)
            todo = np.nonzero(gap < np.array([b.distance for b in best]))[0]
            if not len(todo):
                continue
            for i, c in zip(todo, exact_closest(pose, points[todo])):
                if c.distance < best[i].distance:
                    best[i] = Closest(c.distance, c.point, k)
        return best
    if isinstance(shape, Box):
        d, w = _box_closest(shape, points)
    elif isinstance(shape, Cylinder):
        d, w = _cylinder_closest(shape, points)
    elif isinstance(shape, MeshShape):
        d, w = _mesh_closest(shape, points)
    else:  # pragma: no cover - unknown shape type
        d = shape.sdf(points)
        w = points - shape.gradient(points) * d[:, None]
    return [Closest(float(a), np.asarray(b, dtype=float)) for a, b in zip(d, w)]


def legacy_nearest_normal_sdf(shape: Shape, points: np.ndarray) -> np.ndarray:
    """The pre-85f2fb6 mesh SDF: sign from the outward normal of the nearest surface
    sample. Kept only to reproduce the review packet's false penetrations; never use
    it for checks (its sign is arbitrary at edges and corners)."""
    points = np.atleast_2d(points)
    if isinstance(shape, SweptShape):
        return np.min([legacy_nearest_normal_sdf(s, points) for s in shape.shapes], axis=0)
    if isinstance(shape, MeshShape):
        distance, index = shape._tree.query(points)
        sign = np.sign(np.einsum("ij,ij->i", points - shape._samples[index], shape._normals[index]))
        sign[sign == 0] = 1.0
        return distance * sign
    return shape.sdf(points)


# ------------------------------------------------------------ MuJoCo distance
def _pose_shape(shape: Shape, pose: int) -> Shape:
    return shape.shapes[pose] if isinstance(shape, SweptShape) and 0 <= pose < len(shape.shapes) else shape


def mujoco_distance(shape: Shape, center: np.ndarray, radius: float, distmax: float = 0.3) -> tuple[float, np.ndarray]:
    """Signed distance from MuJoCo's narrow phase between a sphere (``center``, ``radius``)
    and the shape (CAD meshes as their exact convex pieces), and MuJoCo's witness on the
    part. Points deeper inside a piece report that piece's penetration depth."""
    import xml.etree.ElementTree as ET

    import mujoco

    from .mujoco_cables import _fmt, _quat, convex_pieces

    mj = ET.Element("mujoco", model="witness")
    asset = ET.SubElement(mj, "asset")
    world = ET.SubElement(mj, "worldbody")
    names: list[str] = []
    if isinstance(shape, MeshShape):
        pieces, _ = convex_pieces(shape.vertices, shape.faces)
        for k, piece in enumerate(pieces):
            ET.SubElement(asset, "mesh", name=f"p{k}", vertex=_fmt(piece))
            ET.SubElement(world, "geom", name=f"g{k}", type="mesh", mesh=f"p{k}", contype="0", conaffinity="0")
            names.append(f"g{k}")
    elif isinstance(shape, Box):
        ET.SubElement(world, "geom", name="g0", type="box", pos=_fmt(shape.center), quat=_fmt(_quat(shape.rotation)),
                      size=_fmt(shape.half), contype="0", conaffinity="0")
        names.append("g0")
    elif isinstance(shape, Cylinder):
        ET.SubElement(world, "geom", name="g0", type="cylinder", pos=_fmt(shape.center),
                      quat=_fmt(_quat(shape.rotation)), size=_fmt([shape.radius, shape.half_length]),
                      contype="0", conaffinity="0")
        names.append("g0")
    else:
        return math.nan, np.full(3, math.nan)
    ET.SubElement(world, "geom", name="probe", type="sphere", pos=_fmt(center), size=f"{max(radius, 1e-5):.9g}",
                  contype="0", conaffinity="0")
    model = mujoco.MjModel.from_xml_string(ET.tostring(mj, encoding="unicode"))
    data = mujoco.MjData(model)
    mujoco.mj_kinematics(model, data)
    probe = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "probe")
    best, witness = math.inf, np.full(3, math.nan)
    fromto = np.zeros(6)
    for name in names:
        geom = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, name)
        d = mujoco.mj_geomDistance(model, data, geom, probe, distmax, fromto)
        if d < best:
            best, witness = float(d), fromto[:3].copy()
    return best, witness


# ------------------------------------------------------------ verification
@dataclass
class Measurement:
    """Three representations of one clearance, and its witness pair."""

    component: str
    router: float  # router/checker value (bundle surface to part, signed)
    exact: float
    mujoco: float
    wire_point: np.ndarray  # witness on the bundle surface
    part_point: np.ndarray  # witness on the part surface
    centerline_point: np.ndarray
    radius: float
    pose: int = 0

    @property
    def agree(self) -> bool:
        def close(a: float, b: float) -> bool:
            return math.isfinite(a) and math.isfinite(b) and abs(a - b) <= AGREEMENT_TOLERANCE + AGREEMENT_FRACTION * abs(b)

        mujoco_ok = (not math.isfinite(self.mujoco)) or close(self.mujoco, self.exact) or \
            (self.exact < 0 and self.mujoco < 0)  # MuJoCo reports per-piece depth inside a union
        return close(self.router, self.exact) and mujoco_ok

    def as_data(self) -> dict[str, Any]:
        return {"distance_router": self.router, "distance_exact": self.exact,
                "distance_mujoco": self.mujoco if math.isfinite(self.mujoco) else None,
                "witness_wire": self.wire_point.tolist(), "witness_part": self.part_point.tolist(),
                "witness_centerline": self.centerline_point.tolist(), "bundle_radius": self.radius,
                "witness_pose": self.pose, "representations_agree": self.agree}


def measure(obstacle: Obstacle, centerline: np.ndarray, radius: float | np.ndarray, swept: bool,
            router_value: float | None = None, use_mujoco: bool = True) -> Measurement:
    """Exact minimum over ``centerline`` samples (bundle surface = centerline - radius),
    the witness pair and the MuJoCo cross-check at the witness."""
    shape = obstacle.swept if swept else obstacle.shape
    centerline = np.atleast_2d(centerline)
    radii = np.broadcast_to(np.asarray(radius, dtype=float), (len(centerline),))
    # Pre-filter with the router field, then measure the nearest candidates exactly.
    approx = shape.sdf_bounded(centerline, 0.2) - radii
    order = np.argsort(approx)[: min(len(centerline), 24)]
    found = exact_closest(shape, centerline[order])
    values = np.array([c.distance for c in found]) - radii[order]
    k = int(np.argmin(values))
    best = found[k]
    point, r = centerline[order[k]], float(radii[order[k]])
    direction = point - best.point
    norm = float(np.linalg.norm(direction))
    unit = direction / norm if norm > 1e-12 else np.array([0.0, 0.0, 1.0])
    unit = unit if best.distance >= 0 else -unit  # inside: the surface lies beyond the centre
    wire_point = point - unit * r
    router = float(approx[order[k]]) if router_value is None else float(router_value)
    mj = math.nan
    if use_mujoco:
        d, _ = mujoco_distance(_pose_shape(shape, best.pose), point, r)
        mj = d
    return Measurement(obstacle.component, router, float(values[k]), mj, wire_point, best.point, point, r, best.pose)


def _wire_samples(wire: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    points, arc = resample_polyline(wire.path, 0.001)
    vertex_arc = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(wire.path, axis=0), axis=1))])
    return points, arc, np.interp(arc, vertex_arc, wire.bundle_radius)


def verify_clearances(diagnostics: list[Diagnostic], obstacles: ObstacleSet, routing: Any, catalog: Any = None,
                      use_mujoco: bool = True) -> tuple[list[Diagnostic], list[dict[str, Any]]]:
    """Add witnesses to every static clearance finding and downgrade findings whose
    representations disagree to ``geometry_uncertain``. Returns the new diagnostic list
    and a flat witness table (for rendering)."""
    out: list[Diagnostic] = []
    witnesses: list[dict[str, Any]] = []
    for diagnostic in diagnostics:
        if diagnostic.code not in CHECKED_CODES:
            out.append(diagnostic)
            continue
        component = diagnostic.data.get("component", "")
        obstacle = obstacles.by_component(component)
        if obstacle is None:
            out.append(diagnostic)
            continue
        connector = obstacles.by_component(diagnostic.subjects[1]) if len(diagnostic.subjects) > 1 and \
            diagnostic.code == "connector_clearance_violation" else None
        measurement = _measure_diagnostic(diagnostic, obstacle, routing, catalog, use_mujoco,
                                          connector_body=connector.shape if connector else None)
        if measurement is None:
            out.append(diagnostic)
            continue
        data = {**diagnostic.data, **measurement.as_data()}
        witnesses.append({"code": diagnostic.code, "subjects": diagnostic.subjects, "component": component,
                          **measurement.as_data()})
        if measurement.agree:
            out.append(diagnostic.model_copy(update={"data": data}))
            continue
        witnesses[-1]["code"] = "geometry_uncertain"
        out.append(Diagnostic.warning(
            "geometry_uncertain",
            f"{diagnostic.code} on {', '.join(diagnostic.subjects[:2])} vs {component} is not confirmed: router "
            f"{measurement.router * 1e3:.1f} mm, exact {measurement.exact * 1e3:.1f} mm, MuJoCo "
            f"{measurement.mujoco * 1e3:.1f} mm",
            subjects=diagnostic.subjects, stage=diagnostic.stage,
            data={**data, "replaces": diagnostic.code, "replaced_severity": diagnostic.severity.value},
            hint="The geometric representations disagree; audit the part mesh (cad_mesh_* diagnostics) "
                 "before acting on this finding."))
    return out, witnesses


def _measure_diagnostic(diagnostic: Diagnostic, obstacle: Obstacle, routing: Any, catalog: Any,
                        use_mujoco: bool, connector_body: Shape | None = None) -> Measurement | None:
    data = diagnostic.data
    if diagnostic.code == "connector_clearance_violation":
        # Use the same posed model as the checker, including each individual
        # cable-side half; the old pair box cannot verify detailed housings.
        body = connector_body if connector_body is not None else _connector_body(diagnostic, routing, catalog)
        if body is None:
            return None
        return measure(obstacle, body.surface_points(0.001), 0.0, swept=True, router_value=data.get("min_distance"),
                       use_mujoco=use_mujoco)
    per_wire = data.get("per_wire_penetration") or {}
    wire_id = data.get("worst_wire") or (max(per_wire, key=per_wire.get) if per_wire else None) or \
        next((s for s in diagnostic.subjects if s in routing.wires), None)
    if wire_id not in routing.wires:
        return None
    wire = routing.wires[wire_id]
    points, arc, radius = _wire_samples(wire)
    swept = diagnostic.code == "wire_clearance_violation"
    keep = ~_exempt(routing, wire, arc, points, obstacle)
    s0 = data.get("arc_length")
    if s0 is not None:
        keep &= np.abs(arc - float(s0)) <= 0.015  # the checker's worst point and its neighbourhood
    if not keep.any():
        return None
    points, radius = points[keep], radius[keep]
    router = data.get("min_distance") if swept else -float(data.get("penetration", 0.0))
    return measure(obstacle, points, radius, swept=swept, router_value=router, use_mujoco=use_mujoco)


def _exempt(routing: Any, wire: Any, arc: np.ndarray, points: np.ndarray, obstacle: Obstacle) -> np.ndarray:
    """The checker's exemptions: termination zones on the connector-owning part only,
    and this harness's drilled pass-throughs. Mount ancestors remain obstacles."""
    nodes = routing.nodes[wire.harness]
    start, end = nodes[wire.from_node], nodes[wire.to_node]
    mask = np.zeros(len(arc), dtype=bool)
    if obstacle.component in start.exempt:
        mask |= arc <= start.termination_length
    if obstacle.component in end.exempt:
        mask |= arc[-1] - arc <= end.termination_length
    if obstacle.plate_axis is not None:
        for hole in routing.holes:
            if hole.plate == obstacle.component and wire.harness in hole.harnesses:
                in_plane = [i for i in range(3) if i != hole.axis]
                mask |= np.linalg.norm((points - hole.center)[:, in_plane], axis=1) <= max(hole.diameter / 2, 0.004) + 0.002
    return mask


def _connector_body(diagnostic: Diagnostic, routing: Any, catalog: Any) -> Box | None:
    """The connector body box the checker measured: a single cable-side half
    (``face + direction * length / 2``) or a floating mated pair."""
    label = diagnostic.subjects[1] if len(diagnostic.subjects) > 1 else ""
    connector_type = diagnostic.data.get("connector_type")
    if catalog is None or not connector_type or connector_type == "integral":
        return None
    size = catalog.connector(connector_type).body_size
    pair = next((f for f in routing.floating if label == f"{f.a}|{f.b}"), None)
    if pair is not None:
        center, z, length = np.asarray(pair.center, dtype=float), np.asarray(pair.axis, dtype=float), pair.length
    else:
        node = next((n for nodes in routing.nodes.values() for n in nodes.values() if n.ref == label), None)
        if node is None or node.direction is None or node.face is None:
            return None
        from .terminations import body_length

        length = body_length(catalog, connector_type)
        z = node.direction / np.linalg.norm(node.direction)
        center = node.face + z * length / 2
    z = z / np.linalg.norm(z)
    helper = np.array([0.0, 0.0, 1.0]) if abs(z[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    x = np.cross(helper, z)
    x /= np.linalg.norm(x)
    return Box(center, np.column_stack([x, np.cross(z, x), z]), np.array([size[0] / 2, size[1] / 2, length / 2]))


# ------------------------------------------------------------ CAD audit
def audit_cad(project: Project, obstacles: ObstacleSet) -> tuple[list[Diagnostic], dict[str, Any]]:
    """Audit the CAD meshes the obstacles were built from, and CAD staleness."""
    from r3_model.pulled_view import cad_view
    from r3_onshape.step_geometry import load_geometry
    cad = cad_view(project)
    exact = load_geometry(project.root)
    for name, geometry in exact.parts.items():
        cad["parts"][name].update(volume=geometry.manifold().volume(), bbox_min=geometry.lo.tolist(), bbox_max=geometry.hi.tolist())
    parts = cad.get("parts", {}) if isinstance(cad, dict) else {}
    diagnostics: list[Diagnostic] = []
    table: dict[str, Any] = {}
    for obstacle in obstacles.obstacles:
        if obstacle.source != "pulled_stl" or not isinstance(obstacle.shape, MeshShape):
            continue
        part = parts.get(obstacle.component, {})
        entry: dict[str, Any] = {}
        # 1. The CAD export itself (what Onshape tessellated), not the clustered routing copy.
        export = project.root / "build" / "cad" / part["mesh"] if part.get("mesh") else None
        if export is not None and export.exists():
            audit = audit_mesh(obstacle.component, *load_stl(export), part)
            entry = {"watertight": audit.watertight, "winding_consistent": audit.winding_consistent,
                     "volume": audit.volume, "cad_volume": audit.cad_volume, "volume_error": audit.volume_error,
                     "allowed_deficit": audit.allowed_deficit, "allowed_excess": audit.allowed_excess,
                     "surface_area": audit.surface_area, "chord_tolerance": audit.chord_tolerance,
                     "bbox_error": audit.bbox_error, "scale_ratio": audit.scale_ratio, **audit.repair,
                     "problems": list(audit.problems)}
            if audit.problems:
                diagnostics.append(Diagnostic.error(
                    "cad_mesh_invalid", f"{obstacle.component} mesh: {'; '.join(audit.problems)}",
                    subjects=[obstacle.component],
                    data={"representation": "cad_export", "watertight": audit.watertight,
                          "winding_consistent": audit.winding_consistent, "volume": audit.volume,
                          "cad_volume": audit.cad_volume, "volume_error": audit.volume_error,
                          "allowed_deficit": audit.allowed_deficit, "allowed_excess": audit.allowed_excess,
                          "surface_area": audit.surface_area, "chord_tolerance": audit.chord_tolerance,
                          "bbox_error": audit.bbox_error, "scale_ratio": audit.scale_ratio,
                          "welded_vertices": audit.repair.get("welded_vertices"),
                          "seam_loops_filled": audit.repair.get("seam_loops_filled"),
                          "open_loops": audit.repair.get("open_loops"), "units": "m"},
                    hint="Re-export the part from the CAD stage; clearance findings on this part are unreliable."))
        # 2. The clustered routing mesh whose winding number signs every clearance.
        routing = audit_routing_mesh(obstacle.component, obstacle.shape.vertices, obstacle.shape.faces, part,
                                     ROUTING_MESH_CELL, entry.get("volume") if entry.get("watertight") else None)
        entry["routing_mesh"] = {"cell": routing.cell, "unbalanced_edges": routing.unbalanced_edges,
                                 "bbox_error": routing.bbox_error, "bbox_limit": routing.bbox_limit,
                                 "volume_ratio": routing.volume_ratio, "problems": list(routing.problems)}
        entry["problems"] = entry.get("problems", []) + routing.problems
        table[obstacle.component] = entry
        if routing.problems:
            diagnostics.append(Diagnostic.error(
                "cad_mesh_invalid", f"{obstacle.component} mesh: {'; '.join(routing.problems)}",
                subjects=[obstacle.component],
                data={"representation": "routing_mesh", "cell": routing.cell,
                      "unbalanced_edges": routing.unbalanced_edges, "bbox_error": routing.bbox_error,
                      "bbox_limit": routing.bbox_limit, "volume_ratio": routing.volume_ratio, "units": "m"},
                hint="The CAD export is checked separately; the vertex-clustered routing copy "
                     "(geometry.cluster_decimate) must stay a closed oriented surface."))
    summary = {"meshes": len(table), "invalid": sorted(c for c, v in table.items() if v["problems"]),
               "source": cad["source"], "stale": False, "parts": table}

    return diagnostics, summary


def severity_counts(diagnostics: list[Diagnostic]) -> dict[str, int]:
    return {s.value: sum(1 for d in diagnostics if d.severity is s) for s in Severity}


__all__ = ["AGREEMENT_TOLERANCE", "BBOX_TOLERANCE", "Closest", "Measurement", "MeshAudit", "audit_cad", "audit_mesh", "exact_closest",
           "legacy_nearest_normal_sdf", "measure", "mujoco_distance", "verify_clearances", "VOLUME_TOLERANCE"]

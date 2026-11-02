"""Routing obstacles read exclusively from the verified native pulled STL copy."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from r3_model.diagnostics import Diagnostic
from r3_model.project import Project

from .bodies import ROOT, Kinematics, Transform
from .geometry import Box, Cylinder, MeshShape, Shape, SweptShape, axis_matrix, cluster_decimate, load_stl

ROUTING_MESH_CELL = 1e-3  # m: vertex-clustering cell for routing obstacles (3 mm voxels; see _cad_shape)

# A structure plate thinner than this may get drilled pass-through holes.
PLATE_MAX_THICKNESS = 0.005
SWEEP_STEP = math.radians(1.0)


@dataclass
class Obstacle:
    component: str
    kind: str
    shape: Shape  # nominal pose, robot frame
    swept: Shape  # envelope over joint travel (== shape for static parts)
    keepout: float
    moving: bool
    body: str
    structure: bool  # static structure: tie anchor and preferred running surface
    plate_axis: int | None = None  # robot-frame normal axis of a drillable plate
    source: str = "catalog"
    color: str = "#888888"

    def current(self, transform: Transform) -> Shape:
        """Shape after the body moves by ``transform`` (relative to nominal)."""
        return self.shape.transformed(*transform)


@dataclass
class ObstacleSet:
    obstacles: list[Obstacle]
    kinematics: Kinematics
    estimated: list[str] = field(default_factory=list)
    cad_source: dict[str, Any] = field(default_factory=dict)
    cable_bodies: list[CableBody] = field(default_factory=list)

    def by_component(self, component: str) -> Obstacle | None:
        for obstacle in [*self.obstacles, *self.cable_bodies]:
            if obstacle.component == component:
                return obstacle
        return None

    @property
    def static(self) -> list[Obstacle]:
        return [o for o in self.obstacles if not o.moving]

    @property
    def moving(self) -> list[Obstacle]:
        return [o for o in self.obstacles if o.moving]

    @property
    def structures(self) -> list[Obstacle]:
        return [o for o in self.obstacles if o.structure]

    def floor(self) -> Obstacle | None:
        """The base plate: see :func:`base_plate`."""
        return base_plate(self.structures)

    def min_distance(self, points: np.ndarray, obstacles: list[Obstacle], swept: bool = False,
                     keepout: bool = False, exclude: set[str] | None = None,
                     cutoff: float = 0.2) -> tuple[np.ndarray, list[str]]:
        """Per-point minimum signed distance (and which component) over ``obstacles``;
        distances beyond ``cutoff`` are reported as ``cutoff``."""
        points = np.atleast_2d(points)
        best = np.full(len(points), np.inf)
        which = [""] * len(points)
        for obstacle in obstacles:
            if exclude and obstacle.component in exclude:
                continue
            shape = obstacle.swept if swept else obstacle.shape
            margin = obstacle.keepout if keepout else 0.0
            # Outside an enclosing AABB, no surface can be closer than the
            # box. Skip only queries that cannot improve the current minimum;
            # inside points still need the real signed-distance calculation.
            lo, hi = shape.aabb()
            outside = np.maximum(np.maximum(lo - points, points - hi), 0.0)
            bound = np.minimum(np.linalg.norm(outside, axis=1), cutoff) - margin
            inside = np.all((points >= lo) & (points <= hi), axis=1)
            # Conservative roundoff allowance affects pruning only: candidates
            # near a tie are evaluated with the original, unchanged SDF.
            rounding = 32 * np.finfo(float).eps * (1 + np.max(np.abs(points), axis=1)
                                                   + np.max(np.abs(lo)) + np.max(np.abs(hi)))
            candidates = np.flatnonzero(inside | (bound <= best + rounding))
            if not len(candidates):
                continue
            value = shape.sdf_bounded(points[candidates], cutoff) - margin
            better = value < best[candidates]
            if better.any():
                indices = candidates[better]
                best[indices] = value[better]
                for k in indices:
                    which[k] = obstacle.component
        return best, which


@dataclass
class CableBody(Obstacle):
    """A routed connector half or splice sleeve; ids match viewer connector ids.

    Component bodies (including fuse holders) stay in ``obstacles``. Keeping
    routed hardware separately avoids treating it as a support host or CAD part.
    """

    node: Any = None
    owner: str | None = None
    mates: str | None = None
    entry_points: np.ndarray | None = None
    entry_direction: np.ndarray | None = None

    def entry_mask(self, points: np.ndarray, radius: float | np.ndarray = 0.0) -> np.ndarray:
        """Local conductor-entry aperture, never an exemption for the whole body.

        Only the last 1 mm of the housing at the wire exit is an entry zone.
        Its transverse extent includes the contact row and the incident bundle.
        Splice sleeves are estimated 6 mm long and admit their incident ends.
        """
        node = self.node
        origin = self.entry_points.mean(axis=0) if self.entry_points is not None else node.exit
        rel = np.atleast_2d(points) - origin
        if node.kind == "splice":
            return np.linalg.norm(rel, axis=1) <= 0.003 + node.od / 2 + radius
        direction = self.entry_direction if self.entry_direction is not None else node.direction
        axial = rel @ direction
        transverse = np.linalg.norm(rel - axial[:, None] * direction, axis=1)
        spread = float(np.linalg.norm(self.entry_points - origin, axis=1).max()) \
            if self.entry_points is not None else 0.0
        aperture = spread + max(node.od / 2, 0.0005)
        return (axial >= -0.001 - radius) & (axial <= node.termination_length) & (transverse <= aperture + radius)


def cable_obstacles(project: Project, nodes: dict[str, dict[str, Any]]) -> list[CableBody]:
    """All cable-side halves and splice sleeves at their current routed poses.

    Integral pigtails have no separate housing: their fuse/ESC/etc. owner is
    already a component obstacle, and their conductors enter wire occupancy.
    """
    from r3_connectors.frames import Frame, any_perpendicular

    from r3_model.pulled import PullError
    from r3_model.spec_files import validate_pulled_spec as load_pulled
    from r3_model.pulled_view import world_mesh
    pulled = load_pulled(project.root)
    out = []
    for harness in sorted(nodes):
        for name, node in sorted(nodes[harness].items()):
            if node.exit is None:
                continue
            entries, entry_direction = None, None
            if node.kind == "splice":
                rotation = (Frame.from_axes(node.exit, any_perpendicular(node.direction), node.direction).rotation
                            if node.direction is not None else np.eye(3))
                source = "pulled_stl"
            elif node.connector_type and node.connector_type != "integral":
                frame = node.frame
                if frame is None:
                    from .terminations import body_length

                    direction = node.direction if node.direction is not None else np.array([0.0, 0.0, 1.0])
                    origin = node.face if node.face is not None else node.exit - direction * body_length(
                        project.catalog, node.connector_type)
                    frame = Frame.from_axes(origin,
                                            any_perpendicular(direction), -direction)
                source = "pulled_stl"
                geometry = project.catalog.connector(node.connector_type).geometry
                if geometry is not None:
                    exits = [pin.wire_entry for pin in geometry.pins.values() if pin.wire_entry is not None]
                    entries = frame.points(np.asarray(exits)) if exits else None
                entry_direction = -frame.z
            else:
                continue
            if node.ref not in pulled.parts:
                raise PullError("cad_pull_missing", f"{node.ref}: connector/splice hardware is absent from pulled CAD")
            mesh = world_mesh(pulled, node.ref)
            shape = MeshShape(mesh.vertices, mesh.faces)
            source = "pulled_stl"
            moving = project.spec.components.get(node.component) is not None and bool(
                project.catalog.component(project.spec.components[node.component].type).mechanical.moving)
            out.append(CableBody(node.ref, "splice" if node.kind == "splice" else "connector", shape, shape,
                                 0.0005, bool(moving), node.body, False, source=source, node=node,
                                 owner=node.component, mates=node.mates, entry_points=entries,
                                 entry_direction=entry_direction))
    return out


def _cad_shape(project: Project, part: dict[str, Any]) -> tuple[Shape, str]:
    """Compatibility entry point: resolve only a pinned pulled part by identity."""
    from r3_model.pulled import PullError
    from r3_model.spec_files import validate_pulled_spec as load_pulled
    from r3_model.pulled_view import world_mesh
    pulled = load_pulled(project.root)
    name = part.get("part_name") or part.get("name")
    if name not in pulled.parts:
        raise PullError("cad_pull_missing", "Mesh request does not identify a pulled part")
    mesh = world_mesh(pulled, name)
    vertices, faces = cluster_decimate(mesh.vertices, mesh.faces, ROUTING_MESH_CELL)
    if not len(faces):
        vertices, faces = mesh.vertices, mesh.faces
    return MeshShape(vertices, faces), "pulled_stl"


def _spin_invariant(shape: Shape, axis: np.ndarray, origin: np.ndarray) -> bool:
    """True when rotating about (origin, axis) maps the shape onto itself."""
    if not isinstance(shape, Cylinder):
        return False
    parallel = np.linalg.norm(np.cross(shape.axis, axis)) < 1e-6
    offset = shape.center - origin
    on_axis = np.linalg.norm(offset - axis * (offset @ axis)) < 1e-6
    return bool(parallel and on_axis)


def _revolution_envelope(shape: MeshShape, axis: np.ndarray, origin: np.ndarray) -> Cylinder:
    """The exact volume a body sweeps spinning about (origin, axis) is its solid
    of revolution; bound it by the cylinder of the mesh's maximum radius over its
    axial span (conservative for filleted tires and hubs)."""
    axis = np.asarray(axis, dtype=float) / np.linalg.norm(axis)
    rel = shape.vertices - np.asarray(origin, dtype=float)
    along = rel @ axis
    radial = np.linalg.norm(rel - np.outer(along, axis), axis=1)
    center = np.asarray(origin, dtype=float) + axis * (along.max() + along.min()) / 2
    helper = np.array([1.0, 0, 0]) if abs(axis[0]) < 0.9 else np.array([0, 1.0, 0])
    x = np.cross(helper, axis)
    x /= np.linalg.norm(x)
    rotation = np.column_stack([x, np.cross(axis, x), axis])
    return Cylinder(center, rotation, float(radial.max()), float((along.max() - along.min()) / 2))


def _swept(kin: Kinematics, shape: Shape, body: str, label: str) -> Shape:
    for joint in kin.chain(body):
        # Spinning meshes (wheels, axles) sweep their solid of revolution: replace the
        # mesh by that envelope instead of sampling hundreds of spin angles.
        if joint.type == "continuous" and isinstance(shape, MeshShape) and joint.child == body:
            shape = _revolution_envelope(shape, joint.axis, joint.origin)
    skip = {j.id for j in kin.chain(body) if j.type == "continuous" and _spin_invariant(shape, j.axis, j.origin)}
    configs = kin.configurations(body, step_angle=SWEEP_STEP, skip=skip)
    if len(configs) == 1 and not configs[0]:
        return shape
    return SweptShape([shape.transformed(*kin.body_transform(body, q)) for q in configs], label)


def build_obstacles(project: Project) -> tuple[ObstacleSet, list[Diagnostic]]:
    from r3_model.spec_files import validate_pulled_spec as load_pulled
    from r3_model.pulled_view import apply_pulled, world_mesh

    pulled = load_pulled(project.root)
    assembly = apply_pulled(project, pulled)
    spec, catalog = project.spec, project.catalog
    kin = Kinematics(spec)
    obstacles: list[Obstacle] = []
    diagnostics = list(assembly.diagnostics)
    cable_parts = {f"{h.id}.{name}" for h in spec.electrical.harnesses
                   for name in [*h.connectors, *h.splices]}
    for cid, part in pulled.parts.items():
        # Cable hardware is added once by cable_obstacles, whose entry masks
        # distinguish its own wires from foreign harnesses.
        if cid in cable_parts:
            continue
        comp = spec.components.get(cid)
        ctype = catalog.components.get(comp.type) if comp else None
        mech = ctype.mechanical if ctype else None
        mesh = world_mesh(pulled, cid)
        vertices, faces = np.asarray(mesh.vertices), np.asarray(mesh.faces)
        coarse_vertices, coarse_faces = cluster_decimate(vertices, faces, ROUTING_MESH_CELL)
        if len(coarse_faces):
            vertices, faces = coarse_vertices, coarse_faces
        shape = MeshShape(vertices, faces)
        body = kin.body_of(cid)
        moving = kin.is_moving(body)
        swept = _swept(kin, shape, body, cid) if moving else shape
        kind = ctype.kind if ctype else "part"
        obstacles.append(Obstacle(cid, kind, shape, swept, float(mech.keepout_margin) if mech else 0.0,
            moving, body, kind == "structure" and not moving, source="pulled_stl",
            color=mech.color if mech else "#888888"))

    floor = base_plate([o for o in obstacles if o.structure])
    for obstacle in obstacles:
        if obstacle.structure and obstacle is not floor and isinstance(obstacle.shape, Box):
            rotation = obstacle.shape.rotation
            aligned = np.allclose(np.abs(rotation), np.round(np.abs(rotation)), atol=1e-6)
            thin = int(np.argmin(obstacle.shape.half))
            if aligned and 2 * obstacle.shape.half[thin] <= PLATE_MAX_THICKNESS:
                obstacle.plate_axis = int(np.argmax(np.abs(rotation[:, thin])))

    return ObstacleSet(obstacles, kin, [], {"document_id": pulled.document_id,
                                         "version_id": pulled.version_id}), diagnostics


def base_plate(structures: list[Obstacle]) -> Obstacle | None:
    """The base plate: the static structure with the largest horizontal footprint (lowest on a tie).
    Not simply the lowest part: a strap or tie looping under the plate reaches below it, and taking
    it as the floor would put the routing volume's floor at the top of the strap loop."""
    if not structures:
        return None

    def key(o: Obstacle) -> tuple[float, float]:
        lo, hi = o.shape.aabb()
        return (-float((hi[0] - lo[0]) * (hi[1] - lo[1])), float(lo[2]))

    return min(structures, key=key)


def plate_obstacles(obstacles: ObstacleSet) -> list[Obstacle]:
    return [o for o in obstacles.obstacles if o.plate_axis is not None]


def describe(obstacles: ObstacleSet) -> dict[str, Any]:
    """JSON summary for the routing detail artifact."""
    out = {}
    for o in [*obstacles.obstacles, *obstacles.cable_bodies]:
        lo, hi = o.shape.aabb()
        slo, shi = o.swept.aabb()
        out[o.component] = {"kind": o.kind, "source": o.source, "moving": o.moving, "body": o.body,
                            "keepout": o.keepout, "bbox_min": lo.tolist(), "bbox_max": hi.tolist(),
                            "swept_bbox_min": slo.tolist(), "swept_bbox_max": shi.tolist(),
                            "swept_poses": len(o.swept.shapes) if isinstance(o.swept, SweptShape) else 1,
                            "drillable_plate": o.plate_axis is not None}
    return out


__all__ = ["Obstacle", "ObstacleSet", "ROOT", "base_plate", "build_obstacles", "describe", "plate_obstacles", "Path"]

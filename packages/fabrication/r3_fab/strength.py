"""Strength of printed parts on frame load paths: linear elasticity with scikit-fem.

The part is meshed as voxel hexahedra (20-node serendipity ``ElementHexS2``) in its
component frame, in mm / N / MPa. Supports and loads come from the compiled
assembly graph: for each join between this part and a neighbour, the
neighbour's side of the join graph (this part removed) either reaches a wheel
(the join carries the part to the ground: a fixed support on this part's
interface region) or not (the mass on that side, times the load-case
acceleration, is a load spread over the interface region). The part's own
weight is a body force. Quasi-static load cases are accelerations in g of
every mass (an effective gravity field), so the force on a carried mass m is
F = m a g0. Linear elasticity is sign-symmetric: von Mises stress does not
depend on the sign of a load case.

Quadratic hexes do not shear-lock in bending the way trilinear ones do (a
3-layer ``ElementHex1`` cantilever deflects only ~91 % of Euler-Bernoulli); one
layer of ``ElementHexS2`` deflects ~97 % with corner-recovered mid-span stresses
within +10 % (+3.5 % at three layers), verified in
tests/test_fabrication_strength.py. The rigid edges of support and
load patches are stress singularities, so corners inside a patch are not used
for the reported maximum.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import scipy.ndimage as ndimage
import scipy.sparse.linalg as spla
import trimesh

G0 = 9.80665  # standard gravity, m/s^2
MAX_ELEMENTS = 3000  # first voxel budget
MAX_DOFS = 60000  # then coarsened until the system has at most this many DOFs
DIRECT_DOFS = 30000  # SuperLU up to this size, AMG-CG above
MIN_PITCH_MM = 0.2
COS45 = math.cos(math.radians(45.0))

DEFAULT_LOAD_CASES = (
    {"id": "bump_5g", "accel": [0.0, 0.0, -5.0],
     "description": "5 g vertical landing/bump (estimate for a 1/10 robot jump; gravity included)"},
    {"id": "crash_10g", "accel": [-10.0, 0.0, 0.0],
     "description": "10 g frontal impact along robot x (estimate; sign does not change von Mises stress)"},
)


@dataclass
class Region:
    """A support or load patch on the part, from one join."""

    role: str  # support | load
    join: str
    neighbour: str
    interface: str
    kind: str
    frame_mm: np.ndarray  # interface frame in the component frame (mm)
    params: dict[str, Any]
    holes: list[tuple[float, float, float]]  # (x, y, d) mm in the interface frame
    mass_kg: float = 0.0
    side: list[str] = field(default_factory=list)
    footprint_radius_mm: float | None = None


@dataclass
class LoadPaths:
    supports: list[Region] = field(default_factory=list)
    loads: list[Region] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class VoxelModel:
    mesh: Any  # skfem.MeshHex
    pitch_mm: list[float]
    elements: int
    dropped_elements: int = 0

    @property
    def volume_mm3(self) -> float:
        return self.elements * float(np.prod(self.pitch_mm))


@dataclass
class CaseResult:
    case: str
    accel_g: list[float]
    max_von_mises_mpa: float
    location_mm: list[float]
    principal_direction: list[float]
    max_principal_mpa: float
    max_deflection_mm: float
    load_n: float
    element_vm: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0))
    element_dirs: np.ndarray = field(repr=False, default_factory=lambda: np.zeros((0, 3)))
    displacement: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0))


# ----------------------------------------------------------------------------- load paths


def _adjacency(project: Any) -> dict[str, list[tuple[str, str]]]:
    """Component -> [(neighbour, join id)] over the compiled (valid) joins."""
    result = project.assembly
    adjacency: dict[str, list[tuple[str, str]]] = {}
    if result is None:
        return adjacency
    for join_id, (a, b) in result.valid.items():
        adjacency.setdefault(a.obj, []).append((b.obj, join_id))
        adjacency.setdefault(b.obj, []).append((a.obj, join_id))
    return adjacency


def _side(adjacency: dict[str, list[tuple[str, str]]], start: str, removed: str) -> set[str]:
    seen = {start}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for other, _ in adjacency.get(node, []):
            if other != removed and other not in seen:
                seen.add(other)
                queue.append(other)
    return seen


def component_mass(project: Any, cid: str, cad_parts: dict[str, Any]) -> float:
    """CAD mass when the CAD stage measured one, else the catalog mass (kg)."""
    entry = cad_parts.get(cid) or {}
    if entry.get("mass"):
        return float(entry["mass"])
    comp = project.spec.components[cid]
    if comp.type not in project.catalog.components:
        return 0.0
    return float(project.catalog.component(comp.type).mechanical.mass)


def _is_wheel(project: Any, cid: str) -> bool:
    comp = project.spec.components[cid]
    return comp.type in project.catalog.components and project.catalog.component(comp.type).kind == "wheel"


def _footprint_radius(project: Any, cid: str) -> float | None:
    """Radius (mm) of the circle with the area of the neighbour's two largest envelope extents."""
    comp = project.spec.components[cid]
    if comp.type not in project.catalog.components:
        return None
    mech = project.catalog.component(comp.type).mechanical
    size = [float(v) * 1e3 for v in mech.size]
    if mech.shape == "cylinder":
        return size[0] / 2
    a, b = sorted(size)[-2:]
    return math.sqrt(a * b / math.pi)


def load_paths(project: Any, cid: str, cad_parts: dict[str, Any]) -> LoadPaths:
    """Supports (joins whose far side reaches a wheel) and carried masses of one part."""
    out = LoadPaths()
    result = project.assembly
    if result is None:
        out.notes.append("no assembly section: supports and loads cannot be derived from joins")
        return out
    adjacency = _adjacency(project)
    joins = adjacency.get(cid, [])
    if not joins:
        out.notes.append(f"{cid} has no valid joins")
        return out
    sides: dict[str, set[str]] = {}
    for neighbour, join_id in joins:
        sides[join_id] = _side(adjacency, neighbour, cid)
    # Joins that reach the same set of carried parts share its mass equally.
    groups: dict[frozenset[str], list[str]] = {}
    for join_id, side in sides.items():
        groups.setdefault(frozenset(side), []).append(join_id)
    for neighbour, join_id in joins:
        a, b = result.valid[join_id]
        mine = a if a.obj == cid else b
        frame = mine.frame.copy()
        frame[:3, 3] *= 1e3
        holes = [(h.x * 1e3, h.y * 1e3, h.d * 1e3) for h in mine.holes] if mine.kind in ("hole_pattern", "face_mount") else []
        side = sides[join_id]
        region = Region(role="support" if any(_is_wheel(project, c) for c in side) else "load", join=join_id, neighbour=neighbour,
                        interface=mine.name, kind=mine.kind, frame_mm=frame, params=dict(mine.params), holes=holes,
                        side=sorted(side), footprint_radius_mm=_footprint_radius(project, neighbour))
        if region.role == "load":
            share = len(groups[frozenset(side)])
            region.mass_kg = sum(component_mass(project, c, cad_parts) for c in side) / share
            out.loads.append(region)
        else:
            out.supports.append(region)
    return out


# ----------------------------------------------------------------------------- voxel mesh


def choose_pitch(mesh: trimesh.Trimesh, typical_wall_mm: float, max_elements: int = MAX_ELEMENTS) -> float:
    """Target voxel size: one layer through the typical wall (a single serendipity layer bends
    within 3 % of beam theory), coarsened until the estimated element count (volume / voxel volume
    of the snapped grid) fits the budget."""
    pitch = max(MIN_PITCH_MM, typical_wall_mm)
    volume = float(abs(mesh.volume))
    lo, hi = mesh.bounds
    while True:
        voxel = float(np.prod([np.diff(axis).mean() for axis in _grid(lo, hi, pitch)]))
        if volume / voxel <= max_elements:
            return pitch
        pitch *= 1.08


def _grid(lo: np.ndarray, hi: np.ndarray, pitch: float) -> list[np.ndarray]:
    """Per-axis node coordinates spanning the bounding box exactly (layers align with flat faces)."""
    counts = np.maximum(1, np.ceil((hi - lo) / pitch - 1e-9).astype(int))
    return [np.linspace(lo[i], hi[i], counts[i] + 1) for i in range(3)]


def filled_voxels(mesh: trimesh.Trimesh, axes: list[np.ndarray]) -> np.ndarray:
    """Inside test of voxel centres by ray parity along z columns (one ray per column)."""
    cx, cy, cz = [(a[:-1] + a[1:]) / 2 for a in axes]
    # A tiny irrational offset keeps rays off shared triangle edges (double hits).
    jitter = 1e-4 * np.array([0.6180339887, 0.4142135624])
    gx, gy = np.meshgrid(cx + jitter[0], cy + jitter[1], indexing="ij")
    origins = np.column_stack([gx.ravel(), gy.ravel(), np.full(gx.size, mesh.bounds[0, 2] - 1.0)])
    directions = np.tile([0.0, 0.0, 1.0], (len(origins), 1))
    locations, rays, _ = mesh.ray.intersects_location(origins, directions, multiple_hits=True)
    filled = np.zeros((len(cx), len(cy), len(cz)), dtype=bool)
    order = np.argsort(rays, kind="stable")
    rays, hits = rays[order], locations[order, 2]
    starts = np.searchsorted(rays, np.arange(len(origins)))
    ends = np.searchsorted(rays, np.arange(len(origins)), side="right")
    for ray in np.nonzero(ends > starts)[0]:
        z = np.unique(np.round(hits[starts[ray]:ends[ray]], 9))
        below = np.searchsorted(z, cz)
        i, j = divmod(int(ray), len(cy))
        filled[i, j, :] = below % 2 == 1
    return filled


def voxel_model(mesh: trimesh.Trimesh, pitch: float, keep: Any = None) -> VoxelModel:
    """skfem MeshHex of the filled voxels. ``keep(points) -> bool mask`` selects the face-connected
    voxel clusters to keep (those touching a support); others would be unrestrained."""
    from skfem import MeshHex

    lo, hi = mesh.bounds
    axes = _grid(lo, hi, pitch)
    filled = filled_voxels(mesh, axes)
    labels, count = ndimage.label(filled)  # 6-connectivity: clusters joined by faces only
    if count > 1 and keep is not None:
        corners = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1)
        kept = []
        for label in range(1, count + 1):
            idx = np.argwhere(labels == label)
            pts = np.concatenate([corners[idx[:, 0] + di, idx[:, 1] + dj, idx[:, 2] + dk]
                                  for di in (0, 1) for dj in (0, 1) for dk in (0, 1)])
            if keep(pts).any():
                kept.append(label)
        mask = np.isin(labels, kept) if kept else labels == np.argmax(np.bincount(labels.ravel())[1:]) + 1
    elif count > 1:
        mask = labels == np.argmax(np.bincount(labels.ravel())[1:]) + 1
    else:
        mask = filled
    dropped = int(filled.sum() - mask.sum())
    reference = MeshHex.init_tensor(np.array([0.0, 1.0]), np.array([0.0, 1.0]), np.array([0.0, 1.0]))
    offsets = np.rint(reference.p[:, reference.t[:, 0]].T).astype(int)  # skfem local node order
    n = [len(a) for a in axes]
    ijk = np.argwhere(mask)
    node_ids = np.stack([(ijk[:, 0] + o[0]) * n[1] * n[2] + (ijk[:, 1] + o[1]) * n[2] + (ijk[:, 2] + o[2]) for o in offsets])
    unique, inverse = np.unique(node_ids, return_inverse=True)
    i, rest = np.divmod(unique, n[1] * n[2])
    j, k = np.divmod(rest, n[2])
    points = np.vstack([axes[0][i], axes[1][j], axes[2][k]])
    hexes = MeshHex(points, inverse.reshape(node_ids.shape))
    return VoxelModel(mesh=hexes, pitch_mm=[float(np.diff(a).mean()) for a in axes], elements=int(mask.sum()), dropped_elements=dropped)


# ----------------------------------------------------------------------------- regions -> nodes


def region_mask(points: np.ndarray, region: Region, pitch: float) -> np.ndarray:
    """Nodes (3 x N, mm) of the part under one interface patch."""
    frame = region.frame_mm
    local = (points.T - frame[:3, 3]) @ frame[:3, :3]
    lx, ly, lz = local[:, 0], local[:, 1], local[:, 2]
    tol = 0.75 * pitch
    if region.kind in ("shaft", "bore", "spline", "ball_stud", "ball_socket"):
        d = _quantity_mm(region.params.get("d")) or 2 * pitch
        depth = _quantity_mm(region.params.get("depth") or region.params.get("length")) or d
        radial = np.hypot(lx, ly)
        return (radial <= d / 2 + 1.5 * pitch) & (lz <= tol) & (lz >= -depth - tol)
    on_plane = np.abs(lz) <= tol
    if region.holes:
        near = np.zeros(len(lx), dtype=bool)
        for x, y, d in region.holes:
            # Bearing annulus of a screw head / nut / standoff end: about one diameter around the axis.
            near |= np.hypot(lx - x, ly - y) <= max(0.9 * d, d / 2 + 1.5 * pitch)
        return on_plane & near
    radius = None
    if region.params.get("area") is not None:
        radius = math.sqrt(_quantity_mm2(region.params["area"]) / math.pi)
    radius = radius or region.footprint_radius_mm or max(5.0, 2 * pitch)
    return on_plane & (np.hypot(lx, ly) <= max(radius, 1.5 * pitch))


def _quantity_mm(value: Any) -> float | None:
    if value is None:
        return None
    from r3_model.units import to_si

    return to_si(value) * 1e3


def _quantity_mm2(value: Any) -> float:
    text = str(value)
    if text.endswith("mm^2") or text.endswith("mm2"):
        return float(text.split()[0])
    return float(value) * 1e6


def nodes_for(points: np.ndarray, region: Region, pitch: float, minimum: int = 4) -> np.ndarray:
    """Region node indices; the nearest nodes to the interface origin when the patch misses the mesh."""
    idx = np.nonzero(region_mask(points, region, pitch))[0]
    if len(idx) >= minimum:
        return idx
    distance = np.linalg.norm(points.T - region.frame_mm[:3, 3], axis=1)
    return np.argsort(distance)[: max(minimum, 8)]


# ----------------------------------------------------------------------------- solve


def _lame(youngs_mpa: float, nu: float) -> tuple[float, float]:
    return youngs_mpa * nu / ((1 + nu) * (1 - 2 * nu)), youngs_mpa / (2 * (1 + nu))


def _element() -> Any:
    from skfem import ElementHexS2, ElementVector

    return ElementVector(ElementHexS2())


def element_matrices(pitch_mm: list[float], lam: float, mu: float) -> tuple[np.ndarray, np.ndarray]:
    """Stiffness (60 x 60) and unit body-force vectors (3 x 60) of one voxel hexahedron, assembled
    by scikit-fem in the local DOF order of ``Basis.element_dofs``. Every voxel has the same size
    and node order, so the global system is these scattered over the elements."""
    from skfem import Basis, LinearForm, MeshHex, asm
    from skfem.models.elasticity import linear_elasticity

    hx, hy, hz = pitch_mm
    single = MeshHex.init_tensor(np.array([0.0, hx]), np.array([0.0, hy]), np.array([0.0, hz]))
    basis = Basis(single, _element(), intorder=4)
    local = basis.element_dofs[:, 0]
    stiffness = asm(linear_elasticity(lam, mu), basis).toarray()[np.ix_(local, local)]
    loads = []
    for axis in range(3):
        unit = LinearForm(lambda v, w, axis=axis: v[axis])
        loads.append(asm(unit, basis)[local])
    return stiffness, np.array(loads)


def _von_mises(stress: np.ndarray) -> np.ndarray:
    """sqrt(3/2 s:s) of stress tensors (..., 3, 3), s the deviator."""
    deviatoric = stress - np.einsum("...,ij->...ij", np.trace(stress, axis1=-2, axis2=-1) / 3, np.eye(3))
    return np.sqrt(1.5 * np.einsum("...ij,...ij->...", deviatoric, deviatoric))


@dataclass
class FeSystem:
    """Basis plus the DOF locations: vertices first, then edge midpoints (serendipity nodes)."""

    basis: Any
    points: np.ndarray  # (3, locations) mm
    dofs: np.ndarray  # (locations, 3) global DOF of each displacement component


def fe_system(model: VoxelModel) -> FeSystem:
    from skfem import Basis, MeshHex

    mesh = model.mesh
    reference = MeshHex.init_tensor(np.array([0.0, 1.0]), np.array([0.0, 1.0]), np.array([0.0, 1.0]))
    corners = reference.p[:, reference.t[:, 0]]  # reference corners in local node order: stress recovery points
    basis = Basis(mesh, _element(), quadrature=(corners, np.full(8, 1.0 / 8.0)))
    points = np.hstack([mesh.p, mesh.p[:, mesh.edges].mean(axis=1)])
    dofs = np.vstack([basis.nodal_dofs.T, basis.edge_dofs.T])
    return FeSystem(basis=basis, points=points, dofs=dofs)


def rigid_body_modes(system: FeSystem, free: np.ndarray) -> np.ndarray:
    """Near-null space of 3D elasticity (3 translations, 3 rotations) on the free DOFs, for AMG."""
    location = np.empty(system.basis.N, dtype=int)
    component = np.empty(system.basis.N, dtype=int)
    for axis in range(3):
        location[system.dofs[:, axis]] = np.arange(system.dofs.shape[0])
        component[system.dofs[:, axis]] = axis
    x, y, z = system.points[:, location[free]]
    c = component[free]
    modes = np.zeros((len(free), 6))
    for axis in range(3):
        modes[c == axis, axis] = 1.0
    # Rotations about z, x, y: u = omega x r.
    modes[c == 0, 3], modes[c == 1, 3] = -y[c == 0], x[c == 1]
    modes[c == 1, 4], modes[c == 2, 4] = -z[c == 1], y[c == 2]
    modes[c == 2, 5], modes[c == 0, 5] = -x[c == 2], z[c == 0]
    return modes


class SolverStalled(RuntimeError):
    """AMG-CG did not converge on this voxel grid."""


def linear_solver(matrix: Any, system: FeSystem, free: np.ndarray, direct_fallback: bool = True) -> Any:
    """``solve(rhs) -> u`` for the SPD stiffness: SuperLU for small systems; for larger ones
    smoothed-aggregation AMG (pyamg; rigid-body near-null space, energy-minimizing smoothing)
    preconditioned CG to 1e-7, falling back to SuperLU when CG stalls (some voxel grids of
    one-voxel walls are badly conditioned) or raising :class:`SolverStalled` when
    ``direct_fallback`` is false (the caller then shifts the grid)."""
    if matrix.shape[0] <= DIRECT_DOFS:
        return spla.splu(matrix.tocsc(), permc_spec="COLAMD").solve
    import pyamg

    csr = matrix.tocsr()
    hierarchy = pyamg.smoothed_aggregation_solver(csr, B=rigid_body_modes(system, free), symmetry="symmetric",
                                                  strength=("symmetric", {"theta": 0.0}), smooth=("energy", {"krylov": "cg"}))
    fallback: list[Any] = []

    def solve_one(rhs: np.ndarray) -> np.ndarray:
        if not fallback:
            u = hierarchy.solve(rhs, tol=1e-7, accel="cg", maxiter=150)  # converging grids need 60-90
            if np.linalg.norm(csr @ u - rhs) <= 1e-5 * max(float(np.linalg.norm(rhs)), 1e-300):
                return u
            if not direct_fallback:
                raise SolverStalled(f"AMG-CG stalled on {matrix.shape[0]} DOFs")
            fallback.append(spla.splu(matrix.tocsc(), permc_spec="COLAMD").solve)
        return fallback[0](rhs)

    return solve_one


def solve(model: VoxelModel, system: FeSystem, youngs_pa: float, nu: float, density: float, supports: np.ndarray,
          loads: list[tuple[np.ndarray, float]], cases: list[dict[str, Any]], rotation_to_component: np.ndarray,
          direct_fallback: bool = True) -> list[CaseResult]:
    """Linear static solve for every load case with one SuperLU factorization.

    ``supports``/``loads`` index DOF locations (``FeSystem.points``): fixed, or carrying a mass (kg)
    spread evenly; ``cases`` are accelerations in g in the robot frame, rotated into the component
    frame by ``rotation_to_component`` (R^T of the pose). Stresses are recovered at the element
    corners. Corners inside a support or load patch are skipped: the rigid patch edge is a
    singularity whose value grows without bound under refinement."""
    import scipy.sparse as sparse
    from skfem.helpers import sym_grad

    mesh, basis = model.mesh, system.basis
    lam, mu = _lame(youngs_pa / 1e6, nu)
    ke, fe = element_matrices(model.pitch_mm, lam, mu)
    dofs = basis.element_dofs  # (60, elements)
    size = dofs.shape[0]
    stiffness = sparse.coo_matrix((np.repeat(ke.ravel()[:, None], dofs.shape[1], axis=1).ravel(),
                                   (np.repeat(dofs, size, axis=0).ravel(), np.tile(dofs, (size, 1)).ravel())),
                                  shape=(basis.N, basis.N)).tocsr()
    fixed = system.dofs[supports].ravel()
    free = np.setdiff1d(np.arange(basis.N), fixed)
    solver = linear_solver(stiffness[free][:, free], system, free, direct_fallback)
    unit_body = [np.bincount(dofs.ravel(), weights=np.repeat(fe[axis][:, None], dofs.shape[1], axis=1).ravel(), minlength=basis.N)
                 for axis in range(3)]  # consistent nodal forces of a unit body force (N per N/mm^3)
    patch = np.concatenate([supports, *[nodes for nodes, _ in loads]])
    skipped = np.isin(mesh.t.T, patch[patch < mesh.p.shape[1]])  # (elements, 8) corners inside a patch
    results = []
    for case in cases:
        accel = rotation_to_component @ (np.asarray(case["accel"], dtype=float) * G0)  # m/s^2, component frame
        body = density * 1e-9 * accel  # N/mm^3 = kg/m^3 x m/s^2 x 1e-9
        force = sum(body[axis] * unit_body[axis] for axis in range(3))
        carried = 0.0
        for nodes, mass in loads:
            for axis in range(3):
                force[system.dofs[nodes, axis]] += mass * accel[axis] / len(nodes)  # N, spread evenly
            carried += mass
        u = np.zeros(basis.N)
        u[free] = solver(force[free])
        strain = sym_grad(basis.interpolate(u))  # (3, 3, elements, corners)
        trace = strain[0, 0] + strain[1, 1] + strain[2, 2]
        stress = np.moveaxis(2 * mu * strain + lam * np.einsum("ij,...->ij...", np.eye(3), trace), (0, 1), (-2, -1))  # (e, 8, 3, 3) MPa
        vm_corners = np.where(skipped, 0.0, _von_mises(stress))
        corner = np.argmax(vm_corners, axis=1)
        vm = vm_corners[np.arange(len(corner)), corner]
        critical = stress[np.arange(len(corner)), corner]
        values, vectors = np.linalg.eigh(critical)
        major = np.argmax(np.abs(values), axis=1)
        directions = vectors[np.arange(len(critical)), :, major]
        worst = int(np.argmax(vm))
        displacement = u[system.dofs]
        location = mesh.p[:, mesh.t[corner[worst], worst]]
        results.append(CaseResult(
            case=case["id"], accel_g=[float(v) for v in case["accel"]], max_von_mises_mpa=float(vm[worst]),
            location_mm=location.tolist(), principal_direction=directions[worst].tolist(),
            max_principal_mpa=float(values[worst, major[worst]]), max_deflection_mm=float(np.linalg.norm(displacement, axis=1).max()),
            load_n=float(np.linalg.norm(accel) * carried), element_vm=vm, element_dirs=directions, displacement=displacement))
    return results


def safety_factor(case: CaseResult, strength_xy: float, strength_z: float, build_direction: np.ndarray, top: int = 50) -> tuple[float, bool]:
    """Minimum strength / von Mises over the most stressed elements, with the Z (interlayer)
    strength where the element's major principal stress is within 45 deg of the build direction.
    Returns (safety factor, critical element loaded across layers)."""
    if len(case.element_vm) == 0 or case.max_von_mises_mpa <= 0:
        return math.inf, False
    order = np.argsort(case.element_vm)[::-1][:top]
    across = np.abs(case.element_dirs[order] @ np.asarray(build_direction, dtype=float)) >= COS45
    strength = np.where(across, strength_z, strength_xy) / 1e6  # MPa
    factors = strength / np.maximum(case.element_vm[order], 1e-12)
    k = int(np.argmin(factors))
    return float(factors[k]), bool(across[k])


@dataclass
class StrengthResult:
    applicable: bool
    reason: str = ""
    pitch_mm: list[float] = field(default_factory=list)
    elements: int = 0
    dropped_elements: int = 0
    supports: list[Region] = field(default_factory=list)
    loads: list[Region] = field(default_factory=list)
    cases: list[CaseResult] = field(default_factory=list)
    support_nodes: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))
    load_nodes: list[np.ndarray] = field(default_factory=list)
    model: VoxelModel | None = None
    points: np.ndarray = field(default_factory=lambda: np.zeros((3, 0)))  # DOF locations (mm)
    volume_error: float = 0.0  # voxel model volume / part volume - 1
    runtime_s: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def worst(self) -> CaseResult | None:
        return max(self.cases, key=lambda c: c.max_von_mises_mpa) if self.cases else None


def _build(mesh: trimesh.Trimesh, pitch: float, paths: LoadPaths) -> tuple[VoxelModel, FeSystem, np.ndarray, list[tuple[np.ndarray, float]]] | None:
    """Voxel model coarsened to the DOF budget, its FE system and the support/load DOF locations."""
    while True:
        keep = lambda pts, h=pitch: np.any([region_mask(pts.T, r, h) for r in paths.supports], axis=0)  # noqa: E731
        model = voxel_model(mesh, pitch, keep)
        if model.elements == 0:
            return None
        system = fe_system(model)
        if system.basis.N <= MAX_DOFS:
            break
        pitch *= 1.1
    h = max(model.pitch_mm)
    supports = np.unique(np.concatenate([nodes_for(system.points, r, h) for r in paths.supports]))
    loads = [(nodes_for(system.points, r, h), r.mass_kg) for r in paths.loads]
    return model, system, supports, loads


def analyze(project: Any, cid: str, mesh: trimesh.Trimesh, material: Any, typical_wall_mm: float,
            cases: list[dict[str, Any]], cad_parts: dict[str, Any]) -> StrengthResult:
    """Supports/loads from the joins, voxel model, linear static solve for every case."""
    start = time.perf_counter()
    paths = load_paths(project, cid, cad_parts)
    result = StrengthResult(applicable=False, supports=paths.supports, loads=paths.loads, notes=list(paths.notes))
    if not paths.supports:
        result.reason = "no join of this part leads to a wheel (no support on a frame load path)"
        return result
    if not paths.loads:
        result.reason = "the part carries no other mass (only its own weight)"
        return result
    pitch = choose_pitch(mesh, typical_wall_mm)
    _, rotation = project.graph.component_pose(cid)
    for attempt in range(2):
        built = _build(mesh, pitch, paths)
        if built is None:
            result.reason = f"voxel model at {pitch:.2f} mm pitch is empty"
            return result
        model, system, supports, loads = built
        try:
            result.cases = solve(model, system, float(material.youngs_modulus), float(material.poisson_ratio), float(material.density),
                                 supports, loads, cases, rotation.T, direct_fallback=attempt == 1)
            break
        except SolverStalled:
            # Badly conditioned grid of one-voxel walls: shift the grid once (4 % coarser), then use SuperLU.
            pitch = max(model.pitch_mm) * 1.04
            result.notes.append(f"AMG-CG stalled at {max(model.pitch_mm):.3f} mm; voxel grid shifted to {pitch:.3f} mm")
    result.points = system.points
    result.applicable = True
    result.pitch_mm = model.pitch_mm
    result.elements = model.elements
    result.dropped_elements = model.dropped_elements
    result.volume_error = model.volume_mm3 / max(float(abs(mesh.volume)), 1e-12) - 1.0
    result.support_nodes = supports
    result.load_nodes = [nodes for nodes, _ in loads]
    result.model = model
    result.runtime_s = time.perf_counter() - start
    return result

"""Offline recomposition of ``build/cad/cad_model.json`` (no Onshape calls).

Objects are rigid Part Studios joined at interfaces, so a change that only moves
objects (a new join, a re-clocked tape joint, a moved pad interface) needs no
Onshape rebuild: the recorded Part Studio geometry of every part
(``fixtures/onshape_local_geometry.json.gz``, written by the organized build) is
moved to the newly compiled poses and the exact-geometry checks (fit, motion
sweep, RF feed point) run again on it.

A part whose *plan* changed (a notch, a new clip, a thinner board) cannot reuse
its Onshape geometry. Its plan is compared with the plan the recording was
built from (``fixtures/onshape_local_plans.json.gz``) by an exact mesh boolean;
when the solids differ, the part is built locally from the same standard
operations (:func:`r3_onshape.geometry.plan_manifold`), its analytic faces
(planes, cylinders, spheres) are recovered from the plan, and it is listed in
``source.recomposed.pending_rebuild``. The cad stage reports those parts as
``cad_part_rebuild_pending`` until the next Onshape build replaces them.

Run: ``python -m r3_onshape.recompose examples/robots/rc-car``.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np

from r3_model.diagnostics import Diagnostic, StageResult
from r3_model.project import Project

from .geometry import Hole, Prism, Revolve, _AXES, plan_manifold, unit
from .model import Face, GeometryModel, PartGeometry, _orient_faces_from_mesh, _weld, part_from_onshape

GEOMETRY_FIXTURE = "fixtures/onshape_local_geometry.json.gz"
PLAN_FIXTURE = "fixtures/onshape_local_plans.json.gz"
MASS_FIXTURE = "fixtures/onshape_local_mass.json"
SAME_SOLID_TOL = 1e-12  # m^3 (0.001 mm^3) of symmetric difference: the same solid
ON_SURFACE_TOL = 2e-6  # m: a facet vertex this close to an analytic surface lies on it
PARALLEL_TOL = 1e-3  # |cos| below which a facet normal is perpendicular to an axis


# ----------------------------------------------------------------- recorded plans
def plan_sha(plan: Any) -> str:
    from .direct import _jsonable

    return hashlib.sha256(json.dumps(_jsonable(plan), sort_keys=True, default=str).encode()).hexdigest()


def record_plans(project: Project, parts: dict[str, Any], digest: str) -> Path:
    """Store every part's plan digest and plan solid next to the geometry recording,
    so a later recomposition can tell exactly which Onshape parts are still valid."""
    out: dict[str, Any] = {"generator_digest": digest, "parts": {}}
    for key, part in sorted(parts.items()):
        mesh = plan_manifold(part.plan).to_mesh()
        out["parts"][key] = {"plan_sha256": plan_sha(part.plan),
                             "vertices": np.asarray(mesh.vert_properties)[:, :3].round(9).tolist(),
                             "triangles": np.asarray(mesh.tri_verts).tolist()}
    path = project.build_dir("cad") / PLAN_FIXTURE
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as handle:
        json.dump(out, handle)
    return path


def _manifold(vertices: Any, triangles: Any):
    import manifold3d as m3d

    return m3d.Manifold(m3d.Mesh(vert_properties=np.asarray(vertices, dtype=np.float32),
                                 tri_verts=np.asarray(triangles, dtype=np.uint32)))


def same_solid(plan: Any, recorded: dict[str, Any]) -> tuple[bool, float]:
    """Exact symmetric-difference volume of the current plan solid and the recorded one."""
    if recorded.get("plan_sha256") == plan_sha(plan):
        return True, 0.0
    new = plan_manifold(plan)
    old = _manifold(recorded["vertices"], recorded["triangles"])
    difference = float((new - old).volume() + (old - new).volume())
    return difference <= SAME_SOLID_TOL, difference


# ----------------------------------------------------------------- local part geometry
Cone = tuple[np.ndarray, np.ndarray, tuple[float, float], tuple[float, float]]  # point, axis, (r0, t0), (r1, t1)


def _surfaces(plan: Any) -> tuple[list[tuple[np.ndarray, np.ndarray, float]], list[tuple[np.ndarray, float]], list[Cone]]:
    """Analytic cylinders (point, axis, radius), spheres (center, radius) and cones (a
    generatrix from (r0, t0) to (r1, t1) about an axis) the plan's operations create."""
    cylinders: list[tuple[np.ndarray, np.ndarray, float]] = []
    spheres: list[tuple[np.ndarray, float]] = []
    cones: list[Cone] = []
    for op in plan.ops:
        if isinstance(op, Hole):
            point, axis, radius = np.asarray(op.point, float), unit(op.axis), op.diameter / 2
            cylinders.append((point, axis, radius))
            if op.depth is not None:  # 118 deg drill point (geometry.operation_manifold)
                cones.append((point, axis, (radius, op.depth), (0.0, op.depth + radius / math.tan(math.radians(59)))))
        elif isinstance(op, Revolve):
            point, axis = np.asarray(op.point, float), unit(op.axis)
            loop = op.loop_rt()
            for seg in loop.segments:
                if seg.kind == "line" and abs(seg.p0[0] - seg.p1[0]) < 1e-12 and seg.p0[0] > 0:
                    cylinders.append((point, axis, float(seg.p0[0])))
                elif seg.kind == "line" and abs(seg.p0[1] - seg.p1[1]) > 1e-12 and max(seg.p0[0], seg.p1[0]) > 0:
                    cones.append((point, axis, (float(seg.p0[0]), float(seg.p0[1])), (float(seg.p1[0]), float(seg.p1[1]))))
                elif seg.kind == "arc" and abs(seg.center[0]) < 1e-12:
                    spheres.append((point + axis * float(seg.center[1]), float(seg.radius)))
        elif isinstance(op, Prism):
            u, v = _AXES[op.plane.base]
            normal = op.plane.normal
            for loop in op.loops:
                if loop.circle_center is not None:
                    centers = [(loop.circle_center, loop.circle_radius)]
                else:
                    centers = [(seg.center, seg.radius) for seg in loop.segments if seg.kind == "arc"]
                for center, radius in centers:
                    world = op.plane.origin + u * float(center[0]) + v * float(center[1])
                    cylinders.append((world, normal, float(radius)))
    return cylinders, spheres, cones


def _components(triangles: np.ndarray, members: np.ndarray) -> list[np.ndarray]:
    """Edge-connected groups of the selected triangles."""
    index = {int(t): i for i, t in enumerate(members)}
    parent = list(range(len(members)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    edges: dict[tuple[int, int], int] = {}
    for t in members:
        a, b, c = (int(x) for x in triangles[t])
        for e in ((a, b), (b, c), (c, a)):
            key = (min(e), max(e))
            if key in edges:
                ra, rb = find(index[int(t)]), find(edges[key])
                if ra != rb:
                    parent[ra] = rb
            else:
                edges[key] = index[int(t)]
    groups: dict[int, list[int]] = {}
    for i, t in enumerate(members):
        groups.setdefault(find(i), []).append(int(t))
    return [np.asarray(g) for g in groups.values()]


def part_from_plan(name: str, plan: Any, part_id: str = "") -> PartGeometry:
    """A part built locally from its plan: the exact boolean solid plus analytic
    faces recovered from the operations that made each facet."""
    mesh = plan_manifold(plan).to_mesh()
    vertices = np.asarray(mesh.vert_properties, dtype=float)[:, :3]
    triangles = np.asarray(mesh.tri_verts, dtype=np.int64)
    soup = vertices[triangles]
    vertices, triangles = _weld(soup)
    corners = vertices[triangles]
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    areas = np.linalg.norm(normals, axis=1) / 2
    unit_normals = normals / np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-30)
    assigned = np.full(len(triangles), -1)
    surfaces: list[tuple[str, np.ndarray, np.ndarray, float]] = []
    cylinders, spheres, cones = _surfaces(plan)
    # Facets of a 96-gon cylinder: every vertex at the radius, normal perpendicular to the axis.
    for point, axis, radius in cylinders:
        rel = corners - point
        radial = rel - np.einsum("tvi,i->tv", rel, axis)[..., None] * axis
        on = np.all(np.abs(np.linalg.norm(radial, axis=2) - radius) < ON_SURFACE_TOL, axis=1)
        on &= np.abs(unit_normals @ axis) < PARALLEL_TOL
        on &= assigned < 0
        if on.any():
            surfaces.append(("CYLINDER", point, axis, radius))
            assigned[on] = len(surfaces) - 1
    for point, axis, (r0, t0), (r1, t1) in cones:
        rel = corners - point
        t = np.einsum("tvi,i->tv", rel, axis)
        r = np.linalg.norm(rel - t[..., None] * axis, axis=2)
        expected = r0 + (t - t0) * (r1 - r0) / (t1 - t0)
        inside = (t >= min(t0, t1) - ON_SURFACE_TOL) & (t <= max(t0, t1) + ON_SURFACE_TOL)
        on = np.all((np.abs(r - expected) < ON_SURFACE_TOL) & inside, axis=1) & (assigned < 0)
        on &= np.abs(unit_normals @ axis) > PARALLEL_TOL  # not a flat annulus at the cone's end
        if on.any():
            surfaces.append(("CONE", point, axis, max(r0, r1)))
            assigned[on] = len(surfaces) - 1
    for center, radius in spheres:
        on = np.all(np.abs(np.linalg.norm(corners - center, axis=2) - radius) < ON_SURFACE_TOL, axis=1) & (assigned < 0)
        if on.any():
            surfaces.append(("SPHERE", center, np.zeros(3), radius))
            assigned[on] = len(surfaces) - 1
    # Remaining facets: planes, grouped by outward normal and offset.
    planes: dict[tuple, int] = {}
    for t in np.flatnonzero(assigned < 0):
        n = unit_normals[t]
        key = tuple(np.round(n, 4)) + (round(float(n @ corners[t, 0]), 6),)
        if key not in planes:
            surfaces.append(("PLANE", corners[t, 0], n, 0.0))
            planes[key] = len(surfaces) - 1
        assigned[t] = planes[key]
    faces: list[Face] = []
    for s, (kind, origin, direction, radius) in enumerate(surfaces):
        for g, members in enumerate(_components(triangles, np.flatnonzero(assigned == s))):
            tris = corners[members]
            pts = tris.reshape(-1, 3)
            if kind == "PLANE":
                weights = areas[members]
                normal = unit((unit_normals[members] * weights[:, None]).sum(axis=0))
                face_origin = (tris.mean(axis=1) * weights[:, None]).sum(axis=0) / weights.sum()
            else:
                normal, face_origin = direction, origin
            faces.append(Face(id=f"L{s}_{g}", part=name, kind=kind, origin=np.asarray(face_origin, float),
                              direction=np.asarray(normal, float), radius=radius, area=float(areas[members].sum()),
                              lo=pts.min(axis=0), hi=pts.max(axis=0), triangles=tris))
    part = PartGeometry(name=name, part_id=part_id or f"local:{name}", faces=faces)
    part.vertices, part.triangles = vertices, triangles
    _orient_faces_from_mesh(part)
    return part


def local_mass(part: PartGeometry, plan: Any) -> dict[str, Any]:
    """Mass properties of a locally built part in its own frame (Onshape massproperties layout):
    purchased parts carry the catalog mass, fabricated ones volume x density."""
    import trimesh

    mesh = trimesh.Trimesh(part.vertices, part.triangles, process=False)
    volume = float(mesh.volume)
    mass = float(plan.mass) if plan.mass is not None else volume * float(plan.density or 0.0)
    density = mass / volume if volume > 0 else 0.0
    mesh.density = density
    inertia = np.asarray(mesh.moment_inertia, float)  # about the centroid, part axes
    return {"mass": [mass], "volume": [volume], "centroid": np.asarray(mesh.center_mass, float).tolist(),
            "inertia": inertia.reshape(-1).tolist()}


def _local_from_world(entry: dict[str, Any], pose: np.ndarray) -> dict[str, Any]:
    """Invert ``organized._world_mass`` (centroid and inertia back into the part frame)."""
    rotation, translation = pose[:3, :3], pose[:3, 3]
    ixx, iyy, izz, ixy, ixz, iyz = entry["inertia"]
    world = np.array([[ixx, ixy, ixz], [ixy, iyy, iyz], [ixz, iyz, izz]])
    return {"mass": [entry["mass"]], "volume": [entry.get("volume", 0.0)],
            "centroid": (rotation.T @ (np.asarray(entry["com"]) - translation)).tolist(),
            "inertia": (rotation.T @ world @ rotation).reshape(-1).tolist()}


def recorded_masses(project: Project, previous: dict[str, Any], placements: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Part-frame Onshape mass properties per part key, derived once from the cad_model.json
    the recording belongs to and cached in ``fixtures/onshape_local_mass.json``."""
    path = project.build_dir("cad") / MASS_FIXTURE
    if path.exists():
        return json.loads(path.read_text())
    if previous.get("source", {}).get("recomposed"):
        raise SystemExit(f"{path} is missing and cad_model.json is already recomposed: its poses no longer match the recording")
    out: dict[str, dict[str, Any]] = {}
    for iid, (key, matrix) in placements.items():
        entry = previous.get("parts", {}).get(iid)
        if entry is None or key in out:
            continue
        out[key] = _local_from_world(entry, np.asarray(matrix, float))
    path.write_text(json.dumps(out, indent=1))
    return out


# ----------------------------------------------------------------- recomposition
def recompose(project: Project, result: StageResult | None = None) -> dict[str, Any]:
    """Rewrite ``cad_model.json`` and the meshes for the current robot.yaml without Onshape."""
    from .fit import verify_fit
    from .motion import run_motion
    from .organized import ORGANIZER_VERSION, _world_mass, compose_world, input_digest, plan_assembly, transform_part
    from .parts import build_part_catalog
    from .sync import SyncReport, _json_safe, antenna_position, full_lock_views, mesh_filename, stl_bytes

    started = time.time()
    result = result or StageResult(stage="cad")
    build = project.build_dir("cad")
    previous = json.loads((build / "cad_model.json").read_text())
    with gzip.open(build / GEOMETRY_FIXTURE, "rt") as handle:
        recording = json.load(handle)
    plan_path = build / PLAN_FIXTURE
    if not plan_path.exists():
        raise SystemExit(f"{plan_path} is missing: record the plans of the build commit first (record_plans)")
    with gzip.open(plan_path, "rt") as handle:
        recorded_plans = json.load(handle)
    if recorded_plans.get("generator_digest") != previous["source"].get("recorded_digest", previous["source"]["generator_digest"]):
        raise SystemExit("the recorded plans belong to a different CAD build than cad_model.json")
    parts, instances = build_part_catalog(project)
    plan = plan_assembly(project, parts, instances)
    digest = input_digest(project)
    masses = recorded_masses(project, previous, recording["placements"])

    local: dict[str, PartGeometry] = {}
    local_masses: dict[str, dict[str, Any]] = {}
    pending: dict[str, Any] = {}
    for key in sorted({p.key for p in plan.placements.values()}):
        part = parts[key]
        recorded = recorded_plans["parts"].get(key)
        geometry = recording["parts"].get(key)
        same, difference = same_solid(part.plan, recorded) if recorded is not None and geometry is not None else (False, math.inf)
        if same:
            local[key] = part_from_onshape(key, geometry["part_id"], geometry["body"], geometry["tessellation"])
            local_masses[key] = masses[key]
        else:
            local[key] = part_from_plan(key, part.plan)
            local_masses[key] = local_mass(local[key], part.plan)
            pending[key] = {"reason": "new part" if recorded is None else "plan changed",
                            "symmetric_difference_mm3": None if math.isinf(difference) else round(difference * 1e9, 3),
                            "studio": part.studio, "library": part.library, "instances": sorted(
                                iid for iid, p in plan.placements.items() if p.key == key)}
    poses = {iid: p.pose for iid, p in plan.placements.items()}
    model = compose_world(local, plan, poses)
    model.source = "recomposed"
    materials = {iid: parts[p.key].plan.material for iid, p in plan.placements.items()}
    fit = verify_fit(project, model, materials, result)
    kinematics = run_motion(project, model, fit, result)
    rf = antenna_position(project, model)

    mesh_dir = build / "meshes"
    mesh_dir.mkdir(parents=True, exist_ok=True)
    for stale in mesh_dir.glob("*.stl"):
        stale.unlink()
    imported: dict[str, Any] = {}
    for iid, placement in plan.placements.items():
        geometry = model.parts.get(iid)
        if geometry is None:
            continue
        part = parts[placement.key]
        data = stl_bytes(geometry)
        filename = mesh_filename(iid)
        (mesh_dir / filename).write_bytes(data)
        old = previous["parts"].get(iid, {})
        entry = {"part_id": geometry.part_id, "part_name": part.name, "part_key": placement.key, "kind": part.plan.kind,
                 "owner": part.plan.owner if part.plan.kind == "fastener" else iid, "material": part.plan.material,
                 "density": part.plan.density or None,
                 "mass_status": "catalog_override" if part.plan.kind == "purchased" else "cad_geometry",
                 "bbox_min": geometry.lo.tolist(), "bbox_max": geometry.hi.tolist(), "mesh": f"meshes/{filename}",
                 "mesh_triangles": int(len(geometry.triangles)), "mesh_sha256": hashlib.sha256(data).hexdigest(),
                 "subassembly": placement.level, "studio": part.studio, "library": part.library,
                 "geometry_source": "local_plan" if placement.key in pending else "onshape"}
        entry.update(_world_mass(local_masses[placement.key], poses[iid]))
        if part.plan.kind == "fastener":
            fastener = next((f for f in project.spec.mechanical.fasteners if iid in (f.part, f.nut_part)), None)
            entry["owner"] = (fastener.thread_into or (fastener.clamps[0] if fastener.clamps else "")) if fastener else old.get("owner", "")
        imported[iid] = entry

    plans = {iid: parts[p.key].plan for iid, p in plan.placements.items()}
    lock_report = SyncReport()
    lock_report.kinematics = kinematics
    full_lock_views(model, plans, kinematics, build, lock_report, result)
    hashes = {name: part["mesh_sha256"] for name, part in sorted(imported.items())}
    source = dict(previous["source"])
    source.update({"generator_digest": digest, "generator_version": ORGANIZER_VERSION,
                   "mesh_digest": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:16],
                   "mesh_sha256": hashes, "recorded_digest": recorded_plans["generator_digest"],
                   "recomposed": {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "from_microversion": previous["source"].get("microversion"),
                                  "pending_rebuild": pending,
                                  "diagnostics": [d.model_dump(mode="json") for d in result.diagnostics if d.severity != "info"],
                                  "note": "Part Studio geometry recorded from Onshape, moved to the compiled poses offline; "
                                          "pending_rebuild parts are built locally from their plans (exact booleans) and must be "
                                          "rebuilt in Onshape, and the Onshape assembly re-mated, at the next organized build."}})
    model_json = {**previous, "inputs": project.fingerprint(), "source": source, "parts": imported,
                  "fastener_owner": {n: p.get("owner") for n, p in imported.items() if p.get("kind") == "fastener"},
                  "fit": fit.summary(), "kinematics": {"steering": kinematics}, "rf": rf,
                  "images": [i for i in previous.get("images", []) if i.startswith("views/")] + lock_report.images,
                  "recompose_seconds": round(time.time() - started, 1)}
    path = build / "cad_model.json"
    path.write_text(json.dumps(_json_safe(model_json), indent=1, default=str))
    errors = [d for d in result.diagnostics if d.severity == "error"]
    return {"path": str(path), "digest": digest, "pending_rebuild": pending, "errors": len(errors),
            "diagnostics": [d.model_dump(mode="json") for d in result.diagnostics]}


def main(argv: list[str] | None = None) -> int:
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    project = Project.load(argv[0] if argv else "examples/robots/rc-car")
    out = recompose(project)
    for d in out["diagnostics"]:
        if d["severity"] != "info":
            print(d["severity"], d["code"], d["message"][:220])
    for key, info in out["pending_rebuild"].items():
        print(f"pending Onshape rebuild: {key} ({info['reason']}, {info['symmetric_difference_mm3']} mm^3) -> {info['instances']}")
    print(f"cad_model.json recomposed (digest {out['digest']}): {out['errors']} errors")
    return 1 if out["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

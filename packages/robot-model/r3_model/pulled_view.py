"""Consumer projections of pulled CAD; never author or repair geometry.

World-frame mesh copies are derived build artifacts for existing renderers. The
pinned part-local inputs remain untouched. Every projection validates the pull.
"""
from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any
import numpy as np

from .pulled import PulledModel, PullError
from .spec_files import validate_pulled_spec as load_pulled


def world_mesh(pulled: PulledModel, name: str):
    import trimesh

    from .mesh_io import load_exported_mesh
    mesh = load_exported_mesh(pulled.parts[name].mesh)
    if not isinstance(mesh, trimesh.Trimesh) or not len(mesh.faces):
        raise PullError("cad_pull_invalid", f"{name}: pulled mesh has no triangles")
    mesh.apply_transform(pulled.poses[name])
    return mesh


def require_mass(pulled: PulledModel) -> None:
    for name, part in pulled.parts.items():
        if part.mass is None or part.com is None or part.inertia is None:
            raise PullError("cad_pull_mass_missing", f"{name}: pulled mass, COM and inertia are required; assign material/mass in Onshape and pull again")
        inertia = np.asarray(part.inertia).reshape(3, 3)
        eigenvalues = np.linalg.eigvalsh(inertia)
        if not np.allclose(inertia, inertia.T) or eigenvalues.min() <= 0 or eigenvalues[-1] > eigenvalues[:2].sum() * (1 + 1e-7):
            raise PullError("cad_pull_invalid", f"{name}: inertia must be positive definite and satisfy the principal-moment triangle inequality")


@dataclass(frozen=True)
class _Projection:
    path: Path
    sha256: str
    lo: tuple[float, ...]
    hi: tuple[float, ...]
    triangles: int


# Cache only small derived metadata, not native meshes. Each caller first
# validates every pinned input and local mirror. A hit also hashes the derived
# file, so deleting/editing build artifacts never changes geometry authority.
_PROJECTIONS: OrderedDict[tuple[str, str], _Projection] = OrderedDict()
_PROJECTION_LOCK = threading.RLock()
_PROJECTION_LIMIT = 1024


def _projection(pulled: PulledModel, name: str, out: Path) -> _Projection:
    part, pose = pulled.parts[name], pulled.poses[name]
    digest = part.sha256['obj' if part.obj else 'gltf' if part.gltf else 'stl']
    key = hashlib.sha256((name + digest + repr(pose.tolist())).encode()).hexdigest()
    path = out / f"{key}.stl"
    cache_key = (str(out.resolve()), key)
    # Concurrent model/CAD requests share a conversion instead of each parsing
    # and sorting the same large native OBJ. Other checks still read native data.
    with _PROJECTION_LOCK:
        cached = _PROJECTIONS.get(cache_key)
        if cached is not None:
            try:
                with path.open('rb') as stream:
                    matches = hashlib.file_digest(stream, 'sha256').hexdigest() == cached.sha256
            except OSError:
                matches = False
            if matches:
                _PROJECTIONS.move_to_end(cache_key)
                return cached
        mesh = world_mesh(pulled, name)
        content = mesh.export(file_type='stl')
        if not path.exists() or path.read_bytes() != content:
            path.write_bytes(content)
        bounds = mesh.bounds
        result = _Projection(path, hashlib.sha256(content).hexdigest(),
                             tuple(bounds[0]), tuple(bounds[1]), len(mesh.faces))
        _PROJECTIONS[cache_key] = result
        _PROJECTIONS.move_to_end(cache_key)
        while len(_PROJECTIONS) > _PROJECTION_LIMIT:
            _PROJECTIONS.popitem(last=False)
        return result


def resolve_native_fasteners(project: Any, pulled: PulledModel) -> dict[str, dict[str, Any]]:
    """Read seat and insertion axis from native datums; never move any CAD body."""
    frames = {}
    for fastener in project.spec.mechanical.fasteners:
        if fastener.mate_connector is None:
            continue
        body, selector = fastener.body, fastener.mate_connector
        if body not in pulled.parts:
            raise PullError("cad_pull_missing", f"Fastener {fastener.id}: native body {body!r} is absent from the pull")
        part = pulled.parts[body]
        if selector not in part.mate_connectors:
            raise PullError("cad_native_connector_missing",
                f"Fastener {fastener.id}: select an existing seating mate connector on {body}",
                {"component": body, "mate_connector": selector,
                 "available_connectors": sorted(part.mate_connectors)})
        if fastener.nut_part is not None and fastener.nut_part not in pulled.parts:
            raise PullError("cad_pull_missing", f"Fastener {fastener.id}: native nut {fastener.nut_part!r} is absent from the pull")
        frame = pulled.poses[body] @ part.mate_connectors[selector]
        # Derived runtime fields must not become authored YAML values.
        object.__setattr__(fastener, "position", frame[:3, 3].tolist())
        object.__setattr__(fastener, "axis", frame[:3, 2].tolist())
        frames[fastener.id] = {"source": "pulled", "body": body, "mate_connector": selector,
            "document_id": pulled.document_id, "version_id": pulled.version_id, "matrix": frame.tolist()}
    return frames


def cad_view(project: Any, *, mass_required: bool = False) -> dict[str, Any]:
    pulled = load_pulled(project.root)
    resolve_native_fasteners(project, pulled)
    if mass_required:
        require_mass(pulled)
    missing = set(project.spec.components) - pulled.parts.keys()
    if missing:
        raise PullError("cad_pull_missing", f"Parts absent from pulled CAD: {', '.join(sorted(missing))}")
    out = project.build_dir("cad/pulled-meshes")
    parts = {}
    for name, part in pulled.parts.items():
        projection = _projection(pulled, name, out)
        pose = pulled.poses[name]
        inertia = pose[:3, :3] @ np.asarray(part.inertia).reshape(3, 3) @ pose[:3, :3].T if part.inertia is not None else None
        parts[name] = {
            "part_name": name, "mesh": str(projection.path.relative_to(project.build_dir("cad"))),
            "mesh_sha256": projection.sha256, "geometry_source": "pulled",
            "mass": part.mass, "mass_status": "pulled", "material": part.material,
            "com": (pose[:3, :3] @ part.com + pose[:3, 3]).tolist() if part.com is not None else None,
            "inertia": [float(inertia[i, j]) for i, j in [(0, 0), (1, 1), (2, 2), (0, 1), (0, 2), (1, 2)]] if inertia is not None else None,
            "bbox_min": list(projection.lo), "bbox_max": list(projection.hi),
            "mesh_triangles": projection.triangles, "purchased": part.purchased,
            "geometry_qualification": part.geometry_qualification,
            "volume": part.native_volume,
        }
    return {"source": {"provider": "pulled", "document_id": pulled.document_id, "version_id": pulled.version_id},
            "parts": parts, "inputs": project.fingerprint()}


def apply_pulled(project: Any, pulled: PulledModel | None = None):
    """Project solved poses and a mate spanning tree for downstream kinematics.

    This is a graph traversal of existing solved CAD, not a pose solver. Closed
    loops remain in the assembly report for simulator constraints/diagnostics.
    """
    from .assembly import AssemblyResult, ResolvedInterface, rpy_from_matrix
    from .diagnostics import Diagnostic
    from .spec import Joint, Pose
    from .description import check_pulled
    pulled = pulled or load_pulled(project.root)
    check_pulled(project.root, pulled)
    declared = getattr(project.spec, "assembly", None)
    root = declared.root if declared and declared.root in pulled.parts else next(iter(pulled.parts), "")
    source_joints = {joint.id: joint for joint in project.spec.mechanical.joints}
    qualification = {item.id: item for item in project.spec.mechanical.interfaces}
    result = AssemblyResult(root=root, poses=dict(pulled.poses))
    result.fastener_frames = resolve_native_fasteners(project, pulled)
    if root:
        result.root_pose = pulled.poses[root]
    for name, pose in pulled.poses.items():
        if name in project.spec.components:
            project.spec.components[name].pose = Pose(xyz=pose[:3, 3].tolist(), rpy=rpy_from_matrix(pose))
            project.spec.components[name].mount = "world"
        for key, frame in pulled.parts[name].mate_connectors.items():
            result.interfaces[f"{name}.{key}"] = ResolvedInterface(name, key, "mate_connector", frame, {}, pulled.parts[name].material or "")
    tol_pos = float(declared.tolerance.position) if declared else 1e-5
    tol_angle = float(declared.tolerance.angle) if declared else np.deg2rad(0.2)
    adjacency: dict[str, list] = {name: [] for name in pulled.parts}
    for mate in pulled.mates:
        fa = pulled.poses[mate.a[0]] @ pulled.parts[mate.a[0]].mate_connectors[mate.a[1]]
        fb = pulled.poses[mate.b[0]] @ pulled.parts[mate.b[0]].mate_connectors[mate.b[1]]
        distance = float(np.linalg.norm(fa[:3, 3] - fb[:3, 3]))
        angle = float(np.arccos(np.clip((np.trace(fa[:3, :3].T @ fb[:3, :3]) - 1) / 2, -1, 1)))
        valid = mate.kind != "fastened" or (distance <= tol_pos and angle <= tol_angle)
        entry = {"a": '.'.join(mate.a), "b": '.'.join(mate.b), "kind": mate.kind,
                 "valid": valid, "position_error": distance, "angle_error": angle}
        result.joins[mate.id] = entry
        if not valid:
            result.diagnostics.append(Diagnostic.error("assembly_misfit", f"{mate.id}: pulled fastened connector frames do not coincide",
                subjects=[mate.id, mate.a[0], mate.b[0]], data={**entry, "position_tolerance": tol_pos, "angle_tolerance": tol_angle}))
        adjacency[mate.a[0]].append((mate, mate.b[0], fa, False))
        adjacency[mate.b[0]].append((mate, mate.a[0], fb, True))
    seen, used = set(), set()
    for start in [root, *pulled.parts]:
        if not start or start in seen:
            continue
        seen.add(start)
        queue = [start]
        for parent in queue:
            for mate, child, frame, reverse in adjacency[parent]:
                if child in seen:
                    continue
                seen.add(child); used.add(mate.id); queue.append(child)
                if child in project.spec.components:
                    project.spec.components[child].mount = parent
                kind = {"fastened": "fixed", "revolute": "revolute" if mate.limits else "continuous", "slider": "prismatic"}.get(mate.kind)
                if kind is None:
                    result.diagnostics.append(Diagnostic.error("cad_mate_unsupported", f"{mate.id}: {mate.kind} mate requires a native simulator constraint not supported by this adapter", subjects=[mate.id]))
                    continue
                # Pull records the native axis in the assembly/world frame.
                a_frame = pulled.poses[mate.a[0]] @ pulled.parts[mate.a[0]].mate_connectors[mate.a[1]]
                axis = np.asarray(mate.axis, float) if mate.axis is not None else a_frame[:3, 2]
                if reverse:
                    axis = -axis
                limits = [-mate.limits[1], -mate.limits[0]] if reverse and mate.limits else mate.limits
                source = source_joints.get(mate.id)
                iid = source.interface if source else None
                if iid not in qualification or set(qualification[iid].parts) != {parent, child}:
                    candidates = [item.id for item in qualification.values() if set(item.parts) == {parent, child}]
                    iid = candidates[0] if len(candidates) == 1 else mate.id
                result.joints.append(Joint(id=mate.id, type=kind, parent=parent, child=child,
                    origin=frame[:3, 3].tolist(), axis=axis.tolist(), limits=limits, interface=iid))
    for mate in pulled.mates:
        if mate.id not in used:
            result.loops.append({"join": mate.id, **result.joins[mate.id]})
    project.spec.mechanical.joints = result.joints
    project._assembly = result
    project._pulled = pulled
    project.__dict__.pop("_graph", None)
    return result

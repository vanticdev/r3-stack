"""Consumer projections of pulled CAD; never author or repair geometry.

World-frame mesh copies are derived build artifacts for existing renderers. The
pinned part-local inputs remain untouched. Every projection validates the pull.
"""
from __future__ import annotations

import hashlib
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


def cad_view(project: Any, *, mass_required: bool = False) -> dict[str, Any]:
    pulled = load_pulled(project.root)
    if mass_required:
        require_mass(pulled)
    missing = set(project.spec.components) - pulled.parts.keys()
    if missing:
        raise PullError("cad_pull_missing", f"Parts absent from pulled CAD: {', '.join(sorted(missing))}")
    out = project.build_dir("cad/pulled-meshes")
    parts = {}
    for name, part in pulled.parts.items():
        mesh = world_mesh(pulled, name)
        pose = pulled.poses[name]
        # Hash identity and transform, never place an arbitrary part name in a path.
        key = hashlib.sha256((name + part.sha256['gltf' if part.gltf else 'stl'] + repr(pose.tolist())).encode()).hexdigest()
        path = out / f"{key}.stl"
        content = mesh.export(file_type="stl")
        if not path.exists() or path.read_bytes() != content:
            path.write_bytes(content)
        inertia = pose[:3, :3] @ np.asarray(part.inertia).reshape(3, 3) @ pose[:3, :3].T if part.inertia is not None else None
        parts[name] = {
            "part_name": name, "mesh": str(path.relative_to(project.build_dir("cad"))),
            "mesh_sha256": hashlib.sha256(content).hexdigest(), "geometry_source": "pulled",
            "mass": part.mass, "mass_status": "pulled", "material": part.material,
            "com": (pose[:3, :3] @ part.com + pose[:3, 3]).tolist() if part.com is not None else None,
            "inertia": [float(inertia[i, j]) for i, j in [(0, 0), (1, 1), (2, 2), (0, 1), (0, 2), (1, 2)]] if inertia is not None else None,
            "bbox_min": mesh.bounds[0].tolist(), "bbox_max": mesh.bounds[1].tolist(),
            "mesh_triangles": len(mesh.faces), "purchased": part.purchased,
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
    project.__dict__.pop("_graph", None)
    return result

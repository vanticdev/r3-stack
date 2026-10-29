"""Watertight print geometry of one part, in its own component frame (mm).

Sources, best first:

1. ``cad``: the Onshape tessellation ``build/cad/meshes/<cid>.stl``. The CAD stage
   writes it in the robot frame and in metres (``r3_onshape.sync.stl_bytes`` of the
   posed part), so the inverse of the compiled component pose brings it back into the
   component frame.
2. ``solids``: the catalog ``mechanical.solids`` (box, cylinder, prism, revolve,
   sphere; add/remove; ``"@name"`` profiles from the instance params), plus the holes
   and bores that the CAD generator cuts from the part's interfaces and features
   (``r3_onshape.parts._interface_ops`` / ``plan._feature_ops``). Booleans use
   manifold3d.
3. ``envelope``: the catalog box/cylinder envelope with the same cuts.

Mirrored instances (``params.mirror``) are mirrored like the CAD part frame
(``diag(mirror)``) and use mirrored interfaces.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import manifold3d as mf
import numpy as np
import trimesh

from r3_model.assembly import frame_matrix, mirrored_interface, ResolvedInterface
from r3_model.units import to_si

MM = 1e3  # m -> mm
SEGMENTS = 48  # circle facets of round solids
TIP_CLEARANCE_MM = 1.5  # drill depth beyond a blind tapped hole, as r3_onshape.parts.TIP_CLEARANCE


@dataclass
class PartGeometry:
    mesh: trimesh.Trimesh  # component frame, mm, watertight
    source: str  # cad | solids | envelope
    notes: list[str] = field(default_factory=list)
    holes: list[dict[str, Any]] = field(default_factory=list)  # cut holes: interface, kind, d_mm, centre/axis (mm)


# ----------------------------------------------------------------------------- manifold helpers


def _unit(v: Any) -> np.ndarray:
    a = np.asarray(v, dtype=float)
    return a / np.linalg.norm(a)


def _basis(axis: Any, u_axis: Any | None = None) -> np.ndarray:
    """Columns u, v, w with w = axis (v = w x u), as the CAD prism plane convention."""
    w = _unit(axis)
    if u_axis is not None:
        u = np.asarray(u_axis, dtype=float)
        u = u - w * float(u @ w)
    else:
        helper = np.array([1.0, 0.0, 0.0]) if abs(w[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        u = helper - w * float(helper @ w)
    u = u / np.linalg.norm(u)
    return np.column_stack([u, np.cross(w, u), w])


def _placed(solid: mf.Manifold, rotation: np.ndarray, origin_mm: np.ndarray) -> mf.Manifold:
    return solid.transform(np.column_stack([rotation, origin_mm]))


def _cylinder(base_mm: np.ndarray, axis: Any, diameter_mm: float, length_mm: float) -> mf.Manifold:
    body = mf.Manifold.cylinder(length_mm, diameter_mm / 2, diameter_mm / 2, SEGMENTS)
    return _placed(body, _basis(axis), base_mm)


def _profile(solid: Any, params: dict[str, Any]) -> list[list[float]]:
    profile = solid.profile
    if isinstance(profile, str):
        profile = params[profile.lstrip("@")]
    return [[to_si(q[0]) * MM, to_si(q[1]) * MM] for q in profile]


def _solid(solid: Any, params: dict[str, Any]) -> mf.Manifold:
    pos = np.asarray(solid.position, dtype=float) * MM
    if solid.kind == "box":
        size = np.asarray(solid.size, dtype=float) * MM
        return mf.Manifold.cube(size.tolist(), True).translate(pos.tolist())
    if solid.kind == "cylinder":
        return _cylinder(pos, solid.axis, solid.diameter * MM, solid.length * MM)
    if solid.kind == "sphere":
        return mf.Manifold.sphere(solid.diameter * MM / 2, SEGMENTS).translate(pos.tolist())
    if solid.kind == "prism":
        loops = [_profile(solid, params)] + [[[to_si(q[0]) * MM, to_si(q[1]) * MM] for q in loop] for loop in solid.inner]
        section = mf.CrossSection([np.asarray(loop) for loop in loops], mf.FillRule.EvenOdd)
        return _placed(section.extrude(solid.depth * MM), _basis(solid.axis, solid.u_axis), pos)
    if solid.kind == "revolve":
        rt = _profile(solid, params)  # [radius, distance along the axis]
        body = mf.CrossSection([np.asarray(rt)], mf.FillRule.EvenOdd).revolve(SEGMENTS)
        return _placed(body, _basis(solid.axis), pos)
    raise ValueError(f"unknown solid kind {solid.kind!r}")


def _envelope(mech: Any, params: dict[str, Any]) -> mf.Manifold:
    """The box/cylinder envelope (component-frame conventions of r3_onshape.plan._envelope_solids)."""
    size = [to_si(v) * MM for v in params["size"]] if "size" in params else [float(v) * MM for v in mech.size]
    if mech.shape == "cylinder":
        diameter, length = size[0], size[1]
        axis = {"x": [1.0, 0, 0], "y": [0, 1.0, 0], "z": [0, 0, 1.0]}[mech.axis]
        base = np.zeros(3) if mech.axis == "z" else -np.asarray(axis) * length / 2
        return _cylinder(base, axis, diameter, length)
    return mf.Manifold.cube(size, True).translate([0.0, 0.0, size[2] / 2])


def to_trimesh(solid: mf.Manifold) -> trimesh.Trimesh:
    mesh = solid.to_mesh()
    return trimesh.Trimesh(np.asarray(mesh.vert_properties)[:, :3], np.asarray(mesh.tri_verts), process=True)


def from_trimesh(mesh: trimesh.Trimesh) -> mf.Manifold:
    return mf.Manifold(mf.Mesh(np.asarray(mesh.vertices, dtype=np.float32), np.asarray(mesh.faces, dtype=np.uint32)))


# ----------------------------------------------------------------------------- interfaces and cuts


def part_interfaces(ctype: Any, mirror: list[float] | None) -> list[ResolvedInterface]:
    """All catalog interfaces of a part in its (mirrored) component frame (SI)."""
    out = []
    for name, spec in ctype.interfaces.items():
        iface = ResolvedInterface("", name, spec.kind, frame_matrix(spec.origin, spec.z, spec.x), dict(spec.params),
                                  ctype.mechanical.material or "")
        out.append(mirrored_interface(iface, mirror) if mirror else iface)
    return out


def _tap_drill_mm(catalog: Any, nominal: float) -> float:
    screw = catalog.fasteners.get(f"shcs-m{nominal * 1e3:g}")
    return screw.tap_drill * MM if screw else 0.85 * nominal * MM  # ISO 724 tap drill ~ d - P ~ 0.85 d for M2-M5


def interface_cuts(iface: ResolvedInterface, catalog: Any, reach_mm: float) -> list[tuple[mf.Manifold, dict[str, Any]]]:
    """Holes, bores and slots the CAD generator cuts for one interface (r3_onshape.parts._interface_ops)."""
    frame = iface.frame.copy()
    frame[:3, 3] *= MM
    origin, x_axis, y_axis, z_axis = frame[:3, 3], frame[:3, 0], frame[:3, 1], frame[:3, 2]
    cuts: list[tuple[mf.Manifold, dict[str, Any]]] = []
    if iface.kind in ("hole_pattern", "face_mount"):
        for hole in iface.holes:
            if hole.kind == "thread":
                continue  # a stud on this part, modeled by its solids
            point = origin + x_axis * hole.x * MM + y_axis * hole.y * MM
            if hole.kind == "clearance":
                d_mm, depth_mm = hole.d * MM, reach_mm  # through all along -z
            else:
                through = bool(iface.params.get("through_thread"))
                d_mm, depth_mm = _tap_drill_mm(catalog, hole.d), reach_mm if through else hole.depth * MM + TIP_CLEARANCE_MM
            start = point + z_axis * 0.01
            cuts.append((_cylinder(start, -z_axis, d_mm, depth_mm + 0.01),
                         {"interface": iface.name, "kind": hole.kind, "d_mm": d_mm, "nominal_mm": hole.d * MM,
                          "center_mm": point.tolist(), "axis": (-z_axis).tolist(), "depth_mm": hole.depth * MM}))
    elif iface.kind in ("bore", "spline") and (iface.kind == "bore" or iface.params.get("gender") == "female"):
        d = iface.quantity("d")
        depth = iface.quantity("depth")
        if d:
            depth_mm = depth * MM if depth else reach_mm
            cuts.append((_cylinder(origin + z_axis * 0.01, -z_axis, d * MM, depth_mm + 0.01),
                         {"interface": iface.name, "kind": "bore", "d_mm": d * MM, "center_mm": origin.tolist(),
                          "axis": (-z_axis).tolist(), "depth_mm": depth_mm}))
    elif iface.kind == "strap_slot":
        for slot in iface.params.get("slots", []):
            u0, v0, u1, v1 = (to_si(v) * MM for v in slot)
            center = origin + x_axis * (u0 + u1) / 2 + y_axis * (v0 + v1) / 2
            box = mf.Manifold.cube([abs(u1 - u0), abs(v1 - v0), 2 * reach_mm], True)
            cuts.append((_placed(box, frame[:3, :3], center), {"interface": iface.name, "kind": "slot", "center_mm": center.tolist()}))
    return cuts


def feature_ops(feature: Any, catalog: Any, mirror: np.ndarray) -> tuple[str, mf.Manifold] | None:
    """Catalog datasheet features realized on the part (r3_onshape.plan._feature_ops); D-shapes as round."""
    pos = mirror @ np.asarray(feature.position, dtype=float) * MM
    axis = mirror @ np.asarray(feature.axis, dtype=float)
    kind = feature.kind
    depth_mm = feature.depth * MM
    if kind in ("bore", "d_bore", "clearance_hole"):
        return "remove", _cylinder(pos, axis, feature.diameter * MM, depth_mm or 1e3)
    if kind == "tapped_hole" and feature.thread in catalog.fasteners:
        return "remove", _cylinder(pos, axis, catalog.fasteners[feature.thread].tap_drill * MM, depth_mm or 1e3)
    if kind in ("shaft", "d_shaft", "spline"):
        return "add", _cylinder(pos, axis, feature.diameter * MM, depth_mm)
    if kind in ("sphere", "socket"):
        return ("add" if kind == "sphere" else "remove"), mf.Manifold.sphere(feature.diameter * MM / 2, SEGMENTS).translate(pos.tolist())
    if kind == "pocket" and feature.size:
        along = _unit(mirror @ np.asarray(feature.slot_dir or [1.0, 0.0, 0.0], dtype=float))
        w = _unit(axis)
        rotation = np.column_stack([along, np.cross(w, along), w])
        size = np.asarray(feature.size, dtype=float) * MM
        box = mf.Manifold.cube(size.tolist(), True).translate([0.0, 0.0, size[2] / 2])
        return "remove", _placed(box, rotation, pos)
    return None


# ----------------------------------------------------------------------------- build


def catalog_geometry(ctype: Any, params: dict[str, Any], catalog: Any) -> PartGeometry:
    """Watertight mesh from the catalog solids (or envelope) with the interface and feature cuts."""
    from r3_catalog.schema import Solid

    mech = ctype.mechanical
    mirror_list = [float(v) for v in params["mirror"]] if params.get("mirror") is not None else None
    mirror = np.diag(mirror_list) if mirror_list else np.eye(3)
    extra = [Solid.model_validate(s) for s in params.get("solids", [])]
    solids = [*mech.solids, *extra]
    notes: list[str] = []
    body: mf.Manifold | None = None
    source = "solids" if solids else "envelope"
    if not solids:
        body = _envelope(mech, params)
    for solid in solids:
        piece = _solid(solid, params)
        if solid.op == "remove":
            body = body - piece if body is not None else None
        else:
            body = piece if body is None else body + piece
    if body is None or body.is_empty():
        body, source = _envelope(mech, params), "envelope"
        notes.append("catalog solids produced no material; used the envelope")
    if mirror_list:
        body = body.transform(np.column_stack([mirror, np.zeros(3)]))
    reach = float(np.linalg.norm(np.ptp(np.asarray(body.bounding_box()).reshape(2, 3), axis=0))) + 10.0
    holes: list[dict[str, Any]] = []
    for feature in mech.features:
        op = feature_ops(feature, catalog, mirror)
        if op is not None:
            body = body - op[1] if op[0] == "remove" else body + op[1]
    if mech.fabricated:
        for iface in part_interfaces(ctype, mirror_list):
            for cut, info in interface_cuts(iface, catalog, reach):
                body = body - cut
                holes.append(info)
    mesh = to_trimesh(body)
    return PartGeometry(mesh=mesh, source=source, notes=notes, holes=holes)


def cad_mesh_path(build_dir: Path, cid: str) -> Path | None:
    """The CAD stage's mesh of one component (cad_model.json ``mesh`` entry or the default name)."""
    cad_dir = build_dir / "cad"
    model_path = cad_dir / "cad_model.json"
    if model_path.exists():
        import json

        try:
            entry = json.loads(model_path.read_text()).get("parts", {}).get(cid)
        except (OSError, ValueError):
            entry = None
        if entry and entry.get("mesh") and (cad_dir / entry["mesh"]).exists():
            return cad_dir / entry["mesh"]
    path = cad_dir / "meshes" / (cid.replace(":", "__") + ".stl")
    return path if path.exists() else None


def cad_geometry(path: Path, pose_xyz: np.ndarray, pose_rotation: np.ndarray) -> trimesh.Trimesh | None:
    """CAD tessellation (robot frame, m) moved into the component frame (mm); None if unusable."""
    try:
        mesh = trimesh.load_mesh(path, process=True)
    except Exception:  # noqa: BLE001 - a corrupt mesh falls back to the catalog solids
        return None
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        return None
    inverse = np.eye(4)
    inverse[:3, :3] = pose_rotation.T
    inverse[:3, 3] = -pose_rotation.T @ pose_xyz
    mesh.apply_transform(inverse)
    mesh.apply_scale(MM)
    mesh.merge_vertices()
    if not mesh.is_watertight:
        trimesh.repair.fill_holes(mesh)
    if not mesh.is_watertight:
        return None
    trimesh.repair.fix_normals(mesh)
    return mesh


def bbox_mismatch(a: trimesh.Trimesh, b: trimesh.Trimesh) -> float:
    """Largest difference of bounding-box corners (mm) between two meshes in the same frame."""
    return float(np.max(np.abs(a.bounds - b.bounds)))


def build_geometry(project: Any, cid: str) -> PartGeometry:
    """Print geometry of one component: CAD mesh when present and consistent, else the catalog."""
    comp = project.spec.components[cid]
    ctype = project.catalog.component(comp.type)
    catalog_part = catalog_geometry(ctype, comp.params, project.catalog)
    path = cad_mesh_path(project.root / "build", cid)
    if path is None:
        return catalog_part
    xyz, rotation = project.graph.component_pose(cid)
    mesh = cad_geometry(path, xyz, rotation)
    if mesh is None:
        catalog_part.notes.append(f"CAD mesh {path.name} is not watertight; used the catalog {catalog_part.source}")
        return catalog_part
    mismatch = bbox_mismatch(mesh, catalog_part.mesh)
    tolerance = max(2.0, 0.1 * float(np.max(catalog_part.mesh.extents)))
    if mismatch > tolerance:
        catalog_part.notes.append(f"CAD mesh {path.name} differs from the catalog geometry by {mismatch:.1f} mm "
                                  f"(stale CAD export or a different pose); used the catalog {catalog_part.source}")
        return catalog_part
    return PartGeometry(mesh=mesh, source="cad", notes=[f"CAD tessellation {path.relative_to(project.root)}"],
                        holes=catalog_part.holes)


def rotation_from_euler_deg(angles: list[float]) -> np.ndarray:
    """R = Rz(rz) Ry(ry) Rx(rx), the spec rpy convention, from degrees."""
    from r3_model.graph import rpy_matrix

    return rpy_matrix([math.radians(float(a)) for a in angles])


def euler_deg_from_rotation(rotation: np.ndarray) -> list[float]:
    from r3_model.assembly import rpy_from_matrix

    return [round(math.degrees(a), 6) + 0.0 for a in rpy_from_matrix(rotation)]

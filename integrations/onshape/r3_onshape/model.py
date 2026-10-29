"""Exact part geometry imported from Onshape (no FeatureScript).

* ``bodydetails``: every face with its analytic surface (plane origin/normal,
  cylinder/cone/sphere origin/axis/radius), area, box and deterministic id.
* ``tessellatedfaces``: fine triangle meshes per face (chord <= 0.05 mm,
  angle <= 2 deg), welded into one closed mesh per part for manifold3d
  booleans (interference, contact, clearance, motion sweeps).

The same structures can be built from local part plans (offline preview) or
from recorded JSON fixtures (unit tests).
"""

from __future__ import annotations

import gzip
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np

CHORD_TOLERANCE = 5e-5  # m: max chordal deviation of the tessellation
ANGLE_TOLERANCE = math.radians(2.0)  # rad: max angle between facet normals across an edge


def _v(data: Any) -> np.ndarray:
    if isinstance(data, dict):
        return np.array([float(data["x"]), float(data["y"]), float(data["z"])])
    return np.asarray(data, dtype=float)


@dataclass
class Face:
    id: str
    part: str
    kind: str  # PLANE | CYLINDER | CONE | SPHERE | TORUS | OTHER
    origin: np.ndarray
    direction: np.ndarray  # plane: outward normal; cylinder/cone: axis (unit); sphere: zeros
    radius: float
    area: float
    lo: np.ndarray
    hi: np.ndarray
    convex: bool | None = None  # cylinders/spheres: True = shaft/ball, False = bore/socket
    triangles: np.ndarray | None = None  # (n, 3, 3) facets of this face


@dataclass
class PartGeometry:
    name: str
    part_id: str
    faces: list[Face] = field(default_factory=list)
    vertices: np.ndarray | None = None
    triangles: np.ndarray | None = None  # (m, 3) vertex indices
    _manifold: Any = None

    @property
    def lo(self) -> np.ndarray:
        return self.vertices.min(axis=0)

    @property
    def hi(self) -> np.ndarray:
        return self.vertices.max(axis=0)

    def manifold(self):
        """Closed manifold3d solid of the welded tessellation."""
        if self._manifold is None:
            import manifold3d as m3d

            mesh = m3d.Mesh(vert_properties=np.asarray(self.vertices, dtype=np.float32),
                            tri_verts=np.asarray(self.triangles, dtype=np.uint32))
            solid = m3d.Manifold(mesh)
            if solid.status() != m3d.Error.NoError or solid.is_empty():
                raise ValueError(f"{self.name}: tessellation is not a closed manifold ({solid.status()})")
            self._manifold = solid
        return self._manifold

    def faces_of(self, kind: str) -> list[Face]:
        return [f for f in self.faces if f.kind == kind]


@dataclass
class GeometryModel:
    parts: dict[str, PartGeometry]
    microversion: str = ""
    chord_tolerance: float = CHORD_TOLERANCE
    angle_tolerance: float = ANGLE_TOLERANCE
    source: str = "onshape"

    def __getitem__(self, name: str) -> PartGeometry:
        return self.parts[name]

    def __contains__(self, name: str) -> bool:
        return name in self.parts


# ---------------------------------------------------------------- construction
def _weld(triangles: np.ndarray, tolerance: float = 1e-8) -> tuple[np.ndarray, np.ndarray]:
    """Merge coincident vertices of a triangle soup (face tessellations share
    their edge vertices) into an indexed mesh."""
    flat = triangles.reshape(-1, 3)
    keys = np.round(flat / tolerance).astype(np.int64)
    _, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    tri = inverse.reshape(-1, 3)
    keep = (tri[:, 0] != tri[:, 1]) & (tri[:, 1] != tri[:, 2]) & (tri[:, 0] != tri[:, 2])
    return flat[first], tri[keep]


def _face_from_bodydetails(part: str, face: dict[str, Any]) -> Face:
    surface = face.get("surface") or {}
    kind = str(surface.get("type", "OTHER")).upper()
    origin = _v(surface["origin"]) if "origin" in surface else np.zeros(3)
    radius = float(surface.get("radius", 0.0) or 0.0)
    oriented = bool(face.get("orientation", True))
    if kind == "PLANE":
        direction = _v(surface["normal"]) * (1.0 if oriented else -1.0)
    elif kind in ("CYLINDER", "CONE"):
        direction = _v(surface["axis"])
    else:
        direction = np.zeros(3)
    box = face.get("box") or {}
    lo = _v(box["minCorner"]) if "minCorner" in box else np.zeros(3)
    hi = _v(box["maxCorner"]) if "maxCorner" in box else np.zeros(3)
    out = Face(id=face["id"], part=part, kind=kind if kind in ("PLANE", "CYLINDER", "CONE", "SPHERE", "TORUS") else "OTHER",
               origin=origin, direction=direction / (np.linalg.norm(direction) or 1.0), radius=radius,
               area=float(face.get("area", 0.0) or 0.0), lo=lo, hi=hi)
    if kind in ("CYLINDER", "SPHERE"):
        # Parasolid surface normals point away from the axis/center; the face is
        # convex (shaft/ball) when its orientation agrees with the surface normal.
        out.convex = oriented
    return out


def part_from_onshape(name: str, part_id: str, body: dict[str, Any], tessellation: dict[str, Any]) -> PartGeometry:
    faces = {f["id"]: _face_from_bodydetails(name, f) for f in body.get("faces", [])}
    soup: list[np.ndarray] = []
    for tbody in tessellation.get("bodies", []) if isinstance(tessellation, dict) else tessellation:
        for tface in tbody.get("faces", []):
            tris = np.array([[_v(v) for v in facet["vertices"]] for facet in tface.get("facets", []) if len(facet.get("vertices", [])) == 3])
            if tris.size == 0:
                continue
            soup.append(tris)
            if tface.get("id") in faces:
                faces[tface["id"]].triangles = tris
    triangles = np.concatenate(soup) if soup else np.zeros((0, 3, 3))
    geometry = PartGeometry(name=name, part_id=part_id, faces=list(faces.values()))
    geometry.vertices, geometry.triangles = _weld(triangles)
    _orient_faces_from_mesh(geometry)
    return geometry


def _orient_faces_from_mesh(part: PartGeometry) -> None:
    """Cross-check face orientation with the outward facet normals of the mesh
    (tessellations are wound outward): fixes plane normals and convexity."""
    for face in part.faces:
        if face.triangles is None or len(face.triangles) == 0:
            continue
        t = face.triangles
        normals = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        weights = np.linalg.norm(normals, axis=1)
        if weights.sum() == 0:
            continue
        centers = t.mean(axis=1)
        if face.kind == "PLANE":
            mean = (normals).sum(axis=0)
            if np.linalg.norm(mean) > 0 and float(mean @ face.direction) < 0:
                face.direction = -face.direction
        elif face.kind == "CYLINDER":
            a = face.direction
            radial = (centers - face.origin) - np.outer((centers - face.origin) @ a, a)
            face.convex = bool(float(np.sum(np.einsum("ij,ij->i", normals, radial))) > 0)
        elif face.kind == "SPHERE":
            face.convex = bool(float(np.sum(np.einsum("ij,ij->i", normals, centers - face.origin))) > 0)


def fetch_geometry(api: Any, did: str, wid: str, eid: str, part_ids: dict[str, str], fixture_dir: Path | None = None) -> GeometryModel:
    """Read bodydetails (one call) and per-part fine tessellations from Onshape."""
    base = f"/partstudios/d/{did}/w/{wid}/e/{eid}"
    details = api.json("GET", base + "/bodydetails")
    bodies = {b["id"]: b for b in details.get("bodies", [])}
    microversion = ((details.get("microversionId") or {}).get("theId")) or ""
    parts: dict[str, PartGeometry] = {}
    raw: dict[str, Any] = {"microversion": microversion, "parts": {}}
    for name, pid in part_ids.items():
        tess = api.json("GET", base + "/tessellatedfaces", query={"partId": pid, "chordTolerance": CHORD_TOLERANCE,
                                                                   "angleTolerance": ANGLE_TOLERANCE, "outputFaceAppearances": False})
        parts[name] = part_from_onshape(name, pid, bodies.get(pid, {}), tess)
        raw["parts"][name] = {"part_id": pid, "body": bodies.get(pid, {}), "tessellation": tess}
    if fixture_dir is not None:
        fixture_dir.mkdir(parents=True, exist_ok=True)
        with gzip.open(fixture_dir / "onshape_geometry.json.gz", "wt") as handle:
            json.dump(raw, handle)
    return GeometryModel(parts=parts, microversion=microversion)


def load_fixture(path: Path, names: Iterable[str] | None = None) -> GeometryModel:
    with gzip.open(path, "rt") as handle:
        raw = json.load(handle)
    wanted = set(names) if names is not None else None
    parts = {name: part_from_onshape(name, item["part_id"], item["body"], item["tessellation"])
             for name, item in raw["parts"].items() if wanted is None or name in wanted}
    return GeometryModel(parts=parts, microversion=raw.get("microversion", ""), source="fixture")


def save_subset_fixture(source: Path, target: Path, names: Iterable[str]) -> None:
    """Copy a few parts of a recorded fixture (for compact unit-test data)."""
    with gzip.open(source, "rt") as handle:
        raw = json.load(handle)
    subset = {"microversion": raw.get("microversion", ""), "parts": {n: raw["parts"][n] for n in names if n in raw["parts"]}}
    target.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(target, "wt") as handle:
        json.dump(subset, handle)


def load_local_fixture(path: Path, names: Iterable[str] | None = None) -> GeometryModel:
    """A phase B recording (``onshape_local_geometry.json.gz``): each part's Part
    Studio geometry once, plus every instance's solved robot-frame transform."""
    from .organized import transform_part

    with gzip.open(path, "rt") as handle:
        raw = json.load(handle)
    wanted = set(names) if names is not None else None
    local = {key: part_from_onshape(key, item["part_id"], item["body"], item["tessellation"]) for key, item in raw["parts"].items()}
    parts = {}
    for iid, (key, matrix) in raw["placements"].items():
        if (wanted is None or iid in wanted) and key in local:
            parts[iid] = transform_part(local[key], np.asarray(matrix, dtype=float), iid)
    return GeometryModel(parts=parts, microversion=raw.get("microversion", ""), source="fixture")


def save_local_subset(source: Path, target: Path, instances: Iterable[str]) -> None:
    """Copy the parts and placements of a few instances (compact unit-test data)."""
    with gzip.open(source, "rt") as handle:
        raw = json.load(handle)
    placements = {iid: raw["placements"][iid] for iid in instances if iid in raw["placements"]}
    keys = {key for key, _ in placements.values()}
    subset = {"parts": {k: v for k, v in raw["parts"].items() if k in keys}, "placements": placements}
    target.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(target, "wt") as handle:
        json.dump(subset, handle)

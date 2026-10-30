"""3D models: resolution, vendor download, STEP -> GLB and posing.

Sources (``r3_catalog.schema.ModelRef.source``):

* ``kicad``: the official KiCad 3D library of the local KiCad install.
* ``vendor``: a manufacturer STEP archive from ``url``, verified against its
  pinned ``sha256`` and cached in ``$R3_MODEL_CACHE`` (default
  ``~/.cache/r3/models``). Archives are untrusted data: only the named member
  and licence files are extracted, by name, into a fresh directory.
* ``kicad_board``: the STEP exported by the KiCad stage (``build/...``).
* ``proxy``: ``r3_connectors.proxy`` bodies from datasheet dimensions.

STEP files are tessellated with cascadio (OpenCASCADE). Output GLBs are in
metres, posed in their owner frame (connector frame or component frame), and
deterministic: the same inputs give byte-identical files.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import trimesh

from r3_catalog.schema import Catalog, ConnectorGeometry, ModelRef

from .frames import Frame
from .kicad import resolve_model_path
from .proxy import build_proxy

if TYPE_CHECKING:  # pragma: no cover
    from r3_model.project import Project

# Tessellation tolerances: connectors are small, boards large (deflection, m; angle, rad).
CONNECTOR_TOLERANCE = (5e-6, 0.3)  # 5 um chordal deviation
BOARD_TOLERANCE = (4e-5, 0.5)  # 40 um
CASCADIO_TAG = "cascadio-0.1"


class ModelUnavailable(RuntimeError):
    pass


def cache_dir() -> Path:
    return Path(os.environ.get("R3_MODEL_CACHE") or Path.home() / ".cache" / "r3" / "models")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_vendor(ref: ModelRef, timeout: float = 120.0) -> Path:
    """Download (once) and verify a vendor archive; return the STEP member path."""
    if not ref.url or not ref.sha256:
        raise ModelUnavailable("vendor model needs url and sha256")
    root = cache_dir() / "vendor" / ref.sha256
    member = root / Path(ref.path).name
    if member.exists():
        return member
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root.parent) as tmp:
        archive = Path(tmp) / "download"
        try:
            # Some vendor CDNs reject the default Python user agent.
            request = urllib.request.Request(ref.url, headers={"User-Agent": "r3-model-fetch/1.0"})
            with urllib.request.urlopen(request, timeout=timeout) as response, open(archive, "wb") as out:
                shutil.copyfileobj(response, out)
        except OSError as error:
            raise ModelUnavailable(f"download failed: {ref.url}: {error}") from None
        actual = _sha256(archive)
        if actual != ref.sha256:
            raise ModelUnavailable(f"sha256 mismatch for {ref.url}: {actual} != {ref.sha256}")
        staging = Path(tmp) / "out"
        staging.mkdir()
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as zf:
                wanted = {Path(ref.path).name, "LICENSE.txt", "LICENSE", "LICENCE.txt"}
                for info in zf.infolist():
                    name = Path(info.filename).name
                    if name in wanted and not info.is_dir():
                        (staging / name).write_bytes(zf.read(info))
        else:
            shutil.copy2(archive, staging / Path(ref.path).name)
        if not (staging / Path(ref.path).name).exists():
            raise ModelUnavailable(f"{ref.path} not found in {ref.url}")
        staging.rename(root)
    return member


def resolve_step(ref: ModelRef, project: "Project | None" = None) -> Path:
    if ref.params.get("bank_id"):
        from r3_catalog.modelbank import ModelBank
        from r3_catalog.modelbank.fetch import acquire, unpack
        entry = ModelBank().get(ref.params["bank_id"])
        errors = []
        for source in entry.sources:
            if source.format not in ("step", "stp", "stp.zip") or source.kind == "onshape_public":
                continue
            try:
                path, _ = acquire(source)
                return unpack(path, source)
            except (OSError, ValueError) as error:
                errors.append(str(error))
        raise ModelUnavailable(f"{entry.id}: {'; '.join(errors)}")
    if ref.source == "kicad":
        path = resolve_model_path(ref.path)
    elif ref.source == "vendor":
        path = fetch_vendor(ref)
    elif ref.source == "kicad_board":
        if project is None:
            raise ModelUnavailable("kicad_board model needs the project")
        path = project.root / ref.path
    else:
        raise ModelUnavailable(f"{ref.source} has no STEP file")
    if not path.exists():
        raise ModelUnavailable(f"model file not found: {path}")
    return path


def step_to_glb(step: Path, tolerance: tuple[float, float]) -> Path:
    """cascadio tessellation of ``step`` (model frame, metres), cached by content hash."""
    import cascadio

    key = hashlib.sha256(f"{_sha256(step)}:{tolerance}:{CASCADIO_TAG}".encode()).hexdigest()[:24]
    out = cache_dir() / "glb" / f"{key}.glb"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp.glb")
        # STEP models here are in millimetres; cascadio takes the deflection in model units.
        cascadio.step_to_glb(str(step), str(tmp), tol_linear=tolerance[0] * 1e3, tol_angular=tolerance[1],
                             include_materials=True, use_parallel=False)
        tmp.rename(out)
    return out


def _stable_meshes(scene: trimesh.Scene) -> list[tuple[str, trimesh.Trimesh]]:
    """Scene geometry in scene coordinates, in a content-defined order with
    deterministic names (trimesh suffixes duplicate glTF names randomly)."""
    items = []
    for node in scene.graph.nodes_geometry:
        transform, geometry_name = scene.graph[node]
        mesh = scene.geometry[geometry_name].copy()
        mesh.apply_transform(transform)
        base = str(mesh.metadata.get("name") or geometry_name)
        head, _, suffix = base.rpartition("_")
        if head and len(suffix) == 6 and all(c in "0123456789abcdef" for c in suffix):
            base = head  # trimesh's random de-duplication suffix
        key = hashlib.sha256(np.round(mesh.vertices, 9).tobytes() + np.asarray(mesh.faces).tobytes()).hexdigest()
        items.append((base, key, mesh))
    items.sort(key=lambda t: (t[0], t[1]))
    return [(f"{i:04d}_{base}"[:64], mesh) for i, (base, _, mesh) in enumerate(items)]


def _posed(scene: trimesh.Scene, frame: Frame) -> trimesh.Scene:
    """Bake ``frame`` (model frame in the owner frame) into a fresh single-level scene."""
    out = trimesh.Scene()
    for name, mesh in _stable_meshes(scene):
        mesh.apply_transform(frame.matrix())
        mesh.metadata = {"name": name}
        out.add_geometry(mesh, node_name=name, geom_name=name)
    return out


def export_glb(scene: trimesh.Scene, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = trimesh.exchange.gltf.export_glb(scene, include_normals=True)
    path.write_bytes(data)
    return path


def _color(hex_color: str) -> list[int]:
    h = hex_color.lstrip("#")
    return [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255]


def proxy_scene(geometry: ConnectorGeometry) -> trimesh.Scene:
    scene = trimesh.Scene()
    for name, mesh in sorted(build_proxy(geometry).items()):
        mesh = mesh.copy()
        color = geometry.color if name == "body" else geometry.contact_color
        mesh.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
            name=name, baseColorFactor=_color(color), metallicFactor=0.0 if name == "body" else 0.8,
            roughnessFactor=0.6 if name == "body" else 0.35))
        scene.add_geometry(mesh, node_name=name, geom_name=name)
    return scene


def connector_scene(catalog: Catalog, connector_type: str) -> trimesh.Scene:
    """The connector type's model in its connector frame (metres)."""
    geometry = catalog.connector(connector_type).geometry
    if geometry is None:
        raise ModelUnavailable(f"{connector_type} has no geometry")
    if geometry.model.source == "proxy":
        return proxy_scene(geometry)
    # A vendor assembly may be the only published model of its connector. Keep
    # its real surfaces, selected in the vendor frame; never replace them with
    # a sculpted purchased-part proxy. Bounds are SI, before model placement.
    extraction = geometry.model.params.get("extract_bounds")
    glb = step_to_glb(resolve_step(geometry.model), BOARD_TOLERANCE if extraction else CONNECTOR_TOLERANCE)
    scene = trimesh.load(glb, force="scene")
    if extraction:
        selected = trimesh.Scene()
        lo, hi = np.asarray(extraction)
        for node in scene.graph.nodes_geometry:
            pose, name = scene.graph[node]
            mesh = scene.geometry[name].copy()
            mesh.apply_transform(pose)
            if np.any(mesh.bounds[1] < lo) or np.any(mesh.bounds[0] > hi):
                continue
            for axis in range(3):
                normal = np.eye(3)[axis]
                mesh = mesh.slice_plane(lo, normal)
                mesh = mesh.slice_plane(hi, -normal)
            if len(mesh.faces):
                selected.add_geometry(mesh, node_name=node)
        if not selected.geometry:
            raise ModelUnavailable(f"{connector_type}: vendor extraction contains no geometry")
        scene = selected
    return _posed(scene, Frame.from_placement(geometry.model.placement))


# KiCad's STEP export carries copper (tracks, zones, pads) but no solder mask: copper is shown under a
# green mask, the way the assembled board looks.
MASKED_COPPER = ("_copper", "_via")
MASK_COLOR = [47, 122, 62, 255]


def component_scene(ref: ModelRef, project: "Project | None" = None) -> trimesh.Scene:
    """A component's detailed model in its component frame (metres)."""
    glb = step_to_glb(resolve_step(ref, project), BOARD_TOLERANCE)
    scene = _posed(trimesh.load(glb, force="scene"), Frame.from_placement(ref.placement))
    if ref.source == "kicad_board":
        for name in scene.graph.nodes_geometry:
            if any(key in name for key in MASKED_COPPER):
                mesh = scene.geometry[scene.graph[name][1]]
                mesh.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
                    name="masked_copper", baseColorFactor=MASK_COLOR, metallicFactor=0.1, roughnessFactor=0.45))
    return scene


def scene_meshes(scene: trimesh.Scene) -> list[tuple[str, trimesh.Trimesh, list[int]]]:
    """(name, mesh in scene coordinates, RGBA) for every geometry node."""
    out = []
    for node in sorted(scene.graph.nodes_geometry):
        transform, name = scene.graph[node]
        mesh = scene.geometry[name].copy()
        mesh.apply_transform(transform)
        color = [160, 160, 160, 255]
        material = getattr(mesh.visual, "material", None)
        factor = getattr(material, "baseColorFactor", None)
        if factor is not None:
            color = [int(v) for v in np.asarray(factor).ravel()[:4]]
        elif hasattr(mesh.visual, "face_colors") and len(mesh.visual.face_colors):
            color = [int(v) for v in mesh.visual.face_colors[0][:4]]
        out.append((node, mesh, color))
    return out


def glb_digest(path: Path) -> str:
    return _sha256(path)


__all__ = ["BOARD_TOLERANCE", "CONNECTOR_TOLERANCE", "ModelUnavailable", "cache_dir", "component_scene",
           "connector_scene", "export_glb", "fetch_vendor", "glb_digest", "proxy_scene", "resolve_step",
           "scene_meshes", "step_to_glb"]


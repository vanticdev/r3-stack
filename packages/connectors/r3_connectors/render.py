"""Headless close-up renders with MuJoCo's offscreen renderer.

Meshes (connector, Pi and board models) are merged per colour into static
world geoms of an ``mujoco.MjSpec`` scene; wires are capsule chains along their
routed paths. Labels are projected with MuJoCo's own camera frustum and drawn
with PIL. On Linux servers set ``MUJOCO_GL=egl`` (or ``osmesa``); macOS uses CGL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import trimesh

from .frames import Frame
from .models import scene_meshes

WIRE_COLORS = {"red": "#d7261e", "black": "#1b1b1b", "white": "#efefef", "blue": "#1f5fd6", "yellow": "#f2c40f",
               "green": "#1d9a3c", "orange": "#ef7d1a", "brown": "#7b4a21", "purple": "#7a3fb0", "grey": "#8a8a8a",
               "gray": "#8a8a8a"}


def rgba(color: str | Sequence[float], alpha: float = 1.0) -> list[float]:
    if isinstance(color, str):
        h = WIRE_COLORS.get(color, color).lstrip("#")
        return [int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, alpha]
    c = list(color)
    if max(c[:3]) > 1.0:
        c = [v / 255 for v in c[:3]] + [c[3] / 255 if len(c) > 3 else 1.0]
    return [*c[:3], alpha if len(c) < 4 else c[3] * alpha]


@dataclass
class Label:
    position: np.ndarray
    text: str
    color: str = "#ffffff"


@dataclass
class RenderScene:
    meshes: dict[tuple, list[trimesh.Trimesh]] = field(default_factory=dict)
    capsules: list[tuple[np.ndarray, np.ndarray, float, list[float]]] = field(default_factory=list)
    spheres: list[tuple[np.ndarray, float, list[float]]] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)

    def add_scene(self, scene: trimesh.Scene, frame: Frame, alpha: float = 1.0, clip: tuple | None = None,
                  recolor: dict[str, str] | None = None) -> None:
        """Add a posed model; ``clip`` = (lo, hi) world box keeps only nearby meshes (close-ups of big models);
        ``recolor`` maps a node-name substring to a colour."""
        for name, mesh, color in scene_meshes(scene):
            for key, value in (recolor or {}).items():
                if key in name:
                    color = value
            mesh.apply_transform(frame.matrix())
            if clip is not None:
                lo, hi = mesh.bounds
                if np.any(hi < clip[0]) or np.any(lo > clip[1]):
                    continue
            key = tuple(np.round(rgba(color, alpha), 3))
            self.meshes.setdefault(key, []).append(mesh)

    def add_mesh(self, mesh: trimesh.Trimesh, color: str | Sequence[float], alpha: float = 1.0) -> None:
        self.meshes.setdefault(tuple(np.round(rgba(color, alpha), 3)), []).append(mesh)

    def add_wire(self, path: np.ndarray, radius: float, color: str) -> None:
        path = np.asarray(path, dtype=float)
        for a, b in zip(path[:-1], path[1:]):
            if np.linalg.norm(b - a) > 1e-7:
                self.capsules.append((a, b, radius, rgba(color)))

    def add_marker(self, position: np.ndarray, radius: float, color: str, alpha: float = 1.0) -> None:
        self.spheres.append((np.asarray(position, dtype=float), radius, rgba(color, alpha)))

    def label(self, position: np.ndarray, text: str, color: str = "#ffffff") -> None:
        self.labels.append(Label(np.asarray(position, dtype=float), text, color))


def _spec(scene: RenderScene, extent: float):
    import mujoco

    spec = mujoco.MjSpec()
    spec.compiler.degree = False
    spec.visual.global_.offwidth = 2400
    spec.visual.global_.offheight = 1800
    spec.visual.map.znear = 0.0005
    spec.visual.quality.shadowsize = 4096
    spec.visual.headlight.ambient = [0.35, 0.35, 0.35]
    spec.visual.headlight.diffuse = [0.45, 0.45, 0.45]
    spec.visual.headlight.specular = [0.1, 0.1, 0.1]
    spec.stat.extent = extent
    world = spec.worldbody
    for i, (direction, diffuse) in enumerate((([-0.4, 0.3, -1.0], 0.55), ([0.6, -0.5, -0.4], 0.3))):
        light = world.add_light()
        light.type = mujoco.mjtLightType.mjLIGHT_DIRECTIONAL
        light.dir = direction
        light.diffuse = [diffuse] * 3
        light.castshadow = i == 0
    for index, (color, meshes) in enumerate(sorted(scene.meshes.items())):
        merged = trimesh.util.concatenate(meshes)
        if len(merged.faces) == 0:
            continue
        mesh = spec.add_mesh()
        mesh.name = f"m{index}"
        mesh.uservert = np.asarray(merged.vertices, dtype=np.float32).ravel().tolist()
        mesh.userface = np.asarray(merged.faces, dtype=np.int32).ravel().tolist()
        mesh.inertia = mujoco.mjtMeshInertia.mjMESH_INERTIA_SHELL
        geom = world.add_geom()
        geom.type = mujoco.mjtGeom.mjGEOM_MESH
        geom.meshname = mesh.name
        geom.rgba = list(color)
        geom.contype = geom.conaffinity = 0
    for a, b, radius, color in scene.capsules:
        geom = world.add_geom()
        geom.type = mujoco.mjtGeom.mjGEOM_CAPSULE
        geom.fromto = [*a, *b]
        geom.size = [radius, 0, 0]
        geom.rgba = color
        geom.contype = geom.conaffinity = 0
    for position, radius, color in scene.spheres:
        geom = world.add_geom()
        geom.type = mujoco.mjtGeom.mjGEOM_SPHERE
        geom.pos = list(position)
        geom.size = [radius, 0, 0]
        geom.rgba = color
        geom.contype = geom.conaffinity = 0
    return spec


def _project(scn, point: np.ndarray, width: int, height: int) -> tuple[float, float] | None:
    """Pixel position of ``point`` under MuJoCo's mono camera: the mean of the two
    stereo eyes, frustum height from the scene camera and width from the aspect
    (``frustum_width = 0`` means "from the viewport")."""
    left_eye, right_eye = scn.camera[0], scn.camera[1]
    pos = (np.asarray(left_eye.pos) + np.asarray(right_eye.pos)) / 2
    forward, up = np.asarray(left_eye.forward), np.asarray(left_eye.up)
    right = np.cross(forward, up)
    d = point - pos
    z = d @ forward
    if z <= 1e-9:
        return None
    near = left_eye.frustum_near
    half_h = (left_eye.frustum_top - left_eye.frustum_bottom) / 2
    half_w = left_eye.frustum_width or half_h * width / height
    x_near, y_near = near * (d @ right) / z, near * (d @ up) / z
    u = (x_near - (left_eye.frustum_center - half_w)) / (2 * half_w) * width
    v = (left_eye.frustum_top - y_near) / (2 * half_h) * height
    return float(u), float(v)


def render(scene: RenderScene, out: Path, lookat: Iterable[float], distance: float, azimuth: float,
           elevation: float, title: str = "", subtitle: str = "", size: tuple[int, int] = (1600, 1200)) -> Path:
    import mujoco
    from PIL import Image, ImageDraw, ImageFont

    spec = _spec(scene, max(distance, 0.01))
    model = spec.compile()
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    width, height = size
    with mujoco.Renderer(model, height, width) as renderer:
        camera = mujoco.MjvCamera()
        camera.type = mujoco.mjtCamera.mjCAMERA_FREE
        camera.lookat[:] = list(lookat)
        camera.distance = distance
        camera.azimuth = azimuth
        camera.elevation = elevation
        option = mujoco.MjvOption()
        renderer.update_scene(data, camera, option)
        renderer.scene.flags[mujoco.mjtRndFlag.mjRND_SHADOW] = True
        renderer.scene.flags[mujoco.mjtRndFlag.mjRND_SKYBOX] = False
        pixels = renderer.render()
        projected = [(_project(renderer.scene, lab.position, width, height), lab) for lab in scene.labels]
    image = Image.fromarray(pixels)
    background = Image.new("RGB", image.size, (236, 238, 241))
    # MuJoCo clears to black with the skybox off: composite the render over a light background.
    mask = Image.fromarray((pixels.astype(int).sum(axis=2) > 3).astype(np.uint8) * 255)
    background.paste(image, mask=mask)
    image = background
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 22)
        small = ImageFont.truetype("DejaVuSans.ttf", 17)
    except OSError:
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 22)
            small = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 17)
        except OSError:
            font = small = ImageFont.load_default()
    placed: list[tuple[float, float, float, float]] = []
    for uv, lab in sorted(((p, l) for p, l in projected if p is not None), key=lambda t: t[0][1]):
        u, v = uv
        if not (0 <= u <= width and 0 <= v <= height):
            continue
        tw = draw.textlength(lab.text, font=small)
        x, y = u + 14, v - 30
        for _ in range(40):  # nudge up/down to avoid overlapping labels
            box = (x - 4, y - 2, x + tw + 4, y + 21)
            if not any(box[0] < b[2] and box[2] > b[0] and box[1] < b[3] and box[3] > b[1] for b in placed):
                break
            y -= 24
        x = min(x, width - tw - 8)
        placed.append((x - 4, y - 2, x + tw + 4, y + 21))
        draw.line([(u, v), (x - 4, y + 10)], fill=(40, 40, 40), width=2)
        draw.ellipse([u - 4, v - 4, u + 4, v + 4], outline=(20, 20, 20), width=2)
        draw.rectangle((x - 4, y - 2, x + tw + 4, y + 21), fill=(255, 255, 255), outline=(60, 60, 60))
        draw.text((x, y), lab.text, fill=(15, 15, 15), font=small)
    if title:
        draw.rectangle((0, 0, width, 68 if subtitle else 40), fill=(28, 32, 38))
        draw.text((16, 8), title, fill=(255, 255, 255), font=font)
        if subtitle:
            draw.text((16, 38), subtitle, fill=(200, 205, 212), font=small)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    return out


__all__ = ["Label", "RenderScene", "WIRE_COLORS", "render", "rgba"]

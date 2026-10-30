"""What the robot's cameras see: ground texture, track markings, cones, and the
MuJoCo cameras of camera components.

Configured in ``simulation.environment.visual`` (all keys optional)::

    visual:
      ground: asphalt            # grass (default) | asphalt | concrete | checker
      size: [40, 40]             # textured area (m) centred on the track; default: track bounds + margin
      pixels_per_meter: 60       # texture resolution (capped at 4096 px per side)
      track:
        from: mission            # use the scenario waypoints (default when `points` is absent)
        points: [[0, 0], [10, 0], [10, 6], [0, 6]]   # or an explicit centreline (m)
        closed: true
        width: 0.9               # lane width between the edge lines (m)
        line_width: 0.05         # painted edge-line width (m)
        edge_color: white
        center_line: {color: yellow, dash: 0.3, width: 0.04}   # or false
      cones:                     # explicit cones
        - {position: [5, 1.2], color: orange}
      cone_spacing: 3.0          # or cones every N m along both track edges
      cone_collide: false        # cones are visual only unless true

The ground keeps MuJoCo's infinite collision plane (planes collide as infinite
planes; ``size`` only bounds rendering), so physics is unchanged by the
texture. Cones are visual only by default.

Camera components (catalog behaviour model ``camera.*``) get a MuJoCo
``<camera>`` on the body that carries them, at the lens position with the
catalog field of view. MuJoCo cameras look along their local -z with +y up;
the catalog gives the optical axis (``optical_axis``, default +z) and the
image-up direction (``image_up``, default +y) in the component frame.
"""

from __future__ import annotations

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np

from r3_model.project import Project
from r3_model.units import to_si

from .geometry import rotation_to_quat

GROUND_COLORS = {
    "grass": ((84, 128, 66), (74, 115, 59)),
    "asphalt": ((58, 60, 63), (52, 54, 57)),
    "concrete": ((150, 148, 142), (140, 138, 132)),
    "checker": ((200, 200, 200), (90, 90, 90)),
}
NAMED_COLORS = {
    "white": (240, 240, 236), "yellow": (238, 196, 40), "orange": (255, 110, 20), "red": (210, 40, 30),
    "blue": (30, 90, 220), "green": (40, 170, 70), "black": (20, 20, 20),
}
CONE_HEIGHT = 0.18  # m, a typical small training cone (estimate)
CONE_BASE = 0.12    # m


def camera_name(component: str) -> str:
    return f"cam_{component}"


def _rgb(value: Any, default: str) -> tuple[int, int, int]:
    if value is None:
        value = default
    if isinstance(value, str):
        if value.startswith("#") and len(value) == 7:
            return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
        return NAMED_COLORS.get(value, NAMED_COLORS[default])
    return tuple(int(c) for c in value[:3])  # type: ignore[return-value]


def _rgba(rgb: tuple[int, int, int]) -> str:
    return " ".join(f"{c / 255:.4g}" for c in rgb) + " 1"


def track_centerline(visual: dict[str, Any], course: list[list[float]] | None) -> tuple[np.ndarray, bool] | None:
    """Track centreline (n, 2) in world metres and whether it is closed, or None."""
    track = visual.get("track")
    if not track:
        return None
    track = track if isinstance(track, dict) else {}
    points = track.get("points")
    if points is None and track.get("from", "mission") == "mission":
        points = [p[:2] for p in course or []]
    if not points or len(points) < 2:
        return None
    line = np.asarray([[to_si(p[0]), to_si(p[1])] for p in points], dtype=float)
    return line, bool(track.get("closed", True))


def cone_positions(visual: dict[str, Any], centerline: tuple[np.ndarray, bool] | None) -> list[tuple[np.ndarray, tuple[int, int, int]]]:
    cones = [(np.asarray([to_si(c["position"][0]), to_si(c["position"][1])]), _rgb(c.get("color"), "orange"))
             for c in visual.get("cones") or []]
    spacing = visual.get("cone_spacing")
    if spacing and centerline is not None:
        line, closed = centerline
        track = visual.get("track") if isinstance(visual.get("track"), dict) else {}
        offset = to_si(track.get("width", 0.9)) / 2 + to_si(track.get("line_width", 0.05)) + 0.15
        pts = np.vstack([line, line[:1]]) if closed else line
        step = to_si(spacing)
        carry = 0.0
        for a, b in zip(pts[:-1], pts[1:]):
            seg = b - a
            length = float(np.hypot(*seg))
            if length < 1e-9:
                continue
            normal = np.array([-seg[1], seg[0]]) / length
            s = carry
            while s < length:
                p = a + seg * (s / length)
                cones.append((p + normal * offset, NAMED_COLORS["orange"]))
                cones.append((p - normal * offset, NAMED_COLORS["blue"]))
                s += step
            carry = s - length
    return cones


def _extent(visual: dict[str, Any], centerline: tuple[np.ndarray, bool] | None,
            initial_pose: tuple[float, float, float]) -> tuple[np.ndarray, np.ndarray]:
    """(centre, half-size) of the textured area in metres."""
    if visual.get("size"):
        size = np.asarray([to_si(v) for v in visual["size"][:2]], dtype=float)
        if centerline is not None:
            centre = (centerline[0].min(axis=0) + centerline[0].max(axis=0)) / 2
        else:
            centre = np.asarray(initial_pose[:2], dtype=float)
        return centre, size / 2
    points = centerline[0] if centerline is not None else np.asarray([initial_pose[:2]], dtype=float)
    points = np.vstack([points, np.asarray(initial_pose[:2], dtype=float)])
    lo, hi = points.min(axis=0) - 8.0, points.max(axis=0) + 8.0
    return (lo + hi) / 2, np.maximum((hi - lo) / 2, 10.0)


def render_ground_texture(visual: dict[str, Any], centerline: tuple[np.ndarray, bool] | None, centre: np.ndarray,
                          half: np.ndarray, path: Path) -> tuple[int, int]:
    """Draw the ground (with a subtle 1 m tile pattern for texture cues) and the track markings."""
    from PIL import Image, ImageDraw

    ppm = float(visual.get("pixels_per_meter", 60))
    ppm = min(ppm, 4096 / (2 * float(half.max())))
    width, height = int(round(2 * half[0] * ppm)), int(round(2 * half[1] * ppm))
    base, alt = GROUND_COLORS.get(str(visual.get("ground", "grass")), GROUND_COLORS["grass"])
    image = Image.new("RGB", (width, height), base)
    draw = ImageDraw.Draw(image)
    rng = np.random.default_rng(7)
    tile = max(1, int(ppm))  # 1 m tiles: optical-flow / scale cues for vision software
    for ty in range(0, height, tile):
        for tx in range(0, width, tile):
            if (tx // tile + ty // tile) % 2:
                draw.rectangle([tx, ty, tx + tile - 1, ty + tile - 1], fill=alt)
    noise = rng.integers(-6, 7, size=(height, width, 1))
    image = Image.fromarray(np.clip(np.asarray(image, dtype=np.int16) + noise, 0, 255).astype(np.uint8))
    draw = ImageDraw.Draw(image)

    def px(p: np.ndarray) -> tuple[float, float]:
        # Texture row 0 is at +y (MuJoCo maps image top to the plane's +y edge).
        return float((p[0] - centre[0] + half[0]) * ppm), float((half[1] - (p[1] - centre[1])) * ppm)

    if centerline is not None:
        track = visual.get("track") if isinstance(visual.get("track"), dict) else {}
        line, closed = centerline
        pts = [px(p) for p in (np.vstack([line, line[:1]]) if closed else line)]
        lane = to_si(track.get("width", 0.9))
        paint = to_si(track.get("line_width", 0.05))
        surface = GROUND_COLORS["asphalt"][0] if visual.get("ground", "grass") == "grass" else base
        edge = _rgb(track.get("edge_color"), "white")
        # Paint stroke then lane stroke, each with round joints (disks at the vertices), so the edge lines stay
        # continuous around corners and no paint crosses the lane.
        for color, stroke in ((edge, lane + 2 * paint), (surface, lane)):
            radius = stroke * ppm / 2
            draw.line(pts, fill=color, width=max(1, int(round(stroke * ppm))), joint="curve")
            for p in pts:
                draw.ellipse([p[0] - radius, p[1] - radius, p[0] + radius, p[1] + radius], fill=color)
        center = track.get("center_line", {"color": "yellow", "dash": 0.3, "width": 0.04})
        if center:
            center = center if isinstance(center, dict) else {}
            dash = to_si(center.get("dash", 0.3))
            stroke = max(1, int(round(to_si(center.get("width", 0.04)) * ppm)))
            color = _rgb(center.get("color"), "yellow")
            world = np.vstack([line, line[:1]]) if closed else line
            for a, b in zip(world[:-1], world[1:]):
                length = float(np.hypot(*(b - a)))
                s = 0.0
                while s < length:
                    e = min(length, s + dash)
                    draw.line([px(a + (b - a) * s / length), px(a + (b - a) * e / length)], fill=color, width=stroke)
                    s += 2 * dash
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return width, height


def _cone_vertices() -> str:
    angles = np.linspace(0, 2 * math.pi, 16, endpoint=False)
    ring = [(CONE_BASE / 2 * math.cos(a), CONE_BASE / 2 * math.sin(a), 0.0) for a in angles]
    return " ".join(f"{x:.5g} {y:.5g} {z:.5g}" for x, y, z in [*ring, (0.0, 0.0, CONE_HEIGHT)])


def add_scene(project: Project, mj: ET.Element, asset: ET.Element, world: ET.Element, course: list[list[float]] | None,
              initial_pose: tuple[float, float, float], assumptions: list[tuple[str, str]]) -> None:
    """Texture the ground and add track markings and cones per ``environment.visual`` (no-op when empty)."""
    visual = dict(project.spec.simulation.environment.visual or {})
    if not visual:
        return
    centerline = track_centerline(visual, course)
    centre, half = _extent(visual, centerline, initial_pose)
    key = hashlib.sha256(json.dumps([visual, centerline[0].tolist() if centerline else None, centre.tolist(), half.tolist()],
                                    sort_keys=True, default=str).encode()).hexdigest()[:12]
    texture = project.build_dir("sim") / "scene" / f"ground_{key}.png"
    if not texture.exists():
        render_ground_texture(visual, centerline, centre, half, texture)
    ET.SubElement(asset, "texture", name="r3_ground", type="2d", file=str(texture))
    ET.SubElement(asset, "material", name="r3_ground", texture="r3_ground", texrepeat="1 1", texuniform="false",
                  reflectance="0.0", specular="0.1")
    ground = next((g for g in world.iter("geom") if g.get("name") == "ground"), None)
    if ground is not None:
        # Planes collide as infinite planes whatever their size; size bounds the textured rendering.
        ground.set("size", f"{half[0]:.6g} {half[1]:.6g} 0.05")
        ground.set("pos", f"{centre[0]:.6g} {centre[1]:.6g} 0")
        ground.set("material", "r3_ground")
        # Plain surroundings 5 cm below (far enough for the depth buffer), visual only.
        ET.SubElement(world, "geom", name="ground_surround", type="plane", size="0 0 1", pos="0 0 -0.05", material="ground",
                      contype="0", conaffinity="0")
    cones = cone_positions(visual, centerline)
    if cones:
        ET.SubElement(asset, "mesh", name="r3_cone", vertex=_cone_vertices())
        collide = bool(visual.get("cone_collide", False))
        for index, (position, color) in enumerate(cones):
            attrs = {"name": f"cone_{index}", "type": "mesh", "mesh": "r3_cone", "pos": f"{position[0]:.6g} {position[1]:.6g} 0",
                     "rgba": _rgba(color)}
            if not collide:
                attrs.update(contype="0", conaffinity="0")
            ET.SubElement(world, "geom", **attrs)
    assumptions.append(("sensors", f"Camera scene: {visual.get('ground', 'grass')} ground textured over "
                                   f"{2 * half[0]:.0f} x {2 * half[1]:.0f} m"
                                   f"{', track markings' if centerline is not None else ''}"
                                   f"{f', {len(cones)} cones' if cones else ''}; visual only "
                                   "(the collision ground stays an infinite plane with the environment friction)."))


def camera_params(project: Project, cid: str) -> dict[str, Any] | None:
    """Catalog camera behaviour parameters of a component (None if it is not a camera)."""
    comp = project.spec.components.get(cid)
    ctype = project.catalog.components.get(comp.type) if comp is not None else None
    if ctype is None or ctype.behavior is None or not ctype.behavior.model.startswith("camera"):
        return None
    params = dict(ctype.behavior.params)
    params.update((comp.params or {}).get("camera") or {})
    params.setdefault("size_z", float(ctype.mechanical.size[2]) if len(ctype.mechanical.size) > 2 else 0.0)
    return params


def camera_components(project: Project) -> list[str]:
    return [cid for cid in project.spec.components if camera_params(project, cid) is not None]


def camera_frame(project: Project, cid: str) -> tuple[np.ndarray, np.ndarray]:
    """Lens position (robot frame) and the MuJoCo camera rotation (columns x right, y up, z backward)."""
    params = camera_params(project, cid) or {}
    position, rotation = project.graph.component_pose(cid)
    axis = np.asarray(params.get("optical_axis", [0.0, 0.0, 1.0]), dtype=float)
    up = np.asarray(params.get("image_up", [0.0, 1.0, 0.0]), dtype=float)
    lens = np.asarray([to_si(v) for v in params.get("lens_position", [0.0, 0.0, params.get("size_z", 0.0)])], dtype=float)
    z_cam = -axis / np.linalg.norm(axis)
    y_cam = up - z_cam * float(up @ z_cam)
    y_cam /= np.linalg.norm(y_cam)
    x_cam = np.cross(y_cam, z_cam)
    local = np.column_stack([x_cam, y_cam, z_cam])
    return np.asarray(position) + np.asarray(rotation) @ lens, np.asarray(rotation) @ local


def add_cameras(project: Project, elements: dict[str, ET.Element], body_origin: dict[str, np.ndarray], owner: Any,
                assumptions: list[tuple[str, str]]) -> list[str]:
    """A MuJoCo camera per camera component, on the body that carries it. ``owner(cid)`` -> body name."""
    added = []
    for cid in camera_components(project):
        params = camera_params(project, cid) or {}
        body = owner(cid)
        position, rotation = camera_frame(project, cid)
        vfov = to_si(params.get("vfov", 0.0)) or 2 * math.atan(math.tan(to_si(params.get("hfov", 1.0)) / 2) * 3 / 4)
        ET.SubElement(elements[body], "camera", name=camera_name(cid), pos=" ".join(f"{v:.9g}" for v in position - body_origin[body]),
                      quat=" ".join(f"{v:.9g}" for v in rotation_to_quat(rotation)), fovy=f"{math.degrees(vfov):.6g}")
        added.append(cid)
        assumptions.append(("sensors", f"Camera {cid}: pinhole MuJoCo camera at the lens, vertical FOV {math.degrees(vfov):.1f} deg "
                                       "(catalog); no lens distortion, motion blur or rolling shutter."))
    return added

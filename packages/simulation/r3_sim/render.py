"""Offscreen MuJoCo renders (``mujoco.Renderer``) of recorded vehicle states.

Headless: the OpenGL backend is chosen by :mod:`r3_sim.gl` before ``mujoco``
is imported (``MUJOCO_GL=egl`` or ``osmesa`` on Linux servers, ``cgl`` on macOS;
an explicit ``MUJOCO_GL`` wins). :data:`GL_BACKEND` records the choice.
"""

from __future__ import annotations

import os

import math
import shutil
import subprocess
from pathlib import Path

import mujoco
import numpy as np
from PIL import Image

from .gl import BACKEND

GL_BACKEND = os.environ.get("MUJOCO_GL") or BACKEND or "default"


def _renderer(model: mujoco.MjModel, height: int, width: int) -> mujoco.Renderer:
    try:
        return mujoco.Renderer(model, height, width)
    except Exception as error:  # noqa: BLE001 - re-raised with the headless hint
        raise RuntimeError(f"MuJoCo offscreen rendering failed with MUJOCO_GL={GL_BACKEND}: {error}. On a Linux "
                           "server install EGL (libegl1, GPU or Mesa) and set MUJOCO_GL=egl, or OSMesa "
                           "(libosmesa6) and set MUJOCO_GL=osmesa, before mujoco is imported.") from error


def _camera(lookat: np.ndarray, yaw: float, distance: float = 1.1, elevation: float = -22.0) -> mujoco.MjvCamera:
    cam = mujoco.MjvCamera()
    cam.type = mujoco.mjtCamera.mjCAMERA_FREE
    cam.lookat[:] = lookat
    cam.distance = distance
    cam.azimuth = math.degrees(yaw) + 145.0
    cam.elevation = elevation
    return cam


def render_frames(model: mujoco.MjModel, times: list[float], qpos: list[np.ndarray], out_dir: Path, at: list[float],
                  width: int = 960, height: int = 640, prefix: str = "render") -> list[Path]:
    """Render stills at the snapshot closest to each time in ``at``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    data = mujoco.MjData(model)
    paths = []
    with _renderer(model, height, width) as renderer:
        for target in at:
            k = int(np.argmin(np.abs(np.asarray(times) - target)))
            data.qpos[:] = qpos[k]
            mujoco.mj_forward(model, data)
            yaw = _yaw(data.qpos[3:7])
            renderer.update_scene(data, camera=_camera(data.qpos[:3] + np.array([0.13, 0, 0.05]), yaw))
            path = out_dir / f"{prefix}_t{times[k]:07.2f}s.png"
            Image.fromarray(renderer.render()).save(path)
            paths.append(path)
    return paths


def _yaw(quat: np.ndarray) -> float:
    w, x, y, z = quat
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def render_video(model: mujoco.MjModel, times: list[float], qpos: list[np.ndarray], path: Path, max_seconds: float = 12.0,
                 fps: int = 30, width: int = 640, height: int = 400) -> Path | None:
    """Chase-camera clip of the first ``max_seconds`` (MP4 via ffmpeg, else GIF)."""
    if not times:
        return None
    data = mujoco.MjData(model)
    frames = []
    with _renderer(model, height, width) as renderer:
        for k, t in enumerate(times):
            if t > max_seconds:
                break
            data.qpos[:] = qpos[k]
            mujoco.mj_forward(model, data)
            renderer.update_scene(data, camera=_camera(data.qpos[:3] + np.array([0.13, 0, 0.05]), _yaw(data.qpos[3:7]), 1.6, -18.0))
            frames.append(renderer.render().copy())
    if not frames:
        return None
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        mp4 = path.with_suffix(".mp4")
        proc = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
                               "-r", str(fps), "-i", "-", "-pix_fmt", "yuv420p", "-vcodec", "libx264", str(mp4)],
                              input=b"".join(f.tobytes() for f in frames), capture_output=True)
        if proc.returncode == 0:
            return mp4
    gif = path.with_suffix(".gif")
    images = [Image.fromarray(f) for f in frames[::2]]
    images[0].save(gif, save_all=True, append_images=images[1:], duration=int(2000 / fps), loop=0)
    return gif

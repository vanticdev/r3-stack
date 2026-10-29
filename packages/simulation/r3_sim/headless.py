"""Rendering defaults for machines without a display (servers, CI, containers).

MuJoCo picks its OpenGL backend from ``MUJOCO_GL`` when it is first imported;
on Linux the default (GLFW) needs an X display. :func:`configure` selects a
headless backend before anything imports MuJoCo, and forces matplotlib's Agg
backend. Explicit settings are never overridden. Entry points (``r3`` CLI, MCP
server, simulation worker) call it first; importing :mod:`r3_sim` calls it too.

Backend choice: EGL when a GPU device node exists (``/dev/dri/renderD*`` or
``/dev/nvidia0``), so the vendor driver renders. Without a GPU both EGL and
OSMesa fall back to Mesa's CPU rasterizer, and OSMesa is preferred: through
EGL, llvmpipe produced shadow-map acne across the floor in the RC-car renders,
OSMesa did not (R3 worker image, arm64: EGL Mesa 25.2.8, OSMesa 25.1.7).
"""

from __future__ import annotations

import ctypes.util
import os
import sys
from pathlib import Path


def has_display() -> bool:
    if sys.platform != "linux":
        return True  # macOS/Windows render offscreen through their native GL without a display server
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def gpu_present() -> bool:
    dri = Path("/dev/dri")
    return (dri.is_dir() and any(dri.glob("renderD*"))) or Path("/dev/nvidia0").exists()


def headless_gl_backend() -> str | None:
    """The best available headless MuJoCo GL backend on Linux: egl, osmesa or None."""
    egl, osmesa = ctypes.util.find_library("EGL"), ctypes.util.find_library("OSMesa")
    if egl and (gpu_present() or not osmesa):
        return "egl"
    if osmesa:
        return "osmesa"
    return None


def configure() -> dict[str, str]:
    """Apply headless defaults; returns the variables that were set."""
    applied: dict[str, str] = {}
    if has_display():
        return applied
    if "MPLBACKEND" not in os.environ:
        applied["MPLBACKEND"] = os.environ["MPLBACKEND"] = "Agg"
    if "MUJOCO_GL" not in os.environ and "mujoco" not in sys.modules:
        backend = headless_gl_backend()
        if backend:
            applied["MUJOCO_GL"] = os.environ["MUJOCO_GL"] = backend
            if "PYOPENGL_PLATFORM" not in os.environ:
                applied["PYOPENGL_PLATFORM"] = os.environ["PYOPENGL_PLATFORM"] = backend
    return applied

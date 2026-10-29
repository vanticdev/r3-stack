"""Headless OpenGL backend selection for MuJoCo rendering.

MuJoCo's Python package reads ``MUJOCO_GL`` once, when ``mujoco`` is first
imported, so the backend must be chosen before that. :func:`configure` runs
from ``r3_sim/__init__.py``:

* an explicit ``MUJOCO_GL`` is always respected;
* Linux without ``DISPLAY``/``WAYLAND_DISPLAY`` (servers, the R3 worker Docker
  image): ``egl`` (GPU or Mesa EGL, ``libEGL``), else ``osmesa`` (pure
  software, ``libOSMesa``); ``PYOPENGL_PLATFORM`` is set to match;
* Linux with a display: MuJoCo's default (GLFW);
* macOS: ``cgl``, an offscreen CGL context that needs no window or display;
* Windows: MuJoCo's default (WGL through GLFW).
"""

from __future__ import annotations

import ctypes.util
import os
import platform
import sys

_HEADLESS_LINUX = ("egl", "osmesa")


def choose_backend(system: str | None = None, env: dict[str, str] | None = None) -> str | None:
    """The backend :func:`configure` would select (None = MuJoCo's default)."""
    env = os.environ if env is None else env
    system = system or platform.system()
    if env.get("MUJOCO_GL"):
        return env["MUJOCO_GL"].strip().lower()
    if system == "Linux":
        if env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"):
            return None
        return "egl" if ctypes.util.find_library("EGL") else "osmesa"
    if system == "Darwin":
        return "cgl"
    return None


def configure() -> str | None:
    """Set ``MUJOCO_GL`` (and ``PYOPENGL_PLATFORM`` for EGL/OSMesa) unless set. Returns
    the backend in effect; if ``mujoco`` was imported earlier, its choice stands."""
    backend = choose_backend()
    if backend and "MUJOCO_GL" not in os.environ and "mujoco" not in sys.modules:
        os.environ["MUJOCO_GL"] = backend
    if backend in _HEADLESS_LINUX:
        os.environ.setdefault("PYOPENGL_PLATFORM", backend)
    return os.environ.get("MUJOCO_GL") or backend


BACKEND = configure()

__all__ = ["BACKEND", "choose_backend", "configure"]

"""KiCad data through KiCad's own engine (headless ``pcbnew`` module, no GUI).

Library footprints are read with ``pcbnew.FootprintLoad`` and placed board
footprints with ``pcbnew.LoadBoard``; nothing parses KiCad files by hand.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from r3_kicad.library import KicadInstall, find_install

SCRIPT = Path(__file__).parent / "kicad_scripts" / "footprint_dump.py"
MM = 1e-3


def kicad_available() -> bool:
    install = find_install()
    return install is not None and Path(install.python).exists()


def _dump(mode: str, source: str, names: list[str], install: KicadInstall | None = None) -> dict[str, Any]:
    install = install or find_install()
    if install is None:
        raise FileNotFoundError("KiCad installation not found (set R3_KICAD_APP or install KiCad)")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "dump.json"
        cmd = [str(install.python), "-I", str(SCRIPT), mode, source, str(out), *names]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                              env={**os.environ, **install.env, "LC_ALL": "C"})
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(f"pcbnew footprint dump failed: {proc.stdout[-2000:]}{proc.stderr[-2000:]}")
        return json.loads(out.read_text())


@lru_cache(maxsize=None)
def _library_cached(names: tuple[str, ...]) -> str:
    install = find_install()
    return json.dumps(_dump("library", str(install.footprints), list(names), install))


def library_footprints(names: list[str]) -> dict[str, dict[str, Any]]:
    """Official KiCad library footprints, keyed ``Lib:Footprint`` (mm, KiCad axes, y down)."""
    return json.loads(_library_cached(tuple(sorted(set(names)))))["footprints"]


def board_footprints(board: Path, refs: list[str] | None = None) -> dict[str, Any]:
    """Placed footprints of a board (mm from the Edge.Cuts top-left corner, y down)."""
    return _dump("board", str(board), list(refs or []))


def model_dir() -> Path | None:
    install = find_install()
    return Path(install.models_3d) if install is not None else None


def resolve_model_path(path: str) -> Path:
    """Expand ``${KICAD10_3DMODEL_DIR}`` (and older variable names) to the installed library."""
    base = model_dir()
    for var in ("${KICAD10_3DMODEL_DIR}", "${KICAD9_3DMODEL_DIR}", "${KICAD8_3DMODEL_DIR}", "${KICAD_3DMODEL_DIR}"):
        if path.startswith(var):
            if base is None:
                raise FileNotFoundError("KiCad 3D model library not found")
            return base / path[len(var):].lstrip("/")
    return Path(path)


def footprint_to_board(library: dict[str, Any], placed: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, float]:
    """2D map ``board = A @ footprint + t`` (mm, KiCad axes) of a placed
    footprint, fitted to its pads by least squares (rotation and the bottom-side
    mirror come out of the data, not out of sign conventions). Returns (A, t,
    rms residual)."""
    lib = {p["number"]: p["position"] for p in library["pads"] if p["number"]}
    board = {p["number"]: p["position"] for p in placed["pads"] if p["number"]}
    common = sorted(set(lib) & set(board))
    src = np.array([lib[n] for n in common], dtype=float)
    dst = np.array([board[n] for n in common], dtype=float)
    origin = np.asarray(placed["position"], dtype=float)
    angle = math.radians(placed["rotation"])
    # Candidate linear parts: KiCad rotation (CCW on screen, y down) with and without the bottom-side mirror.
    rot = np.array([[math.cos(angle), math.sin(angle)], [-math.sin(angle), math.cos(angle)]])
    best = None
    for mirror in (np.eye(2), np.diag([-1.0, 1.0]), np.diag([1.0, -1.0])):
        a = rot @ mirror
        residual = dst - (src @ a.T + origin)
        rms = float(np.sqrt((residual ** 2).sum(axis=1).mean())) if len(common) else 0.0
        if best is None or rms < best[2]:
            best = (a, origin, rms)
    assert best is not None
    return best


__all__ = ["board_footprints", "footprint_to_board", "kicad_available", "library_footprints", "model_dir",
           "resolve_model_path"]

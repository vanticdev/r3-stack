"""PNG evidence (matplotlib Agg, headless): per-part stress maps and the oriented print preview."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import trimesh  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402

INK = "#1f2328"
MUTED = "#6e7781"
SUPPORT = "#0b6bcb"
LOAD = "#c4320a"


def _equal_axes(ax: Any, points: np.ndarray) -> None:
    lo, hi = points.min(axis=0), points.max(axis=0)
    center, span = (lo + hi) / 2, max(float(np.max(hi - lo)), 1e-6) / 2
    ax.set_xlim(center[0] - span, center[0] + span)
    ax.set_ylim(center[1] - span, center[1] + span)
    ax.set_zlim(center[2] - span, center[2] + span)
    ax.set_box_aspect((1, 1, 1))


def stress_plot(path: Path, cid: str, strength: Any, material: Any, safety: float, strength_mpa: float) -> None:
    """Element von Mises stress of the worst load case on the voxel model (single-hue sequential
    scale, light = low), support patches blue, load patches red."""
    worst = strength.worst
    mesh = strength.model.mesh
    centers = mesh.p[:, mesh.t].mean(axis=1).T
    vm = worst.element_vm
    fig = plt.figure(figsize=(9, 6.5), dpi=130)
    ax = fig.add_subplot(111, projection="3d")
    order = np.argsort(vm)
    scatter = ax.scatter(*centers[order].T, c=vm[order], cmap="Reds", s=6, vmin=0.0, vmax=max(float(vm.max()), 1e-9), linewidths=0)
    p = strength.points.T
    ax.scatter(*p[strength.support_nodes].T, color=SUPPORT, s=4, alpha=0.8, label="supports (joins to the wheels)")
    for k, nodes in enumerate(strength.load_nodes):
        ax.scatter(*p[nodes].T, color=LOAD, s=2, alpha=0.5, label="carried-load patches" if k == 0 else None)
    ax.scatter(*worst.location_mm, color=INK, s=60, marker="x", label=f"max {worst.max_von_mises_mpa:.2f} MPa")
    _equal_axes(ax, p)
    ax.set_xlabel("x (mm)", color=MUTED)
    ax.set_ylabel("y (mm)", color=MUTED)
    ax.set_zlabel("z (mm)", color=MUTED)
    bar = fig.colorbar(scatter, ax=ax, shrink=0.6, pad=0.1)
    bar.set_label("von Mises stress (MPa)", color=INK)
    ax.legend(loc="upper left", fontsize=8, frameon=False)
    ax.set_title(f"{cid}: {material.name}, worst case {worst.case} ({', '.join(f'{a:g}' for a in worst.accel_g)} g)\n"
                 f"safety factor {safety:.2f} vs {strength_mpa:.0f} MPa; max deflection {worst.max_deflection_mm:.3f} mm; "
                 f"voxel {'x'.join(f'{h:.2f}' for h in strength.pitch_mm)} mm, {strength.elements} quadratic hexes", fontsize=9, color=INK)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def preview(path: Path, parts: list[tuple[str, trimesh.Trimesh, str, list[float]]]) -> None:
    """One panel per distinct printed part (label, oriented mesh, color, bed [x, y]), zoomed to the part;
    the dashed square is the bed footprint when the part is large enough to show it."""
    count = max(1, len(parts))
    cols = min(4, count)
    rows = math.ceil(count / cols)
    fig = plt.figure(figsize=(4 * cols, 3.8 * rows), dpi=110)
    for k, (label, mesh, color, bed) in enumerate(parts):
        ax = fig.add_subplot(rows, cols, k + 1, projection="3d")
        shade = 0.55 + 0.45 * np.clip(mesh.face_normals @ np.array([0.3, -0.4, 0.85]), 0, 1)
        rgb = np.asarray(matplotlib.colors.to_rgb(color))
        ax.add_collection3d(Poly3DCollection(mesh.triangles, facecolors=np.clip(rgb[None, :] * shade[:, None], 0, 1), edgecolor="none"))
        points = mesh.vertices
        if max(mesh.extents[:2]) > 0.25 * min(bed):
            bx, by = bed
            ax.plot([0, bx, bx, 0, 0], [0, 0, by, by, 0], [0, 0, 0, 0, 0], color=MUTED, linewidth=0.8, linestyle="--")
            points = np.vstack([points, [[0, 0, 0], [bx, by, 0]]])
        _equal_axes(ax, points)
        ax.set_title(f"{label}\n{mesh.extents[0]:.1f} x {mesh.extents[1]:.1f} x {mesh.extents[2]:.1f} mm (x, y, z)", fontsize=8, color=INK)
        ax.tick_params(labelsize=6, colors=MUTED)
    fig.suptitle("Printed parts in their suggested print orientation (z up, mm)", fontsize=10, color=INK)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)

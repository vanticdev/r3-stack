"""Matplotlib renders of routed harnesses and slack-simulation snapshots."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402
from scipy.spatial import ConvexHull, QhullError  # noqa: E402

from .geometry import Box, Cylinder, Shape, SweptShape  # noqa: E402
from .obstacles import ObstacleSet  # noqa: E402

# Physical insulation colours; white/yellow get a dark outline to stay visible.
WIRE_COLORS = {"red": "#d62728", "black": "#222222", "blue": "#1f5fbf", "yellow": "#e3b505", "white": "#f4f4f4",
               "green": "#2ca02c", "orange": "#ff7f0e", "brown": "#8c564b", "purple": "#7b3fa0", "gray": "#8a8a8a"}
SWEEP_COLOR = "#e8743b"
MM = 1000.0
VIEWS = {"top": (0, 1, "x (mm)", "y (mm)"), "side": (0, 2, "x (mm)", "z (mm)"), "front": (1, 2, "y (mm)", "z (mm)")}


def _wire_color(color: str) -> str:
    return WIRE_COLORS.get(color.lower(), "#555555")


def _outline(color: str) -> bool:
    return color.lower() in ("white", "yellow")


def _faces(shape: Shape) -> list[np.ndarray]:
    if isinstance(shape, Box):
        return shape.faces()
    if isinstance(shape, Cylinder):
        top = shape._ring(shape.half_length, shape.radius, 24)
        bottom = shape._ring(-shape.half_length, shape.radius, 24)
        sides = [np.vstack([top[k], top[(k + 1) % 24], bottom[(k + 1) % 24], bottom[k]]) for k in range(24)]
        return [top, bottom] + sides
    lo, hi = shape.aabb()
    return Box((lo + hi) / 2, np.eye(3), (hi - lo) / 2).faces()


def _hull2d(points: np.ndarray) -> np.ndarray | None:
    try:
        hull = ConvexHull(points)
    except (QhullError, ValueError):
        return None
    return points[hull.vertices]


def _draw_obstacles_2d(ax, obstacles: ObstacleSet, view: str) -> None:
    a, b, _, _ = VIEWS[view]
    depth = 3 - a - b
    ordered = sorted(obstacles.obstacles, key=lambda o: o.shape.aabb()[0][depth])
    for obstacle in ordered:
        if obstacle.moving and isinstance(obstacle.swept, SweptShape):
            for shape in obstacle.swept.shapes[:: max(1, len(obstacle.swept.shapes) // 8)] + [obstacle.swept.shapes[-1]]:
                poly = _hull2d(shape.surface_points(0.004)[:, [a, b]] * MM)
                if poly is not None:
                    ax.fill(poly[:, 0], poly[:, 1], facecolor=SWEEP_COLOR, alpha=0.06, edgecolor=SWEEP_COLOR, lw=0.4)
        poly = _hull2d(obstacle.shape.surface_points(0.004)[:, [a, b]] * MM)
        if poly is None:
            continue
        face = SWEEP_COLOR if obstacle.moving else obstacle.color
        ax.fill(poly[:, 0], poly[:, 1], facecolor=face, alpha=0.22 if obstacle.moving else 0.28,
                edgecolor="#444444", lw=0.5)
        if view == "top" and obstacle.kind != "structure":
            center = poly.mean(axis=0)
            ax.text(center[0], center[1], obstacle.component, fontsize=5.5, color="#333333", ha="center", va="center")


def _stacked(wires: dict[str, dict[str, Any]], lw: float) -> list[tuple[dict[str, Any], float]]:
    """Wires of a harness drawn widest-first so a bundle shows every colour as a stripe."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for wire in wires.values():
        groups.setdefault(wire.get("harness", ""), []).append(wire)
    out = []
    for members in groups.values():
        n = len(members)
        for k, wire in enumerate(members):
            out.append((wire, lw + 0.9 * (n - 1 - k)))
    return out


def _draw_wires_2d(ax, wires: dict[str, dict[str, Any]], view: str, lw: float = 1.4, ls: str = "-",
                   alpha: float = 1.0) -> None:
    a, b, _, _ = VIEWS[view]
    for wire, width in _stacked(wires, lw):
        path = np.asarray(wire["path"]) * MM
        if len(path) < 2:
            continue
        color = wire.get("color", "")
        if _outline(color):
            ax.plot(path[:, a], path[:, b], color="#333333", lw=width + 0.9, ls=ls, alpha=alpha, solid_capstyle="round")
        ax.plot(path[:, a], path[:, b], color=_wire_color(color), lw=width, ls=ls, alpha=alpha, solid_capstyle="round")


def _finish_2d(ax, view: str, title: str) -> None:
    _, _, xl, yl = VIEWS[view]
    ax.set_aspect("equal")
    ax.set_xlabel(xl, fontsize=8)
    ax.set_ylabel(yl, fontsize=8)
    ax.tick_params(labelsize=7)
    ax.grid(True, color="#dddddd", lw=0.4)
    ax.set_title(title, fontsize=9)


def _draw_iso(ax, obstacles: ObstacleSet, wires: dict[str, dict[str, Any]], ties: list, holes: list,
              floating: list, lw: float = 1.6) -> None:
    for obstacle in obstacles.obstacles:
        faces = [f * MM for f in _faces(obstacle.shape)]
        face = SWEEP_COLOR if obstacle.moving else obstacle.color
        ax.add_collection3d(Poly3DCollection(faces, facecolor=face, alpha=0.10 if obstacle.kind == "structure" else 0.16,
                                             edgecolor="#555555", linewidths=0.2))
        if obstacle.moving and isinstance(obstacle.swept, SweptShape):
            shapes = obstacle.swept.shapes
            for shape in (shapes[0], shapes[-1]):
                for edge in shape.edges():
                    ax.plot(*(edge * MM).T, color=SWEEP_COLOR, lw=0.5, alpha=0.6)
    for wire, width in _stacked(wires, lw):
        path = np.asarray(wire["path"]) * MM
        if len(path) < 2:
            continue
        if _outline(wire.get("color", "")):
            ax.plot(*path.T, color="#333333", lw=width + 0.8)
        ax.plot(*path.T, color=_wire_color(wire.get("color", "")), lw=width)
    if ties:
        t = np.asarray(ties) * MM
        ax.scatter(*t.T, color="#111111", marker="s", s=10, depthshade=False, label="tie point")
    if holes:
        h = np.asarray(holes) * MM
        ax.scatter(*h.T, facecolor="none", edgecolor="#7b3fa0", marker="o", s=40, lw=1.2, depthshade=False, label="deck pass-through")
    if floating:
        f = np.asarray(floating) * MM
        ax.scatter(*f.T, color="#e3b505", edgecolor="#333333", marker="D", s=30, depthshade=False, label="floating mate")


def _iso_limits(ax, obstacles: ObstacleSet) -> None:
    lo = np.min([o.swept.aabb()[0] for o in obstacles.obstacles], axis=0) * MM
    hi = np.max([o.swept.aabb()[1] for o in obstacles.obstacles], axis=0) * MM
    ax.set_xlim(lo[0], hi[0]); ax.set_ylim(lo[1], hi[1]); ax.set_zlim(0, hi[2] + 20)
    ax.set_box_aspect((hi[0] - lo[0], hi[1] - lo[1], hi[2] + 20))
    ax.view_init(elev=28, azim=-58)
    ax.set_xlabel("x (mm)", fontsize=7); ax.set_ylabel("y (mm)", fontsize=7); ax.set_zlabel("z (mm)", fontsize=7)
    ax.tick_params(labelsize=6)


def render_routes(out_dir: Path, obstacles: ObstacleSet, routes: dict[str, Any], detail: dict[str, Any],
                  title: str) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    wires = {k: dict(v, color=detail["wires"][k]["color"]) for k, v in routes["wires"].items()}
    ties = [t for h in routes["harnesses"].values() for t in h["tie_points"]]
    holes = [h["center"] for h in detail.get("pass_throughs", [])]
    floating = [f["center"] for f in detail.get("floating_mates", [])]
    paths = []

    fig = plt.figure(figsize=(11, 7.5), dpi=150)
    ax = fig.add_subplot(111, projection="3d")
    _draw_iso(ax, obstacles, wires, ties, holes, floating)
    _iso_limits(ax, obstacles)
    ax.set_title(f"{title}: routed harnesses (isometric). Orange = moving parts and steering sweep", fontsize=10)
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(handles, labels, loc="upper left", fontsize=7, frameon=False)
    path = out_dir / "routes_iso.png"
    fig.tight_layout(); fig.savefig(path); plt.close(fig)
    paths.append(path)

    fig, axes = plt.subplots(2, 1, figsize=(11, 9.5), dpi=150, gridspec_kw={"height_ratios": [1.15, 0.85]})
    for ax, view in zip(axes, ("top", "side")):
        _draw_obstacles_2d(ax, obstacles, view)
        _draw_wires_2d(ax, wires, view)
        a, b, _, _ = VIEWS[view]
        if ties:
            t = np.asarray(ties) * MM
            ax.scatter(t[:, a], t[:, b], color="#111111", marker="s", s=9, zorder=5, label="tie point")
        if holes:
            h = np.asarray(holes) * MM
            ax.scatter(h[:, a], h[:, b], facecolor="none", edgecolor="#7b3fa0", s=40, lw=1.2, zorder=5, label="deck pass-through")
        if floating:
            f = np.asarray(floating) * MM
            ax.scatter(f[:, a], f[:, b], color="#e3b505", edgecolor="#333333", marker="D", s=26, zorder=5, label="floating mate")
        _finish_2d(ax, view, f"{title}: {view} view")
    axes[0].legend(loc="upper right", fontsize=7, frameon=False)
    path = out_dir / "routes_top_side.png"
    fig.tight_layout(); fig.savefig(path); plt.close(fig)
    paths.append(path)

    for view in ("top", "side"):
        fig, ax = plt.subplots(figsize=(11, 6 if view == "top" else 4.2), dpi=150)
        _draw_obstacles_2d(ax, obstacles, view)
        _draw_wires_2d(ax, wires, view)
        _finish_2d(ax, view, f"{title}: {view} view")
        path = out_dir / f"routes_{view}.png"
        fig.tight_layout(); fig.savefig(path); plt.close(fig)
        paths.append(path)
    return paths


def render_slack(out_dir: Path, obstacles: ObstacleSet, routes: dict[str, Any], detail: dict[str, Any],
                 snapshot: dict[str, np.ndarray], timeline: dict[str, np.ndarray], requirement: float,
                 title: str) -> list[Path]:
    """Sagged cable shape at peak bump (solid) over the routed shape (dashed),
    plus the clearance-to-moving-parts time history of the test drive."""
    out_dir.mkdir(parents=True, exist_ok=True)
    routed = {k: dict(v, color=detail["wires"][k]["color"]) for k, v in routes["wires"].items()}
    sagged = {k: {"path": p, "color": detail["wires"][k]["color"], "harness": detail["wires"][k]["harness"]}
              for k, p in snapshot.items() if k in detail["wires"]}
    paths = []
    fig, axes = plt.subplots(2, 1, figsize=(11, 9.5), dpi=150, gridspec_kw={"height_ratios": [1.15, 0.85]})
    for ax, view in zip(axes, ("top", "side")):
        _draw_obstacles_2d(ax, obstacles, view)
        _draw_wires_2d(ax, routed, view, lw=0.9, ls=(0, (3, 2)), alpha=0.55)
        _draw_wires_2d(ax, sagged, view, lw=1.5)
        _finish_2d(ax, view, f"{title}: slack simulation at peak bump, {view} view (dashed = routed, solid = simulated)")
    path = out_dir / "slack_peak_bump.png"
    fig.tight_layout(); fig.savefig(path); plt.close(fig)
    paths.append(path)

    fig = plt.figure(figsize=(11, 7.5), dpi=150)
    ax = fig.add_subplot(111, projection="3d")
    _draw_iso(ax, obstacles, sagged, [], [], [])
    _iso_limits(ax, obstacles)
    ax.set_title(f"{title}: simulated cable shape at peak 3 g bump", fontsize=10)
    path = out_dir / "slack_peak_bump_iso.png"
    fig.tight_layout(); fig.savefig(path); plt.close(fig)
    paths.append(path)

    if timeline:
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), dpi=150, sharex=True)
        t = timeline["time"]
        ax1.plot(t, timeline["min_clearance"] * MM, color="#1f5fbf", lw=1.6, label="min wire clearance to moving parts")
        ax1.axhline(requirement * MM, color="#b42318", lw=1.0, ls="--", label=f"requirement {requirement * MM:.0f} mm")
        ax1.set_ylabel("clearance (mm)", fontsize=8)
        ax1.legend(fontsize=7, frameon=False, loc="lower right")
        ax1.grid(True, color="#e5e5e5", lw=0.4)
        ax1.set_title(f"{title}: vibration + bump + lock-to-lock steering test", fontsize=9)
        ax2.plot(t, timeline["accel_z"] / 9.80665, color="#555555", lw=1.0, label="chassis vertical accel (g)")
        ax2.plot(t, np.degrees(timeline["steer"]), color="#e8743b", lw=1.4, label="steer angle (deg)")
        ax2.set_xlabel("time (s)", fontsize=8)
        ax2.legend(fontsize=7, frameon=False, loc="upper right")
        ax2.grid(True, color="#e5e5e5", lw=0.4)
        for ax in (ax1, ax2):
            ax.tick_params(labelsize=7)
            for side in ("top", "right"):
                ax.spines[side].set_visible(False)
        path = out_dir / "slack_clearance_timeline.png"
        fig.tight_layout(); fig.savefig(path); plt.close(fig)
        paths.append(path)
    return paths

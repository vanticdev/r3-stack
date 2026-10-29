"""Renders for the wiring overhaul: supports as hardware, clearance witnesses,
planned vs. routed paths with violations, and bundle derating."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402

from .obstacles import ObstacleSet  # noqa: E402
from .render import MM, VIEWS, _draw_iso, _draw_obstacles_2d, _draw_wires_2d, _finish_2d, _iso_limits  # noqa: E402

SUPPORT_COLORS = {"adhesive_tie_mount": "#1f77b4", "screw_tie_mount": "#2ca02c", "p_clip": "#9467bd",
                  "post_tie": "#d62728"}
WITNESS_COLORS = {"geometry_uncertain": "#ff7f0e", "wire_clearance_violation": "#d62728",
                  "wire_intersects_component": "#8b0000", "connector_clearance_violation": "#c71585"}


def _support_box(s: dict[str, Any]) -> list[np.ndarray]:
    n = np.asarray(s["normal"], float)
    t = np.asarray(s["axis"], float)
    t = t - (t @ n) * n
    t /= max(np.linalg.norm(t), 1e-12)
    b = np.cross(n, t)
    fx, fy = s["footprint"]
    h = max(s["height"], 0.0008)
    m = np.asarray(s["mount"], float)
    corners = [m + t * u * fx / 2 + b * v * fy / 2 + n * w * h for w in (0, 1) for u in (-1, 1) for v in (-1, 1)]
    c = np.asarray(corners) * MM
    quads = [(0, 1, 3, 2), (4, 5, 7, 6), (0, 1, 5, 4), (2, 3, 7, 6), (0, 2, 6, 4), (1, 3, 7, 5)]
    return [c[list(q)] for q in quads]


def _tie_loop(s: dict[str, Any]) -> np.ndarray:
    n = np.asarray(s["normal"], float)
    t = np.asarray(s["axis"], float)
    t = t - (t @ n) * n
    t /= max(np.linalg.norm(t), 1e-12)
    b = np.cross(n, t)
    r = s["bundle_od"] / 2 + 0.0006
    angles = np.linspace(0, 2 * np.pi, 33)
    return (np.asarray(s["position"]) + np.outer(np.cos(angles), n) * r + np.outer(np.sin(angles), b) * r) * MM


def render_supports(out_dir: Path, obstacles: ObstacleSet, routes: dict[str, Any], colors: dict[str, str],
                    supports: list[dict[str, Any]], spans: list[dict[str, Any]], title: str) -> list[Path]:
    """Isometric + top view of the harnesses with every support drawn as its footprint
    and height on the host face, the tie loop around the bundle, and unsupported spans."""
    wires = {k: dict(v, color=colors.get(k, "")) for k, v in routes["wires"].items()}
    paths = []
    fig = plt.figure(figsize=(12, 8), dpi=150)
    ax = fig.add_subplot(111, projection="3d")
    _draw_iso(ax, obstacles, wires, [], [], [], lw=1.4)
    for s in supports:
        color = SUPPORT_COLORS.get(s["kind"], "#333333")
        ax.add_collection3d(Poly3DCollection(_support_box(s), facecolor=color, alpha=0.85, edgecolor="#111111",
                                             linewidths=0.4))
        loop = _tie_loop(s)
        ax.plot(*loop.T, color="#111111", lw=1.0)
        p = np.asarray(s["position"]) * MM
        ax.text(*p + [0, 0, 6], s["id"].split(".")[-1], fontsize=5, color=color)
    for kind, color in SUPPORT_COLORS.items():
        ax.scatter([], [], [], color=color, marker="s", label=kind.replace("_", " "))
    _iso_limits(ax, obstacles)
    ax.legend(loc="upper left", fontsize=7, frameon=False)
    ax.set_title(f"{title}: cable supports as hardware ({len(supports)} parts on structure faces)", fontsize=10)
    path = out_dir / "supports_iso.png"
    fig.tight_layout(); fig.savefig(path); plt.close(fig)
    paths.append(path)

    fig, ax = plt.subplots(figsize=(12, 6.5), dpi=150)
    _draw_obstacles_2d(ax, obstacles, "top")
    _draw_wires_2d(ax, wires, "top", lw=1.2)
    for s in supports:
        m = np.asarray(s["mount"]) * MM
        color = SUPPORT_COLORS.get(s["kind"], "#333333")
        fx, fy = s["footprint"]
        ax.add_patch(plt.Rectangle((m[0] - fx * MM / 2, m[1] - fy * MM / 2), fx * MM, fy * MM, facecolor=color,
                                   edgecolor="#111111", lw=0.5, alpha=0.85, zorder=6))
        ax.annotate(f"{s['id']}\n{s['mpn'] or s['type']}", (m[0], m[1]), xytext=(4, 4), textcoords="offset points",
                    fontsize=5, color=color, zorder=7)
    shown = set()
    for span in spans:
        if span["length"] <= span["limit"] + 1e-6 or span["from"] == span["to"] == "free end":
            continue
        key = (span["harness"], span["from"], span["to"])
        if key in shown:
            continue
        shown.add(key)
        wire = routes["wires"].get(span["wire"])
        if not wire:
            continue
        pts = np.asarray(wire["path"])
        arc = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
        mask = (arc >= span["start_arc"]) & (arc <= span["end_arc"])
        if mask.sum() >= 2:
            ax.plot(pts[mask, 0] * MM, pts[mask, 1] * MM, color="#ff00ff", lw=4, alpha=0.35, zorder=4)
    ax.plot([], [], color="#ff00ff", lw=4, alpha=0.35, label="span longer than its support limit")
    ax.legend(loc="upper right", fontsize=7, frameon=False)
    _finish_2d(ax, "top", f"{title}: supports (top) and unsupported spans")
    path = out_dir / "supports_top.png"
    fig.tight_layout(); fig.savefig(path); plt.close(fig)
    paths.append(path)
    return paths


def render_support_details(out_dir: Path, obstacles: ObstacleSet, routes: dict[str, Any], colors: dict[str, str],
                           supports: list[dict[str, Any]], title: str, reach: float = 0.022) -> list[Path]:
    """Close-up of every support: the host's real CAD triangles near the mount, the
    mount body (footprint x height), the tie loop and the bundle through it."""
    from .geometry import MeshShape
    from .render import _wire_color

    if not supports:
        return []
    n = len(supports)
    cols = min(4, n)
    rows = (n + cols - 1) // cols
    fig = plt.figure(figsize=(4.2 * cols, 4.0 * rows), dpi=150)
    for k, s in enumerate(supports):
        ax = fig.add_subplot(rows, cols, k + 1, projection="3d")
        centre = np.asarray(s["mount"], float)
        lo, hi = (centre - reach) * MM, (centre + reach) * MM
        host = obstacles.by_component(s["host"])
        if host is not None and isinstance(host.shape, MeshShape):
            tri = host.shape.vertices[host.shape.faces]
            near = np.all(np.abs(tri.mean(axis=1) - centre) < reach, axis=1)
            if near.any():
                ax.add_collection3d(Poly3DCollection(list(tri[near] * MM), facecolor="#b9c4cf", edgecolor="#6c7a89",
                                                     linewidths=0.15, alpha=0.55))
        color = SUPPORT_COLORS.get(s["kind"], "#333333")
        ax.add_collection3d(Poly3DCollection(_support_box(s), facecolor=color, alpha=0.9, edgecolor="#111111",
                                             linewidths=0.5))
        ax.plot(*_tie_loop(s).T, color="#111111", lw=1.2)
        for wire_id in s["wires"]:
            path = np.asarray(routes["wires"][wire_id]["path"]) * MM
            inside = np.all((path >= lo) & (path <= hi), axis=1)
            if inside.sum() >= 2:
                ax.plot(*path[inside].T, color=_wire_color(colors.get(wire_id, "")), lw=2.0)
        ax.set_xlim(lo[0], hi[0]); ax.set_ylim(lo[1], hi[1]); ax.set_zlim(lo[2], hi[2])
        ax.set_box_aspect((1, 1, 1))
        ax.view_init(elev=30, azim=-60)
        ax.tick_params(labelsize=5)
        ax.set_title(f"{s['id']}: {s['mpn'] or s['type']} on {s['host']}\n{s['kind'].replace('_', ' ')}, "
                     f"{s['permitted_motion']}, OD {s['bundle_od'] * 1e3:.1f} mm", fontsize=7)
    fig.suptitle(f"{title}: support hardware on the CAD faces (host triangles in grey)", fontsize=10)
    path = out_dir / "supports_detail.png"
    fig.tight_layout(); fig.savefig(path); plt.close(fig)
    return [path]


def render_witnesses(out_dir: Path, obstacles: ObstacleSet, routes: dict[str, Any], colors: dict[str, str],
                     witnesses: list[dict[str, Any]], title: str) -> list[Path]:
    """Nearest-point witness pairs of every clearance finding: the segment from the point on
    the bundle surface to the point on the part, labelled with all three distances."""
    if not witnesses:
        return []
    wires = {k: dict(v, color=colors.get(k, "")) for k, v in routes["wires"].items()}
    paths = []
    fig, axes = plt.subplots(2, 1, figsize=(12, 10), dpi=150, gridspec_kw={"height_ratios": [1.15, 0.85]})
    for ax, view in zip(axes, ("top", "side")):
        a, b, _, _ = VIEWS[view]
        _draw_obstacles_2d(ax, obstacles, view)
        _draw_wires_2d(ax, wires, view, lw=1.0, alpha=0.6)
        for k, w in enumerate(witnesses):
            p, q = np.asarray(w["witness_wire"]) * MM, np.asarray(w["witness_part"]) * MM
            color = WITNESS_COLORS.get(w["code"], "#333333")
            ax.plot([p[a], q[a]], [p[b], q[b]], color=color, lw=1.6, zorder=8)
            ax.scatter([p[a]], [p[b]], color=color, s=14, zorder=9, marker="o")
            ax.scatter([q[a]], [q[b]], color=color, s=14, zorder=9, marker="x")
            if view == "top":
                mujoco = w["distance_mujoco"]
                ax.annotate(f"{k + 1}: {w['component']} {w['distance_exact'] * 1e3:.1f} mm"
                            f" (mj {mujoco * 1e3:.1f})" if mujoco is not None else f"{k + 1}",
                            (q[a], q[b]), xytext=(3, -8), textcoords="offset points", fontsize=5, color=color, zorder=10)
        for code, color in WITNESS_COLORS.items():
            ax.plot([], [], color=color, lw=1.6, label=code)
        _finish_2d(ax, view, f"{title}: clearance witnesses ({view}); o = bundle surface, x = part surface")
    axes[0].legend(loc="upper right", fontsize=6, frameon=False)
    path = out_dir / "clearance_witnesses.png"
    fig.tight_layout(); fig.savefig(path); plt.close(fig)
    paths.append(path)
    return paths


def render_planning(out_dir: Path, obstacles: ObstacleSet, before: dict[str, np.ndarray], routes: dict[str, Any],
                    colors: dict[str, str], violations: list[dict[str, Any]], title: str) -> list[Path]:
    """Router polyline (dashed) vs. curvature-constrained plan (solid), with the
    infeasibility constraints the planner returned."""
    routed = {k: {"path": v, "harness": routes["wires"][k]["harness"], "color": colors.get(k, "")}
              for k, v in before.items() if k in routes["wires"]}
    planned = {k: dict(v, color=colors.get(k, "")) for k, v in routes["wires"].items()}
    fig, axes = plt.subplots(2, 1, figsize=(12, 10), dpi=150, gridspec_kw={"height_ratios": [1.15, 0.85]})
    for ax, view in zip(axes, ("top", "side")):
        a, b, _, _ = VIEWS[view]
        _draw_obstacles_2d(ax, obstacles, view)
        _draw_wires_2d(ax, routed, view, lw=0.8, ls="--", alpha=0.5)
        _draw_wires_2d(ax, planned, view, lw=1.3)
        for v in violations:
            p = np.asarray(v["position"]) * MM
            ax.scatter([p[a]], [p[b]], marker="X", s=36, color="#ff00ff", edgecolor="#111111", lw=0.4, zorder=9)
        ax.scatter([], [], marker="X", s=36, color="#ff00ff", label="infeasible planning constraint")
        ax.plot([], [], color="#666666", ls="--", label="voxel router (before)")
        ax.plot([], [], color="#666666", label="curvature-constrained plan")
        _finish_2d(ax, view, f"{title}: planned harnesses ({view})")
    axes[0].legend(loc="upper right", fontsize=7, frameon=False)
    path = out_dir / "planned_routes.png"
    fig.tight_layout(); fig.savefig(path); plt.close(fig)
    return [path]


def render_derating(out_dir: Path, derating: dict[str, dict[str, Any]], currents: dict[str, float] | None,
                    title: str) -> list[Path]:
    names = list(derating)
    free = np.array([derating[n]["free_air_ampacity"] for n in names])
    installed = np.array([derating[n]["installed_ampacity"] for n in names])
    fig, ax = plt.subplots(figsize=(10, 0.28 * len(names) + 1.5), dpi=150)
    y = np.arange(len(names))
    ax.barh(y, free, color="#c9d6e8", label="free air (catalog)")
    ax.barh(y, installed, color="#3b6fb6", label="installed (bundle x ambient)")
    if currents:
        cur = [currents.get(n, np.nan) for n in names]
        ax.scatter(cur, y, color="#d62728", marker="|", s=80, label="continuous current (electrical stage)")
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=6)
    ax.invert_yaxis()
    ax.set_xlabel("A", fontsize=8)
    ax.set_title(f"{title}: ampacity derated for bundling (NEC 310.15(C)(1)) and enclosure temperature", fontsize=9)
    ax.legend(fontsize=7, frameon=False, loc="lower right")
    ax.grid(True, axis="x", color="#dddddd", lw=0.4)
    path = out_dir / "ampacity_derating.png"
    fig.tight_layout(); fig.savefig(path); plt.close(fig)
    return [path]


__all__ = ["render_derating", "render_planning", "render_support_details", "render_supports", "render_witnesses"]

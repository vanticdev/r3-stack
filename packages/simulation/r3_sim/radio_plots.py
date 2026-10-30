"""Radio coverage map PNG: Sionna RT radio map around the course plus coherent path-solver RSSI along the route.

Left panel: ``RadioMapSolver`` (handset transmitting, cell-averaged path gain,
isotropic map receiver scaled to the catalog robot-antenna gain) over the area
the robot drove near the transmitter. Right panel: ``PathSolver`` coherent RSSI
at the carrier on a grid over the full route, which shows the two-ray fringes.
Both use the static scene: ground, obstacles and antennas, without the robot
body or shadowing. The white contour is the link threshold (noise +
interference + required SNR at 10 % PER). With the analytic fallback both
panels show the fallback model and are labeled so.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np

from .plots import INK, MUTED, SERIES, _style


def _cached(cache_dir: Path, key: str, compute: Any) -> dict[str, np.ndarray]:
    path = cache_dir / f"coverage_{hashlib.sha256(key.encode()).hexdigest()[:16]}.npz"
    if path.exists():
        with np.load(path) as data:
            return {k: data[k] for k in data.files}
    result = compute()
    cache_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **{k: np.asarray(v) for k, v in result.items()})
    return result


def _near_area(rec: dict[str, np.ndarray], tx: np.ndarray, obstacles: list[dict[str, Any]], radius: float = 40.0) -> tuple[np.ndarray, np.ndarray]:
    near = np.hypot(rec["x"] - tx[0], rec["y"] - tx[1]) <= radius
    xs = np.concatenate([rec["x"][near], [tx[0]], [o["center"][0] for o in obstacles]])
    ys = np.concatenate([rec["y"][near], [tx[1]], [o["center"][1] for o in obstacles]])
    lo = np.array([xs.min(), ys.min()]) - 6.0
    hi = np.array([xs.max(), ys.max()]) + 6.0
    span = np.maximum(hi - lo, 20.0)
    center = (lo + hi) / 2.0
    return center, span


def plot_coverage(radio: Any, rec: dict[str, np.ndarray], context: dict[str, Any], out_dir: Path, cache_dir: Path,
                  h_rx: float) -> Path | None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if not len(rec.get("x", [])):
        return None
    model = radio.model
    tx = np.asarray(radio.tx_position, dtype=float)
    obstacles = context.get("obstacles") or []
    threshold = radio.noise_dbm + radio.snr_required_db
    digest = radio.scene.digest() + model.name

    # Left: course area radio map.
    center, span = _near_area(rec, tx, obstacles)
    cell = 0.25 if float(np.max(span)) <= 120.0 else 0.5
    def near_map() -> dict[str, np.ndarray]:
        if model.name == "sionna_rt":
            # Rays cross a low (0.18 m) measurement plane at grazing angles; 3e7 rays keep a 40 m area filled.
            result = model.radio_map(center, (float(span[0]), float(span[1])), cell, h_rx, samples=30_000_000)
            return {"x": result["x"], "y": result["y"], "rss": radio.tx_power_dbm + result["gain_db"]}
        xs = center[0] - span[0] / 2 + 1.0 * (np.arange(int(span[0])) + 0.5)
        ys = center[1] - span[1] / 2 + 1.0 * (np.arange(int(span[1])) + 0.5)
        gx, gy = np.meshgrid(xs, ys)
        pts = np.column_stack([gx.ravel(), gy.ravel(), np.full(gx.size, h_rx)])
        return {"x": xs, "y": ys, "rss": radio.mean_rx_power_dbm(pts)[0].reshape(gx.shape)}
    near = _cached(cache_dir, f"near|{digest}|{np.round(center, 2)}|{np.round(span, 2)}|{cell}|{h_rx:.3f}", near_map)

    # Right: coherent RSSI over the whole route.
    x_lo = min(float(np.min(rec["x"])), tx[0]) - 5.0
    x_hi = max(float(np.max(rec["x"])), tx[0] + 20.0) * 1.05 + 5.0
    y_half = max(20.0, float(np.max(np.abs(rec["y"] - tx[1]))) + 10.0)
    nx, ny = 120, 21
    def route_map() -> dict[str, np.ndarray]:
        xs = np.linspace(x_lo, x_hi, nx)
        ys = tx[1] + np.linspace(-y_half, y_half, ny)
        gx, gy = np.meshgrid(xs, ys)
        pts = np.column_stack([gx.ravel(), gy.ravel(), np.full(gx.size, h_rx)])
        return {"x": xs, "y": ys, "rss": radio.mean_rx_power_dbm(pts)[0].reshape(gx.shape)}
    route = _cached(cache_dir, f"route|{digest}|{x_lo:.1f}|{x_hi:.1f}|{y_half:.1f}|{nx}|{ny}|{h_rx:.3f}", route_map)

    finite = np.concatenate([near["rss"][np.isfinite(near["rss"])], route["rss"][np.isfinite(route["rss"])]])
    vmax = float(np.percentile(finite, 99.5)) if finite.size else -20.0
    vmin = threshold - 25.0
    fig, axes = plt.subplots(2, 1, figsize=(12, 10), dpi=130, gridspec_kw={"height_ratios": [1.0, 1.0]})
    titles = (f"{'Sionna RT radio map' if model.name == 'sionna_rt' else 'FALLBACK analytic'} near the TX "
              f"({near_cell_label(model, cell)}; blank = no ray sample)",
              f"{'Sionna RT path solver, coherent at the carrier' if model.name == 'sionna_rt' else 'FALLBACK analytic'}: along the route")
    for ax, grid, title in zip(axes, (near, route), titles):
        rss = np.ma.masked_invalid(grid["rss"])
        mesh = ax.pcolormesh(grid["x"], grid["y"], rss, shading="nearest", cmap="viridis", vmin=vmin, vmax=vmax, rasterized=True)
        if np.isfinite(grid["rss"]).any() and np.nanmin(grid["rss"]) < threshold < np.nanmax(grid["rss"]):
            ax.contour(grid["x"], grid["y"], np.nan_to_num(grid["rss"], nan=vmin - 50), levels=[threshold], colors="white", linewidths=0.9)
        ax.plot(rec["x"], rec["y"], color=SERIES[1], linewidth=1.0, label="robot path")
        ax.scatter([tx[0]], [tx[1]], marker="*", s=120, color="white", edgecolor=INK, zorder=5, label="pilot TX")
        for obstacle in obstacles:
            ax.scatter([obstacle["center"][0]], [obstacle["center"][1]], marker="s", s=25, color=SERIES[3], edgecolor=INK, zorder=5)
        lost = context.get("link_lost")
        if lost:
            ax.scatter([lost["x"]], [lost["y"]], marker="X", s=80, color=SERIES[1], edgecolor="white", zorder=6,
                       label=f"link lost ({lost['distance']:.0f} m)")
        ax.set_xlim(float(grid["x"][0]), float(grid["x"][-1]))
        ax.set_ylim(float(grid["y"][0]), float(grid["y"][-1]))
        if ax is axes[0]:
            ax.set_aspect("equal", adjustable="datalim")
        _style(ax, title, "x (m)", "y (m)")
        ax.legend(frameon=True, fontsize=7, loc="upper right")
    bar = fig.colorbar(mesh, ax=axes, shrink=0.9, pad=0.01)
    bar.set_label(f"RX power at {h_rx:.2f} m (dBm); white = link threshold {threshold:.1f} dBm", color=MUTED, fontsize=9)
    fig.suptitle(f"{context.get('scenario', '')}: 2.4 GHz coverage from the pilot handset "
                 f"({radio.tx_power_dbm:.0f} dBm), static scene, no shadowing", x=0.01, ha="left", fontsize=10, color=MUTED)
    out = out_dir / "coverage_map.png"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    return out


def near_cell_label(model: Any, cell: float) -> str:
    return f"cell-averaged, {cell} m cells" if model.name == "sionna_rt" else "1 m grid"

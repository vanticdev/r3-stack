"""PNG plots of a scenario recording (matplotlib, Agg backend)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

INK = "#1f2430"
MUTED = "#6b7280"
GRID = "#e5e7eb"
SERIES = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed"]


def _style(ax: Any, title: str, xlabel: str, ylabel: str) -> None:
    ax.set_title(title, loc="left", fontsize=11, color=INK, fontweight="bold")
    ax.set_xlabel(xlabel, color=MUTED, fontsize=9)
    ax.set_ylabel(ylabel, color=MUTED, fontsize=9)
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.tick_params(colors=MUTED, labelsize=8)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(GRID)


def _events(ax: Any, events: list[dict[str, Any]], kinds: tuple[str, ...]) -> None:
    for event in events:
        if event.get("event") in kinds:
            ax.axvline(event["t"], color=SERIES[1], linewidth=0.8, linestyle="--", alpha=0.7)


def plot_all(rec: dict[str, np.ndarray], context: dict[str, Any], out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    t = rec["t"]
    title = context["scenario"]

    # Trajectory vs course
    fig, ax = plt.subplots(figsize=(8, 5.5), dpi=130)
    waypoints = (context.get("mission") or {}).get("waypoints") or []
    if waypoints:
        wp = np.asarray(waypoints, dtype=float)
        closed = np.vstack([wp, wp[:1]]) if len(wp) > 2 else wp
        ax.plot(closed[:, 0], closed[:, 1], color=MUTED, linestyle="--", linewidth=1.2, label="course")
        ax.scatter(wp[:, 0], wp[:, 1], color=MUTED, s=18, zorder=3)
    ax.plot(rec["x"], rec["y"], color=SERIES[0], linewidth=1.6, label="vehicle (true)")
    for obstacle in context.get("obstacles", []):
        c = obstacle["center"]
        ax.scatter([c[0]], [c[1]], marker="^", color=SERIES[3], s=40, label=obstacle.get("id", "obstacle"))
    tx = context.get("tx_position")
    if tx is not None:
        ax.scatter([tx[0]], [tx[1]], marker="*", color=SERIES[1], s=90, label="radio TX", zorder=4)
    lost = context.get("link_lost")
    if lost:
        ax.scatter([lost["x"]], [lost["y"]], marker="x", color=SERIES[1], s=60, label=f"link lost ({lost['distance']:.0f} m)", zorder=5)
    ax.set_aspect("equal", adjustable="datalim")
    _style(ax, f"{title}: trajectory vs course", "x east (m)", "y north (m)")
    ax.legend(frameon=False, fontsize=8, loc="best")
    fig.tight_layout()
    paths.append(out / "trajectory.png")
    fig.savefig(paths[-1])
    plt.close(fig)

    # Speed + steering
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 5.5), dpi=130, sharex=True)
    ax1.plot(t, rec["speed"], color=SERIES[0], linewidth=1.3, label="forward speed")
    target = (context.get("mission") or {}).get("speed")
    if target:
        ax1.axhline(float(target), color=MUTED, linestyle=":", linewidth=1, label="mission speed")
    _events(ax1, context.get("events", []), ("brownout",))
    _style(ax1, f"{title}: speed and steering", "", "m/s")
    ax1.legend(frameon=False, fontsize=8)
    ax2.plot(t, np.degrees(rec["steer"]), color=SERIES[2], linewidth=1.2, label="kingpin angle (mean)")
    ax2.plot(t, np.degrees(rec["steer_target"]), color=SERIES[3], linewidth=0.8, linestyle="--", label="linkage target")
    _style(ax2, "", "time (s)", "deg")
    ax2.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    paths.append(out / "speed.png")
    fig.savefig(paths[-1])
    plt.close(fig)

    # Battery
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 5.5), dpi=130, sharex=True)
    ax1.plot(t, rec["battery_v"], color=SERIES[0], linewidth=1.2, label="battery terminal")
    ax1.plot(t, rec["rail_5v"], color=SERIES[2], linewidth=1.0, label="5 V rail")
    ax1.plot(t, rec["bec_v"], color=SERIES[4], linewidth=1.0, label="BEC rail")
    _events(ax1, context.get("events", []), ("brownout",))
    _style(ax1, f"{title}: battery and rails", "", "V")
    ax1.legend(frameon=False, fontsize=8, ncol=3)
    ax2.plot(t, 100.0 * rec["soc"], color=SERIES[3], linewidth=1.2)
    _style(ax2, "", "time (s)", "state of charge (%)")
    fig.tight_layout()
    paths.append(out / "battery.png")
    fig.savefig(paths[-1])
    plt.close(fig)

    # RSSI vs distance
    fig, ax = plt.subplots(figsize=(8, 4.8), dpi=130)
    d = np.maximum(rec["tx_distance"], 0.5)
    ax.scatter(d, rec["rssi"], s=2, color=SERIES[0], alpha=0.5, label="uplink RX power")
    curve = context.get("rssi_curve")
    if curve:
        ax.plot(curve["distance"], curve["rssi"], color=INK, linewidth=1.0, label="two-ray mean (no shadowing)")
    ax.axhline(context["sensitivity_dbm"], color=SERIES[1], linestyle="--", linewidth=1, label="datasheet sensitivity")
    ax.axhline(context["noise_dbm"] + context["snr_required_db"], color=SERIES[3], linestyle=":", linewidth=1,
               label="required level (noise + interference)")
    ax.set_xscale("log")
    _style(ax, f"{title}: RSSI vs distance", "distance from transmitter (m)", "dBm")
    ax2 = ax.twinx()
    ax2.plot(d, rec["lq"], color=SERIES[2], linewidth=0.8, alpha=0.7)
    ax2.set_ylabel("LQ (%)", color=SERIES[2], fontsize=9)
    ax2.set_ylim(0, 105)
    ax2.tick_params(colors=SERIES[2], labelsize=8)
    ax.legend(frameon=False, fontsize=8, loc="lower left")
    fig.tight_layout()
    paths.append(out / "rssi_vs_distance.png")
    fig.savefig(paths[-1])
    plt.close(fig)

    # Currents
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=130)
    ax.plot(t, rec["motor_current"], color=SERIES[0], linewidth=1.0, label="motor")
    ax.plot(t, rec["battery_i"], color=SERIES[1], linewidth=1.0, label="battery")
    ax.plot(t, rec["hat_current"], color=SERIES[2], linewidth=1.0, label="HAT supply")
    ax.plot(t, rec["servo_current"], color=SERIES[4], linewidth=0.8, label="servo")
    _style(ax, f"{title}: currents", "time (s)", "A")
    ax.legend(frameon=False, fontsize=8, ncol=4)
    fig.tight_layout()
    paths.append(out / "currents.png")
    fig.savefig(paths[-1])
    plt.close(fig)
    return paths

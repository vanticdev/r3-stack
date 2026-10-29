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
    ax1.plot(t, rec["bec_v"], color=SERIES[4], linewidth=1.0, label="servo rail")
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
    ax.scatter(d, rec["rssi"], s=2, color=SERIES[0], alpha=0.5, label=f"uplink RX power ({context.get('radio_label', 'model')}, per packet)")
    curve = context.get("rssi_curve")
    if curve:
        if curve.get("backend") == "sionna_rt":
            ax.plot(curve["distance"], curve["rssi"], color=INK, linewidth=1.0, label="Sionna RT, static scene, carrier (no shadowing)")
            ax.plot(curve["distance"], curve["rssi_local_mean"], color=INK, linewidth=0.8, linestyle=(0, (1, 1)),
                    label="Sionna RT incoherent path sum")
            ax.plot(curve["distance"], curve["rssi_analytic"], color=SERIES[4 % len(SERIES)], linewidth=1.0, linestyle="--",
                    label="previous analytic two-ray model")
        else:
            ax.plot(curve["distance"], curve["rssi"], color=INK, linewidth=1.0, label="analytic two-ray FALLBACK mean (no shadowing)")
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

    fault = context.get("fault")
    if fault is not None and "esc_pulse_pi" in rec:
        paths.append(_failsafe_plot(rec, context, fault, out / "failsafe.png"))
    return paths


def _failsafe_plot(rec: dict[str, np.ndarray], context: dict[str, Any], fault: dict[str, Any], path: Path) -> Path:
    """Speed, the Pi's ESC pulse vs the pulse the ESC sees, and the arbiter state around an injected fault."""
    t = rec["t"]
    window = (t >= fault["t"] - 2.0) & (t <= fault["t"] + 8.0)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 5.5), dpi=130, sharex=True)
    ax1.plot(t[window], rec["speed"][window], color=SERIES[0], linewidth=1.3, label="forward speed")
    ax1.axvline(fault["t"], color=SERIES[1], linestyle="--", linewidth=1.0, label=f"{fault['type']} (t={fault['t']:.2f} s)")
    for event in context.get("events", []):
        if event.get("event") == "arbiter_failsafe" and event["t"] >= fault["t"] - 1e-9:
            ax1.axvline(event["t"], color=SERIES[3], linestyle=":", linewidth=1.0, label=f"arbiter neutral (t={event['t']:.3f} s)")
            break
    _style(ax1, f"{context['scenario']}: failsafe", "", "m/s")
    ax1.legend(frameon=False, fontsize=8)
    ax2.plot(t[window], rec["esc_pulse_pi"][window], color=SERIES[4], linewidth=1.0, label="Pi PWM (GPIO18)")
    ax2.plot(t[window], rec["esc_pulse"][window], color=SERIES[2], linewidth=1.3, linestyle="--", label="pulse at the ESC")
    ax3 = ax2.twinx()
    ax3.fill_between(t[window], 0, rec["arbiter_failsafe"][window], color=SERIES[1], alpha=0.12, step="post")
    ax3.set_ylim(0, 4)
    ax3.set_yticks([])
    _style(ax2, "", "time (s)", "pulse width (us)")
    ax2.legend(frameon=False, fontsize=8, loc="lower left")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def plot_overview(outcomes: list[Any], path: Path) -> Path:
    """One row per scenario: every expectation as a pass/fail cell with the measured value."""
    rows = [(o.scenario, c) for o in outcomes for c in o.summary["checks"]]
    fig, ax = plt.subplots(figsize=(9, 0.42 * max(len(rows), 1) + 1.2), dpi=130)
    ax.set_axis_off()
    for i, (scenario, check) in enumerate(rows):
        y = len(rows) - i
        color = SERIES[2] if check["pass"] else SERIES[1]
        ax.add_patch(plt.Rectangle((0.0, y - 0.4), 0.06, 0.8, color=color, transform=ax.transData))
        ax.text(0.08, y, scenario, va="center", fontsize=8.5, color=INK, fontweight="bold")
        ax.text(0.36, y, check["key"], va="center", fontsize=8.5, color=INK)
        measured = check["measured"]
        ax.text(0.70, y, f"{measured}", va="center", fontsize=8.5, color=color)
        ax.text(0.86, y, f"limit {check['expected']}", va="center", fontsize=8.5, color=MUTED)
    ax.set_xlim(0, 1)
    ax.set_ylim(0.3, len(rows) + 0.9)
    passed = sum(o.passed for o in outcomes)
    ax.set_title(f"Digital twin scenarios: {passed}/{len(outcomes)} passed", loc="left", fontsize=11, color=INK, fontweight="bold")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path

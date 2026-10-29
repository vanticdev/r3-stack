"""RTK evidence plot: rover solution mode and correction age, and the 915 MHz correction-link budget per packet."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from .plots import MUTED, SERIES, _style

MODE_LEVEL = {1.0: 0, 5.0: 1, 4.0: 2}  # GGA quality -> plot level (single, float, fixed)


def plot_rtk(rec: dict[str, np.ndarray], link: Any, out_dir: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True, constrained_layout=True)
    t = rec["t"]
    level = np.array([MODE_LEVEL.get(float(q), 0) for q in rec.get("gnss_mode", np.ones_like(t))])
    axes[0].step(t, level, where="post", color=SERIES[0])
    axes[0].set_yticks([0, 1, 2], ["single", "RTK float", "RTK fixed"])
    _style(axes[0], "GNSS rover solution (GGA fix quality)", "", "")
    age = np.where(rec.get("gnss_age", np.full_like(t, np.nan)) < 900, rec.get("gnss_age", np.full_like(t, np.nan)), np.nan)
    axes[1].plot(t, age, color=SERIES[3], linewidth=1.0)
    _style(axes[1], "Age of RTCM3 corrections at the rover", "", "age (s)")
    samples = link.samples
    if samples:
        ts = np.array([s.t for s in samples])
        power = np.array([s.rx_power_dbm for s in samples])
        ok = np.array([s.received for s in samples])
        axes[2].plot(ts[ok], power[ok], ".", markersize=2, color=SERIES[2], label="packet received")
        axes[2].plot(ts[~ok], power[~ok], "x", markersize=4, color=SERIES[1], label="packet lost")
        axes[2].axhline(link.sensitivity_dbm, color=MUTED, linestyle="--", linewidth=1.0,
                        label=f"sensitivity {link.sensitivity_dbm:.0f} dBm @ {link.air_rate / 1e3:.0f} kbit/s")
        axes[2].legend(loc="lower left", fontsize=8, frameon=False)
    _style(axes[2], f"SiK {link.frequency / 1e6:.0f} MHz correction link: received power per packet ({link.model.label})",
           "time (s)", "RX power (dBm)")
    path = out_dir / "rtk_gnss.png"
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path

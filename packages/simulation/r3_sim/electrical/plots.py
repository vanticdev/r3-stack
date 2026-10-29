"""PNG evidence for the electrical stage (matplotlib, Agg backend)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

from .models.battery import BatteryLipo  # noqa: E402
from .models.board import BoardPcb  # noqa: E402
from .models.esc import BrushedEsc  # noqa: E402
from .models.fuse import FuseBlade  # noqa: E402
from .models.loads import BuckRegulator, ComputerLoad, ConstantLoad, ServoPwm  # noqa: E402
from .models.motor import DcBrushedMotor  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#8a8984"
GRID = "#e4e3df"
SERIES_CONTINUOUS = "#2a78d6"
SERIES_BURST = "#eb6834"
NET_FILL = "#cde2fb"
COMPONENT_FILL = "#f1f0ec"


def _style(ax: Any) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(INK_MUTED)
    ax.tick_params(colors=INK_SECONDARY, labelsize=8)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def _ports(system: Any, cid: str, model: Any) -> tuple[list[str], list[str]]:
    """(pin labels the component draws power from, pin labels it supplies)."""
    c = lambda pin: f"C:{cid}.{pin.name}"  # noqa: E731
    if isinstance(model, BatteryLipo):
        return [], [c(model.pos)]
    if isinstance(model, BrushedEsc):
        return [c(model.vin)], [c(model.m_a)] + ([c(model.bec)] if model.bec_reg is not None else [])
    if isinstance(model, DcBrushedMotor):
        return [c(model.pos)], []
    if isinstance(model, FuseBlade) and model.branch is not None:
        graph = system.project.graph
        sources = {graph.net_of(f"C:{b}.{m.pos.name}") for b, m in system.models.items() if isinstance(m, BatteryLipo)}
        a, b = model.branch.node_a, model.branch.node_b
        return ([a], [b]) if graph.net_of(a) in sources else ([b], [a])
    if isinstance(model, BoardPcb) and model.core is not None:
        return [c(model.vin_pin)], [c(model.vout_pin)]
    if isinstance(model, BuckRegulator):
        return [f"C:{cid}.{n}" for n, p in model.pins.items() if p.role == "power_in"][:1], \
               [f"C:{cid}.{n}" for n, p in model.pins.items() if p.role == "power_out"][:1]
    if isinstance(model, ComputerLoad):
        return [c(model.vin)], [c(p) for p in model.rail_pins]
    if isinstance(model, (ServoPwm, ConstantLoad)):
        return [c(model.vin)], []
    return [], []


def power_tree(system: Any, point: dict[str, Any], path: Path) -> None:
    graph = system.project.graph
    net_name = {m: name for name, members in system.nets.items() for m in members}
    pins = point["_pins"]
    currents = point["pin_currents"]
    consumers: dict[str, list[tuple[str, str]]] = {}
    suppliers: dict[str, list[tuple[str, str]]] = {}
    leaves: list[tuple[str, str, float]] = []  # (label, net, current)
    for cid, model in system.models.items():
        draws, supplies = _ports(system, cid, model)
        for label in draws:
            consumers.setdefault(net_name.get(label, label), []).append((cid, label))
        for label in supplies:
            suppliers.setdefault(cid, []).append((net_name.get(label, label), label))
        if isinstance(model, BoardPcb):
            for name, info in model.device_info.items():
                leaves.append((f"{cid}.{name}", net_name.get(f"C:{cid}.{info['supply_pin']}", ""),
                               point["components"].get(cid, {}).get("devices", {}).get(name, {}).get("current", info["current"])))
    roots = [cid for cid, m in system.models.items() if isinstance(m, BatteryLipo)]
    layer: dict[str, int] = {r: 0 for r in roots}
    queue = list(roots)
    while queue:
        node = queue.pop(0)
        if node in system.models:  # component -> nets it supplies
            for net, _ in suppliers.get(node, []):
                if net not in layer:
                    layer[net] = layer[node] + 1
                    queue.append(net)
        else:  # net -> components drawing from it
            for cid, _ in consumers.get(node, []):
                if cid not in layer:
                    layer[cid] = layer[node] + 1
                    queue.append(cid)
    for label, net, _ in leaves:
        if net in layer:
            layer[label] = layer[net] + 1
    columns: dict[int, list[str]] = {}
    for item, depth in layer.items():
        columns.setdefault(depth, []).append(item)
    width = max(columns) + 1 if columns else 1
    height = max(len(v) for v in columns.values()) if columns else 1
    fig, ax = plt.subplots(figsize=(2.3 * width + 1, 1.25 * height + 1.6), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ax.axis("off")
    parents: dict[str, list[str]] = {}
    for cid, outs in suppliers.items():
        for net, _ in outs:
            parents.setdefault(net, []).append(cid)
    for net, ins in consumers.items():
        for cid, _ in ins:
            parents.setdefault(cid, []).append(net)
    for label, net, _ in leaves:
        parents.setdefault(label, []).append(net)
    position: dict[str, tuple[float, float]] = {}
    for depth in sorted(columns):
        items = columns[depth]

        def barycenter(item: str) -> float:  # order by parents' heights to avoid crossings
            ys = [position[p][1] for p in parents.get(item, []) if p in position and layer.get(p, -1) < depth]
            return -sum(ys) / len(ys) if ys else 0.0

        items.sort(key=lambda item: (barycenter(item), item))
        for k, item in enumerate(items):
            position[item] = (depth * 2.3, -(k - (len(items) - 1) / 2) * 1.25)
    box_w, box_h = 1.75, 0.82

    def edge(a: str, b: str, text: str) -> None:
        (xa, ya), (xb, yb) = position[a], position[b]
        ax.annotate("", xy=(xb - box_w / 2, yb), xytext=(xa + box_w / 2, ya),
                    arrowprops=dict(arrowstyle="-|>", color=INK_MUTED, lw=1.2, shrinkA=0, shrinkB=0))
        f = 0.5 if abs(ya - yb) < 1e-6 else 0.28  # label near the source end on diagonal edges
        lx, ly = xa + box_w / 2 + f * (xb - xa - box_w), ya + f * (yb - ya)
        ax.text(lx, ly + 0.06, text, fontsize=6.5, color=INK_SECONDARY, ha="center", va="bottom",
                bbox=dict(boxstyle="round,pad=0.15", facecolor=SURFACE, edgecolor="none", alpha=0.9))

    for item, (x, y) in position.items():
        is_net = item not in system.models and not any(item == leaf[0] for leaf in leaves)
        fill = NET_FILL if is_net else COMPONENT_FILL
        ax.add_patch(FancyBboxPatch((x - box_w / 2, y - box_h / 2), box_w, box_h, boxstyle="round,pad=0.02,rounding_size=0.08",
                                    facecolor=fill, edgecolor=INK_MUTED, linewidth=0.8))
        if is_net:
            rep = system.net_representative.get(item, "")
            volts = pins.get(rep[2:], point["nets"].get(item, 0.0))
            ax.text(x, y + 0.12, item, ha="center", va="center", fontsize=8.5, color=INK, weight="bold")
            ax.text(x, y - 0.17, f"{volts:.3f} V", ha="center", va="center", fontsize=7.5, color=INK_SECONDARY)
        else:
            model = system.models.get(item)
            subtitle = model.model if model is not None else "on-board IC"
            ax.text(x, y + 0.14, item, ha="center", va="center", fontsize=8.5, color=INK, weight="bold")
            ax.text(x, y - 0.16, subtitle, ha="center", va="center", fontsize=6.5, color=INK_SECONDARY)
    for cid, outs in suppliers.items():
        for net, label in outs:
            if cid in position and net in position:
                i = -currents.get(label[2:], 0.0)
                edge(cid, net, f"{i:.2f} A  {i * pins.get(label[2:], 0.0):.2f} W")
    for net, ins in consumers.items():
        for cid, label in ins:
            if cid in position and net in position:
                i = currents.get(label[2:], 0.0)
                edge(net, cid, f"{i:.3f} A  {i * pins.get(label[2:], 0.0):.2f} W" if abs(i) < 1 else f"{i:.2f} A  {i * pins.get(label[2:], 0.0):.1f} W")
    for label, net, current in leaves:
        if label in position and net in position:
            edge(net, label, f"{current * 1e3:.1f} mA")
    xs = [p[0] for p in position.values()] or [0]
    ys = [p[1] for p in position.values()] or [0]
    ax.set_xlim(min(xs) - 1.2, max(xs) + 1.2)
    ax.set_ylim(min(ys) - 1.0, max(ys) + 1.0)
    ax.set_title(f"Power tree — {point['operating_point']} @ {point['battery_state']} battery (SOC {point['soc']:.2f})",
                 fontsize=11, color=INK, loc="left")
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def wire_currents(worst: dict[str, Any], path: Path) -> None:
    """Utilization (current / ampacity) per wire: continuous and burst worst cases."""
    rows = sorted(worst.items(), key=lambda kv: (kv[1]["harness"], kv[0]))
    labels = [f"{wid}  ({w['awg']} AWG)" for wid, w in rows]
    cont = [100 * w["continuous_current"] / w["ampacity"] for _, w in rows]
    burst = [100 * w.get("burst_current", 0.0) / w["ampacity"] for _, w in rows]
    n = len(rows)
    fig, ax = plt.subplots(figsize=(10, 0.36 * n + 1.8), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    _style(ax)
    y = list(range(n))
    bar = 0.36
    ax.barh([v - bar / 2 - 0.02 for v in y], cont, height=bar, color=SERIES_CONTINUOUS, label="Worst continuous")
    ax.barh([v + bar / 2 + 0.02 for v in y], burst, height=bar, color=SERIES_BURST, label="Worst burst (≤ 2 s)")
    ax.axvline(100, color=INK, linewidth=1.0, linestyle=(0, (4, 3)))
    ax.text(100, n - 0.3, " ampacity", color=INK, fontsize=7.5, va="bottom")
    top = max(max(cont + burst + [100]) * 1.18, 110)
    for v, (_, w), c, b in zip(y, rows, cont, burst):
        ax.text(c + top * 0.005, v - bar / 2 - 0.02, f"{w['continuous_current']:.2f} A", va="center", fontsize=6.5, color=INK_SECONDARY)
        if "burst_current" in w:
            ax.text(b + top * 0.005, v + bar / 2 + 0.02, f"{w['burst_current']:.2f} A / {w['ampacity']:.0f} A", va="center",
                    fontsize=6.5, color=INK_SECONDARY)
    ax.set_yticks(y, labels, fontsize=7.5, color=INK)
    ax.invert_yaxis()
    ax.set_xlim(0, top)
    ax.set_xlabel("Current as % of continuous ampacity (free air, 30 K rise)", color=INK_SECONDARY, fontsize=8.5)
    ax.set_title("Worst-case current per wire vs ampacity", loc="left", fontsize=11, color=INK)
    ax.legend(loc="lower right", frameon=False, fontsize=8, labelcolor=INK)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def runtime_curve(runtime: dict[str, Any], path: Path) -> None:
    curve = runtime["curve"]
    t = [c["time"] / 60 for c in curve]
    fig, ax = plt.subplots(figsize=(8, 4), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    _style(ax)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.plot(t, [c["cruise_voltage"] for c in curve], color=SERIES_CONTINUOUS, linewidth=2, label="Pack voltage at cruise")
    ax.plot(t, [c["full_throttle_voltage"] for c in curve], color=SERIES_BURST, linewidth=2, label="Pack voltage at full throttle")
    ax.axhline(runtime["cutoff_voltage"], color=INK, linestyle=(0, (4, 3)), linewidth=1)
    ax.text(0, runtime["cutoff_voltage"], f" cutoff {runtime['cutoff_voltage']:.2f} V", va="bottom", fontsize=7.5, color=INK)
    ax.axvline(runtime["runtime"] / 60, color=INK_MUTED, linewidth=1)
    ax.text(runtime["runtime"] / 60, ax.get_ylim()[1], f"{runtime['runtime'] / 60:.1f} min ", ha="right", va="top", fontsize=8, color=INK)
    if runtime.get("requirement"):
        ax.axvline(runtime["requirement"] / 60, color=INK_MUTED, linewidth=1, linestyle=":")
        ax.text(runtime["requirement"] / 60, ax.get_ylim()[0], " required", va="bottom", fontsize=7.5, color=INK_SECONDARY)
    ax.set_xlabel("Time (min)", color=INK_SECONDARY, fontsize=8.5)
    ax.set_ylabel("Battery terminal voltage (V)", color=INK_SECONDARY, fontsize=8.5)
    duty = ", ".join(f"{k} {v:.0%}" for k, v in runtime["duty_cycle"].items())
    ax.set_title(f"Estimated runtime — duty cycle {duty}", loc="left", fontsize=11, color=INK)
    ax.legend(frameon=False, fontsize=8, labelcolor=INK)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def transient(trace: dict[str, list[float]], path: Path) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(8, 7), dpi=150, sharex=True)
    fig.patch.set_facecolor(SURFACE)
    series = [("motor_current", "Motor current (A)", SERIES_CONTINUOUS), ("battery_voltage", "Battery terminal (V)", SERIES_CONTINUOUS),
              ("torque", "Motor torque (N m)", SERIES_CONTINUOUS)]
    for ax, (key, label, color) in zip(axes, series):
        _style(ax)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.plot(trace["time"], trace[key], color=color, linewidth=2)
        ax.set_ylabel(label, color=INK_SECONDARY, fontsize=8.5)
        ax.ticklabel_format(axis="y", useOffset=False)
    axes[-1].set_xlabel("Time (s) — motor speed ramps linearly from 0 to "
                        f"{trace['omega'][-1]:.0f} rad/s", color=INK_SECONDARY, fontsize=8.5)
    pi = trace.get("pi_voltage") or [0.0]
    axes[0].set_title(f"Full-throttle transient (1 ms steps, battery full) — computer supply min {min(pi):.3f} V",
                      loc="left", fontsize=11, color=INK)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)

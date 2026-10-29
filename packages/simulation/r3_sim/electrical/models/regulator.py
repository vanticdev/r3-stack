"""Regulated outputs as SPICE B-sources (shared by ESC BECs, bucks and computer rails).

    V_out = softplus(min_s(V_set - R_o i, V_in - V_drop - R_drop i)) - R_fb softplus(i - I_lim)

``min_s``/``softplus`` are smooth min/max (width 20 mV / 20 mA) so the Newton
iteration stays differentiable through dropout and current-limit foldback.
Input current: linear -> i; switching -> V_out i / (eta(i) V_in) (power
balance, eta from the catalog efficiency table as a ``pwl()``); ``none`` -> the
owning model accounts for the power. An enable is a ``sw`` switch in series
with the output (UVLO comparator with hysteresis, or an external enable).
"""

from __future__ import annotations

import numpy as np

from ..netlist import Netlist, fmt, pwl, smooth_min, softplus

SMOOTH_V = 0.02  # V, smoothing width for regulator min()
SMOOTH_I = 0.02  # A
FOLDBACK_SLOPE = 20.0  # ohm, output resistance beyond the current limit (estimate)
ENABLE_MODEL = "swenable"
ENABLE_CARD = f".model {ENABLE_MODEL} sw vt=0.5 vh=0 ron=1e-5 roff=1e9"


def regulator_lines(net: Netlist, prefix: str, out: str, gnd: str, vin: str, v_set: float, r_out: float,
                    dropout: float, r_dropout: float, current_limit: float, kind: str,
                    efficiency: np.ndarray | None = None, enable: tuple[str, str, str, str] | None = None) -> list[str]:
    """Subcircuit lines of one regulated output.

    The output current is ``i(v<prefix>)`` (out of ``out``), the input
    current ``i(v<prefix>in)`` (kinds ``linear``/``switching``). ``enable`` =
    (control+, control-, switch model, initial state "on"/"off") inserts the
    enable switch; its model card must already be registered."""
    if gnd.lower() == "gnd":
        raise ValueError("ngspice treats a node named 'gnd' as the global ground; use another port name")
    i = f"i(v{prefix})"
    vin_rel = f"v({vin},{gnd})"
    regulated = smooth_min(f"{fmt(v_set)}-{fmt(r_out)}*{i}", f"{vin_rel}-{fmt(dropout)}-{fmt(r_dropout)}*{i}", SMOOTH_V)
    target = f"{softplus(regulated, SMOOTH_V)}-{FOLDBACK_SLOPE}*{softplus(f'{i}-{fmt(current_limit)}', SMOOTH_I)}"
    lines = [f"b{prefix} {prefix}_src {gnd} v = {target}"]
    if enable is not None:
        plus, minus, model, initial = enable
        lines.append(f"s{prefix} {prefix}_src {prefix}_on {plus} {minus} {model} {initial}")
        lines.append(f"v{prefix} {prefix}_on {out} dc 0")
    else:
        lines.append(f"v{prefix} {prefix}_src {out} dc 0")
    if kind == "linear":
        draw = i
    elif kind == "switching":
        table = efficiency if efficiency is not None else np.array([[0.0, 0.9], [1.0, 0.9]])
        draw = f"v({out},{gnd})*{i}/({pwl(f'abs({i})', table)}*max({vin_rel},0.5))"
    else:
        return lines
    # The B-source output current returns through gnd; draw it from vin instead
    # (through a 0 V sense, so the input current is ``i(v<prefix>in)``).
    lines.append(f"v{prefix}in {vin} {prefix}_vin dc 0")
    lines.append(f"b{prefix}in {prefix}_vin {gnd} i = {draw}")
    return lines


def efficiency_at(table: np.ndarray | None, current: float) -> float:
    if table is None:
        return 1.0
    return float(np.interp(abs(current), table[:, 0], table[:, 1]))

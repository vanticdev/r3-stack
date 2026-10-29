"""SPICE netlist construction and solution access.

A :class:`Netlist` collects ngspice cards: one ``.subckt`` per behavior-model
instance, conductor elements, ``EXTERNAL`` voltage sources for quantities
owned outside the circuit (MuJoCo speeds and torques, ESC firmware commands,
computer power), switch models and ``.ic`` initial conditions of the state
nodes. Two flavours are generated from the same models:

* ``tran`` — lockstep transient: integrator capacitors for SOC, RC pairs, fuse
  melting integrals and thermal nodes, initial conditions via ``.ic``;
* ``op``   — static operating points: SOC and the conductor self-heating gain
  are ``EXTERNAL`` sources so one loaded circuit serves every ``op`` request.

Units in the thermal subnetworks: node voltage = temperature in C, current =
heat flow in W, capacitance = heat capacity in J/K, resistance = K/W.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

GROUND = "0"
RSHUNT = 1e9  # ohm from every node to ground (``.options rshunt``): keeps signal islands well posed, like gmin


def fmt(value: float) -> str:
    return f"{float(value):.12g}"


def softplus(expr: str, width: float) -> str:
    """Numerically stable smooth max(x, 0): max(x, 0) + w ln(1 + exp(-|x| / w))."""
    return f"(max(({expr}),0)+{width:.9g}*ln(1+exp(-abs({expr})/{width:.9g})))"


def smooth_min(a: str, b: str, width: float) -> str:
    """Smooth min(a, b) = a - softplus(a - b)."""
    return f"(({a})-{softplus(f'({a})-({b})', width)})"


def temperature(node: str) -> str:
    """Temperature of a thermal node as seen by temperature-dependent elements,
    limited to -60 .. 600 C so early Newton iterates cannot make R(T) negative
    or singular (the physical solution is always inside the range)."""
    return f"max(-60,min(v({node}),600))"


def pwl(argument: str, table: Iterable[tuple[float, float]], clamp: bool = True) -> str:
    """ngspice ``pwl()`` (linear extrapolation outside the table, so the argument is clamped)."""
    points = [(float(x), float(y)) for x, y in table]
    if clamp:
        argument = f"max({fmt(points[0][0])},min({fmt(points[-1][0])},{argument}))"
    return f"pwl({argument}," + ",".join(f"{fmt(x)},{fmt(y)}" for x, y in points) + ")"


@dataclass
class Netlist:
    """Builder for one ngspice deck."""

    title: str
    mode: str  # "tran" | "op"
    body: list[str] = field(default_factory=list)
    subckts: list[str] = field(default_factory=list)
    models: dict[str, str] = field(default_factory=dict)
    externals: dict[str, str] = field(default_factory=dict)  # source name -> node
    initial: dict[str, float] = field(default_factory=dict)  # node -> .ic value
    nodeset: dict[str, float] = field(default_factory=dict)  # node -> .nodeset initial guess

    @property
    def transient(self) -> bool:
        return self.mode == "tran"

    def add(self, *lines: str) -> None:
        self.body.extend(lines)

    def comment(self, text: str) -> None:
        self.body.append(f"* {text}")

    def subckt(self, name: str, ports: list[str], lines: list[str], doc: str = "") -> None:
        block = [f"* {line}" for line in doc.strip().splitlines()] if doc else []
        block.append(f".subckt {name} {' '.join(ports)}")
        block.extend(lines)
        block.append(f".ends {name}")
        self.subckts.append("\n".join(block))

    def model(self, name: str, card: str) -> None:
        self.models[name] = card

    def external(self, name: str) -> str:
        """``EXTERNAL`` voltage source ``v<name>`` (value from the ``GetVSRCData``
        callback); returns its node. The callback receives ``v<name>``."""
        source = f"v{name}"
        node = f"ext_{name}"
        self.externals[source] = node
        self.body.append(f"{source} {node} 0 external")  # no "dc": a DC value would override the callback in operating points
        return node

    def state(self, node: str, value: float) -> None:
        """Initial condition of an integrator state node (transient only)."""
        if self.transient:
            self.initial[node] = float(value)

    def deck(self, options: str, analysis: str) -> list[str]:
        lines = [self.title]
        lines += [line for block in self.subckts for line in block.splitlines()]
        lines += list(self.models.values())
        lines += self.body
        if self.initial:
            lines += [f".ic v({node})={fmt(value)}" for node, value in self.initial.items()]
        if self.nodeset:
            lines += [f".nodeset v({node})={fmt(value)}" for node, value in self.nodeset.items()]
        lines += [options, ".save none", analysis, ".end"]
        return lines


class Solution:
    """Values of every ngspice output vector at one accepted time point.

    ``watch`` holds per-step reductions over all accepted points of the last
    co-simulation step (``min``/``max`` and time-weighted ``mean``)."""

    def __init__(self, values: np.ndarray, index: dict[str, int], time: float,
                 watch: dict[tuple[str, str], float] | None = None) -> None:
        self.values = values
        self.index = index
        self.time = time
        self.watch = watch or {}

    def get(self, name: str) -> float:
        return float(self.values[self.index[name]])

    def v(self, node: str, ref: str = GROUND) -> float:
        values, index = self.values, self.index
        a = values[index[node]] if node != GROUND else 0.0
        if ref == GROUND:
            return float(a)
        return float(a - values[index[ref]])

    def i(self, source: str) -> float:
        """Current through a voltage source (``vname`` or ``v.xinst.vname``), + node to - node inside it."""
        return float(self.values[self.index[f"{source}#branch"]])

    def reduced(self, name: str, kind: str) -> float:
        """Reduction over the last step's accepted points; the point value if not watched."""
        value = self.watch.get((name, kind))
        return self.get(name) if value is None else value

"""``fuse.blade``: melting-integral fuse (ngspice subcircuit).

The fuse element accumulates H with dH/dt = I^2 - H / tau (state node ``h``,
a 1 F integrator with a tau-ohm leak) and melts when H >= I^2t_melt. For short
pulses (t << tau) this is the adiabatic I^2 t criterion; for long overloads
H -> I^2 tau, so the fuse never blows below I_min = sqrt(I^2t_melt / tau). tau
is set from the minimum melting ratio (I_min = ratio x rating, default 1.25:
between the 110 % hold and 135 % must-open points of SAE J2077 / ISO 8820-3
blade fuses) — an estimate.

The element is a voltage-controlled switch (``sw`` model, R_off = cold
resistance, R_on = open) whose control is the melted latch. Melting is
detected on every accepted ngspice time point; the latch then opens the
switch from the next co-simulation step (at most one step of extra
let-through, negligible against the melting I^2 t).
"""

from __future__ import annotations

import math
from typing import Any

from ..netlist import Netlist, Solution, fmt
from .base import BehaviorModel, Event

OPEN_RESISTANCE = 1e9  # ohm, blown fuse


class FuseBlade(BehaviorModel):
    model = "fuse.blade"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        p = self.params
        self.rating = p.get("rating")
        self.cold_resistance = p.get("cold_resistance", 0.07 / self.rating)
        self.i2t_melt = p.get("i2t_melt")
        self.min_melt_ratio = p.get("min_melt_ratio", 1.25)
        self.tau = self.i2t_melt / (self.min_melt_ratio * self.rating) ** 2
        self.branch: Any = None
        self.h_node = self.local("h")
        self.states = {self.h_node: 0.0}
        self.s_blown = False
        self.s_blown_time: float | None = None

    @property
    def s_heat(self) -> float:
        return self.states[self.h_node]

    def attach(self, branch: Any) -> None:
        """Take over a component internal link (``kind: fuse``) of the passive network."""
        self.branch = branch

    def time_to_blow(self, current: float, initial_heat: float = 0.0) -> float:
        """Seconds until melting at constant ``current`` (inf if it never melts):
        H(t) = I^2 tau + (H0 - I^2 tau) exp(-t / tau) = I^2t_melt."""
        steady = current * current * self.tau
        if steady <= self.i2t_melt:
            return math.inf
        return -self.tau * math.log((steady - self.i2t_melt) / (steady - initial_heat))

    def build(self, net: Netlist) -> None:
        if self.branch is None:
            return
        name = f"fuse_blade_{self.inst[1:]}"
        a, b = self.ctx.node(self.branch.node_a), self.ctx.node(self.branch.node_b)
        net.model("swfuse_" + self.inst[1:], f".model swfuse_{self.inst[1:]} sw vt=0.5 vh=0 ron={fmt(OPEN_RESISTANCE)} "
                                             f"roff={fmt(self.cold_resistance)}")
        lines = [
            "vsense a n1 dc 0",
            f"sfuse n1 b blown 0 swfuse_{self.inst[1:]}",
            # melting integral dH/dt = I^2 - H / tau
            "ch h 0 1",
            f"rh h 0 {fmt(self.tau)}",
            "bh 0 h i = i(vsense)*i(vsense)",
        ]
        net.state(self.h_node, self.states[self.h_node])
        blown = self.control(net, "blown", 1.0 if self.s_blown else 0.0)
        net.subckt(name, ["a", "b", "h", "blown"], lines,
                   doc=f"{self.id}: {self.rating:g} A blade fuse, I2t_melt {self.i2t_melt:g} A2s, tau {self.tau:.3g} s")
        net.add(f"{self.inst} {a} {b} {self.h_node} {blown} {name}")

    def initialize(self, initial: dict[str, Any]) -> None:
        if initial.get("blown"):
            self.s_blown = True

    def begin_step(self, dt: float | None, inputs: dict[str, Any], time: float) -> None:
        self.controls["blown"] = 1.0 if self.s_blown else 0.0

    def watches(self) -> list[tuple[str, str]]:
        return [(self.h_node, "max")]

    def end_step(self, dt: float | None, sol: Solution, time: float) -> list[Event]:
        if dt is None or self.branch is None:
            return []
        self.capture_states(sol)
        if self.s_blown:
            return []
        peak = sol.reduced(self.h_node, "max")
        if peak >= self.i2t_melt:
            self.s_blown = True
            self.s_blown_time = time
            i = self.current(sol)
            return [Event(time, "fuse_blown", self.id, f"{self.rating:.0f} A fuse opened at {abs(i):.1f} A",
                          {"current": i, "rating": self.rating, "i2t_melt": self.i2t_melt})]
        return []

    def reset(self) -> None:
        self.s_blown = False
        self.s_blown_time = None
        self.states[self.h_node] = 0.0

    def current(self, sol: Solution) -> float:
        return sol.i(self.sense("vsense")) if self.branch is not None else 0.0

    def resistance(self) -> float:
        return OPEN_RESISTANCE if self.s_blown else self.cold_resistance

    def outputs(self, sol: Solution) -> dict[str, Any]:
        heat = sol.v(self.h_node) if self.h_node in sol.index else 0.0
        return {"current": self.current(sol), "blown": self.s_blown, "heat_i2t": heat,
                "melt_fraction": heat / self.i2t_melt, "rating": self.rating}

"""``fuse.blade``: melting-integral fuse model.

The fuse element accumulates H with dH/dt = I^2 - H / tau and melts when
H >= I^2t_melt. For short pulses (t << tau) this is the adiabatic I^2 t
criterion; for long overloads H -> I^2 tau, so the fuse never blows below
I_min = sqrt(I^2t_melt / tau). tau is set from the minimum melting ratio
(I_min = ratio x rating, default 1.25: between the 110 % hold and 135 %
must-open points of SAE J2077 / ISO 8820-3 blade fuses) — an estimate.
Exact update over dt: H = I^2 tau + (H0 - I^2 tau) exp(-dt / tau).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from .base import BehaviorModel, Event, voltage

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
        self.branch = None
        self.s_heat = 0.0
        self.s_blown = False

    def attach(self, branch: Any, circuit: Any) -> None:
        self.branch = branch
        self.circuit = circuit

    def current(self, x: np.ndarray) -> float:
        if self.branch is None:
            return 0.0
        r = self.circuit.resistors.resistance[self.branch.index]
        return voltage(x, self.branch.a, self.branch.b) / r

    def time_to_blow(self, current: float, initial_heat: float = 0.0) -> float:
        """Seconds until melting at constant ``current`` (inf if it never melts)."""
        steady = current * current * self.tau
        if steady <= self.i2t_melt:
            return math.inf
        return -self.tau * math.log((steady - self.i2t_melt) / (steady - initial_heat))

    def _set_open(self, blown: bool) -> None:
        if self.branch is not None:
            self.circuit.resistors.resistance[self.branch.index] = OPEN_RESISTANCE if blown else self.cold_resistance

    def initialize(self, initial: dict[str, Any]) -> None:
        if initial.get("blown"):
            self.s_blown = True
        self._set_open(self.s_blown)

    def begin_step(self, dt: float | None, inputs: dict[str, Any], time: float) -> None:
        self._set_open(self.s_blown)

    def end_step(self, dt: float | None, x: np.ndarray, time: float) -> list[Event]:
        if dt is None or self.s_blown:
            return []
        i = self.current(x)
        steady = i * i * self.tau
        self.s_heat = steady + (self.s_heat - steady) * math.exp(-dt / self.tau)
        if self.s_heat >= self.i2t_melt:
            self.s_blown = True
            self._set_open(True)
            return [Event(time, "fuse_blown", self.id, f"{self.rating:.0f} A fuse opened at {abs(i):.1f} A",
                          {"current": i, "rating": self.rating, "i2t_melt": self.i2t_melt})]
        return []

    def reset(self) -> None:
        self.s_blown = False
        self.s_heat = 0.0
        self._set_open(False)

    def outputs(self, x: np.ndarray) -> dict[str, Any]:
        return {"current": self.current(x), "blown": self.s_blown, "heat_i2t": self.s_heat,
                "melt_fraction": self.s_heat / self.i2t_melt, "rating": self.rating}

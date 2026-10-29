"""``battery.lipo``: equivalent-circuit lithium-polymer pack.

Per cell: OCV(SOC) in series with R0 and one RC pair (R1 || C1). Pack:
``cells_series`` cells in series, ``cells_parallel`` strings in parallel.

    v_terminal = N_s * OCV(SOC) - v_rc - R0_pack * i
    dv_rc/dt   = i / C1_pack - v_rc / (R1_pack C1_pack)
    dSOC/dt    = -i / Q                      (coulomb counting, Q in A s)
    C_th dT/dt = i^2 R0 + v_rc^2 / R1 - (T - T_amb) / theta

Backward-Euler companion of the RC pair over a step dt:
v_rc[n+1] = a v_rc[n] + b i[n+1] with a = 1 / (1 + dt/tau), b = (dt/C1) a,
so the pack is a Thevenin source E = N_s OCV - a v_rc[n], R = R0 + b.
Resistances follow Arrhenius R(T) = R25 exp(B (1/T - 1/298.15 K)) with B an
estimate (resistance roughly doubles from 25 C to 0 C for LiPo).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..mna import Circuit, VoltageSource
from .base import BehaviorModel, Event, first_order, voltage

ARRHENIUS_B_DEFAULT = 2250.0  # K, estimate


class BatteryLipo(BehaviorModel):
    model = "battery.lipo"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        p = self.params
        self.cells = int(p.get("cells_series"))
        self.parallel = int(p.get("cells_parallel", 1))
        self.capacity = p.get("capacity") * self.parallel  # A s
        self.r0_cell = p.get("r_internal_per_cell")
        rc = p.raw("rc_pair") or {}
        self.r1_cell = _si(rc.get("r", 0.0))  # per cell (assumption: rc_pair is specified per cell like R0)
        self.c1_cell = _si(rc.get("c", 1.0))
        curve = np.asarray(p.raw("ocv_curve"), dtype=float)
        self.ocv_soc, self.ocv_v = curve[:, 0], curve[:, 1]
        self.min_cell_voltage = p.get("min_cell_voltage", float(self.ocv_v[0]))
        self.c_rating = p.get("c_rating", 0.0)
        self.arrhenius_b = p.get("resistance_activation_k", ARRHENIUS_B_DEFAULT)
        thermal = ctype_thermal(self.ctype)
        self.theta = float(thermal.get("theta_ja_c_per_w", 3.0))
        self.heat_capacity = float(thermal.get("heat_capacity_j_per_k", 1000.0 * self.ctype.mechanical.mass))
        self.max_temp_c = float(thermal.get("max_temp_c", 60.0))
        self.pos = self.require(self.pin_by_role("power_out"), "power_out")
        self.neg = self.require(self.pin_by_role("ground"), "ground")
        self.s_soc = 1.0
        self.s_v_rc = 0.0
        self.s_temperature = self.ambient_c
        self.s_undervoltage = False
        self.s_overcurrent = False
        self.last_current = 0.0

    # Pack parameters --------------------------------------------------------
    def _arrhenius(self) -> float:
        t_k = self.s_temperature + 273.15
        return math.exp(self.arrhenius_b * (1.0 / t_k - 1.0 / 298.15))

    @property
    def r0(self) -> float:
        return self.r0_cell * self.cells / self.parallel * self._arrhenius()

    @property
    def r1(self) -> float:
        return self.r1_cell * self.cells / self.parallel * self._arrhenius()

    @property
    def c1(self) -> float:
        return self.c1_cell * self.parallel / self.cells

    def ocv_cell(self, soc: float) -> float:
        return float(np.interp(soc, self.ocv_soc, self.ocv_v))

    def soc_for_cell_ocv(self, cell_voltage: float) -> float:
        return float(np.interp(cell_voltage, self.ocv_v, self.ocv_soc))

    @property
    def max_current(self) -> float:
        return self.c_rating * self.capacity / 3600.0 if self.c_rating else math.inf

    # Lifecycle ----------------------------------------------------------------
    def build(self, circuit: Circuit) -> None:
        self.branch = circuit.branch(f"{self.id}#i")
        self.source = circuit.add(VoltageSource(f"{self.id} ({self.model})", self.pos.node, self.neg.node, self.branch))

    def initialize(self, initial: dict[str, Any]) -> None:
        if "soc" in initial:
            self.s_soc = float(initial["soc"])
        if "temperature_c" in initial:
            self.s_temperature = float(initial["temperature_c"])

    def begin_step(self, dt: float | None, inputs: dict[str, Any], time: float) -> None:
        ocv = self.cells * self.ocv_cell(self.s_soc)
        if dt is None:
            self.source.emf, self.source.resistance = ocv, self.r0 + self.r1
            return
        tau = self.r1 * self.c1
        a = 1.0 / (1.0 + dt / tau) if tau > 0 else 0.0
        b = (dt / self.c1) * a if tau > 0 else self.r1
        self._a, self._b = a, b
        self.source.emf = ocv - a * self.s_v_rc
        self.source.resistance = self.r0 + b

    def end_step(self, dt: float | None, x: np.ndarray, time: float) -> list[Event]:
        i = float(x[self.branch])
        self.last_current = i
        events: list[Event] = []
        if dt is None:
            return events
        v_rc = self._a * self.s_v_rc + self._b * i
        heat = i * i * self.r0 + (v_rc * v_rc / self.r1 if self.r1 > 0 else 0.0)
        self.s_v_rc = v_rc
        self.s_soc = min(1.0, max(0.0, self.s_soc - i * dt / self.capacity))
        self.s_temperature = first_order(self.s_temperature, self.ambient_c + heat * self.theta, dt,
                                         self.theta * self.heat_capacity)
        cell = voltage(x, self.pos.node, self.neg.node) / self.cells
        if cell < self.min_cell_voltage and not self.s_undervoltage:
            events.append(Event(time, "battery_undervoltage", self.id, f"cell voltage {cell:.3f} V below {self.min_cell_voltage} V",
                                {"cell_voltage": cell, "limit": self.min_cell_voltage, "soc": self.s_soc}))
        self.s_undervoltage = cell < self.min_cell_voltage
        if abs(i) > self.max_current and not self.s_overcurrent:
            events.append(Event(time, "battery_overcurrent", self.id, f"{i:.1f} A exceeds {self.max_current:.0f} A (C rating)",
                                {"current": i, "limit": self.max_current}))
        self.s_overcurrent = abs(i) > self.max_current
        return events

    def outputs(self, x: np.ndarray) -> dict[str, Any]:
        i = float(x[self.branch])
        v = voltage(x, self.pos.node, self.neg.node)
        return {"v_terminal": v, "v_ocv": self.cells * self.ocv_cell(self.s_soc), "cell_voltage": v / self.cells,
                "soc": self.s_soc, "current": i, "power": v * i, "temperature_c": self.s_temperature,
                "v_rc": self.s_v_rc, "r_internal": self.r0, "c_rate": i / (self.capacity / 3600.0)}


def ctype_thermal(ctype: Any) -> dict[str, Any]:
    return dict(ctype.thermal) if ctype is not None and ctype.thermal else {}


def _si(value: Any) -> float:
    from r3_model.units import to_si

    return to_si(value)

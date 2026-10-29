"""``battery.lipo``: equivalent-circuit lithium-polymer pack (ngspice subcircuit).

Per cell: OCV(SOC) in series with R0 and one RC pair (R1 || C1). Pack:
``cells_series`` cells in series, ``cells_parallel`` strings in parallel.

    v_terminal = N_s OCV(SOC) - v_rc - R0_pack(T) i
    C1 dv_rc/dt = i - v_rc / R1_pack(T)          (state node ``vrc``)
    Q dSOC/dt   = -i                              (coulomb counting, Q in A s; state node ``soc``)
    C_th dT/dt  = i^2 R0 + v_rc^2 / R1 - (T - T_amb) / theta

OCV(SOC) is a SPICE ``pwl()`` of the catalog curve. Resistances follow
Arrhenius R(T) = R25 exp(B (1/T - 1/298.15 K)) with B an estimate (resistance
roughly doubles from 25 C to 0 C for LiPo). The RC pair and SOC are
ground-referenced integrator nodes (the usual behavioral battery-model
construction), so their values are plain ``.ic`` initial conditions.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..netlist import Netlist, Solution, fmt, pwl, temperature
from .base import BehaviorModel, Event

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
        self.soc_node, self.vrc_node, self.t_node = self.local("soc"), self.local("vrc"), self.local("t")
        self.states = {self.soc_node: 1.0, self.vrc_node: 0.0, self.t_node: self.ambient_c}
        self.s_undervoltage = False
        self.s_overcurrent = False
        self.last_current = 0.0

    # Pack parameters (at the last solved temperature) ------------------------
    @property
    def s_soc(self) -> float:
        return min(1.0, max(0.0, self.states[self.soc_node]))

    @s_soc.setter
    def s_soc(self, value: float) -> None:
        self.states[self.soc_node] = float(value)

    @property
    def s_temperature(self) -> float:
        return self.states[self.t_node]

    @s_temperature.setter
    def s_temperature(self, value: float) -> None:
        self.states[self.t_node] = float(value)

    @property
    def s_v_rc(self) -> float:
        return self.states[self.vrc_node]

    def _arrhenius(self, temperature_c: float | None = None) -> float:
        t_k = (self.s_temperature if temperature_c is None else temperature_c) + 273.15
        return math.exp(self.arrhenius_b * (1.0 / t_k - 1.0 / 298.15))

    @property
    def r0_ref(self) -> float:
        return self.r0_cell * self.cells / self.parallel

    @property
    def r1_ref(self) -> float:
        return self.r1_cell * self.cells / self.parallel

    @property
    def r0(self) -> float:
        return self.r0_ref * self._arrhenius()

    @property
    def r1(self) -> float:
        return self.r1_ref * self._arrhenius()

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

    # Netlist ------------------------------------------------------------------
    def build(self, net: Netlist) -> None:
        name = f"battery_lipo_{self.inst[1:]}"
        arrhenius = f"exp({fmt(self.arrhenius_b)}*(1/({temperature('t')}+273.15)-1/298.15))"
        r0, r1 = f"({fmt(self.r0_ref)}*{arrhenius})", f"({fmt(max(self.r1_ref, 1e-9))}*{arrhenius})"
        lines = [
            f"bocv ocv neg v = {self.cells}*{pwl('v(soc)', zip(self.ocv_soc, self.ocv_v))}",
            f"br0 ocv n1 i = v(ocv,n1)/{r0}",
            "brc n1 n2 v = v(vrc)",
            "vsense n2 pos dc 0",
            # RC pair state: C1 dv/dt = i - v/R1
            f"brc1 vrc 0 i = v(vrc)/{r1}",
            "birc 0 vrc i = i(vsense)",
            # Joule heat of R0 and R1
            f"bloss ploss 0 v = i(vsense)*i(vsense)*{r0}+v(vrc)*v(vrc)/{r1}",
        ]
        if net.transient:
            lines += [f"crc vrc 0 {fmt(self.c1)}", f"csoc soc 0 {fmt(self.capacity)}", "bsoc soc 0 i = i(vsense)"]
            net.state(self.soc_node, self.states[self.soc_node])
            net.state(self.vrc_node, self.states[self.vrc_node])
            net.state(self.t_node, self.states[self.t_node])
            soc = self.soc_node
        else:
            soc = self.control(net, "soc", self.states[self.soc_node])
        self.thermal(net, "t", self.heat_capacity, self.theta, "v(ploss)", lines)
        net.subckt(name, ["pos", "neg", "soc", "vrc", "t", "tamb"], lines,
                   doc=f"{self.id}: {self.cells}S{self.parallel}P LiPo, {self.capacity / 3600:.3g} Ah (battery.lipo)")
        net.add(f"{self.inst} {self.pos.node} {self.neg.node} {soc} {self.vrc_node} {self.t_node} tamb {name}")

    # Lifecycle ----------------------------------------------------------------
    def initialize(self, initial: dict[str, Any]) -> None:
        if "soc" in initial:
            self.s_soc = float(initial["soc"])
        if "temperature_c" in initial:
            self.s_temperature = float(initial["temperature_c"])

    def begin_step(self, dt: float | None, inputs: dict[str, Any], time: float) -> None:
        self.controls["soc"] = self.s_soc

    def end_step(self, dt: float | None, sol: Solution, time: float) -> list[Event]:
        i = sol.i(self.sense("vsense"))
        self.last_current = i
        events: list[Event] = []
        if dt is None:
            return events
        self.capture_states(sol)
        cell = sol.v(self.pos.node, self.neg.node) / self.cells
        if cell < self.min_cell_voltage and not self.s_undervoltage:
            events.append(Event(time, "battery_undervoltage", self.id, f"cell voltage {cell:.3f} V below {self.min_cell_voltage} V",
                                {"cell_voltage": cell, "limit": self.min_cell_voltage, "soc": self.s_soc}))
        self.s_undervoltage = cell < self.min_cell_voltage
        if abs(i) > self.max_current and not self.s_overcurrent:
            events.append(Event(time, "battery_overcurrent", self.id, f"{i:.1f} A exceeds {self.max_current:.0f} A (C rating)",
                                {"current": i, "limit": self.max_current}))
        self.s_overcurrent = abs(i) > self.max_current
        return events

    def outputs(self, sol: Solution) -> dict[str, Any]:
        i = sol.i(self.sense("vsense"))
        v = sol.v(self.pos.node, self.neg.node)
        soc = sol.v(self.soc_node) if self.soc_node in sol.index else self.s_soc
        temperature = sol.v(self.t_node)
        v_rc = sol.v(self.vrc_node)
        return {"v_terminal": v, "v_ocv": sol.v(self.internal("ocv"), self.neg.node), "cell_voltage": v / self.cells,
                "soc": min(1.0, max(0.0, soc)), "current": i, "power": v * i, "temperature_c": temperature,
                "v_rc": v_rc, "r_internal": self.r0_ref * self._arrhenius(temperature), "c_rate": i / (self.capacity / 3600.0),
                "heat": sol.get(self.internal("ploss"))}


def ctype_thermal(ctype: Any) -> dict[str, Any]:
    return dict(ctype.thermal) if ctype is not None and ctype.thermal else {}


def _si(value: Any) -> float:
    from r3_model.units import to_si

    return to_si(value)

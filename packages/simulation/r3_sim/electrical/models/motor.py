"""``motor.dc_brushed``: permanent-magnet brushed DC motor (ngspice subcircuit).

    V = I R(T) + L dI/dt + V_brush tanh(I / I_b) + Ke w
    torque = Kt (I - I0 tanh(w / w_s))
    Ke = Kt = 60 / (2 pi Kv)                       (SI, V s/rad = N m/A)

The rotor speed w is owned by MuJoCo and enters the circuit through an
``EXTERNAL`` voltage source (1 V = 1 rad/s) driving the back-EMF VCVS; the
torque returned to MuJoCo is computed from the SPICE winding current. ``I0``
is the no-load current (friction + iron losses referred to current); the sign
functions are smoothed with tanh (I_b = 0.1 A, w_s = 1 rad/s). Winding
resistance follows copper, R(T) = R25 (1 + 0.00393 (T - 25 C)), with a lumped
thermal node C_th dT/dt = I^2 R + V_brush I tanh(I / I_b) - (T - T_amb) / theta.
"""

from __future__ import annotations

import math
from typing import Any

from ..netlist import Netlist, Solution, fmt, temperature
from ..network import COPPER_ALPHA
from .base import BehaviorModel, Event
from .battery import ctype_thermal

BRUSH_CURRENT_SCALE = 0.1  # A
OMEGA_SIGN_SCALE = 1.0  # rad/s
MOTOR_SPECIFIC_HEAT = 500.0  # J/(kg K), steel/copper mix (estimate)


class DcBrushedMotor(BehaviorModel):
    model = "motor.dc_brushed"
    ramped = ("omega",)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        p = self.params
        kv = p.get("kv_rpm_per_v")
        self.kt = 60.0 / (2.0 * math.pi * kv)
        self.ke = self.kt
        self.r25 = p.get("resistance")
        self.inductance = p.get("inductance")
        self.no_load_current = p.get("no_load_current", 0.0)
        self.brush_drop = p.get("brush_drop", 0.0)
        self.max_rpm = p.get("max_rpm", math.inf)
        thermal = ctype_thermal(self.ctype)
        self.theta = float(thermal.get("theta_ja_c_per_w", 3.0))
        self.max_temp_c = float(thermal.get("max_temp_c", 100.0))
        self.heat_capacity = float(thermal.get("heat_capacity_j_per_k", MOTOR_SPECIFIC_HEAT * self.ctype.mechanical.mass))
        if "heat_capacity_j_per_k" not in thermal:
            self.params.estimated["heat_capacity_j_per_k"] = self.heat_capacity
        terminals = self.pins_by_role("motor")
        if len(terminals) != 2:
            raise ValueError(f"{self.id}: motor.dc_brushed needs exactly two 'motor' pins")
        self.pos, self.neg = terminals
        self.t_node = self.local("t")
        self.states = {self.t_node: self.ambient_c}
        self.omega = 0.0
        self.s_current = 0.0
        self.s_overtemperature = False

    @property
    def s_temperature(self) -> float:
        return self.states[self.t_node]

    @s_temperature.setter
    def s_temperature(self, value: float) -> None:
        self.states[self.t_node] = float(value)

    @property
    def resistance(self) -> float:
        return self.resistance_at(self.s_temperature)

    def resistance_at(self, temperature_c: float) -> float:
        return self.r25 * (1.0 + COPPER_ALPHA * (temperature_c - 25.0))

    def build(self, net: Netlist) -> None:
        name = f"motor_dc_{self.inst[1:]}"
        resistance = f"({fmt(self.r25)}*(1+{COPPER_ALPHA}*({temperature('t')}-25)))"
        brush = f"tanh(i(vsense)/{BRUSH_CURRENT_SCALE})"
        lines = [
            "vsense pos n1 dc 0",
            f"br n1 n2 i = v(n1,n2)/{resistance}",
            f"l1 n2 n3 {fmt(self.inductance)}",
            f"bbrush n3 n4 v = {fmt(self.brush_drop)}*{brush}",
            f"eemf n4 neg omega 0 {fmt(self.ke)}",
            f"bloss ploss 0 v = i(vsense)*i(vsense)*{resistance}+{fmt(self.brush_drop)}*i(vsense)*{brush}",
        ]
        self.thermal(net, "t", self.heat_capacity, self.theta, "v(ploss)", lines)
        net.state(self.t_node, self.states[self.t_node])
        omega = self.control(net, "omega", 0.0)
        net.subckt(name, ["pos", "neg", "omega", "t", "tamb"], lines,
                   doc=f"{self.id}: brushed DC motor Kv {60 / (2 * math.pi * self.kt):.0f} rpm/V, R25 {self.r25} ohm, L {self.inductance} H")
        net.add(f"{self.inst} {self.pos.node} {self.neg.node} {omega} {self.t_node} tamb {name}")

    def initialize(self, initial: dict[str, Any]) -> None:
        self.s_temperature = float(initial.get("temperature_c", self.s_temperature))

    def begin_step(self, dt: float | None, inputs: dict[str, Any], time: float) -> None:
        self.omega = float(inputs.get("omega", 0.0))
        self.controls["omega"] = self.omega

    def torque(self, current: float) -> float:
        return self.kt * (current - self.no_load_current * math.tanh(self.omega / OMEGA_SIGN_SCALE))

    def end_step(self, dt: float | None, sol: Solution, time: float) -> list[Event]:
        if dt is None:
            return []
        self.capture_states(sol)
        self.s_current = sol.i(self.sense("vsense"))
        events = []
        hot = self.s_temperature > self.max_temp_c
        if hot and not self.s_overtemperature:
            events.append(Event(time, "motor_overtemperature", self.id, f"winding {self.s_temperature:.0f} C > {self.max_temp_c} C",
                                {"temperature_c": self.s_temperature, "limit": self.max_temp_c}))
        self.s_overtemperature = hot
        return events

    def watches(self) -> list[tuple[str, str]]:
        return [(f"{self.sense('vsense')}#branch", "mean")]

    def outputs(self, sol: Solution) -> dict[str, Any]:
        i = sol.i(self.sense("vsense"))
        # Torque for MuJoCo from the step-averaged winding current (equals the
        # end-of-step current unless the current ripples within the step).
        i_mean = sol.reduced(f"{self.sense('vsense')}#branch", "mean")
        v = sol.v(self.pos.node, self.neg.node)
        temperature = sol.v(self.t_node)
        resistance = self.resistance_at(temperature)
        torque = self.torque(i_mean)
        return {"torque": torque, "current": i, "current_mean": i_mean, "voltage": v, "back_emf": self.ke * self.omega,
                "omega": self.omega, "power_electrical": v * i, "power_mechanical": torque * self.omega,
                "copper_loss": i * i * resistance, "temperature_c": temperature, "kt": self.kt}

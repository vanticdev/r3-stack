"""``motor.dc_brushed``: permanent-magnet brushed DC motor.

    V = I R(T) + L dI/dt + Ke w + V_brush tanh(I / I_b)
    torque = Kt (I - I0 tanh(w / w_s))
    Ke = Kt = 60 / (2 pi Kv)                       (SI, V s/rad = N m/A)

``I0`` is the no-load current (friction + iron losses referred to current).
The sign functions are smoothed with tanh (I_b = 0.1 A, w_s = 1 rad/s) so the
Newton iteration stays differentiable. Winding resistance follows copper,
R(T) = R25 (1 + 0.00393 (T - 25 C)), with a lumped thermal model
C_th dT/dt = I^2 R + V_brush |I| - (T - T_amb) / theta. The inductor uses the
backward-Euler companion L/dt (I[n+1] - I[n]); in DC operating points dI/dt = 0.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..mna import Circuit, NumericElement, SpiceWriter
from ..network import COPPER_ALPHA
from .base import BehaviorModel, Event, first_order, voltage
from .battery import ctype_thermal

BRUSH_CURRENT_SCALE = 0.1  # A
OMEGA_SIGN_SCALE = 1.0  # rad/s
MOTOR_SPECIFIC_HEAT = 500.0  # J/(kg K), steel/copper mix (estimate)


class MotorBranch(NumericElement):
    def __init__(self, name: str, pos: int, neg: int, branch: int) -> None:
        self.name = name
        self.pos, self.neg, self.branch = pos, neg, branch
        self.rows = [pos, neg, branch]
        self.deps = [pos, neg, branch]
        self.resistance = 0.1
        self.l_over_dt = 0.0
        self.i_prev = 0.0
        self.back_emf = 0.0
        self.brush_drop = 0.0

    def residual(self, v: np.ndarray) -> np.ndarray:
        vp, vn, i = v
        drop = self.resistance * i + self.l_over_dt * (i - self.i_prev) + self.back_emf + self.brush_drop * math.tanh(i / BRUSH_CURRENT_SCALE)
        return np.array([i, -i, (vp - vn) - drop])

    def spice(self, writer: SpiceWriter, x: np.ndarray) -> None:
        writer.comment(f"{self.name}: R={self.resistance:.6g}, L/dt={self.l_over_dt:.6g}, Ke*w={self.back_emf:.6g}")
        sense, mid = writer.name("V"), writer.internal_node()
        mid2 = writer.internal_node()
        writer.add(f"{sense} {writer.node(self.pos)} {mid} DC 0")
        r_total = self.resistance + self.l_over_dt
        writer.add(f"{writer.name('R')} {mid} {mid2} {r_total:.12g}")
        history = -self.l_over_dt * self.i_prev
        writer.add(f"{writer.name('B')} {mid2} {writer.node(self.neg)} V = {self.back_emf + history:.12g}"
                   f"+{self.brush_drop:.12g}*tanh(I({sense})/{BRUSH_CURRENT_SCALE})")


class DcBrushedMotor(BehaviorModel):
    model = "motor.dc_brushed"

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
        self.omega = 0.0
        self.s_current = 0.0
        self.s_temperature = self.ambient_c
        self.s_overtemperature = False

    @property
    def resistance(self) -> float:
        return self.r25 * (1.0 + COPPER_ALPHA * (self.s_temperature - 25.0))

    def build(self, circuit: Circuit) -> None:
        self.branch = circuit.branch(f"{self.id}#i")
        self.element = circuit.add(MotorBranch(f"{self.id} ({self.model})", self.pos.node, self.neg.node, self.branch))

    def initialize(self, initial: dict[str, Any]) -> None:
        self.s_temperature = float(initial.get("temperature_c", self.s_temperature))

    def begin_step(self, dt: float | None, inputs: dict[str, Any], time: float) -> None:
        self.omega = float(inputs.get("omega", 0.0))
        e = self.element
        e.resistance = self.resistance
        e.l_over_dt = 0.0 if dt is None else self.inductance / dt
        e.i_prev = self.s_current
        e.back_emf = self.ke * self.omega
        e.brush_drop = self.brush_drop

    def torque(self, current: float) -> float:
        return self.kt * (current - self.no_load_current * math.tanh(self.omega / OMEGA_SIGN_SCALE))

    def end_step(self, dt: float | None, x: np.ndarray, time: float) -> list[Event]:
        i = float(x[self.branch])
        if dt is None:
            return []
        self.s_current = i
        heat = i * i * self.resistance + self.brush_drop * abs(i)
        self.s_temperature = first_order(self.s_temperature, self.ambient_c + heat * self.theta, dt, self.theta * self.heat_capacity)
        events = []
        hot = self.s_temperature > self.max_temp_c
        if hot and not self.s_overtemperature:
            events.append(Event(time, "motor_overtemperature", self.id, f"winding {self.s_temperature:.0f} C > {self.max_temp_c} C",
                                {"temperature_c": self.s_temperature, "limit": self.max_temp_c}))
        self.s_overtemperature = hot
        return events

    def outputs(self, x: np.ndarray) -> dict[str, Any]:
        i = float(x[self.branch])
        v = voltage(x, self.pos.node, self.neg.node)
        torque = self.torque(i)
        return {"torque": torque, "current": i, "voltage": v, "back_emf": self.ke * self.omega, "omega": self.omega,
                "power_electrical": v * i, "power_mechanical": torque * self.omega,
                "copper_loss": i * i * self.resistance, "temperature_c": self.s_temperature, "kt": self.kt}

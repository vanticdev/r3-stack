"""``esc.brushed``: brushed-motor ESC with averaged H-bridge and linear BEC.

H-bridge, state-space averaged over the PWM period (synchronous rectification):

    drive:  V_A - V_B = d (V_in - V_gnd) - 2 r_on I,   I_in = d I
    brake:  V_A - V_B = -(2 r_on / b) I                (low-side short with brake duty b)
    off:    I = 0                                       (coast / failsafe / cutoff)
    limit:  I = +-I_lim,  I_in = (V_A - V_B + 2 r_on I) I / V_in   (cycle-by-cycle current limit)

Current-limit is an active-set constraint: after a drive solve with |I| > I_lim
the step is re-solved with I clamped; the limit is released when the effective
duty would exceed the commanded one. I_lim = burst current until the overload
budget (I_burst^2 - I_cont^2) * burst_duration of the integral of
max(0, I^2 - I_cont^2) dt is used up, then the continuous rating.

Throttle: pulse -> [-1, 1] with deadband rescaling. Forward/brake/reverse
follows the usual hobby-ESC logic: pushing reverse after forward brakes;
reverse engages only after the stick has been neutral for ``reverse_delay``.
No valid pulse for ``signal_timeout`` -> failsafe coast. Low-voltage cutoff
(latched) when V_in < cells * cutoff_per_cell for ``lvc_delay``; cells are
detected from the first input voltage (ceil(V / 4.35)).

Linear BEC: V_bec = min(V_set, V_in - V_dropout) - R_o I - foldback, input
current = output current, dissipation (V_in - V_bec) I. Losses heat a lumped
case: P = I^2 2 r_on + V_in |I| t_sw f_sw + P_bec + V_in I_q.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..mna import Circuit, NumericElement, SpiceWriter, smooth_min, softplus, spice_smooth_min, spice_softplus
from .base import BehaviorModel, Event, SupplyLoad, constant_current, first_order, voltage
from .battery import ctype_thermal

SMOOTH_V = 0.02  # V, smoothing width for regulator min()
SMOOTH_I = 0.02  # A
FOLDBACK_SLOPE = 20.0  # ohm, output resistance beyond the current limit (estimate)
ALUMINUM_SPECIFIC_HEAT = 900.0  # J/(kg K)


class BridgeElement(NumericElement):
    """Two averaged half-bridge legs (A, B), each referenced to the ESC ground.

    Leg k in source mode: V_k - V_gnd = d_k (V_in - V_gnd) - r_k i_k, drawing
    d_k i_k from V_in; in limit mode i_k = I_lim (signed) and it draws
    (V_k - V_gnd + r_k i_k) i_k / V_in. Forward: d_A = d, d_B = 0; reverse:
    d_A = 0, d_B = |d|; brake: both legs low with r_on / b; off: both legs
    high-impedance (``OFF_RESISTANCE``, gate-driver/sense leakage estimate)."""

    OFF_RESISTANCE = 1e5

    def __init__(self, name: str, a: int, b: int, vin: int, gnd: int, branch_a: int, branch_b: int, r_on: float) -> None:
        self.name = name
        self.a, self.b, self.vin, self.gnd = a, b, vin, gnd
        self.branch_a, self.branch_b = branch_a, branch_b
        self.rows = [a, b, vin, gnd, branch_a, branch_b]
        self.deps = [a, b, vin, gnd, branch_a, branch_b]
        self.r_on = r_on
        self.mode = "off"
        self.duty = 0.0  # signed: > 0 forward (leg A), < 0 reverse (leg B)
        self.brake = 0.0
        self.limit = 0.0  # signed motor current (out of A) in limit mode

    @property
    def r_switch(self) -> float:
        return 2.0 * self.r_on

    @property
    def r_brake(self) -> float:
        return self.r_switch / max(self.brake, 1e-3)

    def legs(self) -> tuple[tuple[str, float, float], tuple[str, float, float]]:
        """(mode, duty, resistance) of legs A and B."""
        if self.mode in ("drive", "limit"):
            forward = self.duty >= 0
            driven = ("limit" if self.mode == "limit" else "source", abs(self.duty), self.r_on)
            low = ("source", 0.0, self.r_on)
            return (driven, low) if forward else (low, driven)
        if self.mode == "brake":
            leg = ("source", 0.0, self.r_on / max(self.brake, 1e-3))
            return leg, leg
        leg = ("source", 0.0, self.OFF_RESISTANCE)
        return leg, leg

    def _leg_limit(self, k: int) -> float:
        # Motor current flows out of A and into B, so leg B carries -I.
        return self.limit if k == 0 else -self.limit

    def residual(self, v: np.ndarray) -> np.ndarray:
        va, vb, vi, vg, ia, ib = v
        vin = max(vi - vg, 0.1)
        draw = 0.0
        equations = []
        for k, ((mode, duty, r), vk, ik) in enumerate(zip(self.legs(), (va, vb), (ia, ib))):
            if mode == "limit":
                equations.append(ik - self._leg_limit(k))
                draw += (vk - vg + r * ik) * ik / vin
            else:
                equations.append((vk - vg) - duty * vin + r * ik)
                draw += duty * ik
        return np.array([-ia, -ib, draw, ia + ib - draw, equations[0], equations[1]])

    def motor_current(self, x: np.ndarray) -> float:
        return float(x[self.branch_a])

    def effective_duty(self, x: np.ndarray) -> float:
        """Signed average duty applied to the motor, (V_A - V_B + 2 r_on I) / V_in in limit mode."""
        if self.mode == "drive":
            return self.duty
        if self.mode == "limit":
            vin = max(voltage(x, self.vin, self.gnd), 0.1)
            return (voltage(x, self.a, self.b) + self.r_switch * self.motor_current(x)) / vin
        return 0.0

    def input_current(self, x: np.ndarray) -> float:
        return float(self.evaluate(x)[2])

    def spice(self, writer: SpiceWriter, x: np.ndarray) -> None:
        writer.comment(f"{self.name}: averaged H-bridge mode={self.mode} duty={self.duty:.6g}")
        vin, gnd = writer.node(self.vin), writer.node(self.gnd)
        draws = []
        for k, ((mode, duty, r), node) in enumerate(zip(self.legs(), (self.a, self.b))):
            if mode == "limit":
                limit = self._leg_limit(k)
                writer.add(f"{writer.name('I')} {gnd} {writer.node(node)} DC {limit:.12g}")
                draws.append(f"({writer.v(node, self.gnd)}+{r * limit:.12g})*{limit:.12g}/max({writer.v(self.vin, self.gnd)},0.1)")
                continue
            src, mid, sense = writer.internal_node(), writer.internal_node(), writer.name("V")
            writer.add(f"{writer.name('B')} {src} {gnd} V = {duty:.12g}*{writer.v(self.vin, self.gnd)}")
            writer.add(f"{writer.name('R')} {src} {mid} {r:.12g}")
            writer.add(f"{sense} {mid} {writer.node(node)} DC 0")
            if duty:
                draws.append(f"{duty:.12g}*I({sense})")
        if draws:
            # The legs' B sources return their current through gnd; move the
            # input share to vin so the net input current is drawn from vin.
            writer.add(f"{writer.name('B')} {vin} {gnd} I = {'+'.join(draws)}")


class RegulatorOutput(NumericElement):
    """Regulated output ``out`` (vs ``gnd``) powered from ``vin``, branch current
    ``i`` delivered out of ``out``.

    V_out = softplus(min(V_set - R_o i, V_in - V_drop - R_drop i)) - R_fb softplus(i - I_lim)

    Input current: linear -> i; switching -> V_out i / (eta(i) V_in) (+ I_q
    modeled separately). ``enabled=False`` -> i = 0 (UVLO / shutdown)."""

    def __init__(self, name: str, out: int, gnd: int, vin: int, branch: int, v_set: float, r_out: float,
                 dropout: float, r_dropout: float, current_limit: float, kind: str,
                 efficiency: np.ndarray | None = None) -> None:
        self.name = name
        self.out, self.gnd, self.vin, self.branch = out, gnd, vin, branch
        self.rows = [out, gnd, vin, branch]
        self.deps = [out, gnd, vin, branch]
        self.v_set, self.r_out, self.dropout, self.r_dropout = v_set, r_out, dropout, r_dropout
        self.current_limit = current_limit
        self.kind = kind
        self.efficiency = efficiency
        self.enabled = True

    def eta(self, i: float) -> float:
        if self.efficiency is None:
            return 1.0
        return float(np.interp(abs(i), self.efficiency[:, 0], self.efficiency[:, 1]))

    def target(self, vin_rel: float, i: float) -> float:
        regulated = smooth_min(self.v_set - self.r_out * i, vin_rel - self.dropout - self.r_dropout * i, SMOOTH_V)
        return softplus(regulated, SMOOTH_V) - FOLDBACK_SLOPE * softplus(i - self.current_limit, SMOOTH_I)

    def input_current(self, vout_rel: float, vin_rel: float, i: float) -> float:
        if self.kind == "linear":
            return i
        if self.kind == "none":  # power accounted by the owning model (e.g. a computer's internal rail)
            return 0.0
        return vout_rel * i / (self.eta(i) * max(vin_rel, 0.5))

    def residual(self, v: np.ndarray) -> np.ndarray:
        vo, vg, vi, i = v
        if not self.enabled:
            return np.array([-i, 0.0, 0.0, i])
        vin_rel, vout_rel = vi - vg, vo - vg
        drawn = self.input_current(vout_rel, vin_rel, i)
        return np.array([-i, -(drawn - i), drawn, vout_rel - self.target(vin_rel, i)])

    def spice(self, writer: SpiceWriter, x: np.ndarray) -> None:
        writer.comment(f"{self.name}: {self.kind} regulator V_set={self.v_set} enabled={self.enabled}")
        if not self.enabled:
            return
        src = writer.internal_node()
        sense = writer.name("V")
        i = f"I({sense})"
        vin = writer.v(self.vin, self.gnd)
        regulated = spice_smooth_min(f"{self.v_set:.12g}-{self.r_out:.12g}*{i}",
                                     f"{vin}-{self.dropout:.12g}-{self.r_dropout:.12g}*{i}", SMOOTH_V)
        expression = f"{spice_softplus(regulated, SMOOTH_V)}-{FOLDBACK_SLOPE}*{spice_softplus(f'{i}-{self.current_limit:.12g}', SMOOTH_I)}"
        writer.add(f"{writer.name('B')} {src} {writer.node(self.gnd)} V = {expression}")
        writer.add(f"{sense} {src} {writer.node(self.out)} DC 0")
        writer.sense[self.branch] = sense
        if self.kind == "none":
            return
        if self.kind == "linear":
            drawn = i
        else:
            table = ",".join(f"{a:.9g},{b:.9g}" for a, b in self.efficiency)
            drawn = f"{writer.v(self.out, self.gnd)}*{i}/(pwl(abs({i}),{table})*max({vin},0.5))"
        writer.add(f"{writer.name('B')} {writer.node(self.vin)} {writer.node(self.gnd)} I = {drawn}")


def throttle_from_pulse(pulse: float, low: float, neutral: float, high: float, deadband: float) -> float:
    """Pulse width (s) -> throttle in [-1, 1] with deadband rescaling."""
    if pulse >= neutral:
        t = (pulse - neutral) / (high - neutral)
    else:
        t = (pulse - neutral) / (neutral - low)
    t = max(-1.0, min(1.0, t))
    if abs(t) <= deadband:
        return 0.0
    return math.copysign((abs(t) - deadband) / (1.0 - deadband), t)


class BrushedEsc(BehaviorModel):
    model = "esc.brushed"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        p = self.params
        self.i_cont = p.get("current_continuous")
        self.i_burst = p.get("current_burst", self.i_cont)
        self.burst_duration = p.get("burst_duration", 1.0)
        self.r_on = p.get("r_on")
        self.f_pwm = p.get("pwm_frequency", 1000.0)
        self.t_switch = p.get("switching_time", 200e-9)
        self.bec_voltage = p.get("bec_voltage", 0.0)
        self.bec_current = p.get("bec_current", 0.0)
        self.bec_type = str(p.raw("bec_type", "linear"))
        self.bec_dropout = p.get("bec_dropout", 1.0)
        self.bec_r_out = p.get("bec_output_resistance", 0.02)
        self.bec_limit = p.get("bec_current_limit", 1.5 * self.bec_current)
        self.pulse_min = p.get("input_min_pulse", 1000e-6)
        self.pulse_max = p.get("input_max_pulse", 2000e-6)
        self.pulse_neutral = p.get("neutral_pulse", 0.5 * (self.pulse_min + self.pulse_max))
        self.deadband = p.get("deadband", 0.0)
        self.signal_timeout = p.get("signal_timeout", 0.5)
        self.lvc_per_cell = p.get("low_voltage_cutoff_per_cell", 0.0)
        self.lvc_delay = p.get("lvc_delay", 0.5)
        self.reverse_delay = p.get("reverse_delay", 0.0)
        self.reverse_fraction = p.get("reverse_max_fraction", 1.0)
        self.quiescent = p.get("quiescent_current", 0.0)
        thermal = ctype_thermal(self.ctype)
        self.theta = float(thermal.get("theta_ja_c_per_w", 10.0))
        self.max_temp_c = float(thermal.get("max_temp_c", 110.0))
        self.heat_capacity = float(thermal.get("heat_capacity_j_per_k", ALUMINUM_SPECIFIC_HEAT * self.ctype.mechanical.mass))
        if "heat_capacity_j_per_k" not in thermal:
            self.params.estimated["heat_capacity_j_per_k"] = self.heat_capacity
        self.vin = self.require(self.pin_by_role("power_in"), "power_in")
        self.gnd = self.require(self.pin_by_role("ground"), "ground")
        motors = self.pins_by_role("motor")
        if len(motors) != 2:
            raise ValueError(f"{self.id}: esc.brushed needs two 'motor' pins")
        self.m_a, self.m_b = motors
        self.bec = self.pin_by_role("power_out")
        self.cells = int(p.get("cells", 0)) if p.has("cells") else 0
        # state
        self.s_mode = "neutral"
        self.s_last_active = ""
        self.s_neutral_since = -math.inf
        self.s_since_signal = 0.0
        self.s_signal_lost = False
        self.s_lvc = False
        self.s_low_time = 0.0
        self.s_overload = 0.0
        self.s_temperature = self.ambient_c
        self.s_thermal_shutdown = False
        self.s_limiting = False
        self.s_bec_overload_time = 0.0
        self.throttle = 0.0
        self.signal_valid = False
        self._failsafe = False

    def build(self, circuit: Circuit) -> None:
        self.branch = circuit.branch(f"{self.id}#leg_a")
        self.branch_b = circuit.branch(f"{self.id}#leg_b")
        self.bridge = circuit.add(BridgeElement(f"{self.id} bridge", self.m_a.node, self.m_b.node, self.vin.node, self.gnd.node,
                                                self.branch, self.branch_b, self.r_on))
        if self.bec is not None and self.bec_voltage > 0:
            self.bec_branch = circuit.branch(f"{self.id}#bec")
            self.bec_reg = circuit.add(RegulatorOutput(
                f"{self.id} BEC", self.bec.node, self.gnd.node, self.vin.node, self.bec_branch, self.bec_voltage,
                self.bec_r_out, self.bec_dropout, 0.0, self.bec_limit,
                "linear" if self.bec_type == "linear" else "switching",
                None if self.bec_type == "linear" else np.array([[0.0, 0.85], [10.0, 0.85]])))
        else:
            self.bec_reg = None
        if self.quiescent > 0:
            fn, expr = constant_current(self.quiescent, 1.0)
            circuit.add(SupplyLoad(f"{self.id} quiescent", self.vin.node, self.gnd.node, fn, expr))

    @property
    def current_limit(self) -> float:
        budget = (self.i_burst ** 2 - self.i_cont ** 2) * self.burst_duration
        return self.i_burst if self.s_overload < budget else self.i_cont

    def _command(self, inputs: dict[str, Any], dt: float | None) -> float:
        """Throttle from inputs; updates signal-timeout state."""
        if "throttle" in inputs:
            self.signal_valid = True
            return max(-1.0, min(1.0, float(inputs["throttle"])))
        pulse = inputs.get("pulse_us")
        if pulse is not None:
            seconds = float(pulse) * 1e-6
            if 0.5 * self.pulse_min <= seconds <= 1.5 * self.pulse_max:
                self.signal_valid = True
                return throttle_from_pulse(seconds, self.pulse_min, self.pulse_neutral, self.pulse_max, self.deadband)
        self.signal_valid = False
        return 0.0

    def begin_step(self, dt: float | None, inputs: dict[str, Any], time: float) -> None:
        t = self._command(inputs, dt)
        bridge = self.bridge
        if dt is None:  # static operating point: direct mapping, no timers
            self.throttle = t
            explicit = inputs.get("mode")
            if t > 0:
                bridge.mode, bridge.duty = "drive", t
            elif t < 0 and explicit == "reverse":
                bridge.mode, bridge.duty = "drive", t * self.reverse_fraction
            elif t < 0:
                bridge.mode, bridge.brake = "brake", -t
            else:
                bridge.mode = "off"
            self._apply_limit_state()
            return
        self.s_since_signal = 0.0 if self.signal_valid else self.s_since_signal + dt
        failsafe = self.s_since_signal > self.signal_timeout
        if failsafe:
            t = 0.0
        self.throttle = t
        self._failsafe = failsafe
        # forward / brake / reverse state machine
        if t > 0:
            self.s_mode = self.s_last_active = "forward"
        elif t < 0:
            ready = (self.s_mode == "reverse" or
                     (self.s_mode == "neutral" and self.s_last_active != "forward"
                      and time - self.s_neutral_since >= self.reverse_delay))
            self.s_mode = self.s_last_active = "reverse" if ready else "brake"
        else:
            if self.s_mode != "neutral":
                self.s_neutral_since = time
            self.s_mode = "neutral"
        drive_blocked = self.s_lvc or self.s_thermal_shutdown or failsafe
        if self.s_mode == "forward" and not drive_blocked:
            bridge.mode, bridge.duty = "drive", t
        elif self.s_mode == "reverse" and not drive_blocked:
            bridge.mode, bridge.duty = "drive", t * self.reverse_fraction
        elif self.s_mode == "brake" and not self.s_thermal_shutdown:
            bridge.mode, bridge.brake = "brake", -t
        else:
            bridge.mode = "off"
        self._apply_limit_state()

    def _apply_limit_state(self) -> None:
        self.s_limiting = False
        if self.bec_reg is not None:
            self.bec_reg.enabled = not self.s_thermal_shutdown

    def resolve(self, x: np.ndarray) -> bool:
        bridge = self.bridge
        i = float(x[self.branch])
        limit = self.current_limit
        if bridge.mode == "drive" and abs(i) > limit:
            bridge.mode, bridge.limit = "limit", math.copysign(limit, i)
            self.s_limiting = True
            return True
        if bridge.mode == "limit" and abs(bridge.effective_duty(x)) > abs(bridge.duty) + 1e-9:
            bridge.mode = "drive"
            self.s_limiting = False
            return True
        return False

    def bec_current_out(self, x: np.ndarray) -> float:
        return float(x[self.bec_branch]) if self.bec_reg is not None and self.bec_reg.enabled else 0.0

    def losses(self, x: np.ndarray) -> dict[str, float]:
        i = float(x[self.branch])
        vin = voltage(x, self.vin.node, self.gnd.node)
        conduction = i * i * self.bridge.r_switch if self.bridge.mode != "off" else 0.0  # two switches conduct
        switching = abs(vin * i) * self.t_switch * self.f_pwm if self.bridge.mode in ("drive", "limit") else 0.0
        bec = 0.0
        if self.bec_reg is not None and self.bec_reg.enabled:
            i_bec = self.bec_current_out(x)
            v_bec = voltage(x, self.bec.node, self.gnd.node)
            drawn = self.bec_reg.input_current(v_bec, vin, i_bec)
            bec = vin * drawn - v_bec * i_bec
        return {"conduction": conduction, "switching": switching, "bec": bec, "quiescent": vin * self.quiescent}

    def end_step(self, dt: float | None, x: np.ndarray, time: float) -> list[Event]:
        if dt is None:
            return []
        events: list[Event] = []
        i = float(x[self.branch])
        vin = voltage(x, self.vin.node, self.gnd.node)
        if self.cells == 0 and vin > 1.0:
            self.cells = max(1, math.ceil(vin / 4.35))
        if self._failsafe and not self.s_signal_lost:
            events.append(Event(time, "esc_signal_lost", self.id, f"no valid pulse for {self.s_since_signal:.2f} s",
                                {"since": self.s_since_signal, "timeout": self.signal_timeout}))
        self.s_signal_lost = self._failsafe
        threshold = self.cells * self.lvc_per_cell
        if threshold > 0 and not self.s_lvc:
            self.s_low_time = self.s_low_time + dt if vin < threshold else 0.0
            if self.s_low_time >= self.lvc_delay:
                self.s_lvc = True
                events.append(Event(time, "low_voltage_cutoff", self.id, f"input {vin:.2f} V below {threshold:.2f} V",
                                    {"voltage": vin, "threshold": threshold, "cells": self.cells}))
        if self.s_limiting:
            events.append(Event(time, "esc_current_limit", self.id, f"motor current limited to {abs(self.bridge.limit):.0f} A",
                                {"current": i, "limit": abs(self.bridge.limit)}))
        self.s_overload = max(0.0, self.s_overload + (i * i - self.i_cont ** 2) * dt)
        losses = sum(self.losses(x).values())
        self.s_temperature = first_order(self.s_temperature, self.ambient_c + losses * self.theta, dt, self.theta * self.heat_capacity)
        if not self.s_thermal_shutdown and self.s_temperature > self.max_temp_c:
            self.s_thermal_shutdown = True
            events.append(Event(time, "esc_overtemperature", self.id, f"case {self.s_temperature:.0f} C > {self.max_temp_c} C",
                                {"temperature_c": self.s_temperature, "limit": self.max_temp_c}))
        elif self.s_thermal_shutdown and self.s_temperature < self.max_temp_c - 10.0:
            self.s_thermal_shutdown = False
        if self.bec_reg is not None:
            i_bec = self.bec_current_out(x)
            self.s_bec_overload_time = self.s_bec_overload_time + dt if i_bec > self.bec_current else 0.0
            if 0 < self.s_bec_overload_time <= dt:
                events.append(Event(time, "bec_overload", self.id, f"BEC {i_bec:.2f} A > {self.bec_current} A rating",
                                    {"current": i_bec, "rating": self.bec_current}))
        return events

    def outputs(self, x: np.ndarray) -> dict[str, Any]:
        i = float(x[self.branch])
        vin = voltage(x, self.vin.node, self.gnd.node)
        duty = self.bridge.effective_duty(x)
        bec_v = voltage(x, self.bec.node, self.gnd.node) if self.bec is not None else 0.0
        losses = self.losses(x)
        return {"v_in": vin, "throttle": self.throttle, "mode": self.bridge.mode, "state": self.s_mode, "duty": duty,
                "motor_current": i, "input_current": self.bridge.input_current(x),
                "current_limited": self.bridge.mode == "limit", "current_limit": self.current_limit,
                "temperature_c": self.s_temperature, "power_loss": sum(losses.values()), "losses": losses,
                "bec_voltage": bec_v, "bec_current": self.bec_current_out(x), "bec_dissipation": losses["bec"],
                "lvc": self.s_lvc, "signal_valid": self.signal_valid, "failsafe": self._failsafe,
                "thermal_shutdown": self.s_thermal_shutdown, "cells": self.cells}

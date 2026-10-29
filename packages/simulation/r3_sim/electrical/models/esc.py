"""``esc.brushed``: brushed-motor ESC (ngspice subcircuit + firmware logic).

Power stage (ngspice): two half-bridge legs A and B, state-space averaged over
the PWM period (synchronous rectification). Leg k is a B current source into
its motor terminal (V relative to the ESC ground), g = 1 / r_on:

    i_k = g (d_k V_in - V_k)        normal operation
    i_k = I_lim,k                   cycle-by-cycle current limit active

The input draws d_k i_k per normal leg (power balance of the averaged switch)
and (V_k + I_lim / g) I_lim / V_in per current-limited leg. Modes: drive (driven leg duty d, other leg low), brake
(both legs low with g = b / r_on), off (g = 1e-5 S: coast, failsafe, cutoff).
Linear BEC: V_bec = min(V_set - R_o I, V_in - V_dropout) with foldback, input
current = output current. Loss nodes: conduction sum i_k^2 / g, switching
|V_in I| t_sw f_pwm while driving, BEC (V_in - V_bec) I, quiescent V_in I_q;
they heat a lumped case (thermal RC).

By default the switch is averaged: ngspice integrates period-averaged
waveforms, no PWM ripple. With ``ElectricalSystem(esc_model="switched")`` the
driven leg's duty source becomes the PWM gate itself (trapezoidal edges with
the catalog switching time, every corner landed on exactly), so ngspice
resolves the switching and the current ripple; measured on the RC car this
runs at ~0.1x real time versus ~2x averaged, so it is opt-in.

The current limit is an active set run by the firmware logic, like the ESC's
cycle-by-cycle limiter: when |I| exceeds I_lim at a step boundary the driven
leg becomes a current source at +-I_lim from the next step (one PWM period at
1 kHz) until the effective duty it needs would exceed the commanded one. A
hard clamp inside the B-source would have zero derivative when saturated and
stall ngspice's Newton iteration on throttle steps.

Firmware logic (Python, it is the ESC's MCU): throttle from pulse (deadband
rescaling), forward/brake/reverse state machine (reverse only after
``reverse_delay`` at neutral, never directly after forward), signal-loss
failsafe after ``signal_timeout`` (a pulse <= 0 or outside 0.5 x min .. 1.5 x
max is no signal), latched low-voltage cutoff, I^2 t overload budget
(I_lim = burst until (I_burst^2 - I_cont^2) burst_duration of
integral max(0, I^2 - I_cont^2) dt is used, then continuous), and thermal
shutdown from the SPICE case temperature.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..netlist import Netlist, Solution, fmt
from .base import BehaviorModel, Event, constant_current
from .battery import ctype_thermal
from .regulator import ENABLE_CARD, ENABLE_MODEL, regulator_lines

ALUMINUM_SPECIFIC_HEAT = 900.0  # J/(kg K)
OFF_CONDUCTANCE = 1e-5  # S per leg when both legs are off (gate-driver/sense leakage estimate)


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
        self.bec_reg = self.bec if self.bec is not None and self.bec_voltage > 0 else None
        self.cells = int(p.get("cells", 0)) if p.has("cells") else 0
        self.t_node = self.local("t")
        self.states = {self.t_node: self.ambient_c}
        # firmware state
        self.s_mode = "neutral"
        self.s_last_active = ""
        self.s_neutral_since = -math.inf
        self.s_since_signal = 0.0
        self.s_signal_lost = False
        self.s_lvc = False
        self.s_low_time = 0.0
        self.s_overload = 0.0
        self.s_thermal_shutdown = False
        self.s_bec_overload_time = 0.0
        self.s_limit = 0.0  # signed driven-leg current while the current limit is active, else 0
        self.switched = False  # True: resolve the PWM switching of the driven leg (see ``time_sources``)
        self.bridge_mode = "off"  # drive | brake | off (command of the power stage)
        self.duty = 0.0  # signed commanded duty: > 0 forward (leg A), < 0 reverse (leg B)
        self.brake = 0.0
        self.throttle = 0.0
        self.signal_valid = False
        self._failsafe = False

    @property
    def s_temperature(self) -> float:
        return self.states[self.t_node]

    @property
    def current_limit(self) -> float:
        budget = (self.i_burst ** 2 - self.i_cont ** 2) * self.burst_duration
        return self.i_burst if self.s_overload < budget else self.i_cont

    # Netlist ------------------------------------------------------------------
    def build(self, net: Netlist) -> None:
        name = f"esc_brushed_{self.inst[1:]}"
        leg = "(abs(v({lim})) > 0 ? v({lim}) : v(g)*(v({duty})*v(vin,vss)-v({node},vss)))"
        # input draw per leg: d i (linear in the unknowns) or V_eff I_lim / V_in when limited
        draw = "(abs(v({lim})) > 0 ? (v({node},vss)+v({lim})/v(g))*v({lim})/max(v(vin,vss),0.1) : v({duty})*i({sense}))"
        lines = [
            "vsin vin vbridge dc 0",
            f"bin vbridge vss i = {draw.format(lim='lim_a', duty='duty_a', node='lega', sense='vsa')}"
            f"+{draw.format(lim='lim_b', duty='duty_b', node='legb', sense='vsb')}",
            f"ba vss lega i = {leg.format(lim='lim_a', duty='duty_a', node='lega')}",
            "vsa lega ma dc 0",
            f"bb vss legb i = {leg.format(lim='lim_b', duty='duty_b', node='legb')}",
            "vsb legb mb dc 0",
            f"biq vin vss i = {constant_current(self.quiescent, 1.0, 'v(vin,vss)')}",
            "bpcond pcond 0 v = (i(vsa)*i(vsa)+i(vsb)*i(vsb))/v(g)",
            f"bpsw psw 0 v = abs(v(vin,vss)*i(vsa))*{fmt(self.t_switch * self.f_pwm)}*(v(duty_a)+v(duty_b) > 1e-9 ? 1 : 0)",
            f"bpq pq 0 v = v(vin,vss)*{fmt(self.quiescent)}",
        ]
        ports = ["vin", "vss", "ma", "mb"]
        nodes = [self.vin.node, self.gnd.node, self.m_a.node, self.m_b.node]
        if self.bec_reg is not None:
            net.model(ENABLE_MODEL, ENABLE_CARD)
            efficiency = None if self.bec_type == "linear" else np.array([[0.0, 0.85], [10.0, 0.85]])
            lines += regulator_lines(net, "bec", "bec", "vss", "vin", self.bec_voltage, self.bec_r_out, self.bec_dropout, 0.0,
                                     self.bec_limit, "linear" if self.bec_type == "linear" else "switching", efficiency,
                                     enable=("en_bec", "0", ENABLE_MODEL, "on"))
            lines.append("bpbec pbec 0 v = v(vin,vss)*i(vbecin)-v(bec,vss)*i(vbec)")
            ports.append("bec")
            nodes.append(self.bec.node)
        else:
            lines.append("bpbec pbec 0 v = 0")
        lines.append("bloss ploss 0 v = v(pcond)+v(psw)+v(pbec)+v(pq)")
        self.thermal(net, "t", self.heat_capacity, self.theta, "v(ploss)", lines)
        net.state(self.t_node, self.states[self.t_node])
        controls = {"duty_a": 0.0, "duty_b": 0.0, "g": OFF_CONDUCTANCE, "lim_a": 0.0, "lim_b": 0.0}
        if self.bec_reg is not None:
            controls["en_bec"] = 1.0
        for control, value in controls.items():
            ports.append(control)
            nodes.append(self.control(net, control, value))
        ports += ["t", "tamb"]
        nodes += [self.t_node, "tamb"]
        net.subckt(name, ports, lines, doc=f"{self.id}: brushed ESC, averaged H-bridge r_on {self.r_on} ohm, "
                                           f"{self.bec_type} BEC {self.bec_voltage:g} V (esc.brushed)")
        net.add(f"{self.inst} {' '.join(nodes)} {name}")

    # Firmware -----------------------------------------------------------------
    def _command(self, inputs: dict[str, Any]) -> float:
        """Throttle from inputs; updates signal validity. ``pulse_us <= 0`` is no signal."""
        if "throttle" in inputs:
            self.signal_valid = True
            return max(-1.0, min(1.0, float(inputs["throttle"])))
        pulse = inputs.get("pulse_us")
        if pulse is not None and float(pulse) > 0.0:
            seconds = float(pulse) * 1e-6
            if 0.5 * self.pulse_min <= seconds <= 1.5 * self.pulse_max:
                self.signal_valid = True
                return throttle_from_pulse(seconds, self.pulse_min, self.pulse_neutral, self.pulse_max, self.deadband)
        self.signal_valid = False
        return 0.0

    def begin_step(self, dt: float | None, inputs: dict[str, Any], time: float) -> None:
        t = self._command(inputs)
        if dt is None:  # static operating point: direct mapping, no timers
            self.throttle = t
            explicit = inputs.get("mode")
            if t > 0:
                self.bridge_mode, self.duty = "drive", t
            elif t < 0 and explicit == "reverse":
                self.bridge_mode, self.duty = "drive", t * self.reverse_fraction
            elif t < 0:
                self.bridge_mode, self.brake = "brake", -t
            else:
                self.bridge_mode = "off"
            self._apply_controls()
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
            self.bridge_mode, self.duty = "drive", t
        elif self.s_mode == "reverse" and not drive_blocked:
            self.bridge_mode, self.duty = "drive", t * self.reverse_fraction
        elif self.s_mode == "brake" and not self.s_thermal_shutdown:
            self.bridge_mode, self.brake = "brake", -t
        else:
            self.bridge_mode = "off"
        self._apply_controls()

    def _apply_controls(self) -> None:
        c = self.controls
        c["duty_a"] = c["duty_b"] = c["lim_a"] = c["lim_b"] = 0.0
        if self.bridge_mode != "drive":
            self.s_limit = 0.0
        elif self.s_limit:
            self.s_limit = math.copysign(self.current_limit, self.s_limit)
        if self.bridge_mode == "drive":
            c["g"] = 1.0 / self.r_on
            if self.duty >= 0:
                c["duty_a"], c["lim_a"] = self.duty, self.s_limit
            else:
                c["duty_b"], c["lim_b"] = -self.duty, self.s_limit
        elif self.bridge_mode == "brake":
            c["g"] = max(self.brake, 1e-3) / self.r_on
        else:
            c["g"] = OFF_CONDUCTANCE
        if self.bec_reg is not None:
            c["en_bec"] = 0.0 if self.s_thermal_shutdown else 1.0

    def time_sources(self, start: float, end: float) -> tuple[dict[str, Any], list[float]]:
        """PWM-switched bridge: the driven leg's duty source becomes the gate
        signal itself (1 = high side on, 0 = low side on), trapezoidal with the
        catalog switching time, period 1 / f_pwm aligned to the ngspice clock.
        The leg element is unchanged: with d(t) in {0, 1} it is exactly the
        synchronous half bridge, so ngspice resolves the current ripple."""
        if not self.switched or self.bridge_mode != "drive" or self.s_limit:
            return {}, []
        duty = min(abs(self.duty), 1.0)
        if duty <= 0.0 or duty >= 1.0:
            return {}, []
        period, rise = 1.0 / self.f_pwm, max(self.t_switch, 1e-9)
        on = max(duty * period, rise)

        def gate(t: float) -> float:
            phase = t % period
            if phase < rise:
                return phase / rise
            if phase < on:
                return 1.0
            if phase < on + rise:
                return 1.0 - (phase - on) / rise
            return 0.0

        edges = []
        first = int(start // period)
        for n in range(first, int(end // period) + 1):
            base = n * period
            edges += [t for t in (base, base + rise, base + on, base + on + rise) if start < t <= end]
        leg = "duty_a" if self.duty >= 0 else "duty_b"
        return {self.sources[leg]: gate}, edges

    # Solution -----------------------------------------------------------------
    def motor_current(self, sol: Solution) -> float:
        return sol.i(self.sense("vsa"))

    def limited(self, sol: Solution) -> bool:
        return self.bridge_mode == "drive" and self.s_limit != 0.0

    def driven_leg_current(self, sol: Solution) -> float:
        return sol.i(self.sense("vsa")) if self.duty >= 0 else sol.i(self.sense("vsb"))

    def effective_duty(self, sol: Solution) -> float:
        """Signed average duty applied to the motor, (V_A - V_B + 2 r_on I) / V_in when current-limited."""
        if self.bridge_mode != "drive":
            return 0.0
        if self.limited(sol):
            vin = max(sol.v(self.vin.node, self.gnd.node), 0.1)
            return (sol.v(self.m_a.node, self.m_b.node) + 2.0 * self.r_on * self.motor_current(sol)) / vin
        return self.duty

    def _update_limit(self, sol: Solution) -> None:
        """Cycle-by-cycle current limit as an active set evaluated at the step boundary."""
        if self.bridge_mode != "drive":
            self.s_limit = 0.0
            return
        if self.s_limit == 0.0:
            leg = self.driven_leg_current(sol)
            if abs(leg) > self.current_limit:
                self.s_limit = math.copysign(self.current_limit, leg)
        elif abs(self.effective_duty(sol)) > abs(self.duty) + 1e-9:
            self.s_limit = 0.0

    def bec_current_out(self, sol: Solution) -> float:
        return sol.i(self.sense("vbec")) if self.bec_reg is not None else 0.0

    def losses(self, sol: Solution) -> dict[str, float]:
        return {"conduction": sol.get(self.internal("pcond")), "switching": sol.get(self.internal("psw")),
                "bec": sol.get(self.internal("pbec")), "quiescent": sol.get(self.internal("pq"))}

    def end_step(self, dt: float | None, sol: Solution, time: float) -> list[Event]:
        if dt is None:
            return []
        self.capture_states(sol)
        events: list[Event] = []
        i = self.motor_current(sol)
        vin = sol.v(self.vin.node, self.gnd.node)
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
        if self.limited(sol):
            events.append(Event(time, "esc_current_limit", self.id, f"motor current limited to {self.current_limit:.0f} A",
                                {"current": i, "limit": self.current_limit}))
        self._update_limit(sol)
        self.s_overload = max(0.0, self.s_overload + (i * i - self.i_cont ** 2) * dt)
        temperature = self.s_temperature
        if not self.s_thermal_shutdown and temperature > self.max_temp_c:
            self.s_thermal_shutdown = True
            events.append(Event(time, "esc_overtemperature", self.id, f"case {temperature:.0f} C > {self.max_temp_c} C",
                                {"temperature_c": temperature, "limit": self.max_temp_c}))
        elif self.s_thermal_shutdown and temperature < self.max_temp_c - 10.0:
            self.s_thermal_shutdown = False
        if self.bec_reg is not None:
            i_bec = self.bec_current_out(sol)
            self.s_bec_overload_time = self.s_bec_overload_time + dt if i_bec > self.bec_current else 0.0
            if 0 < self.s_bec_overload_time <= dt:
                events.append(Event(time, "bec_overload", self.id, f"BEC {i_bec:.2f} A > {self.bec_current} A rating",
                                    {"current": i_bec, "rating": self.bec_current}))
        return events

    def outputs(self, sol: Solution) -> dict[str, Any]:
        i = self.motor_current(sol)
        vin = sol.v(self.vin.node, self.gnd.node)
        bec_v = sol.v(self.bec.node, self.gnd.node) if self.bec is not None else 0.0
        losses = self.losses(sol)
        limited = self.limited(sol)
        mode = "limit" if limited else self.bridge_mode
        return {"v_in": vin, "throttle": self.throttle, "mode": mode, "state": self.s_mode, "duty": self.effective_duty(sol),
                "motor_current": i, "input_current": sol.i(self.sense("vsin")),
                "current_limited": limited, "current_limit": self.current_limit,
                "temperature_c": sol.v(self.t_node), "power_loss": sol.get(self.internal("ploss")), "losses": losses,
                "bec_voltage": bec_v, "bec_current": self.bec_current_out(sol), "bec_dissipation": losses["bec"],
                "lvc": self.s_lvc, "signal_valid": self.signal_valid, "failsafe": self._failsafe,
                "thermal_shutdown": self.s_thermal_shutdown, "cells": self.cells}

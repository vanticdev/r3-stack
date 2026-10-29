"""Hobby PWM steering servo and Ackermann linkage.

Servo: a position loop around a geared DC motor. Pulse width maps linearly to
horn angle over ``travel`` between ``min_pulse`` and ``max_pulse``. Changes
smaller than the ``deadband`` are ignored. The motor obeys the linear DC
torque-speed line scaled with supply voltage V:

    tau(u, w) = tau_stall(V) * (u - w / w_nl(V)),   u = clip(Kp e - Kd w, -1, 1)
    tau_stall(V) = tau_stall_6V * V / 6,  w_nl(V) = (pi/3 / t_60deg) * V / 6

which yields both the stall-torque limit and the no-load rate limit
(datasheet ``speed_60deg_at_6v``). Horn dynamics: J_h dw/dt = tau - tau_load,
where tau_load is the linkage reaction reflected by virtual work,
tau_load = sum_i tau_knuckle_i * d(delta_i)/d(theta_h).

Ackermann: the bicycle steer angle delta = ratio * theta_h defines the turn
radius R = L / tan(delta). The inner/outer kingpins (track T) satisfy
tan(delta_in) = L / (R - T/2), tan(delta_out) = L / (R + T/2).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from r3_model.units import to_si

#: Servo horn reflected inertia, estimated from a 15 ms mechanical time constant
#: (J = t_m * tau_stall / w_nl) typical of 20 kg-cm digital servos.
SERVO_MECHANICAL_TIME_CONSTANT = 0.015


def ackermann_angles(delta: float, wheelbase: float, track: float, limit: float) -> tuple[float, float]:
    """(left, right) kingpin angles for bicycle angle ``delta`` (positive = left turn)."""
    if abs(delta) < 1e-9:
        return 0.0, 0.0
    radius = wheelbase / math.tan(delta)  # signed, positive for left turns
    left = math.atan(wheelbase / (radius - track / 2.0))
    right = math.atan(wheelbase / (radius + track / 2.0))
    return max(-limit, min(limit, left)), max(-limit, min(limit, right))


@dataclass
class SteeringServo:
    stall_torque_6v: float
    speed_60deg_6v: float
    min_pulse_us: float
    max_pulse_us: float
    travel: float
    deadband_us: float
    kp: float = 1.0 / math.radians(2.0)  # full drive beyond 2 deg error (digital servo high gain)
    kd: float = 0.012
    theta: float = 0.0
    omega: float = 0.0
    held_pulse_us: float | None = None
    last_torque: float = 0.0
    inertia: float = field(default=0.0)

    @classmethod
    def from_params(cls, params: dict[str, Any]) -> "SteeringServo":
        def si(key: str, default: float) -> float:
            value = params.get(key)
            return default if value is None else to_si(value)

        servo = cls(stall_torque_6v=si("stall_torque_at_6v", 1.96), speed_60deg_6v=si("speed_60deg_at_6v", 0.15),
                    min_pulse_us=si("min_pulse", 500e-6) * 1e6, max_pulse_us=si("max_pulse", 2500e-6) * 1e6,
                    travel=si("travel", math.radians(270.0)), deadband_us=si("deadband", 3e-6) * 1e6)
        servo.inertia = SERVO_MECHANICAL_TIME_CONSTANT * servo.stall_torque_6v / servo.no_load_speed(6.0)
        return servo

    def no_load_speed(self, voltage: float) -> float:
        return (math.pi / 3.0) / self.speed_60deg_6v * max(voltage, 0.0) / 6.0

    def target_angle(self) -> float | None:
        if self.held_pulse_us is None:
            return None
        center = 0.5 * (self.min_pulse_us + self.max_pulse_us)
        span = self.max_pulse_us - self.min_pulse_us
        pulse = min(max(self.held_pulse_us, self.min_pulse_us), self.max_pulse_us)
        return (pulse - center) / span * self.travel

    def step(self, dt: float, pulse_us: float, voltage: float, load_torque: float, substeps: int = 4) -> None:
        """Advance horn state. ``pulse_us`` <= 0 means no signal (hold last target,
        as digital servos do); ``voltage`` is the servo supply."""
        if pulse_us > 0 and (self.held_pulse_us is None or abs(pulse_us - self.held_pulse_us) > self.deadband_us):
            self.held_pulse_us = pulse_us
        target = self.target_angle()
        h = dt / substeps
        stall = self.stall_torque_6v * max(voltage, 0.0) / 6.0
        w_nl = self.no_load_speed(voltage)
        for _ in range(substeps):
            if target is None or voltage < 3.0:
                torque = 0.0  # unpowered or never commanded: limp (gear-train friction only)
                torque -= 0.05 * self.stall_torque_6v * math.tanh(self.omega / 0.05)
            else:
                u = max(-1.0, min(1.0, self.kp * (target - self.theta) - self.kd * self.omega))
                torque = stall * (u - self.omega / max(w_nl, 1e-6))
            self.omega += (torque - load_torque) / self.inertia * h
            self.theta += self.omega * h
            limit = self.travel / 2.0
            if abs(self.theta) > limit:
                self.theta = math.copysign(limit, self.theta)
                self.omega = 0.0
            self.last_torque = torque

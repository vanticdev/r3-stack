"""Traction control, launch control, motor current limiting and gyro yaw assist.

The car drives the rear axle through an open differential, so full throttle
from rest asks the rear tires for several times the force they can transmit.
Without intervention the driven wheels break loose, the rear axle loses its
lateral grip, and the car yaws and spins out. This module limits the forward
throttle to what the tires and the power path can carry, and counter-steers
yaw rate that disagrees with the driver's (or the path follower's) intent:

* ``GroundSpeedEstimator``: forward ground speed from the accelerometer
  (prediction), GPS speed (10 Hz) and the wheel encoder whenever the wheel is
  not slipping, plus zero-velocity updates at standstill.
* ``DcMotorModel``: the brushed motor's electrical equation, used to turn a
  current (torque) budget into a throttle ceiling.
* ``SlipController``: a feed-forward grip budget plus a PI trim on the wheel
  speed error that holds the slip ratio near its target.
* ``LaunchLimiter``: forward throttle slew-rate limit at low speed ("punch").
* ``YawStabilizer``: gyro yaw damping against the kinematic bicycle model.
* ``BrakeSlipController``: anti-lock modulation of the ESC brake. Full brake
  at speed locks the rear wheels and the car spins out (the twin showed a
  180 degree spin from 8.3 m/s); the brake fraction is servoed on the braking
  slip ratio instead, always staying on the ESC's brake side of neutral.

Everything is pure computation on values handed in by the control loop, so it
is deterministic and unit-testable without a HAL.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from typing import Any, Mapping

STANDARD_GRAVITY = 9.80665


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


@dataclass
class TractionConfig:
    """Tuning and plant parameters. Plant values are catalog estimates (see the
    rc_car.yaml catalog entries named in each comment); replace them with
    measured values for a real car."""

    enabled: bool = True                      # slip and launch control (stall protection is always on)
    yaw_assist_enabled: bool = True
    # --- vehicle and drivetrain (catalog estimates)
    vehicle_mass_kg: float = 1.58             # twin-reported assembled mass
    rear_axle_load_fraction: float = 0.54     # static CoM ahead of the rear axle (estimate)
    tire_friction: float = 0.9                # mu, grass/tire (robot.yaml ground_friction)
    gear_ratio: float = 9.066                 # gearbox-spur-diff (18T pinion)
    gear_efficiency: float = 0.88             # gearbox-spur-diff
    motor_kv_rpm_per_v: float = 2100.0        # motor-540-brushed (estimated)
    motor_resistance_ohm: float = 0.12        # 0.11 armature + 2 x 2 mOhm ESC r_on + leads (estimate)
    motor_no_load_current_a: float = 1.6      # motor-540-brushed
    motor_brush_drop_v: float = 0.2           # motor-540-brushed
    esc_deadband: float = 0.03                # esc-brushed-60a
    nominal_battery_v: float = 8.4            # until the INA226 reports (2S full; the conservative side)
    # --- slip control
    target_slip: float = 0.10                 # optimal slip ratio of a rubber tire (0.1 - 0.15)
    slip_speed_floor_mps: float = 1.0         # epsilon in slip = (v_w - v_g) / max(v_g, epsilon)
    grip_feedforward_fraction: float = 0.85   # share of mu m g (rear share) issued before feedback
    slip_kp_n_per_mps: float = 6.0            # PI on wheel speed error, force units
    slip_ki_n_per_m: float = 25.0
    slip_trim_max_n: float = 8.0              # positive integral authority (force)
    max_force_fraction: float = 2.5           # force budget ceiling, x mu m g (rear share)
    # --- launch control
    launch_speed_mps: float = 2.0             # slew limit applies below this ground speed
    launch_slew_per_s: float = 3.0            # forward throttle rise rate (full scale per second)
    # --- stall protection
    motor_current_limit_a: float = 50.0       # XT60 burst 60 A, ESC continuous 60 A
    # --- yaw assist
    yaw_gain: float = 0.6                     # yaw-rate loop gain (dimensionless, see YawStabilizer)
    yaw_deadband_rps: float = 0.05
    yaw_max_correction_rad: float = 0.30
    yaw_min_speed_mps: float = 0.8
    yaw_integral_gain_per_s: float = 1.5      # heading-hold (model-following) integral gain
    yaw_integral_leak_s: float = 1.0          # leak time constant while steering away from centre
    yaw_hold_steer_rad: float = 0.05          # |steer command| below which heading is held (no leak)
    yaw_integral_limit_rad: float = 0.15
    # --- anti-lock braking (supervisor BRAKING state)
    brake_target_slip: float = 0.12           # braking slip ratio (v_g - v_w) / v_g held by the modulation
    brake_gain_per_s: float = 60.0            # d ln(b)/dt per unit slip error
    brake_min_fraction: float = 0.02          # brake fraction floor: never back to neutral (that arms reverse)
    brake_initial_fraction: float = 0.3       # first brake frame (full brake locks the wheels within 0.1 s)
    brake_full_below_mps: float = 1.0         # below this ground speed the brake is applied fully
    free_rolling_current_a: float = 2.5       # below this estimated motor current the wheel rolls freely
    steer_lag_s: float = 0.08                 # servo first-order lag for the yaw reference (estimate)
    yaw_error_throttle_cut_rps: float = 0.6   # yaw error beyond which forward force is cut back

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "TractionConfig":
        defaults = {f.name: f.default for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        for key, value in (values or {}).items():
            if key in defaults:
                kwargs[key] = bool(value) if isinstance(defaults[key], bool) else float(value)
        return cls(**kwargs)


class DcMotorModel:
    """Brushed DC motor behind an averaging PWM ESC and a reduction gear.

    Armature: d V_bat = V_brush + R I + k_e w_m (inductance negligible at the ESC's
    1 kHz PWM average), torque tau_m = k_t (I - I_0), k_t = k_e = 60 / (2 pi Kv).
    At the tire: F = tau_m N eta / r. The ESC maps throttle u to duty
    d = (u - deadband) / (1 - deadband).
    """

    def __init__(self, config: TractionConfig, wheel_radius_m: float) -> None:
        self.config = config
        self.wheel_radius = wheel_radius_m
        self.k_e = 60.0 / (2.0 * math.pi * config.motor_kv_rpm_per_v)

    def motor_omega(self, wheel_speed_mps: float) -> float:
        return wheel_speed_mps / self.wheel_radius * self.config.gear_ratio

    def current_for_force(self, force_n: float) -> float:
        cfg = self.config
        torque = force_n * self.wheel_radius / (cfg.gear_ratio * cfg.gear_efficiency)
        return torque / self.k_e + cfg.motor_no_load_current_a

    def duty_for_current(self, current_a: float, battery_v: float, wheel_speed_mps: float) -> float:
        cfg = self.config
        back_emf = self.k_e * self.motor_omega(max(0.0, wheel_speed_mps))
        return (cfg.motor_brush_drop_v + cfg.motor_resistance_ohm * current_a + back_emf) / max(battery_v, 1.0)

    def current(self, duty: float, battery_v: float, wheel_speed_mps: float) -> float:
        """Forward-drive armature current; 0 when the back-EMF exceeds the applied voltage
        (the brushed ESC freewheels instead of regenerating in drive mode)."""
        cfg = self.config
        back_emf = self.k_e * self.motor_omega(max(0.0, wheel_speed_mps))
        return max(0.0, (duty * battery_v - cfg.motor_brush_drop_v - back_emf) / cfg.motor_resistance_ohm)

    def throttle_for_duty(self, duty: float) -> float:
        db = self.config.esc_deadband
        return db + _clamp(duty, 0.0, 1.0) * (1.0 - db)

    def duty_for_throttle(self, throttle: float) -> float:
        db = self.config.esc_deadband
        return 0.0 if throttle <= db else (throttle - db) / (1.0 - db)


@dataclass
class GroundSpeedConfig:
    accel_noise_mps2: float = 0.5        # process noise on the accelerometer-driven prediction
    bias_walk_mps2: float = 0.02         # accelerometer bias random walk, per sqrt(s)
    gps_sigma_mps: float = 0.10          # catalog velocity_noise 0.05 m/s, doubled for latency
    gps_min_speed_mps: float = 0.3       # GPS speed is a magnitude: below this its noise is rectified
    wheel_sigma_mps: float = 0.05
    wheel_sigma_fraction: float = 0.03
    slip_gate_sigma: float = 3.0         # driven-wheel readings this far above v_ground are slip
    gps_stale_s: float = 1.0             # without GPS speed for this long, gated wheel readings are used
    stationary_sigma_mps: float = 0.01
    bias_tau_s: float = 0.3              # stationary accelerometer bias averaging time


class GroundSpeedEstimator:
    """Two-state Kalman filter, x = [v, b_ax]: forward ground speed and accelerometer bias.

        predict: v' = v + (f_x - b) dt     (f_x: specific force along the chassis x axis;
                                             on level ground f_x = dv/dt)
        updates: GPS speed (|v|), wheel speed while free-rolling, and v = 0 while
                 stationary (wheel still, throttle neutral), where the bias is also
                 re-averaged so mount tilt and the MPU-6050 zero-g offset (+/-50 mg)
                 do not integrate into speed.

    The driven wheel measures ground speed only when it transmits no drive or brake
    torque. Under torque it slips by an unknown amount, and slip that builds up
    gradually passes any innovation gate (the twin showed a 30 % slip at 5 m/s
    pulling a gated estimate up with it), so under torque the wheel is not used
    unless GPS has gone stale; then only readings inside a one-sided gate (not
    faster than the estimate while driving, not slower while braking) are used.
    """

    def __init__(self, config: GroundSpeedConfig | None = None) -> None:
        self.config = config or GroundSpeedConfig()
        self.v = 0.0
        self.bias = 0.0
        self.p = [[0.05 ** 2, 0.0], [0.0, 0.3 ** 2]]
        self.wheel_rejected = 0
        self.stationary = True
        self.since_gps = math.inf

    @property
    def speed(self) -> float:
        return self.v

    @property
    def sigma(self) -> float:
        return math.sqrt(max(0.0, self.p[0][0]))

    def predict(self, dt: float, accel_x: float | None) -> None:
        if dt <= 0.0:
            return
        cfg = self.config
        self.since_gps += dt
        (p00, p01), (p10, p11) = self.p
        if accel_x is None:
            # No accelerometer: random-walk speed with a large process noise.
            q_v = (3.0 * cfg.accel_noise_mps2) ** 2 * dt
            self.p = [[p00 + q_v, p01], [p10, p11]]
            return
        self.v += (accel_x - self.bias) * dt
        # F = [[1, -dt], [0, 1]]: P' = F P F^T + Q.
        n00 = p00 - dt * (p10 + p01) + dt * dt * p11 + cfg.accel_noise_mps2 ** 2 * dt
        n01 = p01 - dt * p11
        n11 = p11 + cfg.bias_walk_mps2 ** 2 * dt
        self.p = [[n00, n01], [n01, n11]]

    def _update(self, measured: float, sigma: float, gate: float | None = None, direction: int = 0) -> bool:
        (p00, p01), (p10, p11) = self.p
        s = p00 + sigma * sigma
        innovation = measured - self.v
        if gate is not None and direction * innovation > gate * math.sqrt(s):
            return False
        k0, k1 = p00 / s, p10 / s
        self.v += k0 * innovation
        self.bias += k1 * innovation
        self.p = [[(1 - k0) * p00, (1 - k0) * p01], [p10 - k1 * p00, p11 - k1 * p01]]
        self.p[1][0] = self.p[0][1]
        return True

    def update_gps(self, speed: float) -> bool:
        cfg = self.config
        self.since_gps = 0.0  # the receiver is alive even when the speed is too low to use
        if speed < cfg.gps_min_speed_mps:
            return False
        return self._update(math.copysign(speed, self.v if abs(self.v) > cfg.gps_min_speed_mps else 1.0),
                            cfg.gps_sigma_mps)

    def update_wheel(self, wheel_speed: float, throttle: float, free_rolling: bool) -> bool:
        """``wheel_speed`` signed by the commanded direction; ``throttle`` the last command;
        ``free_rolling``: the drivetrain transmits (almost) no torque."""
        cfg = self.config
        sigma = cfg.wheel_sigma_mps + cfg.wheel_sigma_fraction * abs(wheel_speed)
        if free_rolling:
            accepted = self._update(wheel_speed, sigma)
        elif self.since_gps > cfg.gps_stale_s:
            direction = 1 if throttle > 0.0 else -1
            accepted = self._update(wheel_speed, sigma, cfg.slip_gate_sigma, direction)
        else:
            accepted = False
        self.wheel_rejected = 0 if accepted else self.wheel_rejected + 1
        return accepted

    def update_stationary(self, dt: float, accel_x: float | None) -> None:
        cfg = self.config
        if accel_x is not None:
            alpha = dt / (cfg.bias_tau_s + dt)
            self.bias += alpha * (accel_x - self.bias)
        self._update(0.0, cfg.stationary_sigma_mps)


class SlipController:
    """Throttle ceiling that holds the driven-wheel slip ratio near its target.

    Slip ratio lambda = (v_w - v_g) / max(v_g, epsilon). The wheel-speed target is
    v_w* = v_g + lambda* max(v_g, epsilon). The allowed tire force is a feed-forward
    grip budget, F_ff = f mu m g (rear share), plus a PI trim on e = v_w* - v_w; the
    force is turned into an armature current with the motor model, capped by the
    stall-protection current limit, and then into the ESC throttle that produces
    that current at the present wheel speed (``DcMotorModel.duty_for_current``).
    Commanding current rather than duty is what makes this work at launch, where
    the 4-pulse/rev encoder is too slow for feedback alone: the current, and so the
    tire force, is bounded from the first tick.
    """

    def __init__(self, config: TractionConfig, motor: DcMotorModel) -> None:
        self.config = config
        self.motor = motor
        self.integral = 0.0
        self.slip = 0.0
        self.force_budget = 0.0
        self.current_budget = 0.0
        self.current_limited = False

    def reset(self) -> None:
        self.integral = 0.0

    @property
    def grip_force(self) -> float:
        cfg = self.config
        return cfg.tire_friction * cfg.vehicle_mass_kg * STANDARD_GRAVITY * cfg.rear_axle_load_fraction

    def ceiling(self, dt: float, ground_speed: float, wheel_speed: float, battery_v: float,
                force_scale: float = 1.0, driving: bool = True) -> float:
        cfg = self.config
        v_g = max(0.0, ground_speed)
        reference = max(v_g, cfg.slip_speed_floor_mps)
        self.slip = (wheel_speed - v_g) / reference
        # A driven wheel cannot turn slower than the ground; a reading below v_g is
        # encoder lag (4 edges/rev), which must not wind up the integrator at launch.
        error = v_g + cfg.target_slip * reference - max(wheel_speed, v_g)
        feed_forward = cfg.grip_feedforward_fraction * self.grip_force
        # Integral trim: up to +slip_trim_max_n to find grip beyond the estimate, but
        # only down to -F_ff / 2 so a spin event cannot leave the budget pinned at zero
        # (the proportional term already removes force quickly while the wheel spins).
        ki = max(cfg.slip_ki_n_per_m, 1e-9)
        trim_high, trim_low = cfg.slip_trim_max_n / ki, -0.5 * feed_forward / ki
        force = feed_forward + cfg.slip_kp_n_per_mps * error + cfg.slip_ki_n_per_m * self.integral
        force = _clamp(force * force_scale, 0.0, cfg.max_force_fraction * self.grip_force)
        if driving:
            self.integral = _clamp(self.integral + error * dt, trim_low, trim_high)
        self.force_budget = force
        traction_current = self.motor.current_for_force(force)
        self.current_limited = traction_current > cfg.motor_current_limit_a
        self.current_budget = min(traction_current, cfg.motor_current_limit_a)
        return self.throttle_for_current(self.current_budget, battery_v, wheel_speed, v_g)

    def current_limit_ceiling(self, battery_v: float, wheel_speed: float, ground_speed: float) -> float:
        """Stall protection alone (traction control disabled)."""
        self.slip = 0.0
        self.current_limited = True
        self.current_budget = self.config.motor_current_limit_a
        return self.throttle_for_current(self.current_budget, battery_v, wheel_speed, max(0.0, ground_speed))

    def throttle_for_current(self, current_a: float, battery_v: float, wheel_speed: float, ground_speed: float) -> float:
        # The driven wheel turns at least as fast as the ground while driving; the encoder
        # lags at low speed, so v_g bounds the back-EMF from below.
        duty = self.motor.duty_for_current(current_a, battery_v, max(wheel_speed, ground_speed))
        return self.motor.throttle_for_duty(duty) if duty < 1.0 else 1.0


class LaunchLimiter:
    """Forward throttle slew-rate limit below ``launch_speed_mps`` (throttle "punch")."""

    def __init__(self, config: TractionConfig) -> None:
        self.config = config
        self.output = 0.0

    def reset(self) -> None:
        self.output = 0.0

    def update(self, dt: float, throttle: float, ground_speed: float) -> float:
        cfg = self.config
        if throttle <= 0.0 or ground_speed >= cfg.launch_speed_mps:
            self.output = throttle
        else:
            self.output = min(throttle, max(self.output, 0.0) + cfg.launch_slew_per_s * dt)
        return self.output


class YawStabilizer:
    """Gyro yaw damping (the counter-steer of RC "gyro" stability systems).

    Reference yaw rate from the kinematic bicycle model on the commanded steer
    angle, lagged by the servo time constant: w_ref = v tan(delta) / L, limited to
    the friction bound |w| <= mu g / v. With e = w_ref - w_gyro the correction is

        delta_c = (L / v) (k e_db + k_i E),   dE/dt = e - E / tau_leak,

    where e_db is e outside a small deadband and the leak is active only while the
    steering command is away from centre (|delta| > ``yaw_hold_steer_rad``). The bicycle yaw-rate sensitivity is
    d w / d delta = v / L, so the loop gain is ``k`` at every speed. E is the
    heading the car has lost against the model (rad); its leaky integral is the
    "heading hold" of RC gyros, which straightens out a heading error left by a
    disturbance that rate damping alone would only stop, not undo.
    """

    def __init__(self, config: TractionConfig, wheelbase_m: float, max_steer_rad: float) -> None:
        self.config = config
        self.wheelbase = wheelbase_m
        self.max_steer = max_steer_rad
        self.steer_filtered = 0.0
        self.reference = 0.0
        self.error = 0.0
        self.integral = 0.0
        self.correction = 0.0

    def reset(self) -> None:
        self.error = self.correction = self.integral = 0.0

    def update(self, dt: float, steer_rad: float, ground_speed: float, yaw_rate: float) -> float:
        """Returns the corrected steer angle (rad)."""
        cfg = self.config
        self.steer_filtered += (steer_rad - self.steer_filtered) * dt / (cfg.steer_lag_s + dt)
        v = ground_speed
        if v < cfg.yaw_min_speed_mps:
            self.reference = self.error = self.correction = self.integral = 0.0
            return steer_rad
        reference = v * math.tan(self.steer_filtered) / self.wheelbase
        bound = cfg.tire_friction * STANDARD_GRAVITY / v
        self.reference = _clamp(reference, -bound, bound)
        self.error = self.reference - yaw_rate
        limit = cfg.yaw_integral_limit_rad
        leak = self.integral / cfg.yaw_integral_leak_s if abs(steer_rad) > cfg.yaw_hold_steer_rad else 0.0
        self.integral = _clamp(self.integral + (self.error - leak) * dt, -limit, limit)
        excess = math.copysign(max(0.0, abs(self.error) - cfg.yaw_deadband_rps), self.error)
        correction = self.wheelbase / v * (cfg.yaw_gain * excess + cfg.yaw_integral_gain_per_s * self.integral)
        self.correction = _clamp(correction, -cfg.yaw_max_correction_rad, cfg.yaw_max_correction_rad)
        return _clamp(steer_rad + self.correction, -self.max_steer, self.max_steer)

    def force_scale(self) -> float:
        """Forward-force reduction while the yaw error is large: unloading the rear
        tires longitudinally restores their lateral grip (friction circle)."""
        cut = self.config.yaw_error_throttle_cut_rps
        excess = abs(self.error) - cut
        return 1.0 if excess <= 0.0 else _clamp(1.0 - excess / cut, 0.3, 1.0)


class BrakeSlipController:
    """Anti-lock modulation of the brushed ESC's brake.

    The ESC's brake shorts the motor (both low-side FETs, fraction ``b``), so the
    braking torque scales with the back-EMF and hence with speed: at 8 m/s it far
    exceeds the rear tires' grip, the rear wheels lock and lose their lateral grip,
    and the car spins. With braking slip lambda_b = (v_g - v_w) / max(v_g, eps) the
    brake fraction follows

        d ln(b) / dt = -k (lambda_b - lambda*),   b in [b_min, 1],

    a multiplicative (log-domain) integral law: the brake-to-current map of a
    shorted motor is strongly nonlinear in ``b``, and the log domain gives the same
    relative authority at every operating point. The command never returns to
    neutral: after a brake, neutral followed by a reverse pulse is *reverse* on a
    hobby ESC (forward/brake/reverse logic), so ``b`` is floored at ``b_min``.
    Below ``brake_full_below_mps`` (where the 4-pulse encoder is too slow for slip
    feedback and the back-EMF torque is small) the brake is full.
    """

    def __init__(self, config: TractionConfig) -> None:
        self.config = config
        self.fraction = config.brake_initial_fraction
        self.slip = 0.0

    def reset(self) -> None:
        self.fraction = self.config.brake_initial_fraction
        self.slip = 0.0

    def update(self, dt: float, ground_speed: float, wheel_speed: float) -> float:
        """Brake fraction in [b_min, 1] for this tick."""
        cfg = self.config
        v_g = max(0.0, ground_speed)
        if v_g < cfg.brake_full_below_mps:
            self.slip = 0.0
            self.fraction = 1.0
            return self.fraction
        self.slip = (v_g - max(0.0, wheel_speed)) / max(v_g, cfg.slip_speed_floor_mps)
        error = self.slip - cfg.brake_target_slip
        self.fraction = _clamp(self.fraction * math.exp(-cfg.brake_gain_per_s * error * dt), cfg.brake_min_fraction, 1.0)
        return self.fraction


@dataclass(frozen=True)
class TractionOutput:
    throttle: float
    steer_rad: float


class TractionControl:
    """Composes the limiters for the control loop.

    Per tick: ``update_speed`` (estimator), then ``ceiling`` (before the throttle
    command is computed, so the speed controller's anti-windup sees it), then
    ``apply`` (launch slew, ceiling and yaw assist on the final command).
    """

    def __init__(self, config: TractionConfig, wheel_radius_m: float, wheelbase_m: float, max_steer_rad: float) -> None:
        self.config = config
        self.motor = DcMotorModel(config, wheel_radius_m)
        self.ground = GroundSpeedEstimator()
        self.slip = SlipController(config, self.motor)
        self.launch = LaunchLimiter(config)
        self.yaw = YawStabilizer(config, wheelbase_m, max_steer_rad)
        self.brake = BrakeSlipController(config)
        self.battery_v = config.nominal_battery_v
        self.throttle_ceiling = 1.0
        self.motor_current = 0.0
        self.peak_motor_current = 0.0
        self.limiting = False

    def reset(self) -> None:
        self.slip.reset()
        self.launch.reset()
        self.yaw.reset()

    def update_speed(self, dt: float, accel_x: float | None, wheel_speed_signed: float, throttle: float,
                     stationary: bool, gps_speed: float | None = None) -> float:
        self.ground.predict(dt, accel_x)
        if gps_speed is not None:
            self.ground.update_gps(gps_speed)
        self.ground.stationary = stationary
        if stationary:
            self.ground.update_stationary(dt, accel_x)
        else:
            free_rolling = throttle >= 0.0 and self.motor_current < self.config.free_rolling_current_a
            self.ground.update_wheel(wheel_speed_signed, throttle, free_rolling)
        return self.ground.speed

    def ceiling(self, dt: float, wheel_speed: float, battery_v: float | None, driving: bool) -> float:
        if battery_v is not None and battery_v > 1.0:
            self.battery_v = battery_v
        if not self.config.enabled:
            # Stall protection stays active without traction control.
            self.throttle_ceiling = self.slip.current_limit_ceiling(self.battery_v, wheel_speed, self.ground.speed)
            return self.throttle_ceiling
        scale = self.yaw.force_scale() if self.config.yaw_assist_enabled else 1.0
        self.throttle_ceiling = self.slip.ceiling(dt, self.ground.speed, wheel_speed, self.battery_v, scale, driving)
        return self.throttle_ceiling

    def brake_command(self, dt: float, wheel_speed: float, yaw_rate: float) -> TractionOutput:
        """Failsafe brake: anti-lock brake fraction as an ESC throttle (< 0) and yaw-hold steering."""
        v_g = max(0.0, self.ground.speed)
        fraction = self.brake.update(dt, v_g, wheel_speed)
        steer_rad = self.yaw.update(dt, 0.0, v_g, yaw_rate) if self.config.yaw_assist_enabled else 0.0
        self.motor_current = 0.0
        return TractionOutput(-self.motor.throttle_for_duty(fraction), steer_rad)

    def apply(self, dt: float, throttle: float, steer_rad: float, wheel_speed: float, yaw_rate: float) -> TractionOutput:
        v_g = max(0.0, self.ground.speed)
        if throttle > 0.0:
            limited = min(throttle, self.throttle_ceiling)
            self.limiting = limited < throttle - 1e-6
            throttle = self.launch.update(dt, limited, v_g) if self.config.enabled else limited
        else:
            self.limiting = False
            self.launch.update(dt, throttle, v_g)
        if self.config.yaw_assist_enabled:
            steer_rad = self.yaw.update(dt, steer_rad, v_g, yaw_rate)
        self.motor_current = self.motor.current(self.motor.duty_for_throttle(throttle), self.battery_v,
                                                max(wheel_speed, v_g)) if throttle > 0.0 else 0.0
        self.peak_motor_current = max(self.peak_motor_current, self.motor_current)
        return TractionOutput(throttle, steer_rad)


__all__ = ["BrakeSlipController", "DcMotorModel", "GroundSpeedConfig", "GroundSpeedEstimator", "LaunchLimiter", "SlipController",
           "TractionConfig", "TractionControl", "TractionOutput", "YawStabilizer"]

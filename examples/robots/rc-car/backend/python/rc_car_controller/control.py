"""Control laws and safety logic: pulse mapping, pure pursuit, speed control,
battery protection, radio link monitoring and the arming/failsafe supervisor.

Everything here is pure computation (no HAL access) so it can be unit tested
and simulated deterministically.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field

from estimator import wrap_angle


def clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


# --- actuator mapping ---------------------------------------------------------

@dataclass(frozen=True)
class PulseMap:
    """Normalised commands -> servo-style pulse widths.

    Throttle ``u`` in [-1, 1]: +1 full forward, -1 full reverse/brake.
    Steering ``n`` in [-1, 1]: +1 full left (positive yaw, robot +y), -1 full right.
    """

    esc_reverse_us: float = 1000.0
    esc_neutral_us: float = 1500.0
    esc_forward_us: float = 2000.0
    # Defaults equal settings.DEFAULT_CONFIG["steering_pulse_us"]: the CAD-solved linkage end points (+-30 deg horn).
    steer_left_us: float = 1722.0
    steer_center_us: float = 1500.0
    steer_right_us: float = 1278.0
    max_steer_rad: float = 0.4021

    def esc_pulse(self, throttle: float) -> float:
        u = clamp(throttle, -1.0, 1.0)
        span = self.esc_forward_us - self.esc_neutral_us if u >= 0 else self.esc_neutral_us - self.esc_reverse_us
        return self.esc_neutral_us + u * span

    def steer_pulse(self, steer: float) -> float:
        n = clamp(steer, -1.0, 1.0)
        end = self.steer_left_us if n >= 0 else self.steer_right_us
        return self.steer_center_us + abs(n) * (end - self.steer_center_us)

    def steer_from_angle(self, angle_rad: float) -> float:
        return clamp(angle_rad / self.max_steer_rad, -1.0, 1.0)


def stick_to_normalised(pulse_us: float, deadband: float = 0.02) -> float:
    """CRSF stick pulse (988..2012 us, 1500 centre) -> [-1, 1] with a centre deadband."""
    n = clamp((pulse_us - 1500.0) / 512.0, -1.0, 1.0)
    if abs(n) < deadband:
        return 0.0
    return math.copysign((abs(n) - deadband) / (1.0 - deadband), n)


# --- path following -----------------------------------------------------------

@dataclass(frozen=True)
class _Segment:
    x0: float
    y0: float
    dx: float
    dy: float
    s0: float
    length: float


class WaypointPath:
    """Polyline through waypoints, parameterised by arc length ``s``.

    A closed path returns from the last waypoint to the first and repeats; ``s``
    is then unwrapped (lap n covers [n L, (n+1) L)).
    """

    def __init__(self, waypoints: list[tuple[float, float]], closed: bool) -> None:
        if len(waypoints) < 2:
            raise ValueError("a path needs at least two waypoints")
        points = [tuple(map(float, p)) for p in waypoints]
        if closed:
            points.append(points[0])
        self.closed = closed
        self.waypoints = points
        self.segments: list[_Segment] = []
        s = 0.0
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            length = math.hypot(x1 - x0, y1 - y0)
            if length > 1e-9:
                self.segments.append(_Segment(x0, y0, x1 - x0, y1 - y0, s, length))
                s += length
        self.length = s

    def _local(self, s: float) -> float:
        if self.closed:
            return s % self.length
        return clamp(s, 0.0, self.length)

    def _segment_at(self, s_local: float) -> _Segment:
        for segment in self.segments:
            if s_local < segment.s0 + segment.length:
                return segment
        return self.segments[-1]

    def point_at(self, s: float) -> tuple[float, float]:
        s_local = self._local(s)
        seg = self._segment_at(s_local)
        f = clamp((s_local - seg.s0) / seg.length, 0.0, 1.0)
        return seg.x0 + f * seg.dx, seg.y0 + f * seg.dy

    def heading_at(self, s: float) -> float:
        seg = self._segment_at(self._local(s))
        return math.atan2(seg.dy, seg.dx)

    def project(self, x: float, y: float, s_hint: float, behind: float, ahead: float) -> tuple[float, float]:
        """Closest point to (x, y) with unwrapped s in [s_hint - behind, s_hint + ahead].

        Returns (s, signed cross-track error, positive = left of path).
        """
        best: tuple[float, float, float] | None = None  # (distance^2, |ds|, s)
        lap_base = math.floor(s_hint / self.length) * self.length if self.closed else 0.0
        laps = (-1.0, 0.0, 1.0) if self.closed else (0.0,)
        for lap in laps:
            base = lap_base + lap * self.length
            for seg in self.segments:
                f = clamp(((x - seg.x0) * seg.dx + (y - seg.y0) * seg.dy) / (seg.length ** 2), 0.0, 1.0)
                s = base + seg.s0 + f * seg.length
                if not (s_hint - behind <= s <= s_hint + ahead):
                    # Clamp into the window on this segment if it overlaps it.
                    lo, hi = max(s_hint - behind, base + seg.s0), min(s_hint + ahead, base + seg.s0 + seg.length)
                    if lo > hi:
                        continue
                    s = clamp(s, lo, hi)
                    f = (s - base - seg.s0) / seg.length
                px, py = seg.x0 + f * seg.dx, seg.y0 + f * seg.dy
                candidate = ((x - px) ** 2 + (y - py) ** 2, abs(s - s_hint), s)
                if best is None or candidate[:2] < best[:2]:
                    best = candidate
        if best is None:
            return s_hint, 0.0
        s = best[2]
        heading = self.heading_at(s)
        px, py = self.point_at(s)
        cross = -math.sin(heading) * (x - px) + math.cos(heading) * (y - py)
        return s, cross

    def corners(self) -> list[tuple[float, float]]:
        """(s of vertex, turn angle in rad) for interior vertices of one lap."""
        result = []
        count = len(self.segments)
        for i in range(count if self.closed else count - 1):
            a, b = self.segments[i], self.segments[(i + 1) % count]
            turn = abs(wrap_angle(math.atan2(b.dy, b.dx) - math.atan2(a.dy, a.dx)))
            if turn > 1e-3:
                result.append((a.s0 + a.length, turn))
        return result


@dataclass
class PurePursuitConfig:
    wheelbase_m: float = 0.26
    max_steer_rad: float = 0.4021
    lookahead_min_m: float = 1.2
    lookahead_max_m: float = 6.0
    lookahead_base_m: float = 1.0
    lookahead_gain_s: float = 0.5        # L = base + gain * v
    finish_tolerance_m: float = 0.5


@dataclass(frozen=True)
class PursuitCommand:
    steer_rad: float
    curvature: float
    cross_track_m: float
    lookahead_m: float
    target: tuple[float, float]
    progress_m: float
    remaining_m: float
    lap: int
    finished: bool


class PurePursuit:
    """Pure pursuit (Coulter 1992) with an arc-length lookahead carrot.

    Carrot = path point at s_proj + L. With alpha the carrot bearing relative to
    the heading and l_d its distance: curvature k = 2 sin(alpha) / l_d and the
    bicycle-model steer angle delta = atan(wheelbase * k).
    """

    def __init__(self, path: WaypointPath, laps: int = 1, config: PurePursuitConfig | None = None) -> None:
        self.path = path
        self.laps = max(1, int(laps)) if path.closed else 1
        self.config = config or PurePursuitConfig()
        self.progress = 0.0
        self.end_s = path.length * self.laps

    def lookahead(self, speed: float) -> float:
        cfg = self.config
        return clamp(cfg.lookahead_base_m + cfg.lookahead_gain_s * abs(speed), cfg.lookahead_min_m, cfg.lookahead_max_m)

    def update(self, x: float, y: float, yaw: float, speed: float) -> PursuitCommand:
        cfg = self.config
        lookahead = self.lookahead(speed)
        s, cross = self.path.project(x, y, self.progress, behind=1.0, ahead=max(5.0, 2.0 * lookahead))
        self.progress = clamp(max(s, self.progress - 1.0), 0.0, self.end_s)
        if self.progress <= 1e-9:
            # Approach to the first waypoint: report the lateral offset from the first leg's line.
            first = self.path.segments[0]
            cross = (first.dx * (y - first.y0) - first.dy * (x - first.x0)) / first.length
        remaining = self.end_s - self.progress
        target_s = min(self.progress + lookahead, self.end_s)
        tx, ty = self.path.point_at(target_s)
        if target_s >= self.end_s and not self.path.closed:
            # Extend past the final waypoint so the carrot never collapses onto the car.
            heading = self.path.heading_at(self.end_s)
            extra = lookahead - (self.end_s - self.progress)
            tx, ty = tx + extra * math.cos(heading), ty + extra * math.sin(heading)
        alpha = wrap_angle(math.atan2(ty - y, tx - x) - yaw)
        distance = max(math.hypot(tx - x, ty - y), 1e-3)
        curvature = 2.0 * math.sin(alpha) / distance
        steer = clamp(math.atan(cfg.wheelbase_m * curvature), -cfg.max_steer_rad, cfg.max_steer_rad)
        lap = int(self.progress // self.path.length) if self.path.closed else 0
        finished = remaining <= cfg.finish_tolerance_m
        return PursuitCommand(steer, curvature, cross, lookahead, (tx, ty), self.progress, remaining, lap, finished)


@dataclass
class SpeedPlannerConfig:
    cruise_mps: float = 4.0
    max_lateral_accel_mps2: float = 3.5
    accel_mps2: float = 2.5
    decel_mps2: float = 2.5
    corner_setback_m: float = 2.0   # tangent-point setback of the arc pure pursuit cuts at a vertex
    min_corner_speed_mps: float = 1.0


class SpeedPlanner:
    """Reference speed from cruise speed, path curvature and the mission end.

    A vertex of turn angle theta is approximated by an arc with a fixed tangent
    setback d: R = d / tan(theta / 2); corner speed v_c = sqrt(a_lat R). Approach
    speed is limited by v^2 = v_c^2 + 2 a_dec (distance to the arc entry).
    """

    def __init__(self, path: WaypointPath, laps: int, config: SpeedPlannerConfig | None = None) -> None:
        self.config = config or SpeedPlannerConfig()
        self.path = path
        self.laps = laps
        cfg = self.config
        self._corners = []
        for s, turn in path.corners():
            radius = cfg.corner_setback_m / math.tan(turn / 2.0)
            v_corner = max(cfg.min_corner_speed_mps, math.sqrt(cfg.max_lateral_accel_mps2 * radius))
            self._corners.append((s, v_corner))
        self.reference = 0.0
        self.reference_accel = 0.0

    def _limit_at(self, progress: float, end_s: float) -> float:
        cfg = self.config
        limit = cfg.cruise_mps
        limit = min(limit, math.sqrt(2.0 * cfg.decel_mps2 * max(0.0, end_s - progress)))
        length = self.path.length
        lap_start = math.floor(progress / length) * length if self.path.closed else 0.0
        for lap_offset in ((0.0, length) if self.path.closed else (0.0,)):
            for s, v_corner in self._corners:
                s_abs = lap_start + lap_offset + s
                if s_abs >= end_s - 1e-6:
                    continue
                distance = s_abs - self.config.corner_setback_m - progress
                if distance < -2.0 * self.config.corner_setback_m:
                    continue
                limit = min(limit, math.sqrt(v_corner ** 2 + 2.0 * cfg.decel_mps2 * max(0.0, distance)))
        return limit

    def update(self, dt: float, progress: float, end_s: float, curvature: float, cruise_mps: float | None = None) -> float:
        cfg = self.config
        cruise = cfg.cruise_mps if cruise_mps is None else min(cruise_mps, cfg.cruise_mps)
        target = min(cruise, self._limit_at(progress, end_s))
        if abs(curvature) > 1e-6:
            target = min(target, math.sqrt(cfg.max_lateral_accel_mps2 / abs(curvature)))
        previous = self.reference
        if target > self.reference:
            self.reference = min(target, self.reference + cfg.accel_mps2 * dt)
        else:
            self.reference = max(target, self.reference - cfg.decel_mps2 * dt)
        self.reference_accel = (self.reference - previous) / dt if dt > 0 else 0.0
        return self.reference


@dataclass
class SpeedControllerConfig:
    kp: float = 0.2                      # throttle per (m/s)
    ki: float = 0.15                     # throttle per (m/s * s)
    speed_at_full_throttle_mps: float = 10.0  # feed-forward slope (estimate, 540 motor / 9.07:1 / 55 mm wheel)
    deadband_offset: float = 0.04        # ESC deadband 0.03 (catalog) + margin
    integrator_limit: float = 0.35
    max_brake: float = 0.6
    brake_min_speed_mps: float = 0.4
    accel_at_full_throttle_mps2: float = 9.0   # free speed / motor time constant (estimate)
    decel_at_full_brake_mps2: float = 4.4      # traction limited: mu g x rear axle load share (estimate)


class SpeedController:
    """Throttle = feed-forward(v_ref) + Kp e + Ki integral(e), with anti-windup.

    Feed-forward is the steady-state throttle of a DC motor drive,
    u_ff = deadband + v_ref / v_full, plus the reference acceleration over the
    full-throttle acceleration (or full-brake deceleration). Negative throttle
    (ESC brake) is only issued while the car is rolling forward so it can never
    select reverse.
    """

    def __init__(self, config: SpeedControllerConfig | None = None) -> None:
        self.config = config or SpeedControllerConfig()
        self.integral = 0.0

    def reset(self) -> None:
        self.integral = 0.0

    def update(self, dt: float, reference: float, measured: float, max_throttle: float = 1.0,
               reference_accel: float = 0.0) -> float:
        cfg = self.config
        if reference <= 0.05 and measured < cfg.brake_min_speed_mps:
            self.integral = 0.0
            return 0.0
        error = reference - measured
        feed_forward = (cfg.deadband_offset + reference / cfg.speed_at_full_throttle_mps) if reference > 0.05 else 0.0
        if reference_accel > 0:
            feed_forward += reference_accel / cfg.accel_at_full_throttle_mps2
        else:
            feed_forward += reference_accel / cfg.decel_at_full_brake_mps2
        unsaturated = feed_forward + cfg.kp * error + cfg.ki * self.integral
        low = -cfg.max_brake if measured >= cfg.brake_min_speed_mps else 0.0
        output = clamp(unsaturated, low, max_throttle)
        # Conditional integration: not into saturation, and not while the reference
        # ramps (feed-forward carries transients; integrating the measurement lag of
        # the period-based wheel speed during a ramp causes overshoot).
        ramping = abs(reference_accel) > 0.1
        if not ramping and not ((output >= max_throttle and error > 0) or (output <= low and error < 0)):
            self.integral = clamp(self.integral + error * dt, -cfg.integrator_limit / max(cfg.ki, 1e-9),
                                  cfg.integrator_limit / max(cfg.ki, 1e-9))
        return output


# --- battery protection -------------------------------------------------------

@dataclass
class BatteryConfig:
    limit_v: float = 7.0
    cutoff_v: float = 6.6
    filter_tau_s: float = 0.5
    min_throttle_scale: float = 0.25
    cutoff_hold_s: float = 1.5
    rest_margin_v: float = 0.2
    rest_throttle: float = 0.05
    emergency_margin_v: float = 0.4
    emergency_hold_s: float = 0.3


class BatteryMonitor:
    """Sag-aware low-voltage protection.

    LiPo terminal voltage under load is V = V_oc - I R_int, so a single-sample
    threshold would trip on every acceleration. Instead:

    * the bus voltage is low-pass filtered (tau ``filter_tau_s``) to remove ESC
      ripple and short transients;
    * below ``limit_v`` the throttle ceiling falls linearly to
      ``min_throttle_scale`` at ``cutoff_v``. Less throttle means less current and
      less sag, so this regulates the loaded voltage above the cutoff instead of
      tripping on it;
    * cutoff latches only if the filtered voltage stays below ``cutoff_v`` for
      ``cutoff_hold_s`` despite limiting, or below ``cutoff_v + rest_margin_v`` while
      resting (|throttle| < ``rest_throttle``: little sag, so the pack is empty);
    * a raw reading below ``cutoff_v - emergency_margin_v`` for ``emergency_hold_s``
      cuts immediately (cell protection floor).
    """

    def __init__(self, config: BatteryConfig | None = None) -> None:
        self.config = config or BatteryConfig()
        self.filtered_v: float | None = None
        self.cutoff = False
        self.cutoff_reason = ""
        self.limiting = False
        self.min_filtered_v = math.inf
        self._below_since: float | None = None
        self._rest_below_since: float | None = None
        self._emergency_since: float | None = None

    def update(self, now: float, dt: float, voltage: float, throttle: float) -> float:
        """Feed one bus-voltage sample; returns the allowed forward throttle scale."""
        cfg = self.config
        if self.filtered_v is None:
            self.filtered_v = voltage
        else:
            alpha = dt / (cfg.filter_tau_s + dt)
            self.filtered_v += alpha * (voltage - self.filtered_v)
        v = self.filtered_v
        self.min_filtered_v = min(self.min_filtered_v, v)

        def held(since: float | None, condition: bool, hold: float) -> tuple[float | None, bool]:
            if not condition:
                return None, False
            since = now if since is None else since
            return since, now - since >= hold

        self._below_since, below = held(self._below_since, v < cfg.cutoff_v, cfg.cutoff_hold_s)
        resting = abs(throttle) < cfg.rest_throttle
        self._rest_below_since, rest_low = held(self._rest_below_since, resting and v < cfg.cutoff_v + cfg.rest_margin_v,
                                                cfg.cutoff_hold_s)
        self._emergency_since, emergency = held(self._emergency_since, voltage < cfg.cutoff_v - cfg.emergency_margin_v,
                                                cfg.emergency_hold_s)
        if not self.cutoff and (below or rest_low or emergency):
            self.cutoff = True
            self.cutoff_reason = "emergency_floor" if emergency else "sustained_under_load" if below else "resting_voltage"
        if self.cutoff:
            return 0.0
        if v >= cfg.limit_v:
            self.limiting = False
            return 1.0
        self.limiting = True
        fraction = clamp((v - cfg.cutoff_v) / (cfg.limit_v - cfg.cutoff_v), 0.0, 1.0)
        return cfg.min_throttle_scale + (1.0 - cfg.min_throttle_scale) * fraction


# --- radio link and supervisor ------------------------------------------------

class LinkMonitor:
    """Tracks the last valid RC_CHANNELS frame and link statistics."""

    def __init__(self, timeout_s: float) -> None:
        self.timeout_s = timeout_s
        self.channels_us: list[float] | None = None
        self.last_frame_t: float | None = None
        self.link_quality: int | None = None
        self.rssi_dbm: int | None = None
        self.snr_db: int | None = None
        self.last_stats_t: float | None = None
        self.frames = 0

    def on_channels(self, now: float, channels_us: list[float]) -> None:
        self.channels_us = channels_us
        self.last_frame_t = now
        self.frames += 1

    def on_link_statistics(self, now: float, link_quality: int, rssi_dbm: int, snr_db: int) -> None:
        self.link_quality, self.rssi_dbm, self.snr_db, self.last_stats_t = link_quality, rssi_dbm, snr_db, now

    def ok(self, now: float) -> bool:
        if self.last_frame_t is None or now - self.last_frame_t > self.timeout_s:
            return False
        # ELRS reports LQ 0 on a lost link; trust it when the report is fresh.
        stats_fresh = self.last_stats_t is not None and now - self.last_stats_t <= self.timeout_s
        return not (stats_fresh and self.link_quality == 0)

    def channel(self, number: int) -> float:
        if not self.channels_us or number < 1 or number > len(self.channels_us):
            return 1500.0
        return self.channels_us[number - 1]


class State(str, enum.Enum):
    BOOT = "boot"
    DISARMED = "disarmed"
    ARMED = "armed"
    MANUAL = "manual"
    BRAKING = "braking"
    FAILSAFE = "failsafe"
    MISSION_COMPLETE = "mission_complete"
    CUTOFF = "cutoff"


@dataclass
class SupervisorConfig:
    mode: str = "autonomous"
    arm_threshold_us: float = 1700.0
    disarm_threshold_us: float = 1300.0
    failsafe_action: str = "brake"
    brake_throttle: float = 1.0
    brake_stop_speed_mps: float = 0.2
    brake_max_s: float = 3.0
    brake_nominal_decel_mps2: float = 3.0


@dataclass
class SupervisorOutput:
    state: State
    events: list[tuple[str, dict]] = field(default_factory=list)


class Supervisor:
    """Arming, mode and failsafe state machine.

    * Autonomy arms only on a low -> high transition of the AUX arm channel, with a
      valid link and ready sensors; it never arms at boot with the switch already high.
    * Radio failsafe: no valid channel frame for ``radio_failsafe_timeout_s`` (or a
      fresh LQ = 0 report) -> BRAKING (active ESC brake) until the wheels stop or the
      brake time budget ends, then FAILSAFE (neutral, disarmed). Leaving FAILSAFE
      needs a valid link, and autonomy needs a new arm switch cycle.
    * The brake never lasts longer than 1.5 v0 / a_nominal: the hall encoder has no
      direction channel, so a hobby ESC that switched to reverse would otherwise look
      like continued forward motion.
    * Low-voltage cutoff latches CUTOFF (neutral) until restart.
    """

    def __init__(self, config: SupervisorConfig) -> None:
        self.config = config
        self.state = State.BOOT
        self.switch_was_low = False
        self._brake_until = 0.0
        self._after_brake = State.FAILSAFE
        self.failsafe_events = 0

    @property
    def autonomous(self) -> bool:
        return self.config.mode == "autonomous"

    def _brake(self, now: float, speed: float, after: State, events: list, reason: str) -> None:
        cfg = self.config
        if cfg.failsafe_action == "brake" and speed > cfg.brake_stop_speed_mps:
            budget = min(cfg.brake_max_s, max(0.3, 1.5 * speed / cfg.brake_nominal_decel_mps2))
            self._brake_until = now + budget
            self.state = State.BRAKING
            self._after_brake = after
            events.append(("brake_start", {"reason": reason, "speed_mps": speed, "budget_s": budget}))
        else:
            self.state = after

    def update(self, now: float, link_ok: bool, arm_us: float, throttle_us: float, ready: bool, speed: float,
               mission_finished: bool, cutoff: bool) -> SupervisorOutput:
        cfg = self.config
        events: list[tuple[str, dict]] = []
        previous = self.state
        if link_ok and arm_us < cfg.disarm_threshold_us:
            self.switch_was_low = True

        if cutoff and self.state != State.CUTOFF and not (self.state == State.BRAKING and self._after_brake == State.CUTOFF):
            events.append(("low_voltage_cutoff", {"speed_mps": speed}))
            if self.state in (State.ARMED, State.MANUAL):
                self._brake(now, speed, State.CUTOFF, events, "low_voltage")
            elif self.state == State.BRAKING:
                self._after_brake = State.CUTOFF
            else:
                self.state = State.CUTOFF
        elif self.state == State.BRAKING:
            if speed <= cfg.brake_stop_speed_mps or now >= self._brake_until:
                events.append(("brake_end", {"speed_mps": speed}))
                self.state = self._after_brake
        elif self.state == State.BOOT:
            if ready:
                self.state = State.DISARMED
        elif self.state in (State.ARMED, State.MANUAL):
            if not link_ok:
                self.failsafe_events += 1
                events.append(("radio_failsafe", {"speed_mps": speed}))
                self._brake(now, speed, State.FAILSAFE, events, "radio_failsafe")
            elif self.state == State.ARMED and arm_us < cfg.disarm_threshold_us:
                events.append(("disarmed", {"source": "switch"}))
                self._brake(now, speed, State.DISARMED, events, "disarm")
            elif self.state == State.ARMED and mission_finished:
                events.append(("mission_complete", {}))
                self._brake(now, speed, State.MISSION_COMPLETE, events, "mission_complete")
        elif self.state == State.FAILSAFE:
            if link_ok:
                events.append(("link_restored", {}))
                self.state = State.DISARMED
        elif self.state == State.DISARMED and link_ok:
            if not self.autonomous:
                # Manual control resumes only once the throttle stick is at neutral.
                if abs(throttle_us - 1500.0) < 40.0:
                    self.state = State.MANUAL
            elif ready and self.switch_was_low and arm_us > cfg.arm_threshold_us:
                self.switch_was_low = False
                self.state = State.ARMED
                events.append(("armed", {}))
        if self.state != previous:
            events.append(("state", {"from": previous.value, "to": self.state.value}))
        return SupervisorOutput(self.state, events)

    @property
    def braking(self) -> bool:
        return self.state == State.BRAKING

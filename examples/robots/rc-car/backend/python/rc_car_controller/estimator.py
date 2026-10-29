"""Extended Kalman filter for planar vehicle state.

State ``s = [x, y, yaw, v, b]``: east/north position (m) in the local tangent
plane, heading (rad, counter-clockwise from east), forward speed (m/s) and
residual gyro-z bias (rad/s).

Process model (unicycle driven by the measured yaw rate):

    x' = x + v cos(yaw) dt
    y' = y + v sin(yaw) dt
    yaw' = yaw + (w_gyro - b) dt
    v' = v,  b' = b         (random walks)

Measurements: GPS position (x, y), GPS velocity (v cos yaw, v sin yaw) from
RMC/VTG speed and course, and wheel speed v from encoder edges. Pure Python so
the controller has no third-party dependencies on the target.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Matrix = list[list[float]]

N = 5
X, Y, YAW, V, BIAS = range(N)


def wrap_angle(angle: float) -> float:
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def _identity(n: int) -> Matrix:
    return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]


def _matmul(a: Matrix, b: Matrix) -> Matrix:
    columns = list(zip(*b))
    return [[sum(x * y for x, y in zip(row, col)) for col in columns] for row in a]


def _transpose(a: Matrix) -> Matrix:
    return [list(row) for row in zip(*a)]


def _inverse_small(a: Matrix) -> Matrix:
    if len(a) == 1:
        return [[1.0 / a[0][0]]]
    (p, q), (r, s) = a
    det = p * s - q * r
    return [[s / det, -q / det], [-r / det, p / det]]


@dataclass
class EstimatorConfig:
    gps_position_sigma_m: float = 1.3     # CEP 1.5 m (catalog) / 1.1774 -> sigma
    gps_velocity_sigma_mps: float = 0.10  # catalog velocity_noise 0.05 m/s, doubled for course coupling
    gps_min_speed_for_course_mps: float = 0.8
    wheel_speed_sigma_mps: float = 0.05
    wheel_slip_fraction: float = 0.05
    gyro_noise_rps: float = 0.02          # yaw random walk density, rad/s/sqrt(Hz) (estimate)
    accel_noise_mps2: float = 3.0         # unmodelled longitudinal acceleration
    bias_walk_rps: float = 2e-4
    gate_chi2_2dof: float = 13.8          # 99.9 % for 2 degrees of freedom
    max_consecutive_rejections: int = 20


class VehicleEstimator:
    def __init__(self, config: EstimatorConfig | None = None, initial_yaw: float = 0.0,
                 initial_yaw_sigma: float = 0.3) -> None:
        self.config = config or EstimatorConfig()
        self.state = [0.0, 0.0, initial_yaw, 0.0, 0.0]
        self.cov = _identity(N)
        self.cov[X][X] = self.cov[Y][Y] = 100.0 ** 2
        self.cov[YAW][YAW] = initial_yaw_sigma ** 2
        self.cov[V][V] = 0.1 ** 2
        self.cov[BIAS][BIAS] = 0.01 ** 2
        self.position_initialised = False
        self.gps_rejections = 0
        self.gps_updates = 0

    # -- accessors -----------------------------------------------------------
    @property
    def x(self) -> float:
        return self.state[X]

    @property
    def y(self) -> float:
        return self.state[Y]

    @property
    def yaw(self) -> float:
        return self.state[YAW]

    @property
    def speed(self) -> float:
        return self.state[V]

    def sigma(self, index: int) -> float:
        return math.sqrt(max(0.0, self.cov[index][index]))

    # -- filter steps --------------------------------------------------------
    def predict(self, dt: float, yaw_rate_measured: float) -> None:
        if dt <= 0.0:
            return
        x, y, yaw, v, b = self.state
        c, s = math.cos(yaw), math.sin(yaw)
        self.state = [x + v * c * dt, y + v * s * dt, wrap_angle(yaw + (yaw_rate_measured - b) * dt), v, b]
        f = _identity(N)
        f[X][YAW], f[X][V] = -v * s * dt, c * dt
        f[Y][YAW], f[Y][V] = v * c * dt, s * dt
        f[YAW][BIAS] = -dt
        cfg = self.config
        q = [0.01 * dt, 0.01 * dt, cfg.gyro_noise_rps ** 2 * dt, cfg.accel_noise_mps2 ** 2 * dt, cfg.bias_walk_rps ** 2 * dt]
        p = _matmul(_matmul(f, self.cov), _transpose(f))
        for i in range(N):
            p[i][i] += q[i]
        self.cov = p

    def _update(self, innovation: list[float], h: Matrix, r: Matrix, gate: float | None = None) -> bool:
        ph_t = _matmul(self.cov, _transpose(h))
        s = _matmul(h, ph_t)
        for i in range(len(r)):
            for j in range(len(r)):
                s[i][j] += r[i][j]
        s_inv = _inverse_small(s)
        if gate is not None:
            nu = [[value] for value in innovation]
            d2 = _matmul(_matmul(_transpose(nu), s_inv), nu)[0][0]
            if d2 > gate:
                return False
        k = _matmul(ph_t, s_inv)
        for i in range(N):
            self.state[i] += sum(k[i][j] * innovation[j] for j in range(len(innovation)))
        self.state[YAW] = wrap_angle(self.state[YAW])
        # Joseph form: P = (I - K H) P (I - K H)^T + K R K^T keeps P symmetric positive definite.
        i_kh = _identity(N)
        kh = _matmul(k, h)
        for i in range(N):
            for j in range(N):
                i_kh[i][j] -= kh[i][j]
        p = _matmul(_matmul(i_kh, self.cov), _transpose(i_kh))
        krk = _matmul(_matmul(k, r), _transpose(k))
        self.cov = [[p[i][j] + krk[i][j] for j in range(N)] for i in range(N)]
        return True

    def update_gps_position(self, x: float, y: float, hdop: float | None = None) -> bool:
        sigma = self.config.gps_position_sigma_m * max(1.0, hdop or 1.0)
        if not self.position_initialised:
            self.state[X], self.state[Y] = x, y
            self.cov[X][X] = self.cov[Y][Y] = sigma ** 2
            self.position_initialised = True
            return True
        h = [[1.0, 0.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0, 0.0]]
        gate = None if self.gps_rejections >= self.config.max_consecutive_rejections else self.config.gate_chi2_2dof
        accepted = self._update([x - self.state[X], y - self.state[Y]], h, [[sigma ** 2, 0.0], [0.0, sigma ** 2]], gate)
        self.gps_rejections = 0 if accepted else self.gps_rejections + 1
        self.gps_updates += int(accepted)
        return accepted

    def update_gps_velocity(self, speed: float, course_rad_enu: float) -> bool:
        """GPS ground speed and course, course converted to ENU heading."""
        if speed < self.config.gps_min_speed_for_course_mps:
            return False
        _, _, yaw, v, _ = self.state
        c, s = math.cos(yaw), math.sin(yaw)
        z = [speed * math.cos(course_rad_enu), speed * math.sin(course_rad_enu)]
        innovation = [z[0] - v * c, z[1] - v * s]
        h = [[0.0, 0.0, -v * s, c, 0.0], [0.0, 0.0, v * c, s, 0.0]]
        sigma2 = self.config.gps_velocity_sigma_mps ** 2
        # When the heading estimate is far off (e.g. unknown initial heading), the
        # linearisation is poor: re-seed heading from the course first.
        if abs(wrap_angle(course_rad_enu - yaw)) > math.radians(60.0) and v > 0.5:
            self.state[YAW] = course_rad_enu
            self.cov[YAW][YAW] = max(self.cov[YAW][YAW], 0.2 ** 2)
            _, _, yaw, v, _ = self.state
            c, s = math.cos(yaw), math.sin(yaw)
            innovation = [z[0] - v * c, z[1] - v * s]
            h = [[0.0, 0.0, -v * s, c, 0.0], [0.0, 0.0, v * c, s, 0.0]]
        return self._update(innovation, h, [[sigma2, 0.0], [0.0, sigma2]])

    def update_wheel_speed(self, speed: float) -> bool:
        cfg = self.config
        sigma = cfg.wheel_speed_sigma_mps + cfg.wheel_slip_fraction * abs(speed)
        return self._update([speed - self.state[V]], [[0.0, 0.0, 0.0, 1.0, 0.0]], [[sigma ** 2]])


def nmea_course_to_enu(course_deg: float) -> float:
    """NMEA course is degrees clockwise from true north; ENU yaw is CCW from east."""
    return wrap_angle(math.pi / 2.0 - math.radians(course_deg))


class WheelSpeedEstimator:
    """Speed from a single-channel hall encoder via edge counts and edge timestamps.

    Uses the edge-to-edge interval over a sliding window (period measurement), which
    is far less quantised than counting edges per 10 ms tick at ~60 edges/s. With no
    new edge, speed is bounded by one edge spacing over the time since the last edge.
    The sensor has no direction channel; the sign comes from the commanded direction.
    """

    def __init__(self, metres_per_edge: float, window_s: float = 0.1, stale_s: float = 0.5) -> None:
        self.metres_per_edge = metres_per_edge
        self.window_s = window_s
        self.stale_s = stale_s
        self._history: list[tuple[int, float]] = []
        self.speed = 0.0
        self.total_edges = 0

    def update(self, now: float, count: int, last_edge_t: float) -> float:
        # count == 0 has no edge timestamp, so it cannot anchor a period measurement.
        if count > 0 and (not self._history or count != self._history[-1][0]):
            self._history.append((count, last_edge_t))
        self.total_edges = count
        while len(self._history) > 2 and self._history[-1][1] - self._history[1][1] >= self.window_s:
            self._history.pop(0)
        if len(self._history) < 2:
            self.speed = 0.0
            return self.speed
        (c0, t0), (c1, t1) = self._history[0], self._history[-1]
        speed = (c1 - c0) * self.metres_per_edge / (t1 - t0) if t1 > t0 else 0.0
        since_last = now - t1
        if since_last > 0.0:
            speed = min(speed, self.metres_per_edge / since_last)
        if since_last > self.stale_s:
            speed = 0.0
        self.speed = speed
        return speed

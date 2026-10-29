"""In-process fake ``Hal`` for firmware tests: a kinematic-bicycle RC car with
register-level MPU-6050 and INA226 models, a CRSF receiver, an NMEA GPS and a
hall wheel encoder. Time advances only in ``clock.sleep_until`` (lockstep), in
1 ms physics steps, mirroring the twin's time model.

This is a test double, not the digital twin: dynamics are first-order and
traction-limited, chosen so the firmware's closed loops can be exercised.
``CarParams(slip_model=True)`` swaps in a two-mass drivetrain (car + driven
wheel) with a DC motor, a slip-ratio tire curve and a rear-axle spin-out mode,
so traction control and yaw assist can be exercised; it is deliberately
simpler than the MuJoCo twin and its constants are not the firmware's.
"""

from __future__ import annotations

import math
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE_DIR = ROOT / "examples/robots/rc-car/backend/python/rc_car_controller"
if str(FIRMWARE_DIR) not in sys.path:
    sys.path.insert(0, str(FIRMWARE_DIR))

import crsf  # noqa: E402
import nmea  # noqa: E402
from settings import DEFAULT_CONFIG, channel_map  # noqa: E402
from r3_hal.api import Clock, GpioLine, Hal, HalError, I2cBus, PwmChannel, SpiDevice, Uart  # noqa: E402

GRAVITY = 9.80665
ORIGIN = nmea.LocalTangentPlane(47.3977, 8.5456)


@dataclass
class CarState:
    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0
    v: float = 0.0
    steer: float = 0.0
    distance: float = 0.0
    yaw_rate: float = 0.0
    accel: float = 0.0               # forward acceleration (IMU x)
    wheel_v: float = 0.0             # driven-wheel surface speed (slip model)
    spin: float = 0.0                # yaw rate beyond the kinematic bicycle (slip model)
    wheel_distance: float = 0.0


@dataclass
class CarParams:
    wheelbase: float = 0.26
    max_steer: float = 0.45
    steer_rate: float = 5.0          # rad/s at the wheels
    free_speed: float = 11.0         # m/s at full throttle, no load
    motor_tau: float = 1.2           # s, first-order speed response
    traction_accel: float = 4.4      # mu g * rear-axle load fraction (0.9 * 9.81 * 0.5)
    brake_accel: float = 4.4         # full ESC brake, traction limited
    coast_decel: float = 0.35        # rolling resistance + drivetrain drag
    wheel_radius: float = 0.055
    pulses_per_rev: int = 4
    battery_ocv: float = 8.0
    battery_r_int: float = 0.025
    battery_ocv_drop_per_s: float = 0.0
    hat_current: float = 1.4         # Pi + HAT load seen by the INA226
    motor_current_full: float = 45.0
    gyro_bias: float = 0.015         # rad/s
    gyro_noise: float = 0.003
    gps_sigma: float = 0.35          # m (white part; tests use a benign receiver)
    gps_velocity_sigma: float = 0.05
    # --- slip model (CarParams(slip_model=True))
    slip_model: bool = False
    mass: float = 1.58
    drivetrain_mass: float = 0.43    # rotor + wheel inertia reflected to the tire, J / r^2
    grip_force: float = 7.0          # mu m g x rear share: the "about 7 N" of the twin's first run
    peak_slip: float = 0.10          # slip ratio of peak tire force
    slide_force_drop: float = 0.2    # force lost at full slide, a real tire's falling curve
    motor_kv: float = 2100.0
    motor_resistance: float = 0.13   # deliberately not the firmware's 0.12 (model error)
    brush_drop: float = 0.2
    no_load_current: float = 1.6
    gear_ratio: float = 9.6
    gear_efficiency: float = 0.88
    drag_area: float = 0.04
    spin_growth: float = 4.0         # 1/s, yaw divergence while the rear axle slides
    spin_seed: float = 0.02          # rad/s, asymmetry that seeds the divergence
    spin_decay_s: float = 0.3
    yaw_disturbance: Callable[[float], float] | None = None  # extra yaw rate (rad/s) vs time
    blocked: bool = False            # wheels held (car against a wall): motor stall


class _RegisterDevice:
    def __init__(self) -> None:
        self.pointer = 0

    def write(self, data: bytes) -> None:
        raise NotImplementedError

    def read(self, length: int) -> bytes:
        raise NotImplementedError


class FakeMpu6050(_RegisterDevice):
    def __init__(self, hal: "FakeCarHal") -> None:
        super().__init__()
        self.hal = hal
        self.registers = {0x75: 0x68, 0x6B: 0x40, 0x1B: 0, 0x1C: 0}

    def write(self, data: bytes) -> None:
        self.pointer = data[0]
        for offset, value in enumerate(data[1:]):
            self.registers[self.pointer + offset] = value & (0x7F if self.pointer + offset == 0x6B else 0xFF)

    def read(self, length: int) -> bytes:
        if self.pointer == 0x3B:
            gyro_lsb = {0: 131.0, 1: 65.5, 2: 32.8, 3: 16.4}[(self.registers[0x1B] >> 3) & 3]
            accel_lsb = {0: 16384.0, 1: 8192.0, 2: 4096.0, 3: 2048.0}[(self.registers[0x1C] >> 3) & 3]
            p = self.hal.params
            rate = self.hal.car.yaw_rate + p.gyro_bias + self.hal.rng.gauss(0.0, p.gyro_noise)

            def word(value: float) -> bytes:
                return int(max(-32768, min(32767, round(value)))).to_bytes(2, "big", signed=True)

            raw = (word(self.hal.car.accel / GRAVITY * accel_lsb) + word(0) + word(accel_lsb) + word((25.0 - 36.53) * 340)
                   + word(0) + word(0)
                   + word(math.degrees(rate) * gyro_lsb))
            return raw[:length]
        return bytes(self.registers.get(self.pointer + i, 0) for i in range(length))


class FakeIna226(_RegisterDevice):
    def __init__(self, hal: "FakeCarHal", shunt_ohm: float = 0.002) -> None:
        super().__init__()
        self.hal, self.shunt = hal, shunt_ohm
        self.registers = {0x00: 0x4127, 0x05: 0, 0xFE: 0x5449, 0xFF: 0x2260}

    def write(self, data: bytes) -> None:
        self.pointer = data[0]
        if len(data) >= 3:
            value = int.from_bytes(data[1:3], "big")
            if self.pointer == 0x00 and value & 0x8000:
                self.registers.update({0x00: 0x4127, 0x05: 0})
            else:
                self.registers[self.pointer] = value

    def read(self, length: int) -> bytes:
        voltage, current = self.hal.battery_voltage(), self.hal.params.hat_current
        cal = self.registers[0x05]
        lsb = 0.00512 / (cal * self.shunt) if cal else 0.0
        values = {0x01: int(round(current * self.shunt / 2.5e-6)), 0x02: int(round(voltage / 1.25e-3)),
                  0x04: int(round(current / lsb)) if lsb else 0, 0x03: int(round(voltage * current / (25 * lsb))) if lsb else 0}
        value = values.get(self.pointer, self.registers.get(self.pointer, 0))
        return (value & 0xFFFF).to_bytes(2, "big")[:length]


class _FakeClock(Clock):
    def __init__(self, hal: "FakeCarHal") -> None:
        self.hal = hal

    def now(self) -> float:
        return self.hal.t

    def sleep_until(self, t: float) -> None:
        self.hal.advance_to(t)


class _FakePwm(PwmChannel):
    def __init__(self, hal: "FakeCarHal", key: tuple[str, int]) -> None:
        self.hal, self.key = hal, key

    def configure(self, period_ns: int, duty_ns: int, enabled: bool = True) -> None:
        self.hal.pwm_state[self.key] = (period_ns, duty_ns, enabled)


class _FakeI2c(I2cBus):
    def __init__(self, hal: "FakeCarHal") -> None:
        self.hal = hal

    def transfer(self, addr: int, write: bytes = b"", read_len: int = 0) -> bytes:
        device = self.hal.i2c_devices.get(addr)
        if device is None:
            raise HalError("i2c_nack", f"0x{addr:02x}")
        if write:
            device.write(bytes(write))
        return device.read(read_len) if read_len else b""


class _FakeUart(Uart):
    def __init__(self, hal: "FakeCarHal", device: str) -> None:
        self.hal, self.device = hal, device

    def write(self, data: bytes) -> int:
        return len(data)

    def read(self, max_bytes: int = 4096, timeout: float = 0.0) -> bytes:
        buffer = self.hal.uart_rx.setdefault(self.device, bytearray())
        data = bytes(buffer[:max_bytes])
        del buffer[:max_bytes]
        return data


class _FakeGpio(GpioLine):
    def __init__(self, hal: "FakeCarHal") -> None:
        self.hal = hal

    def read(self) -> int:
        return 0

    def write(self, value: int) -> None:
        pass

    def count_edges(self) -> tuple[int, float]:
        return self.hal.edge_count, self.hal.last_edge_t


Pilot = Callable[[float], dict[int, float]]


@dataclass
class LogRecord:
    t: float
    level: str
    message: str
    fields: dict = field(default_factory=dict)


class FakeCarHal(Hal):
    def __init__(self, pilot: Pilot, link_up: Callable[[float], bool] = lambda t: True,
                 params: CarParams | None = None, seed: int = 7, bindings: dict | None = None,
                 link_quality: Callable[[float], int] = lambda t: 100) -> None:
        self.params = params or CarParams()
        self.pilot, self.link_up, self.link_quality = pilot, link_up, link_quality
        self.rng = random.Random(seed)
        self.t = 0.0
        self.car = CarState()
        self.pwm_state: dict[tuple[str, int], tuple[int, int, bool]] = {}
        self.uart_rx: dict[str, bytearray] = {}
        self.i2c_devices: dict[int, _RegisterDevice] = {0x68: FakeMpu6050(self), 0x40: FakeIna226(self)}
        self.edge_count = 0
        self.last_edge_t = 0.0
        self.logs: list[LogRecord] = []
        self.trace: list[tuple[float, float, float, float, float, float]] = []  # t, x, y, yaw, v, esc_us
        self.slip_trace: list[tuple[float, float, float, float, float]] = []  # t, v, wheel_v, yaw_rate, motor_a
        self.clock = _FakeClock(self)
        self._bindings = bindings or {}
        self._next_rc = 0.0
        self._next_stats = 0.0
        self._next_gps = 0.2
        self._utc0 = 12 * 3600.0
        self.motor_current = 0.0

    # -- Hal API -------------------------------------------------------------
    def gpio(self, chip: str, line: int, direction: str = "in", bias: str = "none", edge: str = "none") -> GpioLine:
        return _FakeGpio(self)

    def pwm(self, chip: str, channel: int) -> PwmChannel:
        return _FakePwm(self, (chip, channel))

    def i2c(self, bus: str) -> I2cBus:
        return _FakeI2c(self)

    def spi(self, bus: str, cs: int, speed_hz: int = 1_000_000, mode: int = 0) -> SpiDevice:
        raise HalError("no_such_device", bus)

    def uart(self, device: str, baud: int) -> Uart:
        return _FakeUart(self, device)

    def log(self, level: str, message: str, **fields: object) -> None:
        self.logs.append(LogRecord(self.t, level, message, dict(fields)))

    def bindings(self) -> dict[str, dict]:
        return dict(self._bindings)

    # -- helpers -------------------------------------------------------------
    def pulse_us(self, channel: int) -> float:
        period, duty, enabled = self.pwm_state.get(("pwmchip0", channel), (20_000_000, 1_500_000, True))
        return duty / 1000.0 if enabled else 1500.0

    def events(self, message: str) -> list[LogRecord]:
        return [r for r in self.logs if r.message == message]

    def battery_voltage(self) -> float:
        p = self.params
        ocv = p.battery_ocv - p.battery_ocv_drop_per_s * self.t
        return ocv - p.battery_r_int * (self.motor_current + p.hat_current)

    # -- simulation ----------------------------------------------------------
    def _emit_sensors(self) -> None:
        p, car = self.params, self.car
        if self.t + 1e-9 >= self._next_rc:
            self._next_rc += 1.0 / 150.0  # ELRS 150 Hz packet rate (catalog)
            if self.link_up(self.t):
                channels = self.pilot(self.t)
                pulses = [channels.get(i + 1, 1500.0) for i in range(16)]
                self.uart_rx.setdefault("/dev/ttyAMA0", bytearray()).extend(crsf.encode_rc_channels_us(pulses))
        if self.t + 1e-9 >= self._next_stats:
            self._next_stats += 0.1
            if self.link_up(self.t):
                stats = crsf.LinkStatistics(-60, -62, self.link_quality(self.t), 9, 0, 4, 3, -58, 100, 8)
                self.uart_rx.setdefault("/dev/ttyAMA0", bytearray()).extend(crsf.encode_link_statistics(stats))
        if self.t + 1e-9 >= self._next_gps:
            self._next_gps += 0.1
            gx, gy = car.x + self.rng.gauss(0, p.gps_sigma), car.y + self.rng.gauss(0, p.gps_sigma)
            lat, lon = ORIGIN.to_geodetic(gx, gy)
            vx = car.v * math.cos(car.yaw) + self.rng.gauss(0, p.gps_velocity_sigma)
            vy = car.v * math.sin(car.yaw) + self.rng.gauss(0, p.gps_velocity_sigma)
            speed, course = math.hypot(vx, vy), math.degrees(math.atan2(vx, vy)) % 360.0
            utc = self._utc0 + self.t
            buffer = self.uart_rx.setdefault("/dev/ttyAMA2", bytearray())
            buffer.extend(nmea.format_gga(utc, lat, lon, 400.0) + nmea.format_rmc(utc, lat, lon, speed, course)
                          + nmea.format_vtg(speed, course))

    def _physics(self, dt: float) -> None:
        if self.params.slip_model:
            self._physics_slip(dt)
            return
        p, car = self.params, self.car
        esc = self.pulse_us(2)
        servo = self.pulse_us(3)
        u = (esc - 1500.0) / 500.0
        u = 0.0 if abs(u) < 0.03 else u  # ESC deadband (catalog 0.03)
        n = (servo - 1500.0) / (378.0 if servo >= 1500.0 else 378.0)
        steer_cmd = max(-1.0, min(1.0, n)) * p.max_steer
        car.steer += max(-p.steer_rate * dt, min(p.steer_rate * dt, steer_cmd - car.steer))
        if u > 0:
            accel = min(p.traction_accel, (u * p.free_speed - car.v) / p.motor_tau)
            if accel < 0:
                accel = max(accel, -p.coast_decel)
            self.motor_current = p.motor_current_full * u * max(0.0, 1.0 - car.v / p.free_speed) + 4.0 * u
        elif u < 0 and car.v > 0:
            accel = -min(p.brake_accel, -u * p.brake_accel * 1.2)
            self.motor_current = 0.0
        else:
            accel = -p.coast_decel if car.v > 0 else 0.0
            self.motor_current = 0.0
        car.accel = accel if car.v > 0.0 or accel > 0.0 else 0.0
        car.v = max(0.0, car.v + accel * dt)
        car.yaw_rate = car.v * math.tan(car.steer) / p.wheelbase
        car.yaw = math.atan2(math.sin(car.yaw + car.yaw_rate * dt), math.cos(car.yaw + car.yaw_rate * dt))
        car.x += car.v * math.cos(car.yaw) * dt
        car.y += car.v * math.sin(car.yaw) * dt
        previous = car.distance
        car.distance += car.v * dt
        edge_spacing = 2.0 * math.pi * p.wheel_radius / p.pulses_per_rev
        edges = int(car.distance / edge_spacing) - int(previous / edge_spacing)
        if edges > 0:
            self.edge_count += edges
            self.last_edge_t = self.t + dt

    def tire_force(self, wheel_v: float, v: float) -> float:
        """Slip-ratio tire curve: linear to the peak at ``peak_slip``, then falling to
        (1 - slide_force_drop) of the peak at full slide."""
        p = self.params
        slip = (wheel_v - v) / max(abs(v), 1.0)
        magnitude = abs(slip)
        if magnitude <= p.peak_slip:
            force = p.grip_force * magnitude / p.peak_slip
        else:
            force = p.grip_force * (1.0 - p.slide_force_drop * min(1.0, (magnitude - p.peak_slip) / 0.5))
        return math.copysign(force, slip)

    def _physics_slip(self, dt: float) -> None:
        p, car = self.params, self.car
        u = (self.pulse_us(2) - 1500.0) / 500.0
        duty = 0.0 if u <= 0.03 else (u - 0.03) / 0.97  # forward drive only; ESC deadband rescaling
        servo = self.pulse_us(3)
        steer_cmd = max(-1.0, min(1.0, (servo - 1500.0) / 378.0)) * p.max_steer
        car.steer += max(-p.steer_rate * dt, min(p.steer_rate * dt, steer_cmd - car.steer))
        k_e = 60.0 / (2.0 * math.pi * p.motor_kv)
        omega_m = car.wheel_v / p.wheel_radius * p.gear_ratio
        voltage = self.battery_voltage()
        current = max(0.0, (duty * voltage - p.brush_drop - k_e * omega_m) / p.motor_resistance) if duty > 0 else 0.0
        self.motor_current = current
        motor_force = k_e * max(0.0, current - p.no_load_current) * p.gear_ratio * p.gear_efficiency / p.wheel_radius
        drag = (0.015 * p.mass * GRAVITY if car.v > 0.01 else 0.0) + 0.5 * 1.2 * p.drag_area * car.v * car.v
        if p.blocked:
            car.wheel_v = car.v = car.accel = 0.0
        else:
            tire = self.tire_force(car.wheel_v, car.v)
            car.wheel_v = max(0.0, car.wheel_v + (motor_force - tire) / p.drivetrain_mass * dt)
            car.accel = (tire - drag) / p.mass if car.v > 0.0 or tire > drag else 0.0
            car.v = max(0.0, car.v + car.accel * dt)
            sliding = abs(car.wheel_v - car.v) / max(car.v, 1.0) > 1.5 * p.peak_slip
            # Rear axle sliding: no lateral grip, the yaw rate diverges (spin-out); gripping: it decays.
            growth = p.spin_growth * (car.spin + p.spin_seed) if sliding and car.v > 1.0 else -car.spin / p.spin_decay_s
            car.spin += growth * dt
        disturbance = p.yaw_disturbance(self.t) if p.yaw_disturbance else 0.0
        car.yaw_rate = car.v * math.tan(car.steer) / p.wheelbase + car.spin + disturbance
        car.yaw = math.atan2(math.sin(car.yaw + car.yaw_rate * dt), math.cos(car.yaw + car.yaw_rate * dt))
        car.x += car.v * math.cos(car.yaw) * dt
        car.y += car.v * math.sin(car.yaw) * dt
        car.distance += car.v * dt
        previous = car.wheel_distance
        car.wheel_distance += car.wheel_v * dt
        edge_spacing = 2.0 * math.pi * p.wheel_radius / p.pulses_per_rev
        edges = int(car.wheel_distance / edge_spacing) - int(previous / edge_spacing)
        if edges > 0:
            self.edge_count += edges
            self.last_edge_t = self.t + dt

    def advance_to(self, t: float) -> None:
        dt = 0.001
        while self.t + dt <= t + 1e-9:
            self._emit_sensors()
            self._physics(dt)
            self.t = round(self.t + dt, 9)
            if int(round(self.t * 1000)) % 10 == 0:
                c = self.car
                self.trace.append((self.t, c.x, c.y, c.yaw, c.v, self.pulse_us(2)))
                self.slip_trace.append((self.t, c.v, c.wheel_v, c.yaw_rate, self.motor_current))
        if self.t < t:
            self.t = t


CHANNELS = channel_map(DEFAULT_CONFIG)


def pilot_arm_at(arm_t: float, throttle: Callable[[float], float] | None = None,
                 steering: Callable[[float], float] | None = None, channels: dict[str, int] | None = None) -> Pilot:
    """Pilot: AUX arm switch low until ``arm_t`` then high; optional stick schedules in [-1, 1].
    ``channels``: CRSF channel map by role (default: the firmware's ``crsf_channels``)."""
    ch = channels or CHANNELS

    def pilot(t: float) -> dict[int, float]:
        out = {ch["arm"]: 2000.0 if t >= arm_t else 1000.0}
        if throttle is not None:
            out[ch["throttle"]] = 1500.0 + 500.0 * throttle(t)
        if steering is not None:
            out[ch["steering"]] = 1500.0 + 500.0 * steering(t)
        return out
    return pilot


def firmware_document(mission: dict | None, **config: object) -> dict:
    document = {"config": dict(config), "geodetic_origin": {"lat": ORIGIN.lat0_deg, "lon": ORIGIN.lon0_deg}}
    if mission is not None:
        document["mission"] = mission
    return document

"""RC-car controller entrypoint: radio, sensors, estimation, control, safety.

Runs unchanged on the Raspberry Pi (``R3_HAL=linux``) and against the digital
twin (``R3_HAL=virtual``). All timing uses ``hal.clock`` so a lockstep twin run
is deterministic; the loop never calls ``time.sleep``.

    python3 main.py [--config PATH] [--duration SECONDS]
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass

import vendor  # noqa: F401  (r3_hal search path; must precede r3_hal imports)
from r3_hal import Hal, HalError, open_hal

import crsf
import nmea
from control import (BatteryConfig, BatteryMonitor, LinkMonitor, PulseMap, PurePursuit, PurePursuitConfig,
                     SpeedController, SpeedPlanner, SpeedPlannerConfig, State, Supervisor, SupervisorConfig,
                     WaypointPath, clamp, stick_to_normalised)
from devices import GyroBiasCalibrator, Ina226, Mpu6050
from estimator import VehicleEstimator, WheelSpeedEstimator, nmea_course_to_enu
from settings import MODULE_NAME, FirmwareSettings, load_settings, quantity

EXIT_OK = 0
EXIT_HAL_ERROR = 3
EXIT_BROWNOUT = 4


@dataclass
class Command:
    throttle: float = 0.0
    steer: float = 0.0


class RcCarController:
    def __init__(self, hal: Hal, settings: FirmwareSettings) -> None:
        self.hal = hal
        self.settings = settings
        cfg = self.cfg = settings.config
        self.rate_hz = float(cfg["control_rate_hz"])
        self.dt = 1.0 / self.rate_hz
        b = settings.bindings
        esc, servo = cfg["esc_pulse_us"], cfg["steering_pulse_us"]
        self.pulses = PulseMap(float(esc["reverse"]), float(esc["neutral"]), float(esc["forward"]),
                               float(servo["left"]), float(servo["center"]), float(servo["right"]), float(cfg["max_steer_rad"]))

        self.esc = hal.pwm(str(b["esc_pwm"]["device"]), int(b["esc_pwm"]["channel"]))
        self.esc_hz = quantity(b["esc_pwm"].get("frequency", 50.0))
        self.servo = hal.pwm(str(b["servo_pwm"]["device"]), int(b["servo_pwm"]["channel"]))
        self.servo_hz = quantity(b["servo_pwm"].get("frequency", 50.0))
        self.crsf_uart = hal.uart(str(b["crsf"]["device"]), int(quantity(b["crsf"].get("baud", 420000))))
        self.gps_uart = hal.uart(str(b["gps"]["device"]), int(quantity(b["gps"].get("baud", 115200))))
        self.i2c = hal.i2c(str(b["i2c"]["device"]))
        self.encoder = hal.gpio(str(b["encoder"]["device"]), int(b["encoder"]["channel"]), direction="in", edge="rising")

        self.crsf_parser = crsf.CrsfParser()
        self.nmea_reader = nmea.NmeaReader()
        self.link = LinkMonitor(float(cfg["radio_failsafe_timeout_s"]))
        self.supervisor = Supervisor(SupervisorConfig(mode=str(cfg["mode"]), arm_channel=int(cfg["arm_channel"]),
                                                      failsafe_action=str(cfg["failsafe_action"])))
        self.battery = BatteryMonitor(BatteryConfig(limit_v=float(cfg["low_voltage_limit_v"]),
                                                    cutoff_v=float(cfg["low_voltage_cutoff_v"])))
        self.estimator = VehicleEstimator(initial_yaw=math.radians(float(cfg["initial_heading_deg"])))
        metres_per_edge = 2.0 * math.pi * float(cfg["wheel_radius_m"]) / float(cfg["encoder_pulses_per_rev"])
        self.wheel = WheelSpeedEstimator(metres_per_edge)
        self.calibrator = GyroBiasCalibrator(max(1, int(round(float(cfg["imu_calibration_s"]) * self.rate_hz))))
        self.speed_controller = SpeedController()

        ina = cfg["ina226"]
        self.ina = Ina226(self.i2c, int(ina["address"]), float(ina["shunt_ohm"]), float(ina["max_current_a"]))
        self.imu = Mpu6050(self.i2c, int(cfg["mpu6050"]["address"]))
        self.ina_ok = self._init_device("ina226", self.ina.init)
        self.imu_ok = self._init_device("mpu6050", self.imu.init)

        self.origin: nmea.LocalTangentPlane | None = None
        origin = settings.geodetic_origin
        if origin:
            self.origin = nmea.LocalTangentPlane(float(origin["lat"]), float(origin["lon"]))
        self._boot_fixes: list[tuple[float, float]] = []

        self.pursuit: PurePursuit | None = None
        self.planner: SpeedPlanner | None = None
        self.cruise_mps = float(cfg["max_cruise_speed_mps"])
        mission = settings.mission
        if mission and mission.get("waypoints"):
            waypoints = [tuple(map(float, p[:2])) for p in mission["waypoints"]]
            closed = bool(mission.get("closed", len(waypoints) >= 3))
            path = WaypointPath(waypoints, closed)
            laps = int(mission.get("laps", 1))
            self.cruise_mps = min(self.cruise_mps, float(mission.get("speed", self.cruise_mps)))
            self.pursuit = PurePursuit(path, laps, PurePursuitConfig(wheelbase_m=float(cfg["wheelbase_m"]),
                                                                     max_steer_rad=float(cfg["max_steer_rad"])))
            self.planner = SpeedPlanner(path, laps, SpeedPlannerConfig(cruise_mps=self.cruise_mps))

        self.command = Command()
        self.gyro_z = 0.0
        self.last_encoder = (0, 0.0)
        self.voltage: float | None = None
        self.current: float | None = None
        self.throttle_scale = 1.0
        self.speed_reference = 0.0
        self.cross_track = 0.0
        self.lap = 0
        self.ticks = 0
        self.overruns = 0
        self._last_velocity_t = -1.0
        self._last_telemetry_t = -math.inf
        self._limit_logged = False
        self._start_t = 0.0

    # -- helpers -------------------------------------------------------------
    def _init_device(self, name: str, init) -> bool:
        try:
            init()
        except HalError as error:
            self.hal.log("error", "device_init_failed", device=name, code=error.code, detail=str(error))
            return False
        self.hal.log("info", "device_ready", device=name)
        return True

    def _write_outputs(self, command: Command) -> None:
        self.esc.set_pulse_us(self.pulses.esc_pulse(command.throttle), self.esc_hz)
        self.servo.set_pulse_us(self.pulses.steer_pulse(command.steer), self.servo_hz)

    @property
    def ready(self) -> bool:
        imu_ready = self.calibrator.done or not self.imu_ok
        if not self.supervisor.autonomous:
            return imu_ready
        gps_ready = self.estimator.position_initialised or not bool(self.cfg["require_gps_fix_to_arm"])
        return imu_ready and gps_ready

    # -- sensor processing ---------------------------------------------------
    def _read_radio(self, now: float) -> None:
        data = self.crsf_uart.read(512, 0.0)
        for frame in self.crsf_parser.feed(data):
            decoded = crsf.decode(frame)
            if isinstance(decoded, crsf.RcChannels):
                self.link.on_channels(now, [decoded.us(i + 1) for i in range(crsf.RC_CHANNEL_COUNT)])
            elif isinstance(decoded, crsf.LinkStatistics):
                rssi = max(decoded.uplink_rssi_ant1_dbm, decoded.uplink_rssi_ant2_dbm)
                self.link.on_link_statistics(now, decoded.uplink_link_quality, rssi, decoded.uplink_snr_db)

    def _local(self, lat: float, lon: float) -> tuple[float, float] | None:
        if self.origin is None:
            # No configured origin: average fixes during IMU calibration (the car is
            # stationary and, by convention, at local (0, 0)).
            if (self.imu_ok and not self.calibrator.done) or not self._boot_fixes:
                self._boot_fixes.append((lat, lon))
                return None
            n = len(self._boot_fixes)
            self.origin = nmea.LocalTangentPlane(sum(f[0] for f in self._boot_fixes) / n, sum(f[1] for f in self._boot_fixes) / n)
            self.hal.log("info", "geodetic_origin_from_fixes", lat=self.origin.lat0_deg, lon=self.origin.lon0_deg, fixes=n)
        return self.origin.to_local(lat, lon)

    def _read_gps(self, now: float) -> None:
        data = self.gps_uart.read(2048, 0.0)
        for sentence in self.nmea_reader.feed(data):
            if isinstance(sentence, nmea.Gga) and sentence.fix_quality > 0 and sentence.lat_deg is not None:
                local = self._local(sentence.lat_deg, sentence.lon_deg)
                if local is not None:
                    first = not self.estimator.position_initialised
                    self.estimator.update_gps_position(local[0], local[1], sentence.hdop)
                    if first:
                        self.hal.log("info", "gps_fix", x=round(local[0], 3), y=round(local[1], 3), sats=sentence.satellites)
            elif isinstance(sentence, (nmea.Rmc, nmea.Vtg)):
                valid = sentence.valid if isinstance(sentence, nmea.Rmc) else True
                if valid and sentence.speed_mps is not None and sentence.course_deg is not None \
                        and now - self._last_velocity_t > 0.05 and self.estimator.position_initialised:
                    self.estimator.update_gps_velocity(sentence.speed_mps, nmea_course_to_enu(sentence.course_deg))
                    self._last_velocity_t = now

    def _read_imu(self, moving: bool) -> None:
        if not self.imu_ok:
            # Fallback yaw rate from the bicycle model: w = v tan(delta) / L.
            delta = self.command.steer * self.pulses.max_steer_rad
            self.gyro_z = self.estimator.speed * math.tan(delta) / float(self.cfg["wheelbase_m"])
            return
        try:
            sample = self.imu.read()
        except HalError as error:
            self.hal.log("warning", "imu_read_failed", code=error.code)
            return
        raw = sample.gyro_rps[2]
        if not self.calibrator.done:
            if self.calibrator.add(raw, moving):
                self.hal.log("info", "imu_calibrated", gyro_bias_z_rps=self.calibrator.bias_rps,
                             restarts=self.calibrator.restarts)
            self.gyro_z = 0.0
            return
        self.gyro_z = raw - (self.calibrator.bias_rps or 0.0)

    def _read_power(self, now: float) -> None:
        if not self.ina_ok or self.ticks % max(1, int(self.rate_hz // 20)) != 0:
            return
        try:
            reading = self.ina.read()
        except HalError as error:
            self.hal.log("warning", "ina226_read_failed", code=error.code)
            return
        self.voltage, self.current = reading.bus_voltage_v, reading.current_a
        throttle = self.command.throttle
        self.throttle_scale = self.battery.update(now, 1.0 / 20.0, reading.bus_voltage_v, throttle)
        if self.battery.limiting and not self._limit_logged:
            self.hal.log("warning", "low_voltage_limit", filtered_v=round(self.battery.filtered_v, 3),
                         limit_v=self.battery.config.limit_v, throttle_scale=round(self.throttle_scale, 3))
        self._limit_logged = self.battery.limiting

    # -- control -------------------------------------------------------------
    def _autonomous_command(self) -> Command:
        est = self.estimator
        if self.pursuit is None or self.planner is None:
            return Command()
        result = self.pursuit.update(est.x, est.y, est.yaw, est.speed)
        self.cross_track = result.cross_track_m
        if result.lap != self.lap:
            self.hal.log("info", "lap_completed", lap=result.lap, progress_m=round(result.progress_m, 2))
            self.lap = result.lap
        cruise = self.cruise_mps
        lq = self.link.link_quality
        if lq is not None and lq < int(self.cfg["link_degraded_lq"]):
            cruise = min(cruise, float(self.cfg["link_degraded_speed_mps"]))
        reference = self.planner.update(self.dt, result.progress_m, self.pursuit.end_s, result.curvature, cruise)
        self.speed_reference = reference
        throttle = self.speed_controller.update(self.dt, reference, est.speed, max_throttle=self.throttle_scale,
                                                 reference_accel=self.planner.reference_accel)
        return Command(throttle, self.pulses.steer_from_angle(result.steer_rad))

    def _manual_command(self) -> Command:
        throttle = stick_to_normalised(self.link.channel(int(self.cfg["throttle_channel"])))
        steer = stick_to_normalised(self.link.channel(int(self.cfg["steering_channel"])))
        if throttle > 0:
            throttle *= self.throttle_scale
        return Command(throttle, steer)

    def step(self, now: float) -> None:
        self._read_radio(now)
        self._read_gps(now)
        count, last_t = self.encoder.count_edges()
        moving = count != self.last_encoder[0]
        self.last_encoder = (count, last_t)
        self._read_imu(moving)
        self._read_power(now)

        wheel_speed = self.wheel.update(now, count, last_t)
        reversing = self.command.throttle < -0.05 and not self.supervisor.braking and self.supervisor.state == State.MANUAL
        signed_speed = -wheel_speed if reversing else wheel_speed
        self.estimator.predict(self.dt, self.gyro_z)
        self.estimator.update_wheel_speed(signed_speed)

        link_ok = self.link.ok(now)
        arm_us = self.link.channel(int(self.cfg["arm_channel"])) if link_ok else 1000.0
        throttle_us = self.link.channel(int(self.cfg["throttle_channel"]))
        finished = self.pursuit is not None and self.pursuit.progress >= self.pursuit.end_s - self.pursuit.config.finish_tolerance_m
        previous = self.supervisor.state
        out = self.supervisor.update(now, link_ok, arm_us, throttle_us, self.ready, wheel_speed, finished, self.battery.cutoff)
        for name, fields in out.events:
            level = "warning" if name in ("radio_failsafe", "low_voltage_cutoff") else "info"
            if name == "mission_complete" and self.pursuit is not None:
                fields = {**fields, "laps": self.pursuit.laps, "progress_m": round(self.pursuit.progress, 2)}
            if name == "low_voltage_cutoff":
                fields = {**fields, "filtered_v": round(self.battery.filtered_v or 0.0, 3), "reason": self.battery.cutoff_reason,
                          "cutoff_v": self.battery.config.cutoff_v}
            self.hal.log(level, name, t=round(now, 4), **fields)
        if out.state == State.ARMED and previous != State.ARMED:
            self.speed_controller.reset()
            if self.planner is not None:
                self.planner.reference = wheel_speed

        if out.state == State.ARMED:
            command = self._autonomous_command()
        elif out.state == State.MANUAL:
            command = self._manual_command()
        elif out.state == State.BRAKING:
            command = Command(-self.supervisor.config.brake_throttle, 0.0)
        else:
            command = Command()
            self.speed_reference = 0.0
        self.command = command
        self._write_outputs(command)
        self._telemetry(now, out.state, wheel_speed, link_ok)
        self.ticks += 1

    def _telemetry(self, now: float, state: State, wheel_speed: float, link_ok: bool) -> None:
        period = 1.0 / float(self.cfg["telemetry_rate_hz"])
        if now - self._last_telemetry_t < period - 1e-9:
            return
        self._last_telemetry_t = now
        est = self.estimator
        self.hal.log("info", "telemetry", t=round(now, 4), state=state.value, x=round(est.x, 3), y=round(est.y, 3),
                     yaw=round(est.yaw, 4), v=round(est.speed, 3), v_wheel=round(wheel_speed, 3),
                     v_ref=round(self.speed_reference, 3), throttle=round(self.command.throttle, 4),
                     steer=round(self.command.steer, 4), cross_track_m=round(self.cross_track, 3),
                     progress_m=round(self.pursuit.progress, 2) if self.pursuit else 0.0, lap=self.lap,
                     vbat=round(self.voltage, 3) if self.voltage is not None else None,
                     vbat_filtered=round(self.battery.filtered_v, 3) if self.battery.filtered_v is not None else None,
                     ibat_hat=round(self.current, 3) if self.current is not None else None,
                     throttle_scale=round(self.throttle_scale, 3), link_ok=link_ok, lq=self.link.link_quality,
                     rssi_dbm=self.link.rssi_dbm, pos_sigma_m=round(est.sigma(0), 3), yaw_sigma=round(est.sigma(2), 4),
                     crsf_crc_errors=self.crsf_parser.crc_errors, nmea_checksum_errors=self.nmea_reader.checksum_errors)

    # -- main loop -----------------------------------------------------------
    def run(self, duration_s: float | None = None) -> None:
        clock = self.hal.clock
        self._write_outputs(Command())  # ESCs arm on neutral
        start = next_t = clock.now()
        self._start_t = start
        self.hal.log("info", "boot", module=MODULE_NAME, rate_hz=self.rate_hz, mode=self.cfg["mode"],
                     mission=bool(self.pursuit), config_sources=",".join(self.settings.sources))
        while duration_s is None or next_t - start < duration_s - 1e-9:
            self.step(clock.now())
            next_t += self.dt
            after = clock.now()
            if after > next_t:  # compute overran the period (real hardware only; lockstep compute is instantaneous)
                self.overruns += 1
                next_t = after
            clock.sleep_until(next_t)

    def shutdown(self) -> None:
        try:
            self._write_outputs(Command())
            self.hal.log("info", "shutdown", ticks=self.ticks, overruns=self.overruns,
                         failsafe_events=self.supervisor.failsafe_events, cutoff=self.battery.cutoff,
                         min_filtered_v=round(self.battery.min_filtered_v, 3) if self.battery.filtered_v else None)
        except HalError:
            pass


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", help="firmware config JSON (or base64 JSON)")
    parser.add_argument("--duration", type=float, default=None, help="stop after this many seconds of HAL time")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, hal: Hal | None = None) -> int:
    args = parse_args(argv)
    hal = hal or open_hal(MODULE_NAME)
    controller: RcCarController | None = None
    try:
        offered = getattr(hal, "config", None)  # optional HAL-provided document (see requests/firmware.md)
        settings = load_settings(args.config, hal_bindings=hal.bindings(),
                                 hal_document=offered() if callable(offered) else None)
        controller = RcCarController(hal, settings)
        controller.run(args.duration)
        return EXIT_OK
    except HalError as error:
        if error.code == "twin_disconnected":
            return EXIT_OK
        print(f"rc_car_controller: HAL error {error}", file=sys.stderr, flush=True)
        return EXIT_BROWNOUT if error.code == "brownout" else EXIT_HAL_ERROR
    except KeyboardInterrupt:
        return EXIT_OK
    finally:
        if controller is not None:
            controller.shutdown()
        hal.close()


if __name__ == "__main__":
    sys.exit(main())

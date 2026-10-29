"""Minimal RC-car controller used to exercise the twin before the real firmware exists.

Uses only ``r3_hal``: CRSF over UART (arm/sticks/failsafe), NMEA GPS, INA226
battery voltage, MPU-6050 yaw rate, Hall encoder speed, ESC/servo PWM. Modes
(``R3_SOFTWARE_CONFIG``): manual passes the sticks through; autonomous follows
``mission.waypoints`` with pure pursuit once armed. Failsafe: no RC frame for
``radio_failsafe_timeout_s`` -> full brake, then neutral. Low battery: throttle
limited below ``low_voltage_limit_v`` and latched off below ``low_voltage_cutoff_v``.
"""

from __future__ import annotations

import json
import math
import os

from r3_hal import HalError, open_hal

CRSF_RC, CRSF_STATS = 0x16, 0x14


def crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0xD5) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


class Crsf:
    def __init__(self) -> None:
        self.buf = bytearray()

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        self.buf.extend(data)
        out = []
        while len(self.buf) >= 4:
            if self.buf[0] != 0xC8:
                del self.buf[0]
                continue
            n = self.buf[1]
            if n < 2 or n > 62:
                del self.buf[0]
                continue
            if len(self.buf) < n + 2:
                break
            body = bytes(self.buf[2:1 + n])
            if crc8(body) == self.buf[1 + n]:
                out.append((body[0], body[1:]))
                del self.buf[:n + 2]
            else:
                del self.buf[0]
        return out


def channels_us(payload: bytes) -> list[float]:
    bits = int.from_bytes(payload[:22], "little")
    return [1500.0 + (((bits >> (11 * i)) & 0x7FF) - 992) * 5.0 / 8.0 for i in range(16)]


class Nmea:
    def __init__(self, origin: dict) -> None:
        self.buf = bytearray()
        self.lat0, self.lon0 = origin["lat"], origin["lon"]
        s = math.sin(math.radians(self.lat0))
        e2, a = 6.69437999014e-3, 6378137.0
        self.m = a * (1 - e2) / (1 - e2 * s * s) ** 1.5
        self.n = a / math.sqrt(1 - e2 * s * s)

    def feed(self, data: bytes) -> list[dict]:
        self.buf.extend(data)
        fixes = []
        while b"\n" in self.buf:
            line, _, rest = bytes(self.buf).partition(b"\n")
            self.buf = bytearray(rest)
            text = line.decode("ascii", "replace").strip()
            if not text.startswith("$") or "*" not in text:
                continue
            body, _, cs = text[1:].partition("*")
            value = 0
            for ch in body:
                value ^= ord(ch)
            if f"{value:02X}" != cs.upper():
                continue
            f = body.split(",")
            if f[0].endswith("RMC") and len(f) > 8 and f[2] == "A":
                lat = int(f[3][:2]) + float(f[3][2:]) / 60.0
                lat = -lat if f[4] == "S" else lat
                lon = int(f[5][:3]) + float(f[5][3:]) / 60.0
                lon = -lon if f[6] == "W" else lon
                x = math.radians(lon - self.lon0) * self.n * math.cos(math.radians(self.lat0))
                y = math.radians(lat - self.lat0) * self.m
                speed = float(f[7] or 0) * 0.514444
                course = math.radians(90.0 - float(f[8] or 0))  # to math heading (from +x, CCW)
                fixes.append({"x": x, "y": y, "speed": speed, "heading": course})
        return fixes


def main() -> None:
    config = json.loads(os.environ.get("R3_SOFTWARE_CONFIG", "{}"))
    hal = open_hal("sim_test_controller")
    b = hal.bindings()
    rate = float(config.get("control_rate_hz", 100))
    period = 1.0 / rate
    esc_cfg, steer_cfg = config["esc_pulse_us"], config["steering_pulse_us"]
    max_steer = float(config["max_steer_rad"])
    wheelbase, radius = float(config["wheelbase_m"]), float(config["wheel_radius_m"])
    ppr = int(config["encoder_pulses_per_rev"])
    ch = config.get("crsf_channels", {"steering": 1, "throttle": 2, "arm": 5})
    manual = config.get("mode") == "manual"
    mission = config.get("mission") or {}
    waypoints = [tuple(map(float, w)) for w in mission.get("waypoints", [])]
    laps_target = int(mission.get("laps", 1))
    cruise = float(mission.get("speed", config.get("max_cruise_speed_mps", 3.0)))

    esc = hal.pwm(b["esc_pwm"]["device"], b["esc_pwm"]["channel"])
    servo = hal.pwm(b["servo_pwm"]["device"], b["servo_pwm"]["channel"])
    crsf_uart = hal.uart(b["crsf"]["device"], b["crsf"]["baud"])
    gps_uart = hal.uart(b["gps"]["device"], b["gps"]["baud"])
    i2c = hal.i2c(b["i2c"]["device"])
    enc = hal.gpio(b["encoder"]["device"], b["encoder"]["channel"], "in", "pull_up", "rising")
    # HAT failsafe arbiter heartbeat: the ESC follows our PWM only while this line toggles.
    beat = hal.gpio(b["heartbeat"]["device"], b["heartbeat"]["channel"], "out") if "heartbeat" in b else None
    level = 0
    esc.set_pulse_us(esc_cfg["neutral"])
    servo.set_pulse_us(steer_cfg["center"])
    i2c.write_reg(0x68, 0x6B, b"\x00")  # MPU-6050 wake, internal oscillator
    who = i2c.read_reg(0x68, 0x75, 1)[0]
    i2c.write_reg(0x40, 0x00, (0x4527).to_bytes(2, "big"))  # INA226: 16 avg, 1.1 ms, continuous
    i2c.write_reg(0x40, 0x05, (2560).to_bytes(2, "big"))  # CAL for 1 mA LSB with 2 mOhm
    hal.log("info", "controller started", event="start", mode=config.get("mode"), who_am_i=who)

    crsf, nmea = Crsf(), Nmea(config["gps_origin"])
    sticks = [1500.0] * 16
    last_rc = hal.clock.now()
    failsafe = False
    lvc_latched = False
    v_filt = None
    gyro_bias, bias_n = 0.0, 0
    heading, x, y, have_fix = 0.0, 0.0, 0.0, False
    target, captures, started, done = 0, 0, False, False
    count0, t_count0 = enc.count_edges()[0], hal.clock.now()
    speed = 0.0
    integral = 0.0
    t_next = hal.clock.now()
    tick = 0
    while True:
        now = hal.clock.now()
        for kind, payload in crsf.feed(crsf_uart.read(512, 0.0)):
            if kind == CRSF_RC:
                sticks, last_rc = channels_us(payload), now
        armed = sticks[ch["arm"] - 1] > 1700
        # Encoder speed over >= 50 ms windows.
        count, _ = enc.count_edges()
        if now - t_count0 >= 0.05:
            speed = (count - count0) / ppr * 2 * math.pi * radius / (now - t_count0)
            count0, t_count0 = count, now
        # IMU yaw rate (+-250 dps -> 131 LSB/(deg/s)).
        raw = i2c.read_reg(0x68, 0x47, 2)
        gz = math.radians(int.from_bytes(raw, "big", signed=True) / 131.0)
        if not started and speed == 0.0 and bias_n < 2000:
            gyro_bias = (gyro_bias * bias_n + gz) / (bias_n + 1)
            bias_n += 1
        heading += (gz - gyro_bias) * period
        for fix in nmea.feed(gps_uart.read(1024, 0.0)):
            x, y, have_fix = fix["x"], fix["y"], True
            if fix["speed"] > 1.0:
                err = math.atan2(math.sin(fix["heading"] - heading), math.cos(fix["heading"] - heading))
                heading += 0.2 * err
        x += speed * math.cos(heading) * period
        y += speed * math.sin(heading) * period
        if tick % 10 == 0:
            bus = int.from_bytes(i2c.read_reg(0x40, 0x02, 2), "big") * 1.25e-3
            if bus > 1.0:  # registers read 0 until the first conversion completes
                v_filt = bus if v_filt is None else v_filt + 0.1 * (bus - v_filt)
            if v_filt is not None and v_filt < float(config["low_voltage_cutoff_v"]) and not lvc_latched:
                lvc_latched = True
                hal.log("warn", "low_voltage_cutoff", event="low_voltage_cutoff", voltage=round(v_filt, 3))

        lost = now - last_rc > float(config["radio_failsafe_timeout_s"])
        if lost and not failsafe:
            failsafe = True
            hal.log("warn", "radio failsafe", event="failsafe", x=round(x, 2), y=round(y, 2))
        elif not lost and failsafe:
            failsafe = False
            hal.log("info", "radio link restored", event="failsafe_clear")

        throttle, steer = 0.0, 0.0
        if failsafe:
            throttle = -1.0 if speed > 0.1 else 0.0
        elif manual:
            throttle = (sticks[ch["throttle"] - 1] - 1500.0) / 500.0
            steer = (sticks[ch["steering"] - 1] - 1500.0) / 500.0 * max_steer
        elif armed and have_fix and waypoints and not done:
            if not started:
                started, start_xy = True, (x, y)
                hal.log("info", "mission started", event="mission_start")
            tx, ty = waypoints[target]
            if math.hypot(tx - x, ty - y) < 2.0:
                captures += 1
                closed = len(waypoints) > 2
                if closed and (captures - 1) // len(waypoints) >= laps_target:
                    done = True
                if not closed and captures >= len(waypoints):
                    done = True
                target = (target + 1) % len(waypoints) if closed else min(target + 1, len(waypoints) - 1)
                tx, ty = waypoints[target]
            # Pure pursuit on the segment towards the target, look-ahead L = 1.2 + 0.3 v.
            px, py = start_xy if captures == 0 else waypoints[target - 1]
            sx, sy = tx - px, ty - py
            seg = math.hypot(sx, sy) or 1.0
            s = max(0.0, min(seg, ((x - px) * sx + (y - py) * sy) / seg))
            look = 1.2 + 0.3 * abs(speed)
            s = min(seg, s + look)
            gx, gy = px + sx * s / seg, py + sy * s / seg
            alpha = math.atan2(gy - y, gx - x) - heading
            alpha = math.atan2(math.sin(alpha), math.cos(alpha))
            steer = math.atan2(2.0 * wheelbase * math.sin(alpha), look)
            remaining = math.hypot(tx - x, ty - y)
            v_target = min(cruise, max(1.5, 0.9 * remaining)) if len(waypoints) > 2 else cruise
            err = v_target - speed
            integral = max(-0.3, min(0.3, integral + 0.4 * err * period))
            throttle = v_target / 9.5 + 0.15 * err + integral
        if done and not failsafe:
            throttle = -1.0 if speed > 0.2 else 0.0
        if v_filt is not None and v_filt < float(config["low_voltage_limit_v"]):
            throttle = min(throttle, 0.3)
        if lvc_latched:
            throttle = min(throttle, 0.0)
        # Traction control: cap forward duty a little above the back-EMF of the measured
        # (driven) wheel speed, which bounds motor torque and so rear-wheel slip.
        if throttle > 0:
            throttle = min(throttle, (abs(speed) + 1.2) / 9.5 + 0.08)
        throttle = max(-1.0, min(1.0, throttle))
        pulse = esc_cfg["neutral"] + throttle * ((esc_cfg["forward"] - esc_cfg["neutral"]) if throttle > 0
                                                 else (esc_cfg["neutral"] - esc_cfg["reverse"]))
        steer = max(-max_steer, min(max_steer, steer))
        span = (steer_cfg["left"] - steer_cfg["center"]) if steer > 0 else (steer_cfg["center"] - steer_cfg["right"])
        esc.set_pulse_us(pulse)
        servo.set_pulse_us(steer_cfg["center"] + steer / max_steer * span)
        if beat is not None:
            level ^= 1
            beat.write(level)
        tick += 1
        t_next += period
        hal.clock.sleep_until(t_next)


if __name__ == "__main__":
    try:
        main()
    except HalError as error:
        print(f"hal error: {error}", flush=True)
        raise SystemExit(3)

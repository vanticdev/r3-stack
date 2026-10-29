"""InvenSense MPU-6050 register model (I2C, PS-MPU-6000A rev 3.4 / RM-MPU-6000A rev 4.2).

Implements the register file with an auto-incrementing pointer, the full-scale
selections and their sensitivities (accel 16384/8192/4096/2048 LSB/g; gyro
131/65.5/32.8/16.4 LSB/(deg/s)), sleep after reset (PWR_MGMT_1 = 0x40),
DEVICE_RESET, the DLPF (first-order approximation at the configured bandwidth)
and noise/bias from the datasheet: accel noise density 400 ug/sqrt(Hz), gyro
rate noise density 0.005 deg/s/sqrt(Hz), zero-g offset +/-50 mg (X/Y) and
+/-80 mg (Z), zero-rate offset +/-20 deg/s. Biases are drawn once per power-up
with sigma = tolerance / 3. Temperature: T(C) = raw / 340 + 36.53.
"""

from __future__ import annotations

import math

import numpy as np

STANDARD_GRAVITY = 9.80665
REG_SMPLRT_DIV, REG_CONFIG, REG_GYRO_CONFIG, REG_ACCEL_CONFIG = 0x19, 0x1A, 0x1B, 0x1C
REG_ACCEL_XOUT_H, REG_TEMP_OUT_H, REG_GYRO_XOUT_H = 0x3B, 0x41, 0x43
REG_PWR_MGMT_1, REG_PWR_MGMT_2, REG_WHO_AM_I = 0x6B, 0x6C, 0x75
ACCEL_LSB_PER_G = [16384.0, 8192.0, 4096.0, 2048.0]
GYRO_LSB_PER_DPS = [131.0, 65.5, 32.8, 16.4]
#: DLPF_CFG -> (accel bandwidth Hz, gyro bandwidth Hz); 7 is reserved (treated as 0).
DLPF_BANDWIDTH = {0: (260.0, 256.0), 1: (184.0, 188.0), 2: (94.0, 98.0), 3: (44.0, 42.0), 4: (21.0, 20.0),
                  5: (10.0, 10.0), 6: (5.0, 5.0), 7: (260.0, 256.0)}
ACCEL_NOISE_DENSITY = 400e-6 * STANDARD_GRAVITY  # m/s^2/sqrt(Hz)
GYRO_NOISE_DENSITY = math.radians(0.005)  # rad/s/sqrt(Hz)


def _int16(value: float) -> int:
    return max(-32768, min(32767, int(round(value))))


class Mpu6050:
    def __init__(self, address: int, rng: np.random.Generator) -> None:
        self.address = address
        self.rng = rng
        self.powered = False
        self.reset()

    def reset(self) -> None:
        self.regs = bytearray(128)
        self.regs[REG_PWR_MGMT_1] = 0x40  # SLEEP after reset
        self.regs[REG_WHO_AM_I] = 0x68
        self.pointer = 0
        tol_xy, tol_z = 0.050 * STANDARD_GRAVITY, 0.080 * STANDARD_GRAVITY
        self.accel_bias = self.rng.normal(0.0, 1.0, 3) * np.array([tol_xy, tol_xy, tol_z]) / 3.0
        self.gyro_bias = self.rng.normal(0.0, 1.0, 3) * math.radians(20.0) / 3.0
        self.accel_f = np.array([0.0, 0.0, STANDARD_GRAVITY])
        self.gyro_f = np.zeros(3)

    def set_powered(self, powered: bool) -> None:
        if powered and not self.powered:
            self.reset()
        self.powered = powered

    @property
    def sleeping(self) -> bool:
        return bool(self.regs[REG_PWR_MGMT_1] & 0x40)

    def update(self, dt: float, specific_force: np.ndarray, angular_rate: np.ndarray, temperature_c: float) -> None:
        """Called every simulation step with the true IMU-frame signals."""
        if not self.powered or self.sleeping:
            return
        cfg = self.regs[REG_CONFIG] & 0x07
        bw_a, bw_g = DLPF_BANDWIDTH[cfg]
        # Discrete white noise at the filter bandwidth: sigma = density * sqrt(BW).
        accel = specific_force + self.accel_bias + self.rng.normal(0.0, ACCEL_NOISE_DENSITY * math.sqrt(bw_a), 3)
        gyro = angular_rate + self.gyro_bias + self.rng.normal(0.0, GYRO_NOISE_DENSITY * math.sqrt(bw_g), 3)
        alpha_a = 1.0 - math.exp(-2.0 * math.pi * bw_a * dt)
        alpha_g = 1.0 - math.exp(-2.0 * math.pi * bw_g * dt)
        self.accel_f += alpha_a * (accel - self.accel_f)
        self.gyro_f += alpha_g * (gyro - self.gyro_f)
        afs = (self.regs[REG_ACCEL_CONFIG] >> 3) & 0x03
        gfs = (self.regs[REG_GYRO_CONFIG] >> 3) & 0x03
        a_raw = [_int16(v / STANDARD_GRAVITY * ACCEL_LSB_PER_G[afs]) for v in self.accel_f]
        g_raw = [_int16(math.degrees(v) * GYRO_LSB_PER_DPS[gfs]) for v in self.gyro_f]
        t_raw = _int16((temperature_c - 36.53) * 340.0)
        for base, values in ((REG_ACCEL_XOUT_H, a_raw), (REG_TEMP_OUT_H, [t_raw]), (REG_GYRO_XOUT_H, g_raw)):
            for k, value in enumerate(values):
                raw = value & 0xFFFF
                self.regs[base + 2 * k] = raw >> 8
                self.regs[base + 2 * k + 1] = raw & 0xFF

    def transfer(self, write: bytes, read_len: int) -> bytes:
        if write:
            self.pointer = write[0] & 0x7F
            for byte in write[1:]:
                self._write_reg(self.pointer, byte)
                self.pointer = (self.pointer + 1) & 0x7F
        out = bytearray()
        for _ in range(read_len):
            out.append(self.regs[self.pointer])
            self.pointer = (self.pointer + 1) & 0x7F
        return bytes(out)

    def _write_reg(self, reg: int, value: int) -> None:
        if reg == REG_WHO_AM_I or REG_ACCEL_XOUT_H <= reg <= REG_GYRO_XOUT_H + 5:
            return  # read-only
        if reg == REG_PWR_MGMT_1 and value & 0x80:
            self.reset()
            return
        self.regs[reg] = value & 0xFF

"""I2C device drivers: TI INA226 power monitor and InvenSense MPU-6050 IMU.

Register maps and scale factors are from the TI INA226 datasheet (SBOS547A) and
the MPU-6000/6050 register map (RM-MPU-6000A-00 rev 4.2). Both parts use
big-endian 16-bit registers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import vendor  # noqa: F401  (r3_hal search path)
from r3_hal import HalError, I2cBus

# --- INA226 -------------------------------------------------------------------

INA226_CONFIG = 0x00
INA226_SHUNT_VOLTAGE = 0x01
INA226_BUS_VOLTAGE = 0x02
INA226_POWER = 0x03
INA226_CURRENT = 0x04
INA226_CALIBRATION = 0x05
INA226_MANUFACTURER_ID = 0xFE
INA226_DIE_ID = 0xFF
INA226_TI_ID = 0x5449
INA226_DIE = 0x2260

INA226_BUS_LSB_V = 1.25e-3     # datasheet 7.6.3: 1.25 mV/bit
INA226_SHUNT_LSB_V = 2.5e-6    # datasheet 7.6.2: 2.5 uV/bit
INA226_CAL_CONSTANT = 0.00512  # datasheet eq. 1: CAL = 0.00512 / (Current_LSB * R_shunt)

# AVG = 16 samples (010), VBUSCT = VSHCT = 1.1 ms (100), MODE = shunt+bus continuous (111).
# One result every 16 * (1.1 + 1.1) ms = 35.2 ms; bit 14 is reserved-high.
INA226_AVG_16 = 0b010
INA226_CT_1100US = 0b100
INA226_MODE_CONTINUOUS = 0b111


def ina226_config_word(avg: int = INA226_AVG_16, vbus_ct: int = INA226_CT_1100US,
                       vshunt_ct: int = INA226_CT_1100US, mode: int = INA226_MODE_CONTINUOUS) -> int:
    return 0x4000 | (avg << 9) | (vbus_ct << 6) | (vshunt_ct << 3) | mode


def ina226_calibration(shunt_ohm: float, max_current_a: float) -> tuple[int, float]:
    """Return (CAL register, Current_LSB in A).

    Current_LSB = I_max / 2^15 (datasheet eq. 2), rounded up to a 1-2-5 decade
    step so register values are readable; CAL = 0.00512 / (Current_LSB * R_shunt).
    """
    minimum = max_current_a / 32768.0
    exponent = math.floor(math.log10(minimum))
    current_lsb = next(step * 10.0 ** exponent for step in (1, 2, 5, 10) if step * 10.0 ** exponent >= minimum)
    cal = int(INA226_CAL_CONSTANT / (current_lsb * shunt_ohm))
    if not 0 < cal <= 0x7FFF:
        raise ValueError(f"INA226 calibration {cal} out of range for {shunt_ohm} ohm / {max_current_a} A")
    return cal, current_lsb


def _s16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


@dataclass(frozen=True)
class PowerReading:
    bus_voltage_v: float
    shunt_voltage_v: float
    current_a: float
    power_w: float


class Ina226:
    def __init__(self, bus: I2cBus, address: int = 0x40, shunt_ohm: float = 0.002, max_current_a: float = 10.0) -> None:
        self.bus, self.address = bus, address
        self.calibration, self.current_lsb = ina226_calibration(shunt_ohm, max_current_a)
        self.shunt_ohm = shunt_ohm

    def _write(self, register: int, value: int) -> None:
        self.bus.write_reg(self.address, register, value.to_bytes(2, "big"))

    def _read(self, register: int) -> int:
        data = self.bus.read_reg(self.address, register, 2)
        if len(data) != 2:
            raise HalError("i2c_short_read", f"INA226 register 0x{register:02x}")
        return int.from_bytes(data, "big")

    def init(self) -> None:
        manufacturer, die = self._read(INA226_MANUFACTURER_ID), self._read(INA226_DIE_ID)
        if manufacturer != INA226_TI_ID or (die & 0xFFF0) != INA226_DIE:
            raise HalError("device_id_mismatch", f"INA226 ids 0x{manufacturer:04x}/0x{die:04x}")
        self._write(INA226_CONFIG, 0x8000)  # reset
        self._write(INA226_CONFIG, ina226_config_word())
        self._write(INA226_CALIBRATION, self.calibration)

    def read(self) -> PowerReading:
        bus = self._read(INA226_BUS_VOLTAGE) * INA226_BUS_LSB_V
        shunt = _s16(self._read(INA226_SHUNT_VOLTAGE)) * INA226_SHUNT_LSB_V
        current = _s16(self._read(INA226_CURRENT)) * self.current_lsb
        power = self._read(INA226_POWER) * 25.0 * self.current_lsb  # datasheet: Power_LSB = 25 Current_LSB
        return PowerReading(bus, shunt, current, power)


# --- MPU-6050 -----------------------------------------------------------------

MPU_SMPLRT_DIV = 0x19
MPU_CONFIG = 0x1A
MPU_GYRO_CONFIG = 0x1B
MPU_ACCEL_CONFIG = 0x1C
MPU_ACCEL_XOUT_H = 0x3B
MPU_PWR_MGMT_1 = 0x6B
MPU_WHO_AM_I = 0x75
MPU_WHO_AM_I_VALUE = 0x68

MPU_GYRO_FS_500DPS = 1     # 65.5 LSB/(deg/s)
MPU_ACCEL_FS_4G = 1        # 8192 LSB/g
MPU_DLPF_44HZ = 3          # gyro bandwidth 42 Hz, 1 kHz internal rate
GYRO_SENSITIVITY = {0: 131.0, 1: 65.5, 2: 32.8, 3: 16.4}      # LSB per deg/s
ACCEL_SENSITIVITY = {0: 16384.0, 1: 8192.0, 2: 4096.0, 3: 2048.0}
STANDARD_GRAVITY = 9.80665


@dataclass(frozen=True)
class ImuSample:
    accel_mps2: tuple[float, float, float]
    gyro_rps: tuple[float, float, float]
    temperature_c: float


class Mpu6050:
    def __init__(self, bus: I2cBus, address: int = 0x68, gyro_range: int = MPU_GYRO_FS_500DPS,
                 accel_range: int = MPU_ACCEL_FS_4G, mounting_rpy_deg: tuple[float, float, float] = (0, 0, 0)) -> None:
        self.bus, self.address = bus, address
        roll, pitch, yaw = (math.radians(v) for v in mounting_rpy_deg)
        cr, sr, cp, sp, cy, sy = math.cos(roll), math.sin(roll), math.cos(pitch), math.sin(pitch), math.cos(yaw), math.sin(yaw)
        self.rotation = ((cy*cp, cy*sp*sr-sy*cr, cy*sp*cr+sy*sr),
                         (sy*cp, sy*sp*sr+cy*cr, sy*sp*cr-cy*sr), (-sp, cp*sr, cp*cr))
        self.gyro_range, self.accel_range = gyro_range, accel_range
        self._gyro_scale = math.radians(1.0) / GYRO_SENSITIVITY[gyro_range]
        self._accel_scale = STANDARD_GRAVITY / ACCEL_SENSITIVITY[accel_range]

    def init(self) -> None:
        who = self.bus.read_reg(self.address, MPU_WHO_AM_I, 1)
        if not who or (who[0] & 0x7E) != MPU_WHO_AM_I_VALUE:
            raise HalError("device_id_mismatch", f"MPU-6050 WHO_AM_I {who.hex()}")
        self.bus.write_reg(self.address, MPU_PWR_MGMT_1, bytes([0x80]))  # device reset
        self.bus.write_reg(self.address, MPU_PWR_MGMT_1, bytes([0x01]))  # wake, PLL on X gyro
        self.bus.write_reg(self.address, MPU_CONFIG, bytes([MPU_DLPF_44HZ]))
        self.bus.write_reg(self.address, MPU_SMPLRT_DIV, bytes([9]))     # 1 kHz / (1 + 9) = 100 Hz
        self.bus.write_reg(self.address, MPU_GYRO_CONFIG, bytes([self.gyro_range << 3]))
        self.bus.write_reg(self.address, MPU_ACCEL_CONFIG, bytes([self.accel_range << 3]))

    def read(self) -> ImuSample:
        """Burst-read ACCEL_XOUT_H..GYRO_ZOUT_L (14 bytes) in one transaction."""
        raw = self.bus.read_reg(self.address, MPU_ACCEL_XOUT_H, 14)
        if len(raw) != 14:
            raise HalError("i2c_short_read", "MPU-6050 burst read")
        words = [_s16(int.from_bytes(raw[i:i + 2], "big")) for i in range(0, 14, 2)]
        accel = tuple(w * self._accel_scale for w in words[0:3])
        gyro = tuple(w * self._gyro_scale for w in words[4:7])
        accel = tuple(sum(r[i] * accel[i] for i in range(3)) for r in self.rotation)
        gyro = tuple(sum(r[i] * gyro[i] for i in range(3)) for r in self.rotation)
        return ImuSample(accel, gyro, words[3] / 340.0 + 36.53)  # temperature formula, register map 4.18


class GyroBiasCalibrator:
    """Average gyro z while stationary; restarts if motion is detected.

    Motion test: sample standard deviation above ``max_std_rps`` or any encoder edge.
    """

    def __init__(self, samples_required: int, max_std_rps: float = 0.02) -> None:
        self.samples_required = samples_required
        self.max_std_rps = max_std_rps
        self._samples: list[float] = []
        self.bias_rps: float | None = None
        self.restarts = 0

    @property
    def done(self) -> bool:
        return self.bias_rps is not None

    def add(self, gyro_z: float, moving: bool) -> bool:
        if self.done:
            return True
        if moving:
            self._samples.clear()
            self.restarts += 1
            return False
        self._samples.append(gyro_z)
        if len(self._samples) >= self.samples_required:
            n = len(self._samples)
            mean = sum(self._samples) / n
            std = math.sqrt(sum((s - mean) ** 2 for s in self._samples) / max(1, n - 1))
            if std > self.max_std_rps:
                self._samples.clear()
                self.restarts += 1
                return False
            self.bias_rps = mean
        return self.done

    @property
    def std_rps(self) -> float:
        n = len(self._samples)
        if n < 2:
            return 0.0
        mean = sum(self._samples) / n
        return math.sqrt(sum((s - mean) ** 2 for s in self._samples) / (n - 1))

"""Deterministic register-level instrument fixture, without robot dynamics."""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from r3_hal import HalError, I2cBus

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE_DIR = ROOT / "tests/fixtures/bench/backend/python/bench_controller"
sys.path.insert(0, str(FIRMWARE_DIR))


@dataclass
class SensorParams:
    monitor_current: float = 0.4
    bus_voltage: float = 7.5
    gyro_noise: float = 0.0
    gyro_bias: float = 0.0


class _Device:
    def __init__(self, registers: dict[int, int]) -> None:
        self.registers = registers


class _Bus(I2cBus):
    def __init__(self, rig: SensorHal) -> None:
        self.rig = rig

    def transfer(self, addr: int, write: bytes = b"", read_len: int = 0) -> bytes:
        device = self.rig.i2c_devices.get(addr)
        if device is None:
            raise HalError("i2c_nack")
        register = write[0] if write else 0
        if addr == 0x40:
            if len(write) == 3:
                device.registers[register] = int.from_bytes(write[1:], "big")
            if register == 1:
                value = round(self.rig.params.monitor_current * .002 / 2.5e-6)
            elif register == 2:
                value = round(self.rig.battery_voltage() / 1.25e-3)
            elif register == 4:
                value = round(self.rig.params.monitor_current / 5e-4)
            else:
                value = device.registers.get(register, 0)
            return value.to_bytes(2, "big")[:read_len] if read_len else b""
        for index, byte in enumerate(write[1:]):
            device.registers[register + index] = byte
        if register == 0x3B:
            values = [0, 0, 8192, round((25 - 36.53) * 340), 0, 0,
                      round(math.degrees(self.rig.sensor.yaw_rate) * 65.5)]
            return b"".join((value & 0xffff).to_bytes(2, "big") for value in values)[:read_len]
        return bytes(device.registers.get(register + index, 0) for index in range(read_len))


class SensorHal:
    def __init__(self, params: SensorParams | None = None) -> None:
        self.params = params or SensorParams()
        self.sensor = SimpleNamespace(yaw_rate=0.0)
        self.i2c_devices = {0x40: _Device({0xFE: 0x5449, 0xFF: 0x2260}), 0x68: _Device({0x75: 0x68})}

    def i2c(self, bus: str) -> I2cBus:
        return _Bus(self)

    def battery_voltage(self) -> float:
        return self.params.bus_voltage

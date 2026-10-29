"""TI INA226 current/power monitor register model (SBOS547A).

Registers are 16-bit big-endian behind a pointer register. Semantics:

* Shunt voltage: signed, LSB 2.5 uV. Bus voltage: unsigned, LSB 1.25 mV.
* Calibration: CAL = 0.00512 / (Current_LSB * R_shunt) (written by software).
* Current register = ShuntVoltage_raw * CAL / 2048 (signed).
* Power register = Current_raw * BusVoltage_raw / 20000 (Power_LSB = 25 * Current_LSB).
* Config: AVG[11:9] (1..1024 samples), VBUSCT[8:6], VSHCT[5:3] (140 us..8.244 ms), MODE[2:0];
  bit 15 resets. Results update once per averaging window (continuous modes 5-7).
* Manufacturer ID 0x5449 ("TI"), die ID 0x2260.

The ADC input is the shunt current/voltage of the branch where the shunt is
physically wired (see :class:`r3_sim.kernel.CoSimKernel`).
"""

from __future__ import annotations

import numpy as np

REG_CONFIG, REG_SHUNT, REG_BUS, REG_POWER, REG_CURRENT, REG_CAL, REG_MASK, REG_ALERT = range(8)
REG_MANUFACTURER, REG_DIE = 0xFE, 0xFF
CONFIG_RESET_VALUE = 0x4127
SHUNT_LSB = 2.5e-6
BUS_LSB = 1.25e-3
AVERAGES = [1, 4, 16, 64, 128, 256, 512, 1024]
CONVERSION_TIME = [140e-6, 204e-6, 332e-6, 588e-6, 1.1e-3, 2.116e-3, 4.156e-3, 8.244e-3]
#: Input-referred noise (estimate from datasheet typical plots): ~1 LSB per single conversion at 1.1 ms.
SHUNT_NOISE_V = 2.5e-6
BUS_NOISE_V = 1.25e-3


class Ina226:
    def __init__(self, address: int, shunt_ohm: float, rng: np.random.Generator) -> None:
        self.address = address
        self.shunt = shunt_ohm
        self.rng = rng
        self.powered = False
        self.reset()

    def reset(self) -> None:
        self.regs = [0] * 8
        self.regs[REG_CONFIG] = CONFIG_RESET_VALUE
        self.pointer = 0
        self._acc_shunt = 0.0
        self._acc_bus = 0.0
        self._acc_time = 0.0

    def set_powered(self, powered: bool) -> None:
        if powered and not self.powered:
            self.reset()
        self.powered = powered

    def _window(self) -> tuple[float, int, float, float]:
        cfg = self.regs[REG_CONFIG]
        avg = AVERAGES[(cfg >> 9) & 7]
        vbus_ct = CONVERSION_TIME[(cfg >> 6) & 7]
        vsh_ct = CONVERSION_TIME[(cfg >> 3) & 7]
        return avg * (vbus_ct + vsh_ct), avg, vbus_ct, vsh_ct

    def update(self, dt: float, current_a: float, bus_voltage: float) -> None:
        """Integrate the true shunt current and bus voltage; publish at the end of each window."""
        if not self.powered:
            return
        mode = self.regs[REG_CONFIG] & 7
        if mode in (0, 4):
            return  # power-down
        self._acc_shunt += current_a * self.shunt * dt
        self._acc_bus += bus_voltage * dt
        self._acc_time += dt
        window, avg, vbus_ct, vsh_ct = self._window()
        if self._acc_time + 1e-12 < window:
            return
        v_shunt = self._acc_shunt / self._acc_time
        v_bus = self._acc_bus / self._acc_time
        self._acc_shunt = self._acc_bus = self._acc_time = 0.0
        # Averaging N conversions reduces white noise by sqrt(N); longer conversion times by sqrt(Tc / 1.1 ms).
        v_shunt += self.rng.normal(0.0, SHUNT_NOISE_V / np.sqrt(avg * vsh_ct / 1.1e-3))
        v_bus += self.rng.normal(0.0, BUS_NOISE_V / np.sqrt(avg * vbus_ct / 1.1e-3))
        if mode in (1, 3, 5, 7):
            shunt_raw = max(-32768, min(32767, int(round(v_shunt / SHUNT_LSB))))
            self.regs[REG_SHUNT] = shunt_raw & 0xFFFF
        if mode in (2, 3, 6, 7):
            self.regs[REG_BUS] = max(0, min(0x7FFF, int(round(v_bus / BUS_LSB))))
        self._compute_current_power()
        self.regs[REG_MASK] |= 0x0008  # CVRF conversion ready

    def _compute_current_power(self) -> None:
        shunt_raw = self.regs[REG_SHUNT]
        shunt_signed = shunt_raw - 0x10000 if shunt_raw & 0x8000 else shunt_raw
        cal = self.regs[REG_CAL]
        current = int(shunt_signed * cal / 2048)
        current = max(-32768, min(32767, current))
        self.regs[REG_CURRENT] = current & 0xFFFF
        self.regs[REG_POWER] = min(0xFFFF, int(abs(current) * self.regs[REG_BUS] / 20000))

    def transfer(self, write: bytes, read_len: int) -> bytes:
        if write:
            self.pointer = write[0]
            if len(write) >= 3:
                self._write_reg(self.pointer, (write[1] << 8) | write[2])
        out = bytearray()
        value = self._read_reg(self.pointer)
        for k in range(read_len):
            out.append((value >> 8) & 0xFF if k % 2 == 0 else value & 0xFF)
        if self.pointer == REG_MASK and read_len:
            self.regs[REG_MASK] &= ~0x0008  # reading Mask/Enable clears CVRF
        return bytes(out)

    def _read_reg(self, reg: int) -> int:
        if reg == REG_MANUFACTURER:
            return 0x5449
        if reg == REG_DIE:
            return 0x2260
        if 0 <= reg < 8:
            return self.regs[reg]
        return 0

    def _write_reg(self, reg: int, value: int) -> None:
        if reg == REG_CONFIG:
            if value & 0x8000:
                self.reset()
                return
            self.regs[REG_CONFIG] = (value & 0x0FFF) | 0x4000  # bits 14 reads as 1, 13-12 reserved
        elif reg == REG_CAL:
            self.regs[REG_CAL] = value & 0x7FFF  # bit 15 reserved
            self._compute_current_power()
        elif reg in (REG_MASK, REG_ALERT):
            self.regs[reg] = value & 0xFFFF

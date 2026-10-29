"""Backend-independent hardware interfaces used by robot software."""

from __future__ import annotations

import abc
from dataclasses import dataclass


class HalError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True)
class Edge:
    t: float
    rising: bool


class Clock(abc.ABC):
    @abc.abstractmethod
    def now(self) -> float: ...

    @abc.abstractmethod
    def sleep_until(self, t: float) -> None: ...

    def sleep(self, seconds: float) -> None:
        self.sleep_until(self.now() + seconds)


class GpioLine(abc.ABC):
    @abc.abstractmethod
    def read(self) -> int: ...

    @abc.abstractmethod
    def write(self, value: int) -> None: ...

    @abc.abstractmethod
    def count_edges(self) -> tuple[int, float]:
        """Total rising edges since configure and time of the last one."""


class PwmChannel(abc.ABC):
    @abc.abstractmethod
    def configure(self, period_ns: int, duty_ns: int, enabled: bool = True) -> None: ...

    def set_pulse_us(self, pulse_us: float, frequency_hz: float = 50.0) -> None:
        period = int(round(1e9 / frequency_hz))
        self.configure(period, int(round(pulse_us * 1000)), True)


class I2cBus(abc.ABC):
    @abc.abstractmethod
    def transfer(self, addr: int, write: bytes = b"", read_len: int = 0) -> bytes: ...

    def write_reg(self, addr: int, reg: int, data: bytes) -> None:
        self.transfer(addr, bytes([reg]) + data, 0)

    def read_reg(self, addr: int, reg: int, length: int) -> bytes:
        return self.transfer(addr, bytes([reg]), length)


class SpiDevice(abc.ABC):
    @abc.abstractmethod
    def transfer(self, data: bytes) -> bytes: ...


class Uart(abc.ABC):
    @abc.abstractmethod
    def write(self, data: bytes) -> int: ...

    @abc.abstractmethod
    def read(self, max_bytes: int = 4096, timeout: float = 0.0) -> bytes: ...


class Hal(abc.ABC):
    """Factory for interfaces. Arguments mirror ``robot.yaml`` software bindings."""

    clock: Clock

    @abc.abstractmethod
    def gpio(self, chip: str, line: int, direction: str = "in", bias: str = "none", edge: str = "none") -> GpioLine: ...

    @abc.abstractmethod
    def pwm(self, chip: str, channel: int) -> PwmChannel: ...

    @abc.abstractmethod
    def i2c(self, bus: str) -> I2cBus: ...

    @abc.abstractmethod
    def spi(self, bus: str, cs: int, speed_hz: int = 1_000_000, mode: int = 0) -> SpiDevice: ...

    @abc.abstractmethod
    def uart(self, device: str, baud: int) -> Uart: ...

    def log(self, level: str, message: str, **fields: object) -> None:
        print(f"[{level}] {message} {fields if fields else ''}", flush=True)

    def bindings(self) -> dict[str, dict]:
        """Hardware bindings for this computer (from robot.yaml); may be empty on linux."""
        return {}

    def close(self) -> None:
        pass

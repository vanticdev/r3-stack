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

    def wait_edge(self, timeout: float) -> list[Edge]:
        """Edges since the last call, blocking up to ``timeout`` for at least one."""
        raise HalError("not_supported", "wait_edge is not implemented by this backend")


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


@dataclass(frozen=True)
class Frame:
    """One camera image. ``data`` is row-major, top row first: ``rgb888`` is 3 bytes
    per pixel (R, G, B), ``gray8`` one. ``t`` is the capture time (start of exposure)
    on the HAL clock, ``sequence`` the sensor frame counter (gaps = dropped frames)."""

    t: float
    sequence: int
    width: int
    height: int
    format: str
    data: bytes
    exposure_s: float = 0.0

    def array(self):  # -> numpy.ndarray (height, width, channels); numpy is optional for the HAL
        import numpy as np

        channels = {"rgb888": 3, "gray8": 1}[self.format]
        return np.frombuffer(self.data, dtype=np.uint8).reshape(self.height, self.width, channels)


class Camera(abc.ABC):
    """A frame-paced camera stream (libcamera semantics): ``capture`` returns the next
    completed frame, blocking until the sensor delivers it."""

    width: int
    height: int
    fps: float
    format: str

    @abc.abstractmethod
    def capture(self, timeout: float = 1.0) -> Frame: ...

    def close(self) -> None:
        pass


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

    def camera(self, port: str = "cam0", width: int = 640, height: int = 480, fps: float = 30.0,
               format: str = "rgb888") -> Camera:
        """Open the camera on a CSI port (``cam0``/``cam1``; robot.yaml ``csi`` binding ``device``)."""
        raise HalError("not_supported", "this backend has no camera support")

    def device_request(self, device: str, operation: str, payload: dict | None = None, timeout: float = 0.0) -> dict:
        """Project-defined device operation. Physical backends require the project's vendor driver."""
        raise HalError("not_supported", "project devices need a physical vendor-driver adapter on this backend")

    def log(self, level: str, message: str, **fields: object) -> None:
        print(f"[{level}] {message} {fields if fields else ''}", flush=True)

    def bindings(self) -> dict[str, dict]:
        """Hardware bindings for this computer (from robot.yaml); may be empty on linux."""
        return {}

    def config(self) -> dict:
        """Runtime configuration offered by the platform (the twin's ``hello``
        reply in simulation); empty when the program must read its own config."""
        return {}

    def close(self) -> None:
        pass

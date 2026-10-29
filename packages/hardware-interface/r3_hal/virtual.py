"""Virtual backend: JSON-lines client of the digital twin (protocol r3-hal/1)."""

from __future__ import annotations

import json
import socket
from typing import Any
from urllib.parse import urlparse

from .api import Clock, GpioLine, Hal, HalError, I2cBus, PwmChannel, SpiDevice, Uart

PROTOCOL = "r3-hal/1"


class _Connection:
    def __init__(self, endpoint: str) -> None:
        url = urlparse(endpoint)
        if url.scheme != "tcp":
            raise HalError("bad_endpoint", endpoint)
        self._sock = socket.create_connection((url.hostname, url.port), timeout=None)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._file = self._sock.makefile("rwb")
        self._next_id = 0
        self.t = 0.0

    def call(self, op: str, **args: Any) -> dict[str, Any]:
        self._next_id += 1
        message = {"id": self._next_id, "op": op, **args}
        self._file.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
        self._file.flush()
        line = self._file.readline()
        if not line:
            raise HalError("twin_disconnected", "digital twin closed the connection")
        reply = json.loads(line)
        self.t = reply.get("t", self.t)
        if not reply.get("ok", False):
            raise HalError(reply.get("error", "error"), reply.get("message", ""))
        return reply

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


class _Clock(Clock):
    def __init__(self, conn: _Connection) -> None:
        self._conn = conn

    def now(self) -> float:
        return self._conn.call("clock.now")["t"]

    def sleep_until(self, t: float) -> None:
        self._conn.call("clock.sleep_until", t=t)


class _Gpio(GpioLine):
    def __init__(self, conn: _Connection, chip: str, line: int) -> None:
        self._conn, self._chip, self._line = conn, chip, line

    def read(self) -> int:
        return int(self._conn.call("gpio.read", chip=self._chip, line=self._line)["value"])

    def write(self, value: int) -> None:
        self._conn.call("gpio.write", chip=self._chip, line=self._line, value=int(value))

    def count_edges(self) -> tuple[int, float]:
        reply = self._conn.call("gpio.count_edges", chip=self._chip, line=self._line)
        return int(reply["count"]), float(reply.get("last_t", 0.0))


class _Pwm(PwmChannel):
    def __init__(self, conn: _Connection, chip: str, channel: int) -> None:
        self._conn, self._chip, self._channel = conn, chip, channel

    def configure(self, period_ns: int, duty_ns: int, enabled: bool = True) -> None:
        self._conn.call("pwm.configure", chip=self._chip, channel=self._channel,
                        period_ns=int(period_ns), duty_ns=int(duty_ns), enabled=bool(enabled))


class _I2c(I2cBus):
    def __init__(self, conn: _Connection, bus: str) -> None:
        self._conn, self._bus = conn, bus

    def transfer(self, addr: int, write: bytes = b"", read_len: int = 0) -> bytes:
        reply = self._conn.call("i2c.transfer", bus=self._bus, addr=addr, write=list(write), read_len=read_len)
        return bytes(reply.get("data", []))


class _Spi(SpiDevice):
    def __init__(self, conn: _Connection, bus: str, cs: int, speed_hz: int, mode: int) -> None:
        self._conn, self._args = conn, {"bus": bus, "cs": cs, "speed_hz": speed_hz, "mode": mode}

    def transfer(self, data: bytes) -> bytes:
        return bytes(self._conn.call("spi.transfer", data=list(data), **self._args)["data"])


class _Uart(Uart):
    def __init__(self, conn: _Connection, device: str) -> None:
        self._conn, self._device = conn, device

    def write(self, data: bytes) -> int:
        return int(self._conn.call("uart.write", device=self._device, data=list(data))["written"])

    def read(self, max_bytes: int = 4096, timeout: float = 0.0) -> bytes:
        return bytes(self._conn.call("uart.read", device=self._device, max=max_bytes, timeout=timeout)["data"])


class VirtualHal(Hal):
    def __init__(self, endpoint: str, module: str = "") -> None:
        self._conn = _Connection(endpoint)
        hello = self._conn.call("hello", protocol=PROTOCOL, module=module)
        self._bindings: dict[str, dict] = hello.get("bindings", {})
        self.computer: str = hello.get("computer", "")
        self.clock = _Clock(self._conn)

    def gpio(self, chip: str, line: int, direction: str = "in", bias: str = "none", edge: str = "none") -> GpioLine:
        self._conn.call("gpio.configure", chip=chip, line=line, direction=direction, bias=bias, edge=edge)
        return _Gpio(self._conn, chip, line)

    def pwm(self, chip: str, channel: int) -> PwmChannel:
        return _Pwm(self._conn, chip, channel)

    def i2c(self, bus: str) -> I2cBus:
        return _I2c(self._conn, bus)

    def spi(self, bus: str, cs: int, speed_hz: int = 1_000_000, mode: int = 0) -> SpiDevice:
        return _Spi(self._conn, bus, cs, speed_hz, mode)

    def uart(self, device: str, baud: int) -> Uart:
        self._conn.call("uart.configure", device=device, baud=baud)
        return _Uart(self._conn, device)

    def log(self, level: str, message: str, **fields: object) -> None:
        self._conn.call("log", level=level, message=message, fields=fields)

    def bindings(self) -> dict[str, dict]:
        return dict(self._bindings)

    def close(self) -> None:
        try:
            self._conn.call("bye")
        except HalError:
            pass
        self._conn.close()

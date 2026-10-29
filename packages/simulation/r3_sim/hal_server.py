"""Twin HAL server: ``r3-hal/1`` over TCP, newline-delimited JSON, lockstep.

See ``packages/hardware-interface/PROTOCOL.md``. Simulation time advances only
while the software is blocked in ``clock.sleep_until``, ``uart.read`` with a
timeout, or ``gpio.wait_edge``; every other request is answered at the current
simulation instant. While the computer's supply is browned out every request
is answered with ``brownout`` (the runner then restarts the program after the
modeled boot time).

``hello`` additionally returns ``config`` (``software.config`` merged with the
scenario mode, mission, GPS origin and CRSF channel map); clients that ignore
unknown fields are unaffected.
"""

from __future__ import annotations

import json
import socket
from typing import Any

from .kernel import CoSimKernel

PROTOCOL = "r3-hal/1"


class HalRequestError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TwinHalServer:
    def __init__(self, kernel: CoSimKernel, host: str = "127.0.0.1", port: int = 0, request_timeout: float = 120.0) -> None:
        self.kernel = kernel
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((host, port))
        self.listener.listen(1)
        self.host, self.port = self.listener.getsockname()
        self.request_timeout = request_timeout
        self.conn: socket.socket | None = None
        self._rfile: Any = None
        self.module = ""
        self.requests = 0
        self.ended = False  # scenario finished (bye or duration reached)
        self.bye = False
        self.errors: dict[str, int] = {}
        self.bad_lines: list[bytes] = []
        computer = kernel.computer
        spec = kernel.project.spec
        self.binding_params = {
            name: {k: v for k, v in b.model_dump(mode="json").items() if v not in (None, [], "")}
            for name, b in spec.software.bindings.items() if b.computer == computer}
        self.gpio_bound = {(b.device, int(b.channel or 0)) for b in spec.software.bindings.values()
                           if b.interface == "gpio" and b.computer == computer}

    @property
    def endpoint(self) -> str:
        return f"tcp://{self.host}:{self.port}"

    # ------------------------------------------------------------------ connection
    def accept(self, timeout: float) -> bool:
        self.listener.settimeout(timeout)
        try:
            conn, _ = self.listener.accept()
        except (socket.timeout, OSError):
            return False
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.settimeout(self.request_timeout)
        self.conn = conn
        self._rfile = conn.makefile("rb")
        return True

    def close_connection(self) -> None:
        for closer in (self._rfile, self.conn):
            try:
                if closer is not None:
                    closer.close()
            except OSError:
                pass
        self.conn, self._rfile = None, None

    def close(self) -> None:
        self.close_connection()
        try:
            self.listener.close()
        except OSError:
            pass

    @property
    def connected(self) -> bool:
        return self.conn is not None

    def _send(self, message: dict[str, Any]) -> None:
        assert self.conn is not None
        self.conn.sendall(json.dumps(message, separators=(",", ":")).encode() + b"\n")

    def _reply(self, message: dict[str, Any]) -> bool:
        """Send a reply; False when the program has gone (e.g. killed or reset while the
        request was being served across a brownout): the caller treats it as closed."""
        try:
            self._send(message)
        except OSError:
            return False
        return True

    def serve_one(self) -> str:
        """Read and answer one request. Returns ``ok``, ``closed``, ``timeout``,
        ``brownout`` (a brownout reply was sent), ``freeze`` (a software-freeze fault is
        due: the request is left unanswered and the runner stops the process), or
        ``end`` (scenario over)."""
        assert self._rfile is not None
        try:
            line = self._rfile.readline()
        except socket.timeout:
            return "timeout"
        except OSError:
            return "closed"
        if not line:
            return "closed"
        try:
            request = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.errors["bad_request"] = self.errors.get("bad_request", 0) + 1
            self.bad_lines.append(line[:200])
            return "ok" if self._reply({"id": None, "ok": False, "t": self.kernel.t, "error": "bad_request", "message": "invalid JSON"}) \
                else "closed"
        self.requests += 1
        rid = request.get("id")
        op = str(request.get("op", ""))
        if self.kernel.freeze_due:
            return "freeze"
        if not self.kernel.pi_powered:
            self._error(rid, "brownout", "computer supply below brownout threshold")
            return "brownout"
        try:
            result, status = self._dispatch(op, request)
        except HalRequestError as error:
            self._error(rid, error.code, error.message)
            return "brownout" if error.code == "brownout" else "ok"
        if status == "end":
            self.ended = True
            return "end"
        if status == "freeze":
            return "freeze"
        if not self._reply({"id": rid, "ok": True, "t": round(self.kernel.t, 9), **result}):
            return "closed"
        if op == "bye":
            self.bye = self.ended = True
            return "end"
        return "ok"

    def _error(self, rid: Any, code: str, message: str) -> None:
        self.errors[code] = self.errors.get(code, 0) + 1
        try:
            self._send({"id": rid, "ok": False, "t": round(self.kernel.t, 9), "error": code, "message": message})
        except OSError:
            pass

    # ------------------------------------------------------------------ dispatch
    def _advance(self, done: Any, limit: float | None = None, deadline: float | None = None) -> str:
        reason = self.kernel.advance_until(done, limit, deadline)
        if reason == "brownout":
            raise HalRequestError("brownout", "computer supply browned out while blocked")
        return reason

    @staticmethod
    def _blocked(reason: str) -> str | None:
        """Request status when a blocking wait ended early (scenario end or software freeze)."""
        return reason if reason in ("end", "freeze") else None

    def _dispatch(self, op: str, req: dict[str, Any]) -> tuple[dict[str, Any], str]:
        k = self.kernel
        if op == "hello":
            if req.get("protocol") != PROTOCOL:
                raise HalRequestError("protocol_mismatch", f"twin speaks {PROTOCOL}")
            self.module = str(req.get("module", ""))
            k.software_running = True
            return {"computer": k.computer, "bindings": self.binding_params, "config": k.software_config(),
                    "protocol": PROTOCOL}, "ok"
        if op == "clock.now":
            return {}, "ok"
        if op == "clock.sleep_until":
            target = float(req.get("t", k.t))
            reason = self._advance(lambda: k.t + 1e-9 >= target, deadline=target)
            return {}, self._blocked(reason) or "ok"
        if op.startswith("gpio."):
            return self._gpio(op, req)
        if op == "pwm.configure":
            key = (str(req.get("chip")), int(req.get("channel", -1)))
            if key not in k.pwm_devices:
                raise HalRequestError("pin_not_bound", f"no pwm binding for {key[0]} channel {key[1]}")
            k.pwm_state[key] = {"period_ns": int(req.get("period_ns", 0)), "duty_ns": int(req.get("duty_ns", 0)),
                                "enabled": bool(req.get("enabled", True))}
            return {}, "ok"
        if op == "i2c.transfer":
            return self._i2c(req), "ok"
        if op == "spi.transfer":
            raise HalRequestError("no_such_device", f"no SPI device on {req.get('bus')}")
        if op.startswith("uart."):
            return self._uart(op, req)
        if op == "log":
            k.log(str(req.get("level", "info")), str(req.get("message", "")), dict(req.get("fields") or {}))
            return {}, "ok"
        if op == "bye":
            return {}, "ok"
        raise HalRequestError("unknown_op", op)

    def _gpio(self, op: str, req: dict[str, Any]) -> tuple[dict[str, Any], str]:
        k = self.kernel
        key = (str(req.get("chip")), int(req.get("line", -1)))
        if key not in self.gpio_bound:
            raise HalRequestError("pin_not_bound", f"{key[0]} line {key[1]} is not bound in robot.yaml")
        from .kernel import GpioLineState

        if op == "gpio.configure":
            k.gpio_lines[key] = GpioLineState(key[0], key[1], str(req.get("direction", "in")), str(req.get("bias", "none")),
                                              str(req.get("edge", "none")))
            if k.gpio_lines[key].direction == "out":
                k.gpio_written(key, 0)  # a line configured as output starts low
            return {}, "ok"
        line = k.gpio_lines.get(key)
        if line is None:
            raise HalRequestError("not_configured", "call gpio.configure first")
        if op == "gpio.read":
            if line.direction == "out":
                return {"value": line.value_out}, "ok"
            value = k.encoder.level if (k.encoder is not None and key == k.encoder_line) else (0 if line.bias == "pull_down" else 1)
            return {"value": int(value)}, "ok"
        if op == "gpio.write":
            if line.direction != "out":
                raise HalRequestError("bad_direction", "line is configured as input")
            line.value_out = int(req.get("value", 0))
            k.gpio_written(key, line.value_out)
            return {}, "ok"
        if op == "gpio.count_edges":
            return {"count": line.rising_count, "last_t": line.last_rising_t}, "ok"
        if op == "gpio.wait_edge":
            timeout = float(req.get("timeout", 0.0))
            if not line.pending_edges and timeout > 0:
                deadline = k.t + timeout
                reason = self._advance(lambda: bool(line.pending_edges) or k.t + 1e-9 >= deadline)
                if self._blocked(reason):
                    return {}, reason
            edges = [{"t": round(t, 9), "rising": rising} for t, rising in line.pending_edges if t <= k.t + 1e-12]
            line.pending_edges = [(t, r) for t, r in line.pending_edges if t > k.t + 1e-12]
            return {"edges": edges}, "ok"
        raise HalRequestError("unknown_op", op)

    def _i2c(self, req: dict[str, Any]) -> dict[str, Any]:
        k = self.kernel
        bus = str(req.get("bus"))
        if bus not in k.i2c_devices:
            raise HalRequestError("no_such_device", f"{bus} is not bound")
        addr = int(req.get("addr", -1))
        devices = [d for d in k.i2c_devices[bus] if d.address == addr and d.powered]
        if not devices:
            raise HalRequestError("i2c_nack", f"no device acknowledged address 0x{addr:02x} on {bus}")
        if len(devices) > 1:
            raise HalRequestError("bus_contention", f"{len(devices)} devices answer at 0x{addr:02x}")
        data = devices[0].transfer(bytes(req.get("write") or []), int(req.get("read_len", 0)))
        return {"data": list(data)}

    def _uart(self, op: str, req: dict[str, Any]) -> tuple[dict[str, Any], str]:
        k = self.kernel
        device = str(req.get("device"))
        uart = k.uarts.get(device)
        if uart is None:
            raise HalRequestError("no_such_device", f"{device} is not bound")
        if op == "uart.configure":
            uart.open, uart.baud = True, int(req.get("baud", uart.device_baud))
            uart.rx.clear()
            uart.ready.clear()
            if abs(uart.baud - uart.device_baud) / uart.device_baud > 0.03 and not uart.mismatch_reported:
                uart.mismatch_reported = True
                k.recorder.events.append({"t": k.t, "event": "uart_baud_mismatch", "device": device,
                                          "software_baud": uart.baud, "device_baud": uart.device_baud})
            return {}, "ok"
        if not uart.open:
            raise HalRequestError("not_configured", "call uart.configure first")
        if op == "uart.write":
            data = list(req.get("data") or [])
            uart.written += len(data)
            return {"written": len(data)}, "ok"
        if op == "uart.read":
            maximum = max(1, int(req.get("max", 4096)))
            timeout = float(req.get("timeout", 0.0))
            if k.bytes_available(device) == 0 and timeout > 0:
                deadline = k.t + timeout
                reason = self._advance(lambda: k.bytes_available(device) > 0 or k.t + 1e-9 >= deadline)
                if self._blocked(reason):
                    return {}, reason
            k.bytes_available(device)
            data = bytes(uart.ready[:maximum])
            del uart.ready[:maximum]
            return {"data": list(data)}, "ok"
        raise HalRequestError("unknown_op", op)

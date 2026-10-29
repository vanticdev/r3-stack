"""Minimal r3-hal/1 TCP stub (PROTOCOL.md) shared by the firmware launch tests.

Implements ``hello`` and the ``clock.*`` lockstep ops; every other op gets a
protocol-conformant reply (I2C devices answer ``i2c_nack``).
"""

from __future__ import annotations

import json
import socketserver
import threading


class LockstepStub:
    """Answers r3-hal/1 requests; simulation time moves only on clock.sleep_until."""

    def __init__(self) -> None:
        self.t = 0.0
        self.requests: list[dict] = []
        self.logs: list[dict] = []
        self.done = threading.Event()

    def handle(self, request: dict) -> dict:
        self.requests.append(request)
        op, reply = request["op"], {"id": request["id"], "ok": True}
        if op == "hello":
            assert request.get("protocol") == "r3-hal/1"
            reply.update(computer="pi", bindings={
                "esc_pwm": {"interface": "pwm", "device": "pwmchip0", "channel": 2, "frequency": 50.0},
                "crsf": {"interface": "uart", "device": "/dev/ttyAMA0", "baud": 420000}})
        elif op == "clock.sleep_until":
            self.t = max(self.t, float(request["t"]))
        elif op == "uart.read":
            reply["data"] = []
        elif op == "uart.write":
            reply["written"] = len(request.get("data", []))
        elif op == "gpio.count_edges":
            reply.update(count=0, last_t=0.0)
        elif op == "gpio.read":
            reply["value"] = 0
        elif op == "i2c.transfer":
            reply = {"id": request["id"], "ok": False, "error": "i2c_nack", "message": "no device modelled"}
        elif op == "log":
            self.logs.append(request)
        elif op == "bye":
            self.done.set()
        reply["t"] = self.t
        return reply


def serve(stub: LockstepStub, host: str = "0.0.0.0") -> socketserver.TCPServer:
    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            for line in self.rfile:
                reply = stub.handle(json.loads(line))
                self.wfile.write(json.dumps(reply).encode() + b"\n")
                self.wfile.flush()

    # Docker Desktop forwards host.docker.internal to the host loopback; a Linux
    # engine reaches the host through the bridge gateway, so listen on all interfaces.
    server = socketserver.ThreadingTCPServer((host, 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server

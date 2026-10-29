"""Container smoke test: build the linux/arm64 Debian bookworm image and run the
firmware in it against a minimal in-test r3-hal/1 TCP stub (lockstep clock).

Skipped when Docker is unavailable. The stub implements ``hello`` and the
``clock.*`` ops of PROTOCOL.md; every other op gets a protocol-conformant reply
(I2C devices answer ``i2c_nack``) so the firmware's device-error paths run too.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from firmware_hal_stub import LockstepStub, serve
from r3_hal import container

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "examples/robots/rc-car/backend/python/rc_car_controller"
TAG = "r3/rc-car-controller:smoke-test"

pytestmark = pytest.mark.skipif(not container.docker_available(), reason="Docker is not available")


@pytest.fixture(scope="module")
def image(tmp_path_factory: pytest.TempPathFactory) -> str:
    context = tmp_path_factory.mktemp("container")
    spec = container.spec_from_module(MODULE, context)
    container.write_context(spec, context)
    try:
        container.build_image(context, TAG)
    except subprocess.CalledProcessError:
        pytest.fail("docker build failed:\n" + (context / "docker-build.log").read_text()[-4000:])
    return TAG


def test_image_is_pi_os_userspace(image: str) -> None:
    probe = ("import json, os, platform, sys; print(json.dumps({'machine': platform.machine(), "
             "'python': list(sys.version_info[:2]), 'uid': os.getuid(), 'cwd': os.getcwd(), "
             "'os': open('/etc/os-release').read()}))")
    result = subprocess.run(["docker", "run", "--rm", "--platform", container.PLATFORM, "--entrypoint",
                             "/opt/blitz/.venv/bin/python", image, "-c", probe],
                            capture_output=True, text=True, timeout=120, check=True)
    info = json.loads(result.stdout)
    assert info["machine"] == "aarch64"
    assert info["python"] == [3, 11], "Debian bookworm / Raspberry Pi OS bookworm ship CPython 3.11"
    assert 'VERSION_CODENAME=bookworm' in info["os"] and "ID=debian" in info["os"]
    assert info["uid"] == 1000 and info["cwd"] == "/opt/blitz/backend/python/rc_car_controller"


def test_firmware_runs_in_container_against_tcp_stub(image: str) -> None:
    stub = LockstepStub()
    server = serve(stub)
    port = server.server_address[1]
    command = container.run_command(image, port, extra_args=["--duration", "2.0"])
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    finally:
        server.shutdown()
        server.server_close()
    assert result.returncode == 0, result.stdout + result.stderr
    assert stub.done.is_set(), "firmware closed the session with bye"

    hello = stub.requests[0]
    assert hello["op"] == "hello" and hello["module"] == "rc_car_controller"
    sleeps = [r["t"] for r in stub.requests if r["op"] == "clock.sleep_until"]
    assert len(sleeps) == 200, "100 Hz control loop for 2.0 s of simulated time"
    steps = [b - a for a, b in zip(sleeps, sleeps[1:])]
    assert all(abs(step - 0.01) < 1e-9 for step in steps), "deterministic 10 ms lockstep period"
    assert stub.t == pytest.approx(2.0)

    messages = [entry["message"] for entry in stub.logs]
    assert "boot" in messages and "shutdown" in messages
    failures = {entry["fields"]["device"]: entry["fields"]["code"] for entry in stub.logs
                if entry["message"] == "device_init_failed"}
    assert failures == {"ina226": "i2c_nack", "mpu6050": "i2c_nack"}
    pwm = [r for r in stub.requests if r["op"] == "pwm.configure"]
    assert pwm and all(r["period_ns"] == 20_000_000 and r["duty_ns"] == 1_500_000 for r in pwm), \
        "no radio link: ESC and servo held at neutral"
    uarts = {r["device"]: r["baud"] for r in stub.requests if r["op"] == "uart.configure"}
    assert uarts == {"/dev/ttyAMA0": 420000, "/dev/ttyAMA2": 115200}

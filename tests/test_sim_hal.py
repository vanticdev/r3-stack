import threading
from pathlib import Path

import pytest

from r3_hal import HalError
from r3_hal.virtual import VirtualHal
from r3_model.project import Project
from r3_sim import crsf
from r3_sim.hal_server import TwinHalServer
from r3_sim.kernel import CoSimKernel

RC_CAR = Path(__file__).resolve().parents[1] / "examples" / "robots" / "rc-car"


def _serve(server: TwinHalServer) -> None:
    server.accept(10.0)
    while server.connected:
        status = server.serve_one()
        if status in ("end", "closed", "timeout"):
            break


@pytest.fixture()
def twin():
    project = Project.load(RC_CAR)
    scenario = next(s for s in project.spec.simulation.scenarios if s.id == "full_throttle")
    kernel = CoSimKernel(project, scenario)
    server = TwinHalServer(kernel, request_timeout=2.0)  # r3_hal close() leaves its socket open; do not wait long
    thread = threading.Thread(target=_serve, args=(server,), daemon=True)
    thread.start()
    hal = VirtualHal(server.endpoint, module="test")
    yield kernel, hal
    hal.close()
    thread.join(5.0)
    server.close()


def test_lockstep_round_trip(twin):
    kernel, hal = twin
    assert hal.computer == "pi"
    assert hal.bindings()["esc_pwm"]["channel"] == 2
    assert hal.clock.now() == 0.0
    hal.clock.sleep_until(0.05)
    assert hal.clock.now() == pytest.approx(0.05) and kernel.t == pytest.approx(0.05)
    # Requests that do not block leave simulation time alone.
    i2c = hal.i2c("/dev/i2c-1")
    assert i2c.read_reg(0x68, 0x75, 1) == b"\x68"
    assert int.from_bytes(i2c.read_reg(0x40, 0xFE, 2), "big") == 0x5449
    assert kernel.t == pytest.approx(0.05)
    with pytest.raises(HalError) as nack:
        i2c.read_reg(0x29, 0x00, 1)
    assert nack.value.code == "i2c_nack"
    esc = hal.pwm("pwmchip0", 2)
    esc.set_pulse_us(1600.0)
    assert kernel.pwm_state[("pwmchip0", 2)]["duty_ns"] == 1_600_000
    with pytest.raises(HalError) as unbound:
        hal.pwm("pwmchip0", 1).set_pulse_us(1500)
    assert unbound.value.code == "pin_not_bound"
    hal.clock.sleep(0.01)
    assert kernel.esc_pulse == pytest.approx(1600.0)


def test_uart_read_blocks_until_crsf_frame(twin):
    kernel, hal = twin
    uart = hal.uart("/dev/ttyAMA0", 420000)
    t0 = hal.clock.now()
    data = uart.read(512, timeout=0.1)
    assert data and hal.clock.now() - t0 < 0.02  # 150 Hz packets: first frame within one period
    hal.clock.sleep(0.1)
    frames = crsf.CrsfParser().feed(data + uart.read(4096, 0.0))
    kinds = {kind for kind, _ in frames}
    assert crsf.TYPE_RC_CHANNELS_PACKED in kinds and crsf.TYPE_LINK_STATISTICS in kinds


def test_gpio_encoder_binding(twin):
    kernel, hal = twin
    line = hal.gpio("gpiochip0", 17, "in", "pull_up", "rising")
    assert line.read() in (0, 1)
    assert line.count_edges() == (0, 0.0)
    with pytest.raises(HalError) as unbound:
        hal.gpio("gpiochip0", 4)
    assert unbound.value.code == "pin_not_bound"


def test_brownout_reported_to_software(twin):
    kernel, hal = twin
    original = kernel.electrical.step

    def failing(dt, inputs):
        out = original(dt, inputs)
        if kernel.t > 0.02:
            out[kernel.computer]["powered"] = False
            out["_nets"]["5V"] = 3.9
        return out

    kernel.electrical.step = failing
    with pytest.raises(HalError) as error:
        hal.clock.sleep_until(0.2)
    assert error.value.code == "brownout"
    assert kernel.brownout_times and kernel.brownout_times[0] == pytest.approx(0.021, abs=0.002)
    assert kernel.pwm_state == {}

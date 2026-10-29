"""The KiCad carrier HAT applied across R3: catalog/graph semantics of the failsafe arbiter, the twin's
arbiter device and heartbeat path, the HAT's two regulators in ngspice, the TVS check and the failsafe chain."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from r3_hal.virtual import VirtualHal
from r3_kicad.arbiter import ArbiterParams
from r3_kicad.spice import TVS_PARTS, tvs_clamp
from r3_model.diagnostics import StageResult
from r3_model.graph import ACTIVE_SIGNAL_LINKS, SIGNAL_LINK_KINDS
from r3_model.project import Project
from r3_sim.devices import ArbiterDevice, arbiter_params
from r3_sim.electrical.ngspice import find_library
from r3_sim.hal_server import TwinHalServer
from r3_sim.kernel import CoSimKernel
from r3_sim.signals import map_bindings

RC_CAR = Path(__file__).resolve().parents[1] / "examples" / "robots" / "rc-car"
needs_ngspice = pytest.mark.skipif(find_library() is None, reason="libngspice not installed")


@pytest.fixture(scope="module")
def project():
    return Project.load(RC_CAR)


# ------------------------------------------------------------------ catalog, graph, model stage
def test_catalog_hat_matches_the_board(project):
    hat = project.catalog.component("r3-carrier-hat")
    assert hat.connectors["J_SERVO"].pins["2"] == "SERVO_6V"
    assert hat.pins["ESC_BEC_NC"].role == "nc" and hat.connectors["J_ESC"].pins["2"] == "ESC_BEC_NC"
    assert hat.connectors["J_GPIO"].pins["13"] == "HEARTBEAT"
    link = next(link for link in hat.internal_links if link.a == "PI_ESC_PWM")
    assert link.kind == "failsafe_arbiter" and link.value == pytest.approx(0.1)
    params = hat.behavior.params
    assert params["tvs"]["part"] == "SMAJ10A" and params["servo_regulator"]["max_duty"] == pytest.approx(0.8)


def test_arbiter_link_carries_the_signal_but_no_dc(project):
    assert "failsafe_arbiter" in SIGNAL_LINK_KINDS and "failsafe_arbiter" in ACTIVE_SIGNAL_LINKS
    graph = project.graph
    # Signal continuity (like a series resistor): the Pi's GPIO18 reaches the ESC signal input through U4 ...
    assert graph.connected("pi.J8.12", "esc.J_SIG.1", through_internal=True)
    # ... but the two sides are different nets (an active element re-drives the output).
    assert not graph.connected("pi.J8.12", "esc.J_SIG.1")
    from r3_sim.electrical.network import build_passive_network

    network = build_passive_network(project, None)
    links = network.internal_links.get("hat", [])
    assert not [b for b in links if b.meta.get("link_kind") == "failsafe_arbiter"]
    assert [b for b in links if b.meta.get("link_kind") == "series_resistor"]


def test_esc_bec_is_not_wired_and_servo_runs_from_the_hat(project):
    wires = {w.id: w for h in project.spec.electrical.harnesses for w in h.wires}
    assert "W_ESC_BEC" not in wires and wires["W_SRV_VCC"].signal == "SERVO_6V"
    assert project.graph.connected("servo.J1.2", "hat.SERVO_6V")
    assert not project.graph.connected("servo.J1.2", "esc.J_SIG.2")


def test_model_stage_understands_the_arbiter_and_failsafe_chain(project):
    from r3_orchestrator import model_stage

    result = model_stage.run(project)
    assert not [d for d in result.diagnostics if d.severity.value == "error"], [d.message for d in result.diagnostics]
    chain = next(d for d in result.diagnostics if d.code == "failsafe_chain")
    timeouts = list(chain.data.values())
    assert timeouts == sorted(timeouts) and timeouts[0] == pytest.approx(0.12) and timeouts[-1] == pytest.approx(1.0)


def test_failsafe_chain_flags_a_receiver_that_holds(project):
    from r3_orchestrator.model_stage import ModelChecker, check_failsafe_chain

    data = project.spec.model_dump(mode="json")
    data["components"]["receiver"]["params"] = {"failsafe_mode": "hold"}
    data["software"]["config"]["radio_failsafe_timeout_s"] = 1.5
    from r3_model.spec import RobotSpec

    variant = Project(root=project.root, spec=RobotSpec.model_validate(data), lock=project.lock)
    result = StageResult(stage="model")
    check_failsafe_chain(ModelChecker(variant, result))
    codes = {d.code for d in result.diagnostics}
    assert {"receiver_failsafe_not_no_pulses", "failsafe_chain_order"} <= codes


def test_heartbeat_binding_reaches_the_board(project):
    bindings = map_bindings(project)
    assert [(a.component, a.pin) for a in bindings["heartbeat"].attachments] == [("hat", "HEARTBEAT")]
    assert [a.component for a in bindings["esc_pwm"].attachments] == ["esc"]


# ------------------------------------------------------------------ arbiter device (twin)
def _device(**overrides) -> ArbiterDevice:
    return ArbiterDevice("hat", arbiter_params({"timeout": 0.1, "frame": 0.02, "neutral_us": 1500, "rearm_edges": 3,
                                                "rearm_band_us": 50, **overrides}))


def _run(device: ArbiterDevice, t0: float, t1: float, pulse: float, beat: bool, dt: float = 0.001,
         period: float = 0.01) -> list[tuple[float, float]]:
    out = []
    steps = int(round((t1 - t0) / dt))
    for k in range(steps):
        t = t0 + k * dt
        if beat and k % int(round(period / dt)) == 0:
            device.heartbeat_write(t, 0 if device.level else 1)
        out.append((t, device.output(t, pulse)))
    return out


def test_arbiter_params_from_catalog(project):
    params = arbiter_params(project.catalog.component("r3-carrier-hat").behavior.params["failsafe"])
    assert params == ArbiterParams(part="ATtiny412", timeout=0.1, neutral_us=1500.0, frame=0.02, rearm_edges=3,
                                   rearm_band_us=50.0, brake_us=1000.0, brake_hold=3.0, deadband_us=15.0,
                                   esc_reverse_delay=0.1)


def test_arbiter_boots_neutral_and_rearms_only_at_neutral():
    device = _device()
    trace = _run(device, 0.0, 0.2, 1800.0, beat=True)
    assert all(pulse == 1500.0 for _, pulse in trace)  # a non-neutral command never passes on re-arm
    trace = _run(device, 0.2, 0.3, 1500.0, beat=True)
    assert not device.failsafe and trace[-1][1] == 1500.0
    trace = _run(device, 0.3, 0.4, 1800.0, beat=True)
    assert all(pulse == 1800.0 for _, pulse in trace)


def test_arbiter_brakes_within_timeout_plus_one_frame_after_the_last_edge_then_neutral():
    device = _device(brake_hold=1.0)
    _run(device, 0.0, 0.5, 1500.0, beat=True)
    _run(device, 0.5, 1.0, 1800.0, beat=True)
    last_edge = device.last_edge_t
    trace = _run(device, 1.0, 2.5, 1800.0, beat=False)     # main loop hung: PWM keeps 1800 us, no heartbeat
    first = next(t for t, pulse in trace if pulse != 1800.0)
    assert 0.1 <= first - last_edge <= 0.12 + 1e-9
    assert {pulse for t, pulse in trace if first <= t < first + 1.0 - 1e-9} == {1000.0}  # active brake
    assert {pulse for t, pulse in trace if t >= first + 1.0 + 1e-9} == {1500.0}           # then neutral
    event = next(e for e in device.events if e["event"] == "arbiter_failsafe")
    assert event["last_heartbeat_edge_t"] == pytest.approx(last_edge)
    assert event["brake_until"] == pytest.approx(first + 1.0)
    assert any(e["event"] == "arbiter_brake_end" for e in device.events)


def test_unpowered_arbiter_outputs_no_pulses_and_reboots_in_failsafe():
    device = _device()
    _run(device, 0.0, 0.3, 1500.0, beat=True)
    assert not device.failsafe
    device.set_powered(0.3, False)
    assert device.output(0.31, 1600.0) == 0.0
    device.set_powered(0.5, True)
    assert device.failsafe and device.output(0.52, 1600.0) == 1500.0


def test_heartbeat_gpio_writes_reach_the_arbiter_through_the_hal(rc_car_fresh):
    project = Project.load(rc_car_fresh)  # no stale build/wiring cables in the kernel
    scenario = next(s for s in project.spec.simulation.scenarios if s.id == "pi_hang")
    kernel = CoSimKernel(project, scenario)
    assert kernel.freeze_at == pytest.approx(15.0)
    server = TwinHalServer(kernel, request_timeout=2.0)

    def serve() -> None:
        server.accept(10.0)
        while server.connected and server.serve_one() == "ok":
            pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    hal = VirtualHal(server.endpoint, module="test")
    try:
        device = kernel.heartbeat_lines[("gpiochip0", 27)]
        assert kernel.arbiters[("pwmchip0", 2)] is device
        esc = hal.pwm("pwmchip0", 2)
        beat = hal.gpio("gpiochip0", 27, direction="out")
        level = 0
        for k in range(30):
            esc.set_pulse_us(1500.0)
            level ^= 1
            beat.write(level)
            hal.clock.sleep_until(0.01 * (k + 1))
        assert device.edges >= 29 and not device.failsafe
        esc.set_pulse_us(1700.0)
        hal.clock.sleep_until(0.5)                         # no heartbeat for 200 ms
        # After forward driving the arbiter brakes first (brake_us), then holds neutral.
        assert device.failsafe and kernel.esc_pulse == pytest.approx(device.params.brake_us)
    finally:
        hal.close()
        thread.join(5.0)
        server.close()


# ------------------------------------------------------------------ regulators and TVS (ngspice)
@needs_ngspice
def test_hat_regulators_follow_catalog_uvlo_dropout_and_limits(project):
    from r3_sim.electrical import ElectricalSystem

    system = ElectricalSystem(project, routes={})
    hat = system.models["hat"]
    pi_core, servo_core = hat.cores["regulator"], hat.cores["servo_regulator"]
    assert (pi_core.uvlo_falling, pi_core.uvlo_rising) == (pytest.approx(3.8), pytest.approx(4.4))
    assert pi_core.current_limit == pytest.approx(8.1) and servo_core.current_limit == pytest.approx(5.5)
    assert pi_core.hold_up == pytest.approx(268e-6)
    # TPS56528 dropout: D_max V_in - I (D Rhs + (1 - D) Rls + DCR) = 6.05 V at 2.5 A needs ~7.84 V in.
    assert servo_core.dropout_input(2.5) == pytest.approx(7.84, abs=0.02)
    inputs = {"esc": {"throttle": 0.0}, "motor": {"omega": 0.0}, "servo": {"horn_torque": 0.0, "horn_speed": 0.0},
              "pi": {"activity": 0.5}}
    full = system.operating_point(inputs, soc=1.0)["hat"]
    empty = system.operating_point(inputs, soc=0.0)["hat"]
    assert full["servo_regulator"]["v_out"] == pytest.approx(6.05, abs=0.01) and not full["servo_regulator"]["in_dropout"]
    # Near-empty 2S: the servo rail follows 0.8 V_in, the Pi rail still regulates (low UVLO).
    assert empty["servo_regulator"]["in_dropout"]
    assert empty["servo_regulator"]["v_out"] == pytest.approx(0.8 * empty["v_in"], abs=0.1)
    assert empty["v_out"] == pytest.approx(5.1, abs=0.02) and not empty["uvlo"]
    # INA226 sees the whole HAT branch (both regulators), VBUS on the shunt's load side.
    assert empty["ina226_current"] == pytest.approx(empty["current_in"]) and empty["ina226_bus_voltage"] < empty["v_in"]
    system.close()


@needs_ngspice
def test_hold_up_keeps_the_pi_rail_after_the_input_drops(project):
    """HAT supply lead open: the 268 uF hold-up carries the 5 V rail to the Pi's 4.63 V brown-out in
    t = C (V0^2 - V1^2) / (2 P) (the pcb stage's hold_up_time), then the Pi resets and its 3V3 rail turns off."""
    from r3_kicad.regulators import hold_up_time
    from r3_sim.electrical import ElectricalSystem

    system = ElectricalSystem(project, routes={}, initial={"battery_soc": 0.9})
    inputs = {"esc": {"pulse_us": 1500.0}, "motor": {"omega": 0.0}, "servo": {"pulse_us": 1500.0}, "pi": {"activity": 0.5}}
    for _ in range(5):
        out = system.step(1e-3, inputs)
    v0, load = out["_nets"]["5V"], out["hat"]["regulator"]["power_out"]
    dt, t_fault = 2e-5, system.time
    out = system.step(dt, {**inputs, "_faults": [{"type": "open", "branch": "W_Y_P_HAT"}]})
    while out["pi"]["powered"]:
        out = system.step(dt, inputs)
        assert system.time - t_fault < 0.01
    held = system.time - t_fault
    expected = hold_up_time(268e-6, v0, 4.63, load)
    assert held == pytest.approx(expected, rel=0.3), (held, expected, load)
    for _ in range(20):  # the reset Pi's 3V3 rail switches off cleanly while the hold-up still decays
        out = system.step(1e-4, inputs)
    assert out["_nets"]["3V3"] < 0.1 and 0.5 < out["_nets"]["5V"] < 4.63
    system.close()


def test_tvs_clamp_line_and_choice():
    # SMAJ10A: VC 17.0 V at I_PP 23.5 A; 16.3 V at the 20 A expected surge (2 x the 10 A fuse).
    assert tvs_clamp("SMAJ10A", 23.5) == pytest.approx(17.0)
    assert tvs_clamp("SMAJ10A", 20.0) == pytest.approx(16.3, abs=0.01)
    assert tvs_clamp("SMAJ16A", 15.4) == pytest.approx(26.0)
    assert TVS_PARTS["SMAJ10A"]["v_rwm"] > 8.4  # a full 2S pack stays below the stand-off


@needs_ngspice
def test_electrical_stage_tvs_check(project):
    from r3_sim.electrical import ElectricalSystem
    from r3_sim.electrical.stage import check_tvs

    system = ElectricalSystem(project, routes={})
    result = StageResult(stage="electrical")
    report = check_tvs(system, result)["hat"]
    assert report["expected_surge_current"] == pytest.approx(20.0) and report["clamp_at_expected_surge"] < report["abs_max"]
    assert not [d for d in result.diagnostics if d.code == "tvs_clamp_exceeds_abs_max"]
    system.models["hat"].params.values["tvs"] = {"part": "SMAJ16A", "v_breakdown_max": 19.7, "v_clamp": 26.0, "i_pp": 15.4}
    result = StageResult(stage="electrical")
    check_tvs(system, result)
    assert [d for d in result.diagnostics if d.code == "tvs_clamp_exceeds_abs_max"]
    system.close()


# ------------------------------------------------------------------ pour cross-section (IPC-2152 on filled copper)
def test_pour_bottleneck_width_measures_the_narrowest_cross_section():
    from r3_kicad.pours import bottleneck_width

    strip = [(0, 0), (10, 0), (10, 3), (0, 3)]
    necked = [(0, 0), (4.5, 0), (4.5, 1), (5.5, 1), (5.5, 0), (10, 0), (10, 3), (5.5, 3), (5.5, 2), (4.5, 2), (4.5, 3), (0, 3)]
    ends = [[(0, 0, 0.5, 3)], [(9.5, 0, 10, 3)]]
    assert bottleneck_width([[strip]], ends) == pytest.approx(3.0, abs=0.05)
    assert bottleneck_width([[necked]], ends) == pytest.approx(1.0, abs=0.05)
    # a hole in the strip leaves two 1 mm channels; the widest path uses one of them
    holed = [strip, [(3, 1), (7, 1), (7, 2), (3, 2)]]
    assert bottleneck_width([holed], ends) == pytest.approx(1.0, abs=0.05)
    assert bottleneck_width([[[(0, 0), (1, 0), (1, 1), (0, 1)]], [[(3, 0), (4, 0), (4, 1), (3, 1)]]],
                            [[(0, 0, 0.2, 1)], [(3.8, 0, 4, 1)]]) == 0.0

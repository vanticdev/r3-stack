"""Model stage: the unmodified bench and injected faults."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from orchestrator_helpers import catalog_entry, codes, find, make_project, wire, write_bench_pull
from r3_model.project import Project
from r3_orchestrator import model_stage
from r3_orchestrator.geometry import make_solid, overlap

ELECTRICAL_CODES = {
    "binding_not_connected", "binding_pin_function_missing", "binding_pwm_channel_mismatch", "binding_device_mismatch",
    "binding_gpio_line_mismatch", "uart_tx_tx_conflict", "input_without_driver", "power_conflict", "power_ground_short",
    "power_in_voltage_out_of_range", "power_in_overvoltage", "logic_level_overvoltage", "i2c_address_conflict",
    "connector_mate_mismatch", "wire_gauge_connector_mismatch", "connector_multiply_mated",
}


def run(root):
    write_bench_pull(root)
    return model_stage.run(Project.load(root))


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    root = make_project(tmp_path_factory.mktemp("baseline"))
    return root, run(root)


def test_bench_electrical_and_bindings_clean(baseline):
    _, result = baseline
    assert not (codes(result) & ELECTRICAL_CODES), [d.message for d in result.diagnostics if d.code in ELECTRICAL_CODES]
    assert "mass_exceeds_requirement" not in codes(result)
    assert result.metrics["total_mass"] < result.metrics["mass_limit"]


def test_bench_layout_interference(baseline):
    _, result = baseline
    pairs = {frozenset(d.subjects) for d in find(result, "cad_interference")}
    assert frozenset({"block_a", "block_b"}) in pairs
    for d in find(result, "cad_interference"):
        assert d.data["intersection_volume"] > 0


def test_graph_json_written(baseline):
    root, result = baseline
    graph = json.loads((root / "build" / "model" / "graph.json").read_text())
    assert set(graph["components"]) >= {"pi", "controller", "esc", "battery"}
    assert graph["wires"]["W_ESC_SIG"]["harness"] == "esc_signal"
    assert graph["nets"] and graph["edges"]
    assert {r["source"] for r in graph["rails"]} >= {"battery.POS", "controller.5V", "pi.3V3", "esc.BEC"}
    assert any({d["address"] for d in bus["devices"]} == {0x40, 0x68} for bus in graph["i2c_buses"])
    assert (root / "build" / "model" / "layout.png").stat().st_size > 10_000
    assert {a.path for a in result.artifacts} == {"model/graph.json", "model/layout.png", "model/connections.json"}


def test_uart_tx_rx_swap(tmp_path):
    def swap(spec):
        wire(spec, "W_RX_PI_TX")["from"] = "A.2"  # receiver TX -> HAT CRSF_TX (Pi TX): TX meets TX
        wire(spec, "W_RX_PI_RX")["from"] = "A.3"

    result = run(make_project(tmp_path, swap))
    assert {"binding_not_connected", "uart_tx_tx_conflict", "input_without_driver"} <= codes(result)
    conflict = find(result, "uart_tx_tx_conflict")[0]
    assert set(conflict.subjects) == {"pi.GPIO14", "receiver.TX"}
    unreached = find(result, "binding_not_connected")
    assert {d.subjects[0] for d in unreached} == {"crsf"}
    assert "receiver.TX" in unreached[0].data["net_members"]
    undriven = {d.subjects[0] for d in find(result, "input_without_driver")}
    assert undriven == {"pi.GPIO15", "receiver.RX"}


def test_esc_pwm_on_gpio27(tmp_path):
    def move(spec):
        spec["software"]["bindings"]["esc_pwm"]["pins"] = ["pi.J8.13"]

    result = run(make_project(tmp_path, move))
    missing = find(result, "binding_pin_function_missing")
    assert missing and missing[0].subjects == ["esc_pwm", "pi.GPIO27"]
    assert missing[0].data["functions"] == ["gpio"]
    assert {d.subjects[0] for d in find(result, "binding_not_connected")} == {"esc_pwm"}


def test_pwm_channel_mismatch(tmp_path):
    result = run(make_project(tmp_path, lambda s: s["software"]["bindings"]["esc_pwm"].update(channel=3)))
    mismatch = find(result, "binding_pwm_channel_mismatch")
    assert mismatch and mismatch[0].data == {"binding_channel": 3, "pin_channel": 2}


def test_uart_device_mismatch(tmp_path):
    result = run(make_project(tmp_path, lambda s: s["software"]["bindings"]["gps"].update(device="/dev/ttyAMA0")))
    assert find(result, "binding_device_mismatch")[0].data["expected"] == "/dev/ttyAMA2"


def test_duplicate_i2c_address(tmp_path):
    controller = catalog_entry("test-controller")
    controller["software"].append({"bus": "i2c", "address": 0x40, "protocol": "ina226"})  # second monitor, default strap
    result = run(make_project(tmp_path, catalog_components=[controller]))
    conflict = find(result, "i2c_address_conflict")
    assert len(conflict) == 1 and conflict[0].data == {"address": 0x40, "devices": 2}


def test_component_overlap_volume(tmp_path):
    def mutate(spec):
        spec["components"]["gps"]["type"] = "test-panel"
        # Keep the exact-volume oracle at the local origin, avoiding float32
        # cancellation from translating a 4 mm plate 300 mm in the mesh boolean.
        spec["components"]["gps"]["pose"] = {"xyz": [0, 0, 0]}
        spec["components"]["block_a"]["pose"] = {"xyz": [0, 0, 0]}
    result = run(make_project(tmp_path, mutate))
    hits = [d for d in find(result, "cad_interference") if set(d.subjects) == {"gps", "block_a"}]
    assert hits and hits[0].data["intersection_volume"] == pytest.approx(.09 * .09 * .004, rel=1e-6)


def test_logic_level_overvoltage(tmp_path):
    gps = catalog_entry("gnss-rtk-zed-f9p")
    gps["pins"]["TX"]["logic_level"] = 5.0  # a 5 V-logic GNSS module into the Pi's 3.3 V UART RX
    result = run(make_project(tmp_path, catalog_components=[gps]))
    over = find(result, "logic_level_overvoltage")
    assert over and set(over[0].subjects) == {"gps.TX", "pi.GPIO5"}
    assert over[0].data["driver_level"] == 5.0 and over[0].data["receiver_limit"] == pytest.approx(3.6)


def test_3v3_into_tolerant_inputs_is_fine(baseline):
    _, result = baseline
    assert not find(result, "logic_level_overvoltage")


def test_power_conflict(tmp_path):
    controller = catalog_entry("test-controller")
    controller["pins"]["BEC_6V"]["role"] = "power_out"  # HAT also sourcing the 6 V servo rail the ESC BEC drives
    result = run(make_project(tmp_path, catalog_components=[controller]))
    conflict = find(result, "power_conflict")
    assert conflict and set(conflict[0].subjects) == {"esc.BEC", "controller.BEC_6V"}


def test_supply_out_of_range(tmp_path):
    servo = catalog_entry("servo-digital-20kg")
    servo["pins"]["VCC"].update(voltage_min=4.0, voltage_max=5.5)  # a 5 V-only servo on the 6 V BEC rail
    result = run(make_project(tmp_path, catalog_components=[servo]))
    bad = find(result, "power_in_voltage_out_of_range")
    assert bad and bad[0].subjects == ["servo.VCC", "controller.SERVO_6V"] and bad[0].data["rail_nominal"] == 6.0


def test_wire_gauge_vs_connector(tmp_path):
    result = run(make_project(tmp_path, lambda s: wire(s, "W_RX_5V").update(awg=22)))
    bad = find(result, "wire_gauge_connector_mismatch")
    assert bad and bad[0].data == {"awg": 22, "awg_min": 26, "awg_max": 30}


def test_mass_requirement(tmp_path):
    result = run(make_project(tmp_path, lambda s: s["requirements"].update(max_mass="1.2 kg")))
    over = find(result, "mass_exceeds_requirement")[0]
    assert over.data["limit"] == pytest.approx(1.2) and over.data["mass"] > 1.5
    assert "base" in over.subjects


def test_geometry_box_and_cylinder_volumes():
    a = make_solid("a", "box", [1, 1, 1], "z", [0, 0, 0], [0, 0, 0])
    b = make_solid("b", "box", [1, 1, 1], "z", [0.5, 0, 0], [0, 0, 0])
    assert overlap(a, b)["volume"] == pytest.approx(0.5)
    touching = make_solid("c", "box", [1, 1, 1], "z", [1.0, 0, 0], [0, 0, 0])
    assert overlap(a, touching) is None
    rotated = make_solid("d", "box", [1, 1, 1], "z", [0, 0, 0], [0, 0, math.pi / 4])
    # Square rotated 45 deg inside an equal square: intersection is a regular octagon, area 2(sqrt2 - 1).
    assert overlap(a, rotated)["volume"] == pytest.approx(2 * (math.sqrt(2) - 1), rel=1e-9)
    cylinder = make_solid("e", "cylinder", [1, 2], "y", [0, 0, 0], [0, 0, 0])
    assert cylinder.volume == pytest.approx(math.pi * 0.25 * 2, rel=4e-3)
    z_cylinder = make_solid("f", "cylinder", [0.1, 1], "z", [0, 0, 0], [0, 0, 0])
    assert np.allclose([z_cylinder.aabb[0][2], z_cylinder.aabb[1][2]], [0, 1])


def test_pulled_step_preserves_a_concave_notch(tmp_path):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.gp import gp_Pnt
    from pulled_fixture import write_pull

    root = make_project(tmp_path, lambda spec: spec.update(
        components={"plate": {"type": "test-base"}, "probe": {"type": "test-panel"}},
        mechanical={}, electrical={}, software={}, pcb={}))
    # A real STEP solid with a 20 x 20 mm notch; its AABB overlaps the probe.
    plate = BRepPrimAPI_MakeBox(100, 100, 4).Shape()
    notch = BRepPrimAPI_MakeBox(gp_Pnt(80, 80, -1), 21, 21, 6).Shape()
    plate = BRepAlgoAPI_Cut(plate, notch).Shape()
    probe = BRepPrimAPI_MakeBox(gp_Pnt(85, 85, 0), 10, 10, 4).Shape()
    write_pull(root, {"plate": plate, "probe": probe})
    result = model_stage.run(Project.load(root))
    assert not find(result, "cad_interference")
    assert result.metrics["exact_solids"] == 2
    # Put the probe into real material: the exact STEP intersection must fail.
    probe = BRepPrimAPI_MakeBox(gp_Pnt(10, 10, 0), 10, 10, 4).Shape()
    write_pull(root, {"plate": plate, "probe": probe})
    hit, = find(model_stage.run(Project.load(root)), "cad_interference")
    assert hit.data["intersection_volume"] == pytest.approx(10 * 10 * 4 * 1e-9, rel=1e-6)


def test_model_requires_native_pull_before_graph_checks(tmp_path):
    root = make_project(tmp_path, lambda spec: wire(spec, "W_RX_5V").update(awg=22))
    result = model_stage.run(Project.load(root))
    assert not result.ok and codes(result) == {"cad_pull_missing"}
    assert not (root / "build/model/graph.json").exists()

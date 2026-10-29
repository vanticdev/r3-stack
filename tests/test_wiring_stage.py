"""Wiring stage on the reference RC car: artifacts, contract format, and real findings."""

import csv
import json
import shutil
from pathlib import Path

import pytest

from r3_model.project import Project
from r3_wiring.stage import format_diagnostics, run

RC_CAR = Path(__file__).resolve().parents[1] / "examples" / "robots" / "rc-car"


@pytest.fixture(scope="module")
def stage(tmp_path_factory):
    root = tmp_path_factory.mktemp("rc-car")
    shutil.copy(RC_CAR / "robot.yaml", root / "robot.yaml")
    project = Project.load(root)
    result = run(project)
    print("\n" + format_diagnostics(result))
    print("harness lengths (mm):", {k: round(v * 1e3, 1) for k, v in result.metrics["harness_length"].items()})
    print("worst static clearance:", result.metrics["worst_static_clearance"])
    print("worst dynamic clearance:", result.metrics["worst_dynamic_clearance"])
    return root, result


def test_routes_json_contract(stage):
    root, result = stage
    routes = json.loads((root / "build" / "wiring" / "routes.json").read_text())
    assert set(routes) >= {"wires", "harnesses", "clearances", "release"}  # contract keys + additive release gate
    spec_wires = {w.id for h in Project.load(root).spec.electrical.harnesses for w in h.wires}
    assert set(routes["wires"]) == spec_wires
    for wire in routes["wires"].values():
        assert set(wire) >= {"harness", "awg", "path", "routed_length", "cut_length", "valid", "violations",
                             "installed_ampacity", "cut"}
        assert len(wire["path"]) >= 2 and all(len(p) == 3 for p in wire["path"])
        assert wire["cut_length"] >= wire["routed_length"] > 0
    for harness in routes["harnesses"].values():
        assert set(harness) >= {"bundle_path", "length", "tie_points", "valid", "violations", "supports"}
    assert routes["clearances"] and set(routes["clearances"][0]) == {"wire", "component", "min_distance"}


def test_artifacts_written(stage):
    root, result = stage
    out = root / "build" / "wiring"
    for name in ("routes_iso.png", "routes_top_side.png", "slack_peak_bump.png", "slack_clearance_timeline.png",
                 "harness_bom.csv", "slack_sim.json", "routing_detail.json", "result.json", "supports_iso.png",
                 "planned_routes.png", "geometry_audit.json", "cut_lengths.json", "bundles.json",
                 "manufacturing/manifest.json", "manufacturing/power_y.svg", "manufacturing/cut_list.csv"):
        assert (out / name).stat().st_size > 0, name
    rows = list(csv.DictReader((out / "harness_bom.csv").open()))
    assert {r["section"] for r in rows} >= {"wire", "connector", "splice", "tie", "total"}
    assert result.inputs["robot_yaml_sha256"]


def test_rc_car_findings_are_structured(stage):
    root, result = stage
    detail = json.loads((root / "build" / "wiring" / "routing_detail.json").read_text())
    assert {c["connector"] for c in detail["connector_clearances"]} >= {"esc_motor.BP", "esc_motor.BN"}
    codes = {d.code for d in result.diagnostics}
    assert "cad_geometry_estimated" in codes and "harness_mass" in codes
    for d in result.diagnostics:
        assert d.stage == "wiring" and d.data, d.code
    # Connector bodies are measured against moving parts (the motor's bullets no longer face the
    # right rear wheel since the motor moved inboard); any violation carries its numbers and witnesses.
    for d in result.diagnostics:
        if d.code == "connector_clearance_violation":
            assert d.data["min_distance"] < d.data["required"] and "witness_part" in d.data
        if d.code in ("wire_clearance_violation", "wire_intersects_component"):
            assert "witness_wire" in d.data and d.data["representations_agree"], d.message
    assert not [d for d in result.diagnostics if d.code in ("wire_route_failed", "connector_wire_gauge_mismatch")]
    assert result.metrics["worst_dynamic_clearance"]["wire"]

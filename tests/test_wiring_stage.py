"""Wiring stage on the reference bench: artifacts, contract format, and real findings."""

import csv
import json

import pytest

from r3_model.project import Project



@pytest.fixture(scope="module")
def stage(bench_wiring):
    """The full stage on a fresh copy of the bench (shared session run, see conftest.py)."""
    print("worst static clearance:", bench_wiring.result.metrics["worst_static_clearance"])
    print("worst dynamic clearance:", bench_wiring.result.metrics["worst_dynamic_clearance"])
    return bench_wiring.root, bench_wiring.result


def test_routes_json_contract(stage):
    root, result = stage
    routes = json.loads((root / "build" / "wiring" / "routes.json").read_text())
    assert set(routes) >= {"wires", "harnesses", "clearances", "release"}  # contract keys + additive release gate
    spec_wires = {w.id for h in Project.load(root).spec.electrical.harnesses for w in h.wires}
    assert set(routes["wires"]) == spec_wires
    for wire in routes["wires"].values():
        assert set(wire) >= {"harness", "awg", "path", "routed_length", "cut_length", "ends", "fanout",
                             "valid", "violations", "installed_ampacity", "cut"}
        assert len(wire["ends"]) == 2 and len(wire["fanout"]) == 2
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
                 "manufacturing/manifest.json", "manufacturing/ysplit.svg", "manufacturing/cut_list.csv"):
        assert (out / name).stat().st_size > 0, name
    rows = list(csv.DictReader((out / "harness_bom.csv").open()))
    assert {r["section"] for r in rows} >= {"wire", "connector", "splice", "tie", "total"}
    assert result.inputs["robot_yaml_sha256"]


def test_bench_findings_are_structured(stage):
    root, result = stage
    codes = {d.code for d in result.diagnostics}
    assert "cad_geometry_estimated" not in codes and "harness_mass" in codes
    for d in result.diagnostics:
        assert d.stage == "wiring" and d.data, d.code
    required = Project.load(root).spec.requirements.min_wire_clearance_moving
    clearance = [d for d in result.diagnostics if d.code in ("wire_clearance_violation", "connector_clearance_violation")]
    assert all(d.data["required"] == pytest.approx(required) and d.data["min_distance"] < required for d in clearance)
    # Every measured connector clearance below the requirement is reported, and only those.
    detail = json.loads((root / "build" / "wiring" / "routing_detail.json").read_text())
    measured = {(c["connector"], c["component"]) for c in detail["connector_clearances"] if c["min_distance"] < required}
    reported = {(d.subjects[1], d.data["component"]) for d in clearance if d.code == "connector_clearance_violation"}
    assert reported == measured
    expected_bodies = {f"{h.id}.{name}" for h in Project.load(root).spec.electrical.harnesses
                       for name, connector in h.connectors.items() if connector.type != "integral"}
    assert expected_bodies <= {c["connector"] for c in detail["connector_clearances"]}
    # All declared connector bodies are measured against moving parts; any
    # violation carries its numbers and witnesses, regardless of part choice.
    for d in result.diagnostics:
        if d.code == "connector_clearance_violation":
            assert d.data["min_distance"] < d.data["required"] and "witness_part" in d.data
        if d.code in ("wire_clearance_violation", "wire_intersects_component"):
            assert "witness_wire" in d.data and d.data["representations_agree"], d.message
    assert not [d for d in result.diagnostics if d.code in ("wire_route_failed", "connector_wire_gauge_mismatch")]
    assert result.metrics["worst_dynamic_clearance"]["wire"]

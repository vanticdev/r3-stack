"""Run the electrical stage on the reference bench and print its diagnostics."""

from pulled_fixture import instrumentation_pull

import json
import shutil

import pytest

from conftest import copy_bench
from r3_model.project import Project
from r3_sim.electrical.stage import run
from r3_sim.electrical.ngspice import find_library

pytestmark = pytest.mark.skipif(find_library() is None, reason="libngspice not installed")


def _run_and_check(root, routed: bool):
    project = instrumentation_pull(root)
    result = run(project)
    print(f"\nelectrical stage ok={result.ok} in {result.duration_s:.1f} s")
    for d in result.diagnostics:
        print(f"  [{d.severity.value:7}] {d.code}: {d.message}")
    build = root / "build" / "electrical"
    for name in ("operating_points.json", "power_tree.png", "wire_currents.png", "runtime.png",
                 "full_throttle_transient.png", "result.json"):
        assert (build / name).exists(), name
    document = json.loads((build / "operating_points.json").read_text())
    assert len(document["operating_points"]) == 15
    codes = {d.code for d in result.diagnostics}
    assert (root / "build" / "wiring" / "routes.json").exists() == routed
    # Without a wiring-stage run the lengths are estimates; with one they come from routes.json.
    assert ("wire_length_estimated" in codes) == (not routed)
    for d in result.diagnostics:
        assert all(isinstance(v, (int, float, dict)) or v is None for v in d.data.values()), d
    # cruise at 4 m/s must be cheap, stall must be a burst far above it
    cruise = document["operating_points"]["cruise@nominal"]["components"]["battery"]["current"]
    stall = document["operating_points"]["motor_stall@full"]["components"]["battery"]["current"]
    assert 0.5 < cruise < 5.0 and stall > 10 * cruise
    assert document["runtime"]["runtime"] > 15 * 60
    assert result.assumptions and result.inputs["robot_yaml_sha256"]
    return result


def test_electrical_stage_on_bench(tmp_path):
    """A fresh copy of the bench (no build artifacts): wire lengths are estimated."""
    _run_and_check(copy_bench(tmp_path / "bench"), routed=False)


def test_electrical_stage_uses_routed_wire_lengths(tmp_path):
    """With the wiring stage's routes.json the lengths (and installed ampacity) come from routing."""
    root = copy_bench(tmp_path / "bench")
    (root / "build" / "wiring").mkdir(parents=True)
    project = instrumentation_pull(root)
    # A synthetic measured route per conductor exercises the hand-off independently
    # of router optimization and cable dynamics (which have their own fixtures).
    routes = {"wires": {w.id: {"cut_length": .2, "installed_ampacity": project.catalog.wire(w.awg).ampacity}
                         for h in project.spec.electrical.harnesses for w in h.wires}}
    (root / "build/wiring/routes.json").write_text(json.dumps(routes))
    _run_and_check(root, routed=True)

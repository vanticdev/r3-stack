"""Run the electrical stage on the reference RC car and print its diagnostics."""

import json
from pathlib import Path

from r3_model.project import Project
from r3_sim.electrical.stage import run

RC_CAR = Path(__file__).resolve().parents[1] / "examples" / "robots" / "rc-car"


def test_electrical_stage_on_rc_car():
    project = Project.load(RC_CAR)
    result = run(project)
    print(f"\nelectrical stage ok={result.ok} in {result.duration_s:.1f} s")
    for d in result.diagnostics:
        print(f"  [{d.severity.value:7}] {d.code}: {d.message}")
    build = RC_CAR / "build" / "electrical"
    for name in ("operating_points.json", "power_tree.png", "wire_currents.png", "runtime.png",
                 "full_throttle_transient.png", "result.json"):
        assert (build / name).exists(), name
    document = json.loads((build / "operating_points.json").read_text())
    assert len(document["operating_points"]) == 15
    codes = {d.code for d in result.diagnostics}
    assert "wire_length_estimated" in codes  # no routes.json in the example
    for d in result.diagnostics:
        assert all(isinstance(v, (int, float, dict)) or v is None for v in d.data.values()), d
    # cruise at 4 m/s must be cheap, stall must be a burst far above it
    cruise = document["operating_points"]["cruise@nominal"]["components"]["battery"]["current"]
    stall = document["operating_points"]["motor_stall@full"]["components"]["battery"]["current"]
    assert 0.5 < cruise < 5.0 and stall > 10 * cruise
    assert document["runtime"]["runtime"] > 15 * 60
    assert result.assumptions and result.inputs["robot_yaml_sha256"]

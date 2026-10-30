"""The off-the-shelf drivetrain catalog assembles on a printed chassis and drives in the twin.

Fixture ``printcar``: PETG chassis, Traxxas Magnum 272 + Titan 12T, half-shafts in printed bearing webs,
SCT wheels on 12 mm hexes, Raspberry Pi 5 on a Pololu 5 V buck, QuicRun WP 1060 ESC, open-loop firmware."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from r3_model.project import Project
from r3_orchestrator import stages

FIXTURE = Path(__file__).parent / "fixtures" / "printcar"


@pytest.fixture(scope="module")
def printcar(tmp_path_factory: pytest.TempPathFactory) -> Project:
    root = tmp_path_factory.mktemp("printcar") / "printcar"
    shutil.copytree(FIXTURE, root)
    return Project.load(root)


def test_bought_driveline_assembles_without_errors(printcar: Project) -> None:
    result = stages.run_stage(printcar, "assembly")
    assert result.ok, [(d.code, d.subjects) for d in result.errors]
    poses = json.loads((printcar.root / "build" / "model" / "assembly.json").read_text())["poses"]
    # Wheel centres land on the Slash 2WD track (296 mm) at the 55 mm tire radius.
    for wheel, y in (("wheel_rl", 0.148), ("wheel_rr", -0.148), ("wheel_fl", 0.148), ("wheel_fr", -0.148)):
        assert poses[wheel]["xyz"][1] == pytest.approx(y, abs=1e-4) and poses[wheel]["xyz"][2] == pytest.approx(0.055, abs=1e-4)
    joints = {j.id for j in printcar.spec.mechanical.joints}
    assert {"spin_rl", "spin_rr", "spin_fl", "spin_fr"} <= joints
    model = stages.run_stage(printcar, "model")
    assert not model.ok
    assert any(d.code == "joint_capacity_zero" for d in model.errors)
    assert any(d.code == "interference_checked_by_assembly" for d in model.diagnostics)


def test_unqualified_driveline_is_rejected_with_real_firmware(printcar: Project) -> None:
    stages.run_stage(printcar, "assembly")
    result = stages.run_stage(printcar, "sim", scenarios=["straight"])
    assert not result.ok
    assert {"joint_capacity_zero", "joint_failed"} <= {d.code for d in result.errors}
    summary = json.loads((printcar.root / "build" / "sim" / "straight" / "summary.json").read_text())
    assert not summary["passed"] and summary["software"]["launches"][0]["connected"]
    # Keep the original motion requirement. The unsafe fixture now fails it,
    # rather than gaining rigid mounts from a pose declaration.
    speed_check=next(c for c in summary["checks"] if c["key"]=="min_top_speed_mps")
    assert speed_check["expected"] == 2.0 and not speed_check["pass"]
    metrics = summary["metrics"]
    assert metrics["brownouts"] == 0 and metrics["min_rail_5v"] > 4.75

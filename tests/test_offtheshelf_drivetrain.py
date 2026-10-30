"""Purchased transmission mating and unsafe synthetic drive qualification."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

from r3_model.project import Project
from r3_orchestrator import stages
from r3_sim.scenario import run_scenario
from test_joint_physics import drivetrain_project




def test_unqualified_driveline_is_rejected_with_real_firmware(tmp_path) -> None:
    project = drivetrain_project(tmp_path)
    for interface in project.spec.mechanical.interfaces:
        for key in ("retention_force", "shear_force"):
            if key in interface.params["physics"]:
                interface.params["physics"][key]["value"] = 0
    project.spec.simulation.joint_failure_replay_duration = .08
    scenario = project.spec.simulation.scenarios[0]
    scenario.expect["min_top_speed_mps"] = 2.0
    outcome = run_scenario(project, scenario.id, render=False,
        software_cmd=[sys.executable, str(Path(__file__).parent / "sim_joint_controller.py")])
    assert not outcome.passed
    assert {"joint_capacity_zero", "joint_failed"} <= {d.code for d in outcome.diagnostics}
    summary = outcome.summary
    assert not summary["passed"] and summary["software"]["launches"][0]["connected"]
    speed_check = next(c for c in summary["checks"] if c["key"] == "min_top_speed_mps")
    assert speed_check["expected"] == 2.0 and not speed_check["pass"]
    metrics = summary["metrics"]
    assert metrics["brownouts"] == 0 and metrics["min_rail_5v"] > 4.75

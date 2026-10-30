"""Release gate (requirement evidence matrix) shared by the CLI, MCP server and HTTP API."""

from __future__ import annotations

import json

import pytest

from api_helpers import copy_rc_car, write_sim_scenario, write_stage_outputs
from r3_model.diagnostics import StageResult
from r3_orchestrator import api, cli, mcp_server, release


@pytest.fixture
def root(tmp_path):
    return copy_rc_car(tmp_path)


def items(payload):
    return {i["id"]: i for i in payload["items"]}


def test_nothing_run_is_not_ready(root):
    gate = api.release(root)
    assert gate["schema"] == "r3/release@1" and gate["fabrication_ready"] is False
    assert gate["counts"]["not_run"] == len(gate["items"]) and set(gate["blocking"]) == set(items(gate))
    kinds = {i["kind"] for i in gate["items"]}
    assert kinds == {"requirement", "scenario_expectation", "stage"}
    assert "requirement.max_mass" in items(gate) and "scenario.steering_sweep.min_wire_clearance_m" in items(gate)
    assert items(gate)["requirement.max_mass"]["target"] == pytest.approx(2.2)


def test_status_precedence():
    E = release.Evidence
    assert release._status([]) == "not_run"
    assert release._status([E("sim", "a", True)]) == "verified"
    assert release._status([E("sim", "a", True, estimated=True)]) == "estimated"
    assert release._status([E("sim", "a", True), E("sim", "b", True, stale=True)]) == "stale"
    assert release._status([E("sim", "a", False), E("sim", "b", True, stale=True)]) == "failed"
    assert release._status([E("sim", "a", False, stale=True)]) == "stale"  # a stale failure proves nothing either way


def test_staleness_ignores_git_commit_but_not_revisions():
    current = {"robot_yaml_sha256": "a", "git_commit": "c2", "cad_revision": "r1"}
    assert not release.is_stale({"robot_yaml_sha256": "a", "git_commit": "c1", "cad_revision": "r1"}, current)
    assert release.is_stale({"robot_yaml_sha256": "a", "cad_revision": "r0"}, current)
    assert release.is_stale({"robot_yaml_sha256": "b", "cad_revision": "r1"}, current)
    assert release.is_stale({}, current)


def test_evidence_from_results(root):
    api.validate(root, ["assembly", "model"])
    write_stage_outputs(root)
    fingerprint = api.load_project(root).fingerprint()
    write_sim_scenario(root, "full_throttle", fingerprint=fingerprint)
    folder = write_sim_scenario(root, "autonomous_course", fingerprint=fingerprint)
    summary = json.loads((folder / "summary.json").read_text())
    summary["checks"] = [{"key": "brownouts", "expected": 0, "measured": 0, "pass": True}]
    summary["fallbacks"] = {"electrical": True}
    (folder / "summary.json").write_text(json.dumps(summary))
    electrical = StageResult(stage="electrical", inputs=fingerprint, metrics={"runtime": 1800.0})
    electrical.write(root / "build" / "electrical" / "result.json")

    gate = items(api.release(root))
    mass = gate["requirement.max_mass"]
    assert mass["status"] == "estimated" and mass["evidence"][0]["artifact"] == "model/graph.json"
    assert mass["evidence"][0]["value"] < mass["target"] and mass["evidence"][0]["inputs"]["robot_yaml_sha256"]
    speed = gate["requirement.top_speed"]
    assert speed["status"] == "failed" and speed["evidence"][0]["value"] == 7.4 and speed["evidence"][0]["check"] == "full_throttle.min_top_speed_mps"
    assert gate["scenario.autonomous_course.brownouts"]["status"] == "estimated"  # electrical fallback model
    assert "fallback" in gate["scenario.autonomous_course.brownouts"]["message"]
    assert gate["scenario.autonomous_course.laps_completed"]["status"] == "not_run"
    assert "did not evaluate" in gate["scenario.autonomous_course.laps_completed"]["message"]
    # Interference still defers to exact geometry. The universal connection
    # preflight now also blocks unresolved retention in the reference design.
    assert gate["stage.model"]["status"] == "failed" and gate["stage.cad"]["status"] == "not_run"
    assert gate["stage.model"]["evidence"][0]["value"] > 0
    temperature = gate["requirement.max_component_temperature_c"]
    assert temperature["status"] == "verified" and temperature["evidence"][0]["value"] == 60.0
    assert gate["requirement.min_runtime"]["status"] == "verified" and gate["requirement.min_runtime"]["evidence"][0]["value"] == 1800.0
    assert gate["requirement.max_wire_voltage_drop_fraction"]["status"] == "verified"
    assert gate["stage.electrical"]["status"] == "verified"


def test_cli_status_release(root, capsys):
    assert cli.main(["status", "--release", "--json", "-C", str(root)]) == cli.EXIT_ERRORS
    payload = json.loads(capsys.readouterr().out)
    assert payload["fabrication_ready"] is False and payload["items"]
    assert cli.main(["status", "--release", "-C", str(root)]) == cli.EXIT_ERRORS
    text = capsys.readouterr().out
    assert "requirement.max_mass" in text and "NOT READY" in text


def test_mcp_release_tool(root):
    payload = mcp_server.r3_release_status(project=str(root))
    assert payload["schema"] == "r3/release@1" and mcp_server.TOOL_TIERS["r3_release_status"] == "read"
    assert mcp_server.r3_release_status(project=str(root.parent / "nowhere"))["code"] == "project_not_found"

"""MCP server tools called directly, plus tool registration and permission tiers."""

from __future__ import annotations

import asyncio
import base64

import pytest

from orchestrator_helpers import make_project
from r3_orchestrator import mcp_server as m

EXPECTED_TOOLS = {"r3_status", "r3_validate", "r3_run_stage", "r3_get_diagnostics", "r3_diagnose", "r3_get_spec", "r3_patch_spec",
                  "r3_list_catalog", "r3_run_scenario", "r3_get_artifact", "deploy_fleet", "create_github_repo", "r3_create_project"}


@pytest.fixture
def project(tmp_path):
    return str(make_project(tmp_path))


def test_tools_registered_with_tiers():
    tools = {t.name: t for t in asyncio.run(m.mcp.list_tools())}
    assert EXPECTED_TOOLS <= set(tools)
    for name in ("deploy_fleet", "create_github_repo"):
        assert tools[name].annotations.destructive_hint is True
        assert "confirm" in tools[name].input_schema["properties"]
    assert tools["r3_get_spec"].annotations.read_only_hint is True


def test_validate_and_diagnostics(project):
    result = m.r3_validate(project=project, stages=["model"])
    assert result["ok"] is False and result["stages"][0]["stage"] == "model"
    errors = m.r3_get_diagnostics(project=project, severity="error")
    assert errors["counts"]["error"] == len(errors["diagnostics"]) > 0
    assert all(d["code"] == "component_interference" for d in errors["diagnostics"])
    esc = m.r3_get_diagnostics(project=project, subject="esc")
    assert [d["subjects"] for d in esc["diagnostics"]] == [["esc", "standoff_rl"]]
    status = m.r3_status(project=project)
    assert next(s for s in status["stages"] if s["stage"] == "model")["status"] == "failed"
    assert m.r3_diagnose(project=project)["errors"] > 0


def test_run_stage_returns_assumptions(project):
    result = m.r3_run_stage("model", project=project)
    assert result["assumptions"] and result["stages"][0]["metrics"]["components"] == 23
    assert m.r3_run_stage("bogus", project=project)["code"] == "usage_error"


def test_spec_patch_concurrency(project):
    read = m.r3_get_spec(project=project)
    first = m.r3_patch_spec({"components": {"standoff_rl": {"pose": {"xyz": ["30 mm", "45 mm", "28 mm"]}}}}, read["sha256"], project=project)
    assert first["ok"] and first["previous_sha256"] == read["sha256"] and first["sha256"] != read["sha256"]
    # A second agent still holding the old revision must conflict, not overwrite.
    second = m.r3_patch_spec({"robot": {"version": "9.9.9"}}, read["sha256"], project=project)
    assert second == {"ok": False, "code": "spec_conflict", "expected_sha256": read["sha256"], "current_sha256": first["sha256"],
                      "message": second["message"]}
    assert m.r3_get_spec(project=project, section="robot")["spec"]["version"] == "0.1.0"
    # The applied edit fixed one interference.
    result = m.r3_validate(project=project, stages=["model"])
    pairs = {frozenset(d["subjects"]) for d in result["diagnostics"] if d["code"] == "component_interference"}
    assert frozenset({"esc", "standoff_rl"}) not in pairs


def test_spec_patch_schema_validation_and_dry_run(project):
    read = m.r3_get_spec(project=project)
    bad = m.r3_patch_spec({"components": {"esc": {"mount": "missing_part"}}}, read["sha256"], project=project)
    assert bad["code"] == "spec_invalid" and "missing_part" in bad["errors"][0]["msg"]
    typo = m.r3_patch_spec({"components": {"esc": {"poze": {}}}}, read["sha256"], project=project)
    assert typo["code"] == "spec_invalid" and typo["errors"][0]["loc"] == "components.esc.poze"
    dry = m.r3_patch_spec({"robot": {"version": "0.2.0"}}, read["sha256"], project=project, dry_run=True)
    assert dry["ok"] and dry["changed"] and dry["sha256"] == read["sha256"]
    assert m.r3_get_spec(project=project)["sha256"] == read["sha256"]


def test_spec_patch_preserves_comments_and_layout(project):
    from pathlib import Path

    path = Path(project) / "robot.yaml"
    before = path.read_text().splitlines()
    read = m.r3_get_spec(project=project)
    result = m.r3_patch_spec({"requirements": {"max_mass": "2.1 kg"}}, read["sha256"], project=project)
    assert result["ok"] and result["preserves_comments"] and result["graph_diagnostics"] == []
    after = path.read_text().splitlines()
    changed = [(a, b) for a, b in zip(before, after) if a != b]
    assert len(before) == len(after) and changed == [("  max_mass: 2.2 kg", "  max_mass: 2.1 kg")]


def test_spec_patch_reports_dangling_connectors(project):
    read = m.r3_get_spec(project=project)
    # Removing the fuse is schema-valid but leaves the Y-harness fuse connectors unmated.
    result = m.r3_patch_spec({"components": {"fuse": None}}, read["sha256"], project=project, dry_run=True)
    assert result["ok"] and {d["code"] for d in result["graph_diagnostics"]} == {"unknown_connector"}
    assert {d["subjects"][0] for d in result["graph_diagnostics"]} == {"fuse.J1", "fuse.J2"}


def test_catalog_and_artifacts(project):
    motors = m.r3_list_catalog(project=project, kind="motor")
    ids = [c["id"] for c in motors["components"]]
    assert "motor-540-brushed" in ids and all(c["kind"] == "motor" for c in motors["components"])
    assert m.r3_list_catalog(project=project, query="xt60")["connectors"]
    m.r3_run_stage("model", project=project)
    graph = m.r3_get_artifact("model/graph.json", project=project)
    assert graph["kind"] == "json" and "components" in graph["content"]
    image = m.r3_get_artifact("model/layout.png", project=project)
    assert base64.b64decode(image["base64"])[:4] == b"\x89PNG"
    assert m.r3_get_artifact("../robot.yaml", project=project)["code"] == "artifact_outside_build"
    assert m.r3_get_artifact("nope.json", project=project)["code"] == "artifact_missing"
    assert "model/graph.json" in m.r3_get_artifact("model", project=project)["entries"]


def test_gated_tools_refuse_without_confirm(project, tmp_path, monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("gated tool ran a command without confirm"))
    assert m.deploy_fleet(project=project)["code"] == "confirmation_required"
    assert m.create_github_repo("x", str(tmp_path))["code"] == "confirmation_required"


def test_run_scenario_without_sim_package(project, monkeypatch):
    from r3_orchestrator import stages

    monkeypatch.setitem(stages.ALL_STAGES, "sim", stages.StageSpec("sim", "r3_missing_sim.stage", "test"))
    result = m.r3_run_scenario("full_throttle", project=project)
    assert result["diagnostics"][0]["code"] == "stage_unavailable"


def test_project_not_found(tmp_path):
    assert m.r3_status(project=str(tmp_path))["code"] == "project_not_found"


def test_stdio_round_trip(project):
    """Start the real server over stdio, list tools and call one, as Claude Code / Codex would."""
    import json
    import os
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def session_calls():
        params = StdioServerParameters(command=sys.executable, args=["-m", "r3_orchestrator.mcp_server"],
                                       env={**os.environ, "R3_PROJECT": project, "PYTHONPATH": os.pathsep.join(sys.path)})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = {t.name for t in (await session.list_tools()).tools}
                result = await session.call_tool("r3_get_spec", {"section": "robot"})
                return names, result.structured_content or json.loads(result.content[0].text)

    names, spec = asyncio.run(asyncio.wait_for(session_calls(), timeout=60))
    assert EXPECTED_TOOLS <= names
    assert spec["ok"] and spec["spec"]["name"] == "r3-rc-car" and len(spec["sha256"]) == 64

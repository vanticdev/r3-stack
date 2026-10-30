"""``r3`` CLI: JSON output, exit codes, lazy stage loading and gated deployment."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator_helpers import ROOT, make_project
from r3_orchestrator import api, cli, stages


def r3(capsys, *argv: str) -> tuple[int, dict]:
    code = cli.main(list(argv))
    out = capsys.readouterr().out
    return code, json.loads(out)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return make_project(tmp_path)


@pytest.fixture
def clean_project(tmp_path: Path) -> Path:
    """bench with the layout interferences moved apart, so the model stage passes."""
    def fix(spec):
        spec["components"] = {"base": spec["components"]["base"]}
        spec["mechanical"] = {}
        spec["electrical"] = {}
        spec["software"] = {}
        spec["pcb"] = {}
    return make_project(tmp_path, fix, name="clean")


def test_validate_model_json(capsys, project):
    code, payload = r3(capsys, "validate", "--stage", "model", "--project", str(project), "--json")
    assert code == cli.EXIT_ERRORS
    assert payload["ok"] is False and payload["robot"] == "r3-bench"
    assert [s["stage"] for s in payload["stages"]] == ["model", "report"]
    assert payload["counts"]["error"] == len([d for d in payload["diagnostics"] if d["severity"] == "error"]) > 0
    first = payload["diagnostics"][0]
    assert {"code", "severity", "message", "subjects", "data", "stage", "hint"} <= set(first)
    assert (project / "build" / "model" / "result.json").exists()
    assert (project / "build" / "report" / "summary.json").exists()


def test_validate_clean_exit_zero(capsys, clean_project):
    code, payload = r3(capsys, "validate", "--stage", "model", "-C", str(clean_project), "--json")
    assert code == cli.EXIT_OK, [d["message"] for d in payload["diagnostics"]]
    assert payload["counts"]["error"] == 0


def test_missing_stage_module_is_a_warning(capsys, project, monkeypatch):
    fake = stages.StageSpec("cad", "r3_definitely_missing.stage", "test")
    monkeypatch.setattr(stages, "STAGES", tuple(fake if s.name == "cad" else s for s in stages.STAGES))
    monkeypatch.setitem(stages.ALL_STAGES, "cad", fake)
    code, payload = r3(capsys, "validate", "--stage", "cad", "-C", str(project), "--json")
    assert code == cli.EXIT_OK
    (diag,) = payload["diagnostics"]
    assert diag["code"] == "stage_unavailable" and diag["severity"] == "warning"
    assert diag["data"] == {"module": "r3_definitely_missing.stage", "missing": "r3_definitely_missing"}


def test_crashing_stage_is_an_error(capsys, project, monkeypatch):
    import types

    broken = types.ModuleType("r3_test_broken_stage")
    broken.run = lambda project: 1 / 0
    monkeypatch.setitem(sys.modules, "r3_test_broken_stage", broken)
    fake = stages.StageSpec("wiring", "r3_test_broken_stage", "test")
    monkeypatch.setattr(stages, "STAGES", tuple(fake if s.name == "wiring" else s for s in stages.STAGES))
    monkeypatch.setitem(stages.ALL_STAGES, "wiring", fake)
    code, payload = r3(capsys, "validate", "--stage", "wiring", "-C", str(project), "--json")
    assert code == cli.EXIT_ERRORS
    assert payload["diagnostics"][0]["code"] == "stage_crashed"


def test_scenarios_are_forwarded(project, monkeypatch):
    import types

    seen = {}
    fake_sim = types.ModuleType("r3_test_sim_stage")

    def run(project, scenarios=None):
        seen["scenarios"] = scenarios
        from r3_model.diagnostics import StageResult
        return StageResult(stage="sim")

    fake_sim.run = run
    monkeypatch.setitem(sys.modules, "r3_test_sim_stage", fake_sim)
    monkeypatch.setitem(stages.ALL_STAGES, "sim", stages.StageSpec("sim", "r3_test_sim_stage", "test"))
    assert cli.main(["sim", "run", "--scenario", "full_throttle", "-C", str(project), "--json"]) == cli.EXIT_OK
    assert seen["scenarios"] == ["full_throttle"]


def test_unknown_stage_is_usage_error(capsys, project):
    code, payload = r3(capsys, "validate", "--stage", "nope", "-C", str(project), "--json")
    assert code == cli.EXIT_USAGE and payload["code"] == "usage_error"


def test_status_and_diagnose(capsys, project):
    r3(capsys, "validate", "--stage", "model", "-C", str(project), "--json")
    code, status = r3(capsys, "status", "-C", str(project), "--json")
    assert status["summary"]["components"] == 18 and status["summary"]["bindings"] == 7
    model = next(s for s in status["stages"] if s["stage"] == "model")
    assert model["status"] == "failed" and model["stale"] is False
    code, diagnosis = r3(capsys, "diagnose", "-C", str(project), "--json")
    assert code == cli.EXIT_ERRORS
    subjects = {g["subject"] for g in diagnosis["subjects"]}
    assert "block_a" in subjects
    group = next(g for g in diagnosis["subjects"] if g["subject"] == "block_a")
    assert group["diagnostics"][0]["hint"] and group["diagnostics"][0]["also"] == ["block_b"]
    # Human output is grouped by subject too.
    assert cli.main(["diagnose", "-C", str(project)]) == cli.EXIT_ERRORS
    assert "block_a: 1 errors" in capsys.readouterr().out


def test_status_marks_stale_results(capsys, project):
    r3(capsys, "validate", "--stage", "model", "-C", str(project), "--json")
    spec = project / "robot.yaml"
    spec.write_text(spec.read_text().replace("version: 0.1.0", "version: 0.1.1", 1))
    _, status = r3(capsys, "status", "-C", str(project), "--json")
    assert next(s for s in status["stages"] if s["stage"] == "model")["stale"] is True


def test_deploy_fleet_requires_yes(capsys, project, monkeypatch):
    monkeypatch.setattr(api.subprocess, "run", lambda *a, **k: pytest.fail("must not run without --yes"))
    code, payload = r3(capsys, "deploy", "--target", "fleet", "-C", str(project), "--json")
    assert code == cli.EXIT_USAGE and payload["code"] == "confirmation_required"


def test_deploy_fleet_requires_clean_validation(capsys, project):
    (project / "backend").mkdir()
    (project / "backend" / "deploy.py").write_text("")
    r3(capsys, "validate", "--stage", "model", "-C", str(project), "--json")
    code, payload = r3(capsys, "deploy", "--target", "fleet", "--yes", "-C", str(project), "--json")
    assert code == cli.EXIT_USAGE and payload["code"] == "validation_required" and "model" in payload["failed_stages"]


def test_deploy_fleet_runs_blitz(capsys, clean_project, monkeypatch):
    (clean_project / "backend").mkdir()
    (clean_project / "backend" / "deploy.py").write_text("")
    r3(capsys, "validate", "--stage", "model", "-C", str(clean_project), "--json")
    calls = []

    real_run = subprocess.run

    def fake_run(command, **kwargs):
        if command[0] == "git":  # revision fingerprinting
            return real_run(command, **kwargs)
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="deployed 1 device", stderr="")

    monkeypatch.setattr(api.subprocess, "run", fake_run)
    code, payload = r3(capsys, "deploy", "--target", "fleet", "--yes", "-C", str(clean_project), "--json",
                       "--", "--device", "pi.local:5000")
    assert code == cli.EXIT_OK, payload
    (command, kwargs), = calls
    assert command[1:] == ["backend/deploy.py", "--device", "pi.local:5000"]
    assert kwargs["cwd"] == clean_project.resolve()
    assert (clean_project / payload["log"]).read_text().startswith("$ ")


def test_create_scaffold(capsys, tmp_path):
    code, payload = r3(capsys, "create", "demo-bot", "--dir", str(tmp_path), "--json")
    assert code == cli.EXIT_OK and "robot.yaml" in payload["files"]
    assert "name: demo-bot" in (tmp_path / "demo-bot" / "robot.yaml").read_text()


def test_python_dash_m_entrypoint(tmp_path):
    env_path = ":".join(str(ROOT / p) for p in ("packages/robot-model", "packages/component-catalog", "services/orchestrator",
                                                "integrations/github"))
    out = subprocess.run([sys.executable, "-m", "r3_orchestrator", "--help"], capture_output=True, text=True,
                         env={"PYTHONPATH": env_path, "PATH": "/usr/bin:/bin"}, check=True)
    for command in ("validate", "cad", "pcb", "wiring", "sim", "report", "status", "diagnose", "deploy", "create"):
        assert command in out.stdout

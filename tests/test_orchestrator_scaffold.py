"""r3_github: scaffold tree, workflow YAML, lock pinning, remote creation (mocked)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from r3_github import lock, scaffold
from r3_model.project import Project


@pytest.fixture(autouse=True)
def no_gh(monkeypatch):
    real = subprocess.run

    def guarded(command, *args, **kwargs):
        if command and command[0] == "gh":
            pytest.fail("tests must never call gh")
        return real(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded)


def test_scaffold_tree(tmp_path):
    target = tmp_path / "trail-bot"
    files = scaffold.create_project(target, template="rc-car", platform_url="git+https://example.com/r3.git", platform_ref="abc123")
    for expected in ("robot.yaml", "r3.lock.yaml", "README.md", "AGENTS.md", "CLAUDE.md", ".gitignore", ".mcp.json",
                     ".github/workflows/r3.yml", "cad/README.md", "pcb/carrier-hat/README.md", "catalog/README.md",
                     "backend/deploy.py", "backend/python/rc_car_controller/main.py"):
        assert expected in files and (target / expected).is_file(), expected
    assert "build/" in (target / ".gitignore").read_text().splitlines()
    project = Project.load(target)
    assert project.spec.robot.name == "trail-bot"
    assert project.fingerprint()["platform_revision"] == "abc123"
    # Comments of the template survive the rename.
    assert "# Robot frame: +x forward" in (target / "robot.yaml").read_text()
    agents = (target / "AGENTS.md").read_text()
    assert "r3_patch_spec" in agents and "--yes" in agents and "trail-bot" in agents
    assert (target / "CLAUDE.md").read_text().startswith("# Claude Code instructions\n\n@AGENTS.md")


def test_generated_deploy_keeps_blitz_hooks(tmp_path):
    scaffold.create_project(tmp_path / "bot", platform_ref="main")
    source = (tmp_path / "bot" / "backend" / "deploy.py").read_text()
    compile(source, "deploy.py", "exec")
    assert "def get_modules()" in source and "def pi_name_to_process_types(" in source
    assert 'modules.python("rc_car_controller", process=ProcessType.RC_CAR_CONTROLLER, entrypoint="main.py"' in source
    compile((tmp_path / "bot" / "backend/python/rc_car_controller/main.py").read_text(), "main.py", "exec")


def test_blank_template_and_errors(tmp_path):
    scaffold.create_project(tmp_path / "blank-bot", template="blank", platform_ref="main")
    assert Project.load(tmp_path / "blank-bot").spec.components == {}  # blank starts from nothing
    with pytest.raises(FileExistsError):
        scaffold.create_project(tmp_path / "blank-bot", template="blank")
    with pytest.raises(KeyError):
        scaffold.create_project(tmp_path / "x", template="no-such-robot")
    with pytest.raises(ValueError):
        scaffold.create_project(tmp_path / "y", name="bad name!")


def test_workflow_yaml(tmp_path):
    scaffold.create_project(tmp_path / "bot", platform_url="git+https://example.com/r3.git", platform_ref="v1.2.3")
    workflow = yaml.safe_load((tmp_path / "bot" / ".github/workflows/r3.yml").read_text())
    triggers = workflow.get("on", workflow.get(True))  # PyYAML reads the bare key `on` as True
    assert {"push", "pull_request"} <= set(triggers)
    job = workflow["jobs"]["validate"]
    assert "git+https://example.com/r3.git" in job["env"]["R3_PLATFORM_URL"] and "v1.2.3" in job["env"]["R3_PLATFORM_REF"]
    # Runs inside the R3 worker image pinned to the platform revision.
    assert job["container"]["image"].endswith("'ghcr.io/vanticdev/r3-worker:v1.2.3' }}")
    assert "vars.R3_WORKER_IMAGE" in job["container"]["image"]
    assert job["env"]["MPLBACKEND"] == "Agg" and job["env"]["R3_ONSHAPE_TRANSPORT"] == "api-keys"
    steps = {s.get("name", s.get("uses")): s for s in job["steps"]}
    assert "actions/setup-python@v5" not in steps
    assert "r3-platform" in steps["Install r3-platform override"]["run"]
    assert "r3 doctor" in steps["Worker environment"]["run"]
    assert "r3 validate" in steps["Validate"]["run"] and "--json" in steps["Validate"]["run"]
    assert "ONSHAPE_ACCESS_KEY" in steps["Sync CAD (Onshape)"]["if"]
    assert "kicad-cli" in steps["Build PCBs (KiCad)"]["run"]
    upload = steps["Upload build artifacts"]
    assert upload["uses"].startswith("actions/upload-artifact") and upload["with"]["path"] == "build/" and upload["if"] == "always()"
    assert "build/report/summary.json" in steps["Job summary"]["run"] and "GITHUB_STEP_SUMMARY" in steps["Job summary"]["run"]
    # The embedded job-summary script is valid Python.
    script = steps["Job summary"]["run"].split("<<'PY'\n", 1)[1].rsplit("PY", 1)[0]
    compile(script, "summary.py", "exec")


def test_job_summary_script_renders(tmp_path, monkeypatch):
    """Execute the workflow's summary script against a real report."""
    from orchestrator_helpers import make_project
    from r3_orchestrator import api

    root = make_project(tmp_path)
    api.validate(root, ["model"])
    workflow = yaml.safe_load(scaffold.render_workflow())
    step = next(s for s in workflow["jobs"]["validate"]["steps"] if s.get("name") == "Job summary")
    script = step["run"].split("<<'PY'\n", 1)[1].rsplit("PY", 1)[0]
    out = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(out))
    monkeypatch.chdir(root)
    exec(compile(script, "summary.py", "exec"), {})
    text = out.read_text()
    assert "R3 validation: r3-rc-car FAILED" in text and "`component_interference`" in text


def test_lock_entries(tmp_path):
    lock.pin_onshape(tmp_path, "doc", "ws", "el", "mv42")
    lock.pin_board(tmp_path, "carrier-hat", "deadbeef", kicad="8.0")
    data = lock.read_lock(tmp_path)
    assert data["onshape"]["revision"] == "mv42" and data["onshape"]["document_id"] == "doc"
    assert data["pcb_carrier-hat"] == {**data["pcb_carrier-hat"], "revision": "deadbeef", "kicad": "8.0"}
    assert (tmp_path / "r3.lock.yaml").read_text().startswith("# Pinned external revisions")


def test_create_remote_uses_gh_only_when_called(tmp_path):
    calls: list[list[str]] = []

    def runner(command, **kwargs):
        calls.append(command)
        assert kwargs["cwd"] == tmp_path
        returncode = 1 if command[:3] == ["git", "diff", "--cached"] else 0
        stdout = "https://github.com/me/bot\n" if command[0] == "gh" else ""
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr="")

    result = scaffold.create_remote("bot", tmp_path, private=True, runner=runner)
    assert result == {"ok": True, "url": "https://github.com/me/bot", "private": True, "commands": calls}
    assert calls[0] == ["git", "init", "-b", "main"]
    assert ["git", "commit", "-m", "Initial R3 robot project"] in calls
    assert calls[-1] == ["gh", "repo", "create", "bot", "--private", "--source", str(tmp_path), "--remote", "origin", "--push"]


def test_create_remote_reports_failures(tmp_path):
    def missing(command, **kwargs):
        raise FileNotFoundError(2, "not found", command[0])

    assert scaffold.create_remote("bot", tmp_path, runner=missing)["code"] == "tool_missing"

    def failing(command, **kwargs):
        if command[0] == "gh":
            raise subprocess.CalledProcessError(1, command, stderr="HTTP 422: name already exists")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    failed = scaffold.create_remote("bot", tmp_path, private=False, runner=failing)
    assert failed["code"] == "command_failed" and "already exists" in failed["stderr"] and "--public" in failed["command"]

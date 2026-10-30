"""Session import paths and doctor must survive changing to a fresh project cwd."""

import os
from pathlib import Path
import subprocess
import sys
import tomllib
import shutil

import pytest

from r3_orchestrator.environment import r3_pythonpath
from r3_orchestrator import doctor


def test_session_roots_follow_packaging_manifest(tmp_path):
    root = Path(__file__).resolve().parents[1]
    manifest = tomllib.loads((root / "pyproject.toml").read_text())
    expected = {(root / entry).resolve() for entry in manifest["tool"]["setuptools"]["packages"]["find"]["where"]}
    assert expected <= {Path(entry) for entry in r3_pythonpath().split(os.pathsep)}
    process = subprocess.run([sys.executable, "-c", "import r3_connectors.stage, r3_fab.stage; print('ok')"],
                             cwd=tmp_path, env={**os.environ, "PYTHONPATH": r3_pythonpath()},
                             capture_output=True, text=True, timeout=60)
    assert process.returncode == 0, process.stderr


def test_doctor_imports_every_stage_and_reports_missing_transitive_dependency(monkeypatch):
    from r3_orchestrator.stages import StageSpec

    monkeypatch.setattr(doctor, "ALL_STAGES", {"missing": StageSpec("missing", "r3_does_not_exist.stage", "test")})
    result = doctor.probe_stage_imports()
    assert not result["missing"]["available"]
    assert "ModuleNotFoundError" in result["missing"]["detail"]


def test_doctor_refuses_unimportable_requested_stage(monkeypatch):
    from test_headless_env import _fake_tools

    _fake_tools(monkeypatch)
    monkeypatch.setattr(doctor, "probe_stage_imports", lambda: {
        "fabrication": {"available": False, "module": "r3_fab.stage", "detail": "ModuleNotFoundError: mesh dependency"}})
    result = doctor.run_doctor(["fabrication"])
    assert not result["ok"] and result["code"] == "stage_not_importable"
    stage = next(s for s in result["stages"] if s["stage"] == "fabrication")
    assert stage["level"] == "unavailable" and not stage["headless"]


@pytest.mark.skipif(sys.platform != "darwin" or shutil.which("codex") is None,
                    reason="native Codex/Seatbelt integration probe requires macOS and the CLI")
def test_session_sandbox_can_commit_and_push_its_own_project(tmp_path):
    from r3_orchestrator.api.agents import codex_permission_profile, _toml

    root = (tmp_path / "synthetic-project").resolve()
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    remote = root / "synthetic-remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    (root / "probe.txt").write_text("unrelated synthetic session test\n")
    sandbox = ["codex", "sandbox", "--permission-profile", "r3_session", "-C", str(root), "-c",
               "permissions.r3_session=" + _toml(codex_permission_profile(root)), "--"]
    for command in (["git", "add", "probe.txt"],
                    ["git", "-c", "user.name=R3 Synthetic Test", "-c", "user.email=r3-synthetic@example.invalid", "commit", "-m", "Synthetic probe"],
                    ["git", "push", str(remote), "HEAD:refs/heads/probe"]):
        process = subprocess.run([*sandbox, *command], capture_output=True, text=True, timeout=30)
        assert process.returncode == 0, process.stderr
    head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    pushed = subprocess.check_output(["git", "--git-dir", str(remote), "rev-parse", "refs/heads/probe"], text=True).strip()
    assert pushed == head


def test_robot_session_does_not_inherit_unrelated_account_tools_and_skills(tmp_path):
    from r3_orchestrator.api.agents import codex_scope_overrides

    home = tmp_path / "account"
    config_home = home / ".codex"
    skill = config_home / "skills" / "unrelated-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Unrelated account skill\n")
    account_config = config_home / "config.toml"
    account_config.write_text('[mcp_servers.unrelated]\ncommand="fixture"\n[plugins."unrelated@fixture"]\nenabled=true\n')
    before = account_config.read_bytes()
    args = codex_scope_overrides(tmp_path / "project", account_home=home)
    effective = {}
    for i, value in enumerate(args):
        if value != "-c":
            continue
        key, literal = args[i + 1].split("=", 1)
        target = effective
        # Codex CLI -c paths split on dots; unlike TOML file keys, components
        # are not quoted (plugin ids can contain @).
        parts = key.split(".")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = tomllib.loads("value=" + literal)["value"]
    assert effective["mcp_servers"]["unrelated"]["enabled"] is False
    assert effective["plugins"]["unrelated@fixture"]["enabled"] is False
    assert effective["apps"]["_default"]["enabled"] is False
    assert {"path": str(skill.resolve()), "enabled": False} in effective["skills"]["config"]
    assert {"path": str(skill.resolve() / "SKILL.md"), "enabled": False} in effective["skills"]["config"]
    assert account_config.read_bytes() == before


@pytest.mark.skipif(shutil.which("codex") is None, reason="Codex CLI configuration integration probe")
def test_native_codex_accepts_generated_session_configuration(tmp_path):
    from r3_orchestrator.api.agents import codex_command, mcp_config

    command = codex_command("codex", tmp_path, mcp_config(tmp_path), "gpt-6-astra")
    options = [part for i, value in enumerate(command) if value == "-c" for part in ("-c", command[i + 1])]
    process = subprocess.run(["codex", "app-server", "--print-startup-config", *options], cwd=tmp_path,
                             capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr

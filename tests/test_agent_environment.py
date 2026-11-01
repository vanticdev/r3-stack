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
    from r3_orchestrator.api.agents import codex_permission_profile, session_environment, _toml

    root = (tmp_path / "synthetic-project").resolve()
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    remote = root / "synthetic-remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    (root / "probe.txt").write_text("unrelated synthetic session test\n")
    sandbox = ["codex", "sandbox", "--permission-profile", "r3_session", "-C", str(root), "-c",
               "permissions.r3_session=" + _toml(codex_permission_profile(root)), "--"]
    env = session_environment(root, root / "build/agent/synthetic", {})
    for command in (["git", "add", "probe.txt"],
                    ["git", "-c", "user.name=R3 Synthetic Test", "-c", "user.email=r3-synthetic@example.invalid", "commit", "-m", "Synthetic probe"],
                    ["git", "push", str(remote), "HEAD:refs/heads/probe"]):
        process = subprocess.run([*sandbox, *command], env=env, capture_output=True, text=True, timeout=30)
        assert process.returncode == 0, process.stderr
    head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    pushed = subprocess.check_output(["git", "--git-dir", str(remote), "rev-parse", "refs/heads/probe"], text=True).strip()
    assert pushed == head


def test_session_owns_its_scratch_cache_and_native_runtime(tmp_path):
    from r3_orchestrator.api.agents import session_environment

    root = tmp_path / "project"
    directory = root / "build/agent/synthetic"
    config = {"mcpServers": {"onshape_native": {"env": {"ONSHAPE_NATIVE_RUNTIME": str(tmp_path / "native")}}}}
    env = session_environment(root, directory, config, {"SYNTHETIC_OPTION": "preserved"})
    assert env["SYNTHETIC_OPTION"] == "preserved"
    assert env["ONSHAPE_NATIVE_RUNTIME"] == str(tmp_path / "native")
    assert env["R3_PYTHON"] == sys.executable
    for key in ("TMPDIR", "TMP", "TEMP", "XDG_CACHE_HOME", "UV_CACHE_DIR", "PIP_CACHE_DIR",
                "MPLCONFIGDIR", "npm_config_cache", "R3_MODEL_CACHE"):
        assert Path(env[key]).is_relative_to(root)
    process = subprocess.run([sys.executable, "-c", "import tempfile; print(tempfile.gettempdir())"],
                             env=env, capture_output=True, text=True, check=True)
    assert Path(process.stdout.strip()) == directory / "scratch"


@pytest.mark.skipif(sys.platform != "darwin" or shutil.which("codex") is None,
                    reason="native Codex/Seatbelt read-isolation probe requires macOS and the CLI")
def test_session_can_read_platform_but_not_other_projects_or_shared_temp(tmp_path, monkeypatch):
    from r3_orchestrator.api import agents

    platform = tmp_path / "platform"
    root = platform / "examples/robots/synthetic-project"
    root.mkdir(parents=True)
    (platform / "guide.txt").write_text("synthetic platform guide")
    other = platform / "examples/robots/other-project"
    other.mkdir()
    (other / "private.txt").write_text("synthetic unrelated canary")
    external = tmp_path / "alternate-worktree"
    external.mkdir()
    (external / "private.txt").write_text("synthetic alternate checkout canary")
    (root / "outside-link.txt").symlink_to(external / "private.txt")
    monkeypatch.setattr(agents, "__file__", str(platform / "services/orchestrator/r3_orchestrator/api/agents.py"))
    env = agents.session_environment(root, root / "build/agent/synthetic", {})
    sandbox = ["codex", "sandbox", "--permission-profile", "r3_session", "-C", str(root), "-c",
               "permissions.r3_session=" + agents._toml(agents.codex_permission_profile(root)), "--"]
    for path in (other / "private.txt", external / "private.txt", root / "outside-link.txt"):
        result = subprocess.run([*sandbox, "/bin/cat", str(path)], env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode != 0 and not result.stdout, (path, result.stdout, result.stderr)
    result = subprocess.run([*sandbox, "/bin/cat", str(platform / "guide.txt")], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and result.stdout == "synthetic platform guide", result.stderr
    result = subprocess.run([*sandbox, "rg", "--files", "/private/tmp"], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode != 0 and not result.stdout, result.stderr


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


@pytest.mark.skipif(sys.platform != "darwin" or shutil.which("codex") is None,
                    reason="native Codex instruction-loading regression requires macOS and the CLI")
def test_native_codex_loads_instructions_with_the_session_profile(tmp_path):
    """Configuration parsing alone misses the sandboxed self-helper startup."""
    import json
    import queue
    import threading
    import time
    from r3_orchestrator.api import agents

    root = tmp_path / "synthetic-project"
    root.mkdir()
    instructions = root / "AGENTS.md"
    instructions.write_text("Unrelated synthetic instruction-loading fixture. No model task.\n")
    config = {"mcpServers": {"r3": {"command": sys.executable, "args": [], "env": {}}}}
    command = agents.codex_command(shutil.which("codex"), root, config)
    options = [part for i, value in enumerate(command) if value == "-c"
               and command[i + 1].startswith(("default_permissions=", "permissions.r3_session=", "approval_policy="))
               for part in ("-c", command[i + 1])]
    env = agents.session_environment(root, root / "build/agent/synthetic", {})
    # No account MCP servers, auth files or persisted test conversations.
    env["CODEX_HOME"] = str(root / "test-codex-home")
    Path(env["CODEX_HOME"]).mkdir()
    messages = queue.Queue()
    with (root / "bootstrap.stderr").open("w+") as stderr:
        process = subprocess.Popen([command[0], "app-server", *options], cwd=root, env=env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, text=True, bufsize=1)
        def read():
            for line in process.stdout:
                try:
                    messages.put(json.loads(line))
                except ValueError:
                    pass
            messages.put({"ended": True})
        threading.Thread(target=read, daemon=True).start()
        def send(value):
            process.stdin.write(json.dumps(value) + "\n")
            process.stdin.flush()
        def response(identifier):
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                value = messages.get(timeout=max(.01, deadline - time.monotonic()))
                if value.get("id") == identifier:
                    return value
                if value.get("ended"):
                    stderr.seek(0)
                    pytest.fail("app-server exited: " + stderr.read()[-1000:])
            pytest.fail("instruction bootstrap timed out")
        try:
            send({"method": "initialize", "id": 0, "params": {
                "clientInfo": {"name": "r3_synthetic_probe", "version": "1"},
                "capabilities": {"experimentalApi": True}}})
            assert "error" not in response(0)
            send({"method": "initialized", "params": {}})
            send({"method": "thread/start", "id": 1, "params": {
                "cwd": str(root), "permissions": "r3_session", "approvalPolicy": "never", "ephemeral": True}})
            reply = response(1)
            assert "error" not in reply, reply.get("error")
            assert reply["result"]["thread"]["ephemeral"]
            assert str(instructions) in reply["result"]["instructionSources"]
            # Deliberately no turn/start: this tests startup, not a model answer.
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

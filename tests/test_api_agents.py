"""Headless agent sessions over HTTP with mocked ``claude`` / ``codex`` CLIs (no subscription quota used)."""

from __future__ import annotations

import asyncio
import json
import time

import pytest
import yaml

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api_helpers import copy_bench, fake_cli_bin, sse  # noqa: E402
from r3_orchestrator import mcp_server  # noqa: E402
from r3_orchestrator.api import agents as agent_runner  # noqa: E402
from r3_orchestrator.api.agents import AgentManager  # noqa: E402
from r3_orchestrator.api.server import create_app  # noqa: E402

PID = "bench"
GATED = {"deploy_fleet", "create_github_repo"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    parent = tmp_path / "projects"
    parent.mkdir()
    root = copy_bench(parent)
    bin_dir = fake_cli_bin(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")
    log = tmp_path / "agent-call.json"
    monkeypatch.setenv("FAKE_AGENT_LOG", str(log))
    with TestClient(create_app([parent], agents=AgentManager(), doctor=lambda: {"ok": True})) as client:
        yield client, root, log


def test_gated_tools_are_never_preapproved():
    assert set(mcp_server.gated_tools()) == GATED
    assert "r3_release_status" in mcp_server.ungated_tools() and not GATED & set(mcp_server.ungated_tools())


def test_providers_detected_on_path(env):
    client, _, _ = env
    providers = {p["provider"]: p for p in client.get("/api/agent/providers").json()}
    assert providers["claude"]["available"] and providers["claude"]["version"] == "9.9.9 (Claude Code)"
    assert providers["codex"]["available"] and providers["codex"]["path"].endswith("/bin/codex")


def test_native_mcp_failure_evidence_is_project_local_and_keeps_launcher(env, monkeypatch):
    client, root, _ = env
    original = {"command": "synthetic-native-runtime", "args": ["synthetic-server", "literal-argument"],
                "env": {"ONSHAPE_NATIVE_RUNTIME": str(root / "native-runtime")}}
    monkeypatch.setattr(agent_runner, "native_mcp_config", lambda: original.copy())
    started = client.post(f"/api/projects/{PID}/agent", json={"prompt": "synthetic instruction", "provider": "codex"})
    assert started.status_code == 202
    sid = started.json()["session_id"]
    sse(client, started.json()["events_url"])
    directory = root / "build/agent" / sid
    native = json.loads((directory / ".mcp.json").read_text())["mcpServers"]["onshape_native"]
    assert native["args"][0].endswith("r3_orchestrator/mcp_process.py")
    assert native["args"][1:3] == ["--directory", str(directory / "mcp/onshape_native")]
    assert native["args"][3:] == ["--", original["command"], *original["args"]]
    assert native["env"] == original["env"]


def test_claude_session_streams_transcript_runs_and_spec_diff(env):
    client, root, log = env
    started = client.post(f"/api/projects/{PID}/agent", json={"prompt": "make the battery lead shorter", "provider": "claude",
                                                               "model": "fake-model"})
    assert started.status_code == 202, started.text
    session_id = started.json()["session_id"]
    events = sse(client, started.json()["events_url"])
    types = [e["type"] for e in events]
    assert types[0] == "session_started" and types[-1] == "session_finished"
    assert events[-1]["status"] == "completed" and events[-1]["result"] == "Done: description updated." and events[-1]["spec_changed"]

    call = json.loads(log.read_text())
    argv = call["argv"]
    assert call["stdin"] == "make the battery lead shorter" and call["cwd"] == str(root)
    assert argv[:4] == ["-p", "--output-format", "stream-json", "--verbose"] and argv[argv.index("--model") + 1] == "fake-model"
    assert argv[argv.index("--permission-mode") + 1] == "default" and "--strict-mcp-config" in argv
    allowed = argv[argv.index("--allowedTools") + 1:argv.index("--disallowedTools")]
    denied = argv[argv.index("--disallowedTools") + 1:]
    denied = denied[:denied.index("--model")] if "--model" in denied else denied
    assert "mcp__r3__r3_patch_spec" in allowed and "Read" in allowed and "Bash" not in allowed and "Edit" not in allowed
    assert {f"mcp__r3__{t}" for t in GATED} <= set(denied)
    assert not {f"mcp__r3__{t}" for t in GATED} & set(allowed)
    assert "mcp__onshape_native__native_write" in allowed
    assert "mcp__onshape_native__api_write" in allowed
    assert "mcp__onshape_native__api_request" in allowed
    assert "mcp__onshape_native__evaluate" in denied
    config = json.loads(open(argv[argv.index("--mcp-config") + 1]).read())["mcpServers"]["r3"]
    assert config["args"] == ["-m", "r3_orchestrator.mcp_server"] and config["env"]["R3_PROJECT"] == str(root)
    assert call["env"]["R3_PROJECT"] == str(root) and call["env"]["MCP_TOOL_TIMEOUT"]

    messages = [e for e in events if e["type"] == "message"]
    assert [m["text"] for m in messages if m["role"] == "assistant"] == ["Reading the spec.", "Done: description updated."]
    assert any(m["role"] == "output" and m["text"] == "a non-JSON progress line" for m in messages)
    calls = {e["id"]: e for e in events if e["type"] == "tool_call"}
    assert calls["t1"]["name"] == "r3_patch_spec" and calls["t1"]["server"] == "r3"
    results = {e["id"]: e for e in events if e["type"] == "tool_result"}
    assert results["t1"]["ok"] and json.loads(results["t1"]["content"])["changed"] is True and results["t1"]["name"] == "r3_patch_spec"
    (run_started,) = [e for e in events if e["type"] == "run_started"]
    (run_finished,) = [e for e in events if e["type"] == "run_finished"]
    assert run_started["tool"] == "r3_validate" and run_started["stages"] == ["model"]
    assert run_finished["ok"] is False and run_finished["counts"]["error"] == 1
    (changed,) = [e for e in events if e["type"] == "spec_changed"]
    added = [line for line in changed["diff"].splitlines() if line.startswith("+")]
    assert any("edited by the fake agent" in line for line in added) and changed["sha256"] != changed["previous_sha256"]
    assert changed["cumulative_diff"] == changed["diff"]
    assert yaml.safe_load((root / "robot.yaml").read_text())["robot"]["description"] == "edited by the fake agent"
    assert any(e["type"] == "stderr" and "fake claude warning" in e["line"] for e in events)
    (usage,) = [e for e in events if e["type"] == "usage"]
    assert usage["output_tokens"] == 5
    assert types.index("tool_call") < types.index("spec_changed") < types.index("session_finished")

    status = client.get(f"/api/agent/{session_id}").json()
    assert status["status"] == "completed" and status["returncode"] == 0
    transcript = root / "build" / "agent" / session_id / "transcript.jsonl"
    assert transcript.exists() and json.loads(transcript.read_text().splitlines()[0])["type"] == "system"
    assert client.get(f"/api/projects/{PID}/agent").json()[0]["session_id"] == session_id


def test_codex_session(env):
    client, root, log = env
    started = client.post(f"/api/projects/{PID}/agent", json={"prompt": "design a robot", "provider": "codex"}).json()
    events = sse(client, started["events_url"])
    assert events[-1]["status"] == "completed"
    argv = json.loads(log.read_text())["argv"]
    assert argv[:2] == ["exec", "--json"] and argv[argv.index("-C") + 1] == str(root) and argv[-1] == "-"
    overrides = [argv[i + 1] for i, a in enumerate(argv) if a == "-c"]
    assert 'approval_policy="never"' in overrides
    assert 'default_permissions="r3_session"' in overrides
    permission = next(o for o in overrides if o.startswith("permissions.r3_session="))
    import tomllib
    parsed = tomllib.loads(permission)["permissions"]["r3_session"]
    assert parsed["network"]["enabled"] is True
    assert parsed["filesystem"][str(root / ".git")] == "write"
    assert parsed["filesystem"][":root"] == "deny"
    assert parsed["filesystem"][":slash_tmp"] == "deny"
    assert parsed["extends"] == ":workspace"
    assert 'web_search="live"' in overrides
    assert any(o.startswith("developer_instructions=") for o in overrides)
    assert 'mcp_servers.r3.args=["-m", "r3_orchestrator.mcp_server"]' in overrides
    tool_policy = next(o for o in overrides if o.startswith("mcp_servers.r3.tools="))
    approved = tomllib.loads(tool_policy)["mcp_servers"]["r3"]["tools"]
    assert set(approved) == set(mcp_server.ungated_tools())
    assert all(value == {"approval_mode": "approve"} for value in approved.values())
    assert not set(approved) & GATED
    disabled = next(o for o in overrides if o.startswith("mcp_servers.r3.disabled_tools="))
    assert set(json.loads(disabled.split("=", 1)[1])) == GATED
    assert json.loads(log.read_text())["stdin"] == "design a robot"
    calls = [e for e in events if e["type"] == "tool_call"]
    assert [c["name"] for c in calls] == ["r3_patch_spec", "r3_run_scenario"]
    (run_started,) = [e for e in events if e["type"] == "run_started"]
    assert run_started["stages"] == ["sim"] and run_started["scenarios"] == ["full_throttle"]
    assert [e["ok"] for e in events if e["type"] == "run_finished"] == [True]
    assert any(e["type"] == "message" and e["role"] == "thinking" for e in events)
    assert [e for e in events if e["type"] == "spec_changed"]


def test_failed_and_busy_and_cancelled_sessions(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setenv("FAKE_AGENT_MODE", "fail")
    failed = client.post(f"/api/projects/{PID}/agent", json={"prompt": "x", "provider": "codex"}).json()
    final = sse(client, failed["events_url"])[-1]
    assert final["status"] == "failed" and final["returncode"] == 3 and final["error"] == "fake failure"

    monkeypatch.setenv("FAKE_AGENT_MODE", "hang")
    hanging = client.post(f"/api/projects/{PID}/agent", json={"prompt": "x"}).json()
    deadline = time.time() + 20
    while client.get(f"/api/agent/{hanging['session_id']}").json()["events"] < 3 and time.time() < deadline:
        time.sleep(0.1)
    busy = client.post(f"/api/projects/{PID}/agent", json={"prompt": "y"})
    assert busy.status_code == 409 and busy.json()["code"] == "agent_busy"
    assert client.post(f"/api/agent/{hanging['session_id']}/cancel").status_code == 200
    final = sse(client, hanging["events_url"])[-1]
    assert final["status"] == "cancelled" and final["spec_changed"] is False


def test_generated_mcp_config_starts_the_r3_server(tmp_path):
    """The exact command and environment written to .mcp.json serve R3's tools over stdio."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    root = copy_bench(tmp_path)
    server = agent_runner.mcp_config(root)["mcpServers"]["r3"]

    async def check() -> tuple[set[str], dict]:
        params = StdioServerParameters(command=server["command"], args=server["args"], env=server["env"], cwd=str(tmp_path))
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            status = await session.call_tool("r3_status", {})
            return tools, json.loads(status.content[0].text)

    tools, status = asyncio.run(asyncio.wait_for(check(), 60))
    assert {"r3_status", "r3_patch_spec", "r3_release_status"} | GATED <= tools
    assert status["robot"]["name"] == "r3-bench" and status["project"] == str(root.resolve())

"""Stand-in for the ``claude`` and ``codex`` CLIs in the API agent tests (no subscription quota used).

Invoked as ``api_fake_agent_cli.py <claude|codex> <cli args...>`` through a
wrapper named after the provider. It records its argv, stdin and environment
to ``$FAKE_AGENT_LOG``, edits robot.yaml through ``spec_ops`` (as the R3 MCP
tool would), and prints the provider's JSON event stream. ``FAKE_AGENT_MODE``:
``ok`` (default), ``hang`` (sleep until killed) or ``fail`` (exit 3).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def emit(event: dict) -> None:
    print(json.dumps(event), flush=True)


def edit_spec() -> dict:
    from r3_orchestrator import spec_ops

    root = Path(os.environ["R3_PROJECT"])
    current = spec_ops.get_spec(root)
    return spec_ops.patch_spec(root, {"robot": {"description": "edited by the fake agent"}}, current["sha256"])


def claude(mode: str) -> None:
    emit({"type": "system", "subtype": "init", "session_id": "fake-session", "model": "fake-model",
          "mcp_servers": [{"name": "r3", "status": "connected"}], "tools": ["mcp__r3__r3_status"]})
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "Reading the spec."},
                                                       {"type": "tool_use", "id": "t1", "name": "mcp__r3__r3_patch_spec",
                                                        "input": {"patch": {"robot": {"description": "edited"}}}}]}})
    if mode == "hang":
        time.sleep(600)
    result = edit_spec()
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1",
                                                   "content": [{"type": "text", "text": json.dumps(result)}]}]}})
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t2", "name": "mcp__r3__r3_validate",
                                                        "input": {"stages": ["model"]}}]}})
    validate = {"ok": False, "counts": {"error": 1, "warning": 0, "info": 0}, "stages": [{"stage": "model", "status": "failed"}]}
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2", "content": json.dumps(validate)}]}})
    print("a non-JSON progress line", flush=True)
    print("fake claude warning on stderr", file=sys.stderr, flush=True)
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "Done: description updated."}]}})
    emit({"type": "result", "subtype": "success" if mode != "fail" else "error_during_execution", "is_error": mode == "fail",
          "result": "Done: description updated.", "total_cost_usd": 0.0, "num_turns": 3,
          "usage": {"input_tokens": 10, "output_tokens": 5}})


def codex(mode: str) -> None:
    emit({"type": "thread.started", "thread_id": "fake-thread"})
    emit({"type": "turn.started"})
    emit({"type": "item.completed", "item": {"id": "i0", "type": "reasoning", "text": "Plan the edit."}})
    emit({"type": "item.started", "item": {"id": "i1", "type": "mcp_tool_call", "server": "r3", "tool": "r3_patch_spec",
                                           "arguments": {"patch": {}}, "status": "in_progress"}})
    if mode == "hang":
        time.sleep(600)
    result = edit_spec()
    emit({"type": "item.completed", "item": {"id": "i1", "type": "mcp_tool_call", "server": "r3", "tool": "r3_patch_spec",
                                             "arguments": {}, "status": "completed",
                                             "result": {"content": [{"type": "text", "text": json.dumps(result)}],
                                                        "structured_content": result}}})
    emit({"type": "item.started", "item": {"id": "i2", "type": "mcp_tool_call", "server": "r3", "tool": "r3_run_scenario",
                                           "arguments": {"scenario": "full_throttle"}, "status": "in_progress"}})
    emit({"type": "item.completed", "item": {"id": "i2", "type": "mcp_tool_call", "server": "r3", "tool": "r3_run_scenario",
                                             "arguments": {}, "status": "completed",
                                             "result": {"content": [{"type": "text", "text": json.dumps({"ok": True, "counts": {}})}]}}})
    emit({"type": "item.completed", "item": {"id": "i3", "type": "agent_message", "text": "Done."}})
    if mode == "fail":
        emit({"type": "turn.failed", "error": {"message": "fake failure"}})
        sys.exit(3)
    emit({"type": "turn.completed", "usage": {"input_tokens": 7, "output_tokens": 3}})


def main() -> None:
    provider, args = sys.argv[1], sys.argv[2:]
    if args == ["--version"]:
        print({"claude": "9.9.9 (Claude Code)", "codex": "codex-cli 9.9.9"}[provider])
        return
    stdin = sys.stdin.read()
    mode = os.environ.get("FAKE_AGENT_MODE", "ok")
    log = os.environ.get("FAKE_AGENT_LOG")
    if log:
        Path(log).write_text(json.dumps({"argv": args, "stdin": stdin, "cwd": os.getcwd(),
                                         "env": {k: os.environ.get(k) for k in ("R3_PROJECT", "PYTHONPATH", "MCP_TOOL_TIMEOUT")}}))
    (claude if provider == "claude" else codex)(mode)
    if mode == "fail":
        sys.exit(3)


if __name__ == "__main__":
    main()

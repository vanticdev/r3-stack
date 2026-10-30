"""Headless Claude Code / Codex sessions that edit a project through R3's MCP tools.

``POST /api/projects/{pid}/agent`` starts the provider's own CLI (the user's
subscription or API key, nothing proxied) non-interactively in the project
directory:

* Claude Code: ``claude -p --output-format stream-json --verbose
  --mcp-config <session>/.mcp.json --strict-mcp-config --permission-mode default
  --allowedTools <ungated R3 tools, Read, Glob, Grep> --disallowedTools <gated R3 tools>``
* Codex: ``codex exec --json --skip-git-repo-check -C <project>
  -c approval_policy="never" -c default_permissions="r3_session"
  -c permissions.r3_session=... -c mcp_servers.r3.{command,args,env,disabled_tools}=...``
  The profile keeps workspace restrictions, enables networking and grants writes
  to the project's own .git so explicitly requested publication can run.

The prompt goes to stdin. ``<project>/build/agent/<session>/`` keeps the
generated ``.mcp.json``, ``transcript.jsonl`` (raw CLI output) and ``stderr.log``.

Permission tiers stay intact: gated MCP tools (``deploy_fleet``,
``create_github_repo``) are never pre-approved. Claude Code denies them
explicitly (deny rules apply in every permission mode), Codex disables them
for the session, and the tools themselves still refuse to act without
``confirm=true``. Fleet deployment and GitHub creation remain human actions
(``POST /deploy``, ``POST /projects`` with ``confirm``).

Events (normalized for both providers; ``raw`` events carry anything else):

``session_started``  ``{session_id, provider, model, cwd, command}``
``message``  ``{role: assistant|user|system, text}``
``tool_call``  ``{id, name, server, input}``  (``name`` without the ``mcp__r3__`` prefix)
``tool_result``  ``{id, name, ok, content}``
``run_started`` / ``run_finished``  pipeline runs the agent triggered through ``r3_validate``,
``r3_run_stage`` or ``r3_run_scenario`` (``run_finished`` has ``ok``, ``counts``, ``stages``)
``spec_changed``  ``{previous_sha256, sha256, diff, cumulative_diff}`` whenever robot.yaml changes
``usage``  ``{input_tokens, output_tokens, cost_usd?}``
``stderr``  ``{line}``
``raw``  ``{event}``  unrecognized CLI events, verbatim
``session_finished``  ``{status: completed|failed|cancelled, returncode, result, error?}``
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .errors import ApiError
from .events import EventLog
from ..environment import r3_pythonpath

PROVIDERS = ("claude", "codex")
MCP_SERVER = "r3"
#: Built-in Claude Code tools a headless session may use without asking (read-only).
CLAUDE_READ_TOOLS = ("Read", "Glob", "Grep")
#: Tools that start pipeline work inside the MCP server.
RUN_TOOLS = ("r3_validate", "r3_run_stage", "r3_run_scenario", "r3_run_project_tests")
MAX_CONTENT = 20_000  # characters of a tool result kept in an event
MCP_TOOL_TIMEOUT_S = 3600  # a full validation with every scenario takes many minutes

SYSTEM_PROMPT = """You are independently building an R3 robot project, launched headless from the R3 web app.
Read r3_guide first. robot.yaml is the robot specification: change it through r3_get_spec -> r3_patch_spec
(pass the sha256 you read; on spec_conflict re-read and re-apply). Firmware and other authored project files
may be edited directly. Use r3_get_schema for typed authoring details, the catalog and sourcing tools for parts,
r3_cad_pull for pinned read-only CAD, r3_view_artifact to inspect images, and the pipeline tools to validate.
Run native stages, scenarios and project test suites through R3 MCP tools in the worker environment.
Author device bridges under simulation/ and tests under tests/ in this project; the guide documents the
simulation.devices extension API and r3_run_project_tests. Do not edit platform code to add a project device.
CAD changes must happen only in a real Onshape document, using standard editable features and assemblies.
No FeatureScript, locally fabricated CAD, hand-written external tool formats, or sculpted purchased parts.
Use onshape_native directly: discover native commands/schemas, model parts, add mate connectors,
import vendor STEP, create documents in the designated folder and build real assemblies with real mates.
Use native editor/local bridge routes only; public /api calls, bodydetails, massproperties, shadedviews,
and public translations are unavailable. UI exports use /native/api/v17, never a public fallback.
Search the R3 model bank first for purchased parts, then import them into Onshape and add connectors there.
Verify geometry, regeneration and mates numerically and inspect viewport captures before r3_cad_pull.
Create or select an immutable Onshape version. robot.yaml is an index into parts/, electrical/ and software/;
Onshape-derived fields and cad/pulled files are read-only and refreshed only by pull. Fix CAD in Onshape,
then pull again. Never author solids, dimensions, connector frames, poses or mates locally.
Every viewer and simulation mesh must come from that verified pull. Use real MuJoCo, ngspice and Sionna.
Use gh and normal git for GitHub repository creation and pushing when the user explicitly requests them;
that request supplies authorization. Never deploy to physical hardware without an explicit request.
Work only in this project; you may read the R3 platform source and public technical references.
Do not read other local projects, worktrees, old robot git history or other agents' session files.
There is no human available for routine engineering choices: choose sensible conservative assumptions
for unspecified fasteners, process settings, printer envelope and similar details, label them, validate
against them and list them at the end. Unknown physical measurements remain explicit release blockers.
Pending measurements block final qualification, not other independent work: carry clearly labelled parametric
assumptions through design, firmware and simulation, complete what can be completed, and list the measurements.
Missing catalog entries, models or devices are research and project-authoring tasks using the documented tools.
Never weaken requirements, limits or checks. Iterate using diagnostics and r3_release_status.
Finish with stage and scenario results, remaining diagnostics, assumptions and required measurements."""


# ------------------------------------------------------------------ detection
def detect(provider: str, executable: str | None = None) -> dict[str, Any]:
    """Is the provider's CLI installed? ``{provider, available, path, version, detail}``."""
    path = executable or shutil.which(provider)
    if not path:
        install = {"claude": "npm install -g @anthropic-ai/claude-code", "codex": "npm install -g @openai/codex"}[provider]
        return {"provider": provider, "available": False, "path": None, "version": None,
                "detail": f"`{provider}` not found on PATH; install it ({install}) and sign in"}
    try:
        proc = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=15)
        version = (proc.stdout or proc.stderr).strip().splitlines()[0] if (proc.stdout or proc.stderr).strip() else None
        ok = proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"provider": provider, "available": False, "path": path, "version": None, "detail": f"`{path} --version` failed: {error}"}
    return {"provider": provider, "available": ok, "path": path, "version": version,
            "detail": "" if ok else f"`{path} --version` exited with {proc.returncode}"}


# Only editor/native tooling is exposed. Public REST, evaluation and generated
# FeatureScript are deliberately excluded even when installed in the account.
NATIVE_TOOLS = ("bridge_status", "resolve_target", "search_commands", "browse_commands", "native_catalog",
    "native_state", "native_schema", "native_read", "native_write", "open_document", "editor_tab",
    "document_tree", "element_tree", "document_history", "document_edit", "sidebar_edit", "artifact_page",
    "display_state", "set_visibility", "mate_animation", "view_control", "capture_viewport",
    "assembly_transform", "native_measure", "assembly_feature", "ui_inspect", "ui_action")


def native_mcp_config() -> dict[str, Any] | None:
    """Reuse the installed plugin's launcher, without copying credentials or enabling other plugins.

    Workers can set R3_ONSHAPE_NATIVE_MCP_CONFIG to an installation's .mcp.json.
    Desktop sessions also discover the installed native plugin or stdio account entry.
    """
    explicit = os.environ.get("R3_ONSHAPE_NATIVE_MCP_CONFIG")
    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    if explicit:
        candidates = [Path(explicit).expanduser()]
    else:
        account = home / "config.toml"
        if account.is_file():
            config = tomllib.loads(account.read_text()).get("mcp_servers", {}).get("onshape_native")
            if config and config.get("command"):
                return {key: value for key, value in config.items() if key in ("command", "args", "env", "cwd")}
        candidates = sorted((home / "plugins" / "cache").glob("**/onshape-native/*/.mcp.json"), reverse=True)
    for path in candidates:
        config = json.loads(path.read_text()).get("mcpServers", {}).get("onshape_native")
        if config and config.get("command"):
            return {key: value for key, value in config.items() if key in ("command", "args", "env", "cwd")}
    return None


def mcp_config(project_root: Path, python: str = sys.executable) -> dict[str, Any]:
    """R3 plus the installed native editor server, bound to this project."""
    servers = {MCP_SERVER: {"command": python, "args": ["-m", "r3_orchestrator.mcp_server"],
                           "env": {"R3_PROJECT": str(project_root), "PYTHONPATH": r3_pythonpath()}}}
    native = native_mcp_config()
    if native:
        servers["onshape_native"] = native
        # Give the R3 pull process the same bridge runtime as the editor server.
        runtime = native.get("env", {}).get("ONSHAPE_NATIVE_RUNTIME")
        if runtime:
            servers[MCP_SERVER]["env"]["ONSHAPE_NATIVE_RUNTIME"] = runtime
    return {"mcpServers": servers}


def _tools() -> tuple[list[str], list[str]]:
    from .. import mcp_server

    return mcp_server.ungated_tools(), mcp_server.gated_tools()


def claude_command(executable: str, config_path: Path, model: str | None = None) -> list[str]:
    allowed, gated = _tools()
    command = [executable, "-p", "--output-format", "stream-json", "--verbose",
               "--mcp-config", str(config_path), "--strict-mcp-config", "--permission-mode", "default",
               "--append-system-prompt", SYSTEM_PROMPT,
               "--allowedTools", *[f"mcp__{MCP_SERVER}__{t}" for t in allowed], *CLAUDE_READ_TOOLS,
               "--disallowedTools", *[f"mcp__{MCP_SERVER}__{t}" for t in gated],
               "mcp__onshape_native__evaluate", "mcp__onshape_native__feature_studio", "mcp__onshape_native__api_read",
               "mcp__onshape_native__api_write", "mcp__onshape_native__api_upload", "mcp__onshape_native__api_request"]
    # An explicit allowlist keeps discovery/modeling available under strict MCP config.
    index = command.index("--disallowedTools")
    command[index:index] = [f"mcp__onshape_native__{tool}" for tool in NATIVE_TOOLS]
    if model:
        command += ["--model", model]
    return command


def _toml(value: Any) -> str:
    """TOML literal for a ``codex -c key=value`` override (JSON strings are valid TOML basic strings)."""
    if isinstance(value, dict):
        return "{" + ", ".join(f"{json.dumps(k)} = {_toml(v)}" for k, v in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    return json.dumps(value)


def codex_permission_profile(project_root: Path) -> dict[str, Any]:
    """Keep workspace protections, but allow this project's explicitly requested Git operations.

    The standard workspace profile protects .git. Noninteractive sessions cannot
    ask to escalate a normal commit/push, so grant that exact directory rather
    than disabling the sandbox or making other repositories writable.
    """
    return {"extends": ":workspace", "filesystem": {str(project_root.resolve() / ".git"): "write"},
            "network": {"enabled": True}}


def codex_scope_overrides(project_root: Path, account_home: Path | None = None) -> list[str]:
    """A robot session uses R3 tools and its project, not unrelated account plugins/skills.

    Authentication and provider settings still come from the user's normal CLI.
    Overrides apply only to this process; account files are never rewritten.
    """
    home = account_home or Path.home()
    codex_home = home / ".codex" if account_home else Path(os.environ.get("CODEX_HOME", home / ".codex"))
    path = codex_home / "config.toml"
    try:
        config = tomllib.loads(path.read_text()) if path.exists() else {}
    except (OSError, tomllib.TOMLDecodeError):
        config = {}  # the CLI reports malformed account configuration itself
    overrides = []
    for name in sorted(config.get("mcp_servers", {})):
        if name not in (MCP_SERVER, "onshape_native"):
            overrides += ["-c", f"mcp_servers.{name}.enabled=false"]
    for name in sorted(config.get("plugins", {})):
        overrides += ["-c", f"plugins.{name}.enabled=false"]
    overrides += ["-c", "apps._default.enabled=false"]
    for name in sorted(config.get("apps", {})):
        if name != "_default":
            overrides += ["-c", f"apps.{name}.enabled=false"]
    roots = (codex_home / "skills", home / ".agents" / "skills")
    skills = {p.parent.resolve() for root in roots if root.exists() for p in root.rglob("SKILL.md")}
    # rglob does not traverse directory symlinks. Standalone skill links are
    # common in account roots, and must receive the same scope treatment.
    skills.update(p.resolve() for root in roots if root.exists() for p in root.iterdir() if (p / "SKILL.md").is_file())
    skills.update(Path(item["path"]).expanduser().resolve() for item in config.get("skills", {}).get("config", []) if item.get("path"))
    project_root = project_root.resolve()
    platform_root = Path(__file__).resolve().parents[4]
    outside = [p for p in sorted(skills) if p != project_root and project_root not in p.parents
               and p != platform_root and platform_root not in p.parents]
    # Releases differ on whether skill policy keys identify the folder or its
    # SKILL.md. Include both forms; neither enables or copies account content.
    disabled = [{"path": str(path), "enabled": False} for p in outside for path in (p, p / "SKILL.md")]
    overrides += ["-c", f"skills.config={_toml(disabled)}"]
    return overrides


def codex_command(executable: str, project_root: Path, config: dict[str, Any], model: str | None = None) -> list[str]:
    allowed, gated = _tools()
    server = config["mcpServers"][MCP_SERVER]
    prefix = f"mcp_servers.{MCP_SERVER}"
    command = [executable, "exec", "--json", "--skip-git-repo-check", "--color", "never", "-C", str(project_root),
               "-c", 'approval_policy="never"',
               "-c", 'default_permissions="r3_session"',
               "-c", f"permissions.r3_session={_toml(codex_permission_profile(project_root))}",
               "-c", 'web_search="live"',
               "-c", f"developer_instructions={_toml(SYSTEM_PROMPT)}",
               "-c", f"{prefix}.enabled=true",
               "-c", f"{prefix}.command={_toml(server['command'])}", "-c", f"{prefix}.args={_toml(server['args'])}",
               "-c", f"{prefix}.env={_toml(server['env'])}", "-c", f"{prefix}.disabled_tools={_toml(gated)}",
               "-c", f"{prefix}.enabled_tools={_toml(allowed)}",
               "-c", f'{prefix}.default_tools_approval_mode="prompt"',
               "-c", f"{prefix}.tools={_toml({tool: {'approval_mode': 'approve'} for tool in allowed})}",
               "-c", f"{prefix}.startup_timeout_sec=120", "-c", f"{prefix}.tool_timeout_sec={MCP_TOOL_TIMEOUT_S}"]
    native = config["mcpServers"].get("onshape_native")
    if native:
        for key in ("command", "args", "env", "cwd"):
            if key in native:
                command += ["-c", f"mcp_servers.onshape_native.{key}={_toml(native[key])}"]
        command += ["-c", "mcp_servers.onshape_native.enabled=true",
                    "-c", f"mcp_servers.onshape_native.enabled_tools={_toml(NATIVE_TOOLS)}",
                    "-c", f"mcp_servers.onshape_native.tools={_toml({tool: {'approval_mode': 'approve'} for tool in NATIVE_TOOLS})}",
                    "-c", f"mcp_servers.onshape_native.tool_timeout_sec={MCP_TOOL_TIMEOUT_S}"]
    command += codex_scope_overrides(project_root)
    if model:
        command += ["--model", model]
    return [*command, "-"]


# ------------------------------------------------------------- normalization
def _bare_tool(name: str) -> tuple[str, str | None]:
    if name.startswith("mcp__"):
        _, server, tool = name.split("__", 2)
        return tool, server
    return name, None


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_text(c.get("text", c) if isinstance(c, dict) else c) for c in content)
    if isinstance(content, dict):
        if "text" in content:
            return str(content["text"])
        if "content" in content:
            return _text(content["content"])
        return json.dumps(content, default=str)
    return "" if content is None else str(content)


def _json_payload(content: Any) -> dict[str, Any] | None:
    if isinstance(content, dict) and isinstance(content.get("structured_content"), dict):
        return content["structured_content"]
    text = _text(content).strip()
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def normalize_claude(event: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    kind = event.get("type")
    if kind == "system":
        if event.get("subtype") == "init":
            return [("message", {"role": "system", "text": f"session {event.get('session_id', '')} model {event.get('model', '')}",
                                 "mcp_servers": event.get("mcp_servers"), "tools": event.get("tools")})]
        return [("raw", {"event": event})]
    if kind in ("assistant", "user"):
        out = []
        content = (event.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else [{"type": "text", "text": _text(content)}]:
            btype = block.get("type")
            if btype == "text" and block.get("text"):
                out.append(("message", {"role": kind, "text": block["text"]}))
            elif btype == "tool_use":
                name, server = _bare_tool(block.get("name", ""))
                out.append(("tool_call", {"id": block.get("id"), "name": name, "server": server, "input": block.get("input") or {}}))
            elif btype == "tool_result":
                out.append(("tool_result", {"id": block.get("tool_use_id"), "ok": not block.get("is_error", False),
                                            "content": _text(block.get("content"))[:MAX_CONTENT],
                                            "_payload": _json_payload(block.get("content"))}))
            elif btype == "thinking" and block.get("thinking"):
                out.append(("message", {"role": "thinking", "text": block["thinking"]}))
        return out
    if kind == "result":
        usage = event.get("usage") or {}
        return [("usage", {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
                           "cost_usd": event.get("total_cost_usd"), "turns": event.get("num_turns")}),
                ("_result", {"ok": not event.get("is_error", False) and event.get("subtype") == "success",
                             "result": event.get("result", ""), "subtype": event.get("subtype")})]
    return [("raw", {"event": event})]


def normalize_codex(event: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    kind = event.get("type", "")
    item = event.get("item") or {}
    itype = item.get("type")
    if kind == "thread.started":
        return [("message", {"role": "system", "text": f"thread {event.get('thread_id', '')}"})]
    if kind in ("item.started", "item.updated", "item.completed") and itype:
        done = kind == "item.completed"
        if itype == "agent_message" and done:
            return [("message", {"role": "assistant", "text": item.get("text", "")})]
        if itype == "reasoning" and done:
            return [("message", {"role": "thinking", "text": item.get("text", "")})]
        if itype == "mcp_tool_call":
            if kind == "item.started":
                return [("tool_call", {"id": item.get("id"), "name": item.get("tool"), "server": item.get("server"),
                                       "input": item.get("arguments") or {}})]
            if done:
                error = item.get("error")
                result = item.get("result")
                return [("tool_result", {"id": item.get("id"), "name": item.get("tool"),
                                         "ok": not error and item.get("status") != "failed",
                                         "content": _text(error or result)[:MAX_CONTENT], "_payload": _json_payload(result)})]
            return []
        if itype == "command_execution":
            if kind == "item.started":
                return [("tool_call", {"id": item.get("id"), "name": "shell", "server": None, "input": {"command": item.get("command")}})]
            if done:
                return [("tool_result", {"id": item.get("id"), "name": "shell", "ok": item.get("exit_code") == 0,
                                         "content": str(item.get("aggregated_output", ""))[:MAX_CONTENT], "_payload": None})]
            return []
        if itype == "file_change" and done:
            return [("tool_result", {"id": item.get("id"), "name": "file_change", "ok": item.get("status") != "failed",
                                     "content": json.dumps(item.get("changes", []))[:MAX_CONTENT], "_payload": None})]
        if not done:
            return []
    if kind == "turn.completed":
        usage = event.get("usage") or {}
        return [("usage", {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")})]
    if kind == "turn.failed":
        return [("_result", {"ok": False, "result": "", "error": (event.get("error") or {}).get("message")})]
    if kind == "error":
        return [("message", {"role": "error", "text": event.get("message", "")})]
    return [("raw", {"event": event})]


NORMALIZERS = {"claude": normalize_claude, "codex": normalize_codex}


# ----------------------------------------------------------------- sessions
@dataclass
class AgentSession:
    id: str
    project_id: str
    root: Path
    provider: str
    model: str | None
    prompt: str
    command: list[str] = field(default_factory=list)
    status: str = "starting"
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    returncode: int | None = None
    result: str = ""
    error: str | None = None
    events: EventLog = field(default_factory=EventLog)
    directory: Path | None = None
    cancel_requested: bool = False
    _process: Any = None
    _calls: dict[str, dict[str, Any]] = field(default_factory=dict)
    _spec_start: str = ""
    _spec_last: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def to_json(self) -> dict[str, Any]:
        return {"session_id": self.id, "project": self.project_id, "provider": self.provider, "model": self.model,
                "prompt": self.prompt, "status": self.status, "created_at": self.created_at, "finished_at": self.finished_at,
                "returncode": self.returncode, "result": self.result, "error": self.error, "events": len(self.events),
                "transcript": str(self.directory / "transcript.jsonl") if self.directory else None}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _diff(before: str, after: str) -> str:
    return "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                        "a/robot.yaml", "b/robot.yaml"))


class AgentManager:
    def __init__(self, executables: dict[str, str] | None = None, python: str = sys.executable,
                 extra_env: dict[str, str] | None = None) -> None:
        self.executables = dict(executables or {})
        self.python = python
        self.extra_env = dict(extra_env or {})
        self._sessions: dict[str, AgentSession] = {}
        self._detected: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------- queries
    def providers(self, refresh: bool = False) -> list[dict[str, Any]]:
        if refresh:
            self._detected.clear()
        for provider in PROVIDERS:
            if provider not in self._detected:
                self._detected[provider] = detect(provider, self.executables.get(provider))
        return [self._detected[p] for p in PROVIDERS]

    def get(self, session_id: str) -> AgentSession | None:
        return self._sessions.get(session_id)

    def list(self, project_id: str | None = None) -> list[AgentSession]:
        return sorted((s for s in self._sessions.values() if project_id in (None, s.project_id)), key=lambda s: s.created_at)

    def active(self, project_id: str) -> AgentSession | None:
        return next((s for s in self.list(project_id) if s.status in ("starting", "running")), None)

    # ------------------------------------------------------------ lifecycle
    def start(self, project_id: str, root: Path, prompt: str, provider: str = "claude", model: str | None = None) -> AgentSession:
        if provider not in PROVIDERS:
            raise ApiError("usage_error", f"unknown provider {provider!r}; use one of {', '.join(PROVIDERS)}")
        info = next(p for p in self.providers() if p["provider"] == provider)
        if not info["available"]:
            info = next(p for p in self.providers(refresh=True) if p["provider"] == provider)
        if not info["available"]:
            raise ApiError("agent_cli_missing", info["detail"], {"provider": provider, "providers": self.providers()})
        busy = self.active(project_id)
        if busy is not None:
            raise ApiError("agent_busy", f"agent session {busy.id} is still running on {project_id}; cancel it first",
                           {"session_id": busy.id})
        session = AgentSession(uuid.uuid4().hex[:12], project_id, Path(root), provider, model, prompt)
        session.directory = Path(root) / "build" / "agent" / session.id
        session.directory.mkdir(parents=True, exist_ok=True)
        config = mcp_config(Path(root), self.python)
        config_path = session.directory / ".mcp.json"
        config_path.write_text(json.dumps(config, indent=2))
        if provider == "claude":
            session.command = claude_command(info["path"], config_path, model)
            stdin = prompt
        else:
            session.command = codex_command(info["path"], Path(root), config, model)
            stdin = prompt
        spec = Path(root) / "robot.yaml"
        session._spec_start = session._spec_last = spec.read_text() if spec.exists() else ""
        env = {**os.environ, **self.extra_env, "R3_PROJECT": str(root), "PYTHONPATH": r3_pythonpath(),
               "MCP_TOOL_TIMEOUT": str(MCP_TOOL_TIMEOUT_S * 1000), "MCP_TIMEOUT": "120000"}
        self._sessions[session.id] = session
        session.events.emit("session_started", session_id=session.id, project=project_id, provider=provider, model=model,
                            cwd=str(root), command=[c if c != SYSTEM_PROMPT else "<system prompt>" for c in session.command],
                            cli_version=info["version"])
        try:
            process = subprocess.Popen(session.command, cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, bufsize=1, start_new_session=True)
        except OSError as error:
            self._finish(session, "failed", error=f"could not start {provider}: {error}")
            return session
        session._process = process
        session.status = "running"
        threading.Thread(target=self._feed, args=(process, stdin), daemon=True).start()
        stderr_reader = threading.Thread(target=self._read_stderr, args=(session, process), daemon=True)
        stderr_reader.start()
        threading.Thread(target=self._read_stdout, args=(session, process, stderr_reader), daemon=True,
                         name=f"r3-agent-{session.id}").start()
        return session

    def cancel(self, session_id: str) -> AgentSession | None:
        session = self._sessions.get(session_id)
        if session is None or session.status not in ("starting", "running"):
            return session
        session.cancel_requested = True
        process = session._process
        if process is not None and process.poll() is None:
            _signal_group(process, signal.SIGTERM)
            threading.Timer(5.0, lambda: process.poll() is None and _signal_group(process, signal.SIGKILL)).start()
        return session

    def shutdown(self) -> None:
        for session in list(self._sessions.values()):
            self.cancel(session.id)

    # ------------------------------------------------------------ internals
    @staticmethod
    def _feed(process: subprocess.Popen, text: str) -> None:
        try:
            process.stdin.write(text)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    def _read_stderr(self, session: AgentSession, process: subprocess.Popen) -> None:
        with open(session.directory / "stderr.log", "w") as log:
            for line in process.stderr:
                log.write(line)
                if line.strip():
                    self._emit(session, "stderr", line=line.rstrip()[:4000])

    def _read_stdout(self, session: AgentSession, process: subprocess.Popen, stderr_reader: threading.Thread) -> None:
        normalize = NORMALIZERS[session.provider]
        outcome: dict[str, Any] = {}
        with open(session.directory / "transcript.jsonl", "w") as transcript:
            for line in process.stdout:
                transcript.write(line)
                transcript.flush()
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    self._emit(session, "message", role="output", text=line[:MAX_CONTENT])
                    continue
                if not isinstance(event, dict):
                    self._emit(session, "raw", event=event)
                    continue
                for kind, payload in normalize(event):
                    if kind == "_result":
                        outcome = payload
                    else:
                        self._handle(session, kind, payload)
        returncode = process.wait()
        stderr_reader.join(timeout=5)
        session.returncode = returncode
        self._check_spec(session)
        if session.cancel_requested:
            status = "cancelled"
        elif returncode == 0 and outcome.get("ok", True):
            status = "completed"
        else:
            status = "failed"
        session.result = str(outcome.get("result") or "")
        error = outcome.get("error") or (None if status != "failed" else f"{session.provider} exited with code {returncode}")
        self._finish(session, status, error=error)

    def _handle(self, session: AgentSession, kind: str, payload: dict[str, Any]) -> None:
        if kind == "tool_call":
            session._calls[str(payload.get("id"))] = payload
            self._emit(session, "tool_call", **payload)
            if payload.get("name") in RUN_TOOLS and payload.get("server") in (MCP_SERVER, None):
                arguments = payload.get("input") or {}
                stages = arguments.get("stages") or ([arguments["stage"]] if arguments.get("stage") else None)
                scenarios = arguments.get("scenarios") or ([arguments["scenario"]] if arguments.get("scenario") else None)
                if payload.get("name") == "r3_run_project_tests":
                    stages = ["tests"]
                self._emit(session, "run_started", tool_call_id=payload.get("id"), tool=payload.get("name"),
                           stages=stages if payload.get("name") != "r3_run_scenario" else ["sim"], scenarios=scenarios)
            return
        if kind == "tool_result":
            parsed = payload.pop("_payload", None)
            call = session._calls.get(str(payload.get("id")), {})
            payload["name"] = payload.get("name") or call.get("name")
            self._emit(session, "tool_result", **payload)
            if call.get("name") in RUN_TOOLS:
                parsed = parsed or {}
                self._emit(session, "run_finished", tool_call_id=payload.get("id"), tool=call.get("name"),
                           ok=bool(parsed.get("ok", payload.get("ok"))), counts=parsed.get("counts"),
                           stages=parsed.get("stages"), code=parsed.get("code"))
            self._check_spec(session)
            return
        self._emit(session, kind, **payload)

    def _check_spec(self, session: AgentSession) -> None:
        spec = session.root / "robot.yaml"
        current = spec.read_text() if spec.exists() else ""
        with session._lock:
            if current == session._spec_last:
                return
            previous, session._spec_last = session._spec_last, current
        self._emit(session, "spec_changed", previous_sha256=_sha(previous), sha256=_sha(current), diff=_diff(previous, current),
                   cumulative_diff=_diff(session._spec_start, current))

    @staticmethod
    def _emit(session: AgentSession, kind: str, **payload: Any) -> None:
        try:
            session.events.emit(kind, session_id=session.id, **payload)
        except RuntimeError:  # session already finished (late stderr after exit)
            pass

    def _finish(self, session: AgentSession, status: str, error: str | None = None) -> None:
        session.status = status
        session.error = error
        session.finished_at = time.time()
        self._emit(session, "session_finished", status=status, returncode=session.returncode, result=session.result,
                   error=error, spec_changed=session._spec_last != session._spec_start,
                   cumulative_diff=_diff(session._spec_start, session._spec_last))
        session.events.close()


def _signal_group(process: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(process.pid, sig)
    except (ProcessLookupError, PermissionError):
        try:
            process.send_signal(sig)
        except ProcessLookupError:
            pass


def available(providers: Iterable[dict[str, Any]]) -> list[str]:
    return [p["provider"] for p in providers if p["available"]]

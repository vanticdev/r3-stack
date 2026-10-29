"""Operations shared by the ``r3`` CLI and the MCP server.

Every function returns JSON-serializable dicts so humans (CLI), agents (MCP,
``--json``) and CI see identical structured results.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from r3_model.diagnostics import Diagnostic, Severity, StageResult
from r3_model.project import Project

from . import stages as stage_runner
from .hints import hint_for

PROJECT_ENV = "R3_PROJECT"
MAX_INLINE_ARTIFACT = 2_000_000  # bytes returned inline by get_artifact


class ProjectNotFound(FileNotFoundError):
    pass


def resolve_project(path: str | Path | None = None) -> Path:
    """Explicit path, else ``$R3_PROJECT``, else the nearest ancestor of the cwd with a robot.yaml."""
    start = Path(path or os.environ.get(PROJECT_ENV) or Path.cwd()).expanduser().resolve()
    for candidate in (start, *start.parents):
        if (candidate / "robot.yaml").exists():
            return candidate
    raise ProjectNotFound(f"no robot.yaml in {start} or its parents (pass --project or set {PROJECT_ENV})")


def load_project(path: str | Path | None = None) -> Project:
    return Project.load(resolve_project(path))


def _diag(d: Diagnostic) -> dict[str, Any]:
    data = d.model_dump(mode="json")
    data["hint"] = hint_for(d.code, d.hint)
    return data


def _stage_summary(name: str, result: StageResult | None, fingerprint: dict[str, str]) -> dict[str, Any]:
    if result is None:
        return {"stage": name, "status": "missing", "ok": None}
    stale = result.inputs.get("robot_yaml_sha256") != fingerprint.get("robot_yaml_sha256")
    return {"stage": name, "status": stage_runner.status_of(result), "ok": result.ok, "stale": stale,
            "counts": stage_runner.counts(result.diagnostics), "duration_s": round(result.duration_s, 3),
            "started_at": result.started_at, "metrics": result.metrics,
            "artifacts": [a.model_dump(mode="json") for a in result.artifacts]}


def _results_payload(project: Project, results: list[StageResult]) -> dict[str, Any]:
    fingerprint = project.fingerprint()
    diagnostics = [d for r in results for d in r.diagnostics]
    return {
        "ok": all(r.ok for r in results),
        "project": str(project.root),
        "robot": project.spec.robot.name,
        "inputs": fingerprint,
        "counts": stage_runner.counts(diagnostics),
        "stages": [_stage_summary(r.stage, r, fingerprint) for r in results],
        "diagnostics": [_diag(d) for d in diagnostics],
    }


def validate(project_path: str | Path | None = None, stages: list[str] | None = None,
             scenarios: list[str] | None = None) -> dict[str, Any]:
    project = load_project(project_path)
    results = stage_runner.validate(project, stages, scenarios=scenarios)
    return _results_payload(project, results)


def run_stage(project_path: str | Path | None, stage: str, scenarios: list[str] | None = None) -> dict[str, Any]:
    project = load_project(project_path)
    result = stage_runner.run_stage(project, stage, scenarios=scenarios)
    payload = _results_payload(project, [result])
    payload["assumptions"] = [a.model_dump(mode="json") for a in result.assumptions]
    return payload


def stored_diagnostics(project_path: str | Path | None = None, stage: str | None = None, severity: str | None = None,
                       subject: str | None = None) -> dict[str, Any]:
    project = load_project(project_path)
    results = stage_runner.load_results(project)
    selected = [r for name, r in results.items() if stage in (None, "", name)]
    diagnostics = [d for r in selected for d in r.diagnostics
                   if (not severity or d.severity.value == severity) and (not subject or subject in d.subjects)]
    payload = _results_payload(project, selected)
    payload["diagnostics"] = [_diag(d) for d in diagnostics]
    payload["counts"] = stage_runner.counts(diagnostics)
    return payload


def diagnose(project_path: str | Path | None = None, include_warnings: bool = True) -> dict[str, Any]:
    """Errors (and warnings) grouped by their primary subject, worst first."""
    project = load_project(project_path)
    results = stage_runner.load_results(project)
    if not results:
        stage_runner.validate(project)
        results = stage_runner.load_results(project)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results.values():
        for d in result.diagnostics:
            if d.severity is Severity.ERROR or (include_warnings and d.severity is Severity.WARNING):
                entry = _diag(d)
                entry["also"] = d.subjects[1:]
                groups[d.subjects[0] if d.subjects else d.stage or "general"].append(entry)
    rank = {"error": 0, "warning": 1, "info": 2}
    ordered = sorted(groups.items(), key=lambda kv: (min(rank[e["severity"]] for e in kv[1]), -len(kv[1]), kv[0]))
    errors = sum(1 for entries in groups.values() for e in entries if e["severity"] == "error")
    return {"ok": errors == 0, "project": str(project.root), "errors": errors,
            "subjects": [{"subject": subject, "diagnostics": sorted(entries, key=lambda e: rank[e["severity"]])}
                         for subject, entries in ordered]}


def status(project_path: str | Path | None = None) -> dict[str, Any]:
    project = load_project(project_path)
    fingerprint = project.fingerprint()
    results = stage_runner.load_results(project)
    git: dict[str, Any] = {}
    try:
        branch = subprocess.run(["git", "-C", str(project.root), "rev-parse", "--abbrev-ref", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(project.root), "status", "--porcelain", "--", "."],
                               capture_output=True, text=True, check=True).stdout.splitlines()
        git = {"branch": branch, "commit": fingerprint.get("git_commit", ""), "dirty_files": len(dirty)}
    except (subprocess.CalledProcessError, FileNotFoundError):
        git = {}
    spec = project.spec
    return {
        "ok": all(r.ok for r in results.values()),
        "project": str(project.root),
        "robot": spec.robot.model_dump(mode="json"),
        "inputs": fingerprint,
        "git": git,
        "lock": project.lock,
        "summary": {"components": len(spec.components), "harnesses": len(spec.electrical.harnesses),
                    "wires": sum(len(h.wires) for h in spec.electrical.harnesses), "boards": len(spec.pcb.boards),
                    "bindings": len(spec.software.bindings), "scenarios": [s.id for s in spec.simulation.scenarios]},
        "stages": [_stage_summary(name, results.get(name), fingerprint) for name in stage_runner.ALL_STAGES],
    }


def list_catalog(project_path: str | Path | None = None, kind: str | None = None, query: str | None = None) -> dict[str, Any]:
    project = load_project(project_path)
    catalog = project.catalog
    needle = (query or "").lower()

    def matches(*fields: str) -> bool:
        return not needle or any(needle in f.lower() for f in fields if f)

    components = [
        {"id": c.id, "kind": c.kind, "name": c.name, "part_number": c.part_number, "mass": c.mechanical.mass,
         "size": c.mechanical.size, "shape": c.mechanical.shape, "connectors": {n: i.type for n, i in c.connectors.items()},
         "pins": {n: p.role for n, p in c.pins.items()}, "behavior": c.behavior.model if c.behavior else None,
         "provenance": c.provenance.status}
        for c in catalog.components.values() if (not kind or c.kind == kind) and matches(c.id, c.name, c.part_number, c.description)]
    connectors = [{"id": c.id, "family": c.family, "gender": c.gender, "mates_with": c.mates_with, "pin_count": c.pin_count,
                   "current_rating_per_pin": c.current_rating_per_pin, "awg": [c.awg_min, c.awg_max]}
                  for c in catalog.connectors.values() if kind in (None, "", "connector") and matches(c.id, c.family)]
    wires = [{"awg": w.awg, "ampacity": w.ampacity, "resistance_per_m": w.resistance_per_m, "outer_diameter": w.outer_diameter}
             for w in catalog.wires.values() if kind in (None, "", "wire")]
    return {"ok": True, "components": components, "connectors": connectors, "wires": wires}


def get_artifact(project_path: str | Path | None, path: str) -> dict[str, Any]:
    """Read a file under ``build/``. JSON is parsed, text returned, images base64-encoded."""
    project = load_project(project_path)
    build = (project.root / "build").resolve()
    target = (build / path.removeprefix("build/")).resolve()
    if build not in (target, *target.parents):
        return {"ok": False, "code": "artifact_outside_build", "path": path}
    if not target.exists():
        available = sorted(str(p.relative_to(build)) for p in build.rglob("*") if p.is_file())[:200] if build.exists() else []
        return {"ok": False, "code": "artifact_missing", "path": path, "available": available}
    if target.is_dir():
        return {"ok": True, "path": path, "kind": "directory",
                "entries": sorted(str(p.relative_to(build)) for p in target.rglob("*") if p.is_file())}
    size = target.stat().st_size
    entry: dict[str, Any] = {"ok": True, "path": str(target.relative_to(build)), "size": size}
    if size > MAX_INLINE_ARTIFACT:
        return {**entry, "kind": "file", "truncated": True, "absolute_path": str(target)}
    suffix = target.suffix.lower()
    if suffix == ".json":
        return {**entry, "kind": "json", "content": json.loads(target.read_text())}
    if suffix in (".png", ".jpg", ".jpeg", ".gif", ".npz", ".stl", ".glb", ".zip"):
        return {**entry, "kind": "binary", "mime": {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(suffix, "application/octet-stream"),
                "base64": base64.b64encode(target.read_bytes()).decode()}
    return {**entry, "kind": "text", "content": target.read_text(errors="replace")}


def deploy_sim(project_path: str | Path | None = None, scenarios: list[str] | None = None) -> dict[str, Any]:
    payload = run_stage(project_path, "sim", scenarios)
    payload["target"] = "sim"
    return payload


def deploy_fleet(project_path: str | Path | None = None, confirm: bool = False, deploy_args: list[str] | None = None,
                 force: bool = False) -> dict[str, Any]:
    """Deploy robot software to physical targets through BLITZ ``backend/deploy.py``.

    Touches real hardware, so it requires ``confirm=True`` and, unless
    ``force``, a stored validation with no errors for the current spec."""
    if not confirm:
        return {"ok": False, "code": "confirmation_required", "target": "fleet",
                "message": "fleet deployment changes software on physical robots; re-run with --yes (CLI) or confirm=true (MCP)"}
    project = load_project(project_path)
    deploy = project.root / "backend" / "deploy.py"
    if not deploy.exists():
        return {"ok": False, "code": "deploy_script_missing", "path": str(deploy),
                "message": "project has no BLITZ backend/deploy.py"}
    if not force:
        results = stage_runner.load_results(project)
        fingerprint = project.fingerprint()
        blocking = [name for name, r in results.items() if not r.ok]
        stale = [name for name, r in results.items() if r.inputs.get("robot_yaml_sha256") != fingerprint["robot_yaml_sha256"]]
        if "model" not in results or blocking or "model" in stale:
            return {"ok": False, "code": "validation_required", "failed_stages": blocking, "stale_stages": stale,
                    "message": "run `r3 validate` on the current robot.yaml (no errors) before deploying, or pass --force"}
    venv_python = project.root / ".venv" / "bin" / "python"
    python = str(venv_python) if venv_python.exists() else sys.executable
    command = [python, "backend/deploy.py", *(deploy_args or [])]
    started = time.time()
    completed = subprocess.run(command, cwd=project.root, capture_output=True, text=True)
    log = project.build_dir("deploy") / "fleet.log"
    log.write_text(f"$ {' '.join(command)}\n{completed.stdout}\n{completed.stderr}")
    return {"ok": completed.returncode == 0, "target": "fleet", "command": command, "returncode": completed.returncode,
            "duration_s": round(time.time() - started, 3), "log": str(log.relative_to(project.root)),
            "stdout_tail": completed.stdout[-4000:], "stderr_tail": completed.stderr[-4000:]}


def create_project(name: str, parent: str | Path = ".", template: str = "rc-car", github: bool = False, private: bool = True,
                   confirm: bool = False, platform_url: str | None = None) -> dict[str, Any]:
    from r3_github import scaffold

    target = Path(parent).expanduser().resolve() / name
    try:
        created = scaffold.create_project(target, name=name, template=template, platform_url=platform_url)
    except (FileExistsError, KeyError, ValueError) as error:
        return {"ok": False, "code": "create_failed", "message": str(error)}
    payload: dict[str, Any] = {"ok": True, "path": str(target), "files": created}
    if github:
        if not confirm:
            return {**payload, "ok": False, "code": "confirmation_required",
                    "message": "creating a GitHub repository is an external action; pass confirm=true"}
        payload["github"] = scaffold.create_remote(name, target, private=private)
        payload["ok"] = bool(payload["github"].get("ok"))
    return payload

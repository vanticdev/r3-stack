"""Project discovery and the per-domain read models served by the HTTP API.

Every view reads the same files the CLI and MCP server read (``robot.yaml``,
``build/<stage>/result.json`` and the stage hand-off JSON of
``docs/architecture/r3-pipeline.md``); nothing is cached across requests except
the decoded simulation recordings (keyed by file mtime). Paths to files under
``build/`` are returned as API URLs (``/api/projects/<pid>/artifacts/<path>``).
A view never fails because a stage has not run: missing inputs are ``null``
and listed in ``missing``.
"""

from __future__ import annotations

import csv
import json
import math
import re
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from r3_model.project import Project

from .. import api, release, spec_ops
from .. import stages as stage_runner
from .errors import ApiError

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".svg", ".gif")
VIDEO_SUFFIXES = (".mp4", ".webm")
_PID = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class ProjectRef:
    id: str
    root: Path
    projects_root: Path


def default_projects_roots() -> list[Path]:
    from r3_github.scaffold import examples_dir

    return [examples_dir()]


class ProjectRegistry:
    """Projects are directories with a ``robot.yaml``: each root itself, or its direct children.
    The project id is the directory name (``-2``, ``-3`` ... on collisions between roots)."""

    def __init__(self, roots: list[str | Path] | None = None) -> None:
        self.roots = [Path(r).expanduser().resolve() for r in (roots or default_projects_roots())]

    def scan(self) -> dict[str, ProjectRef]:
        found: dict[str, ProjectRef] = {}
        for root in self.roots:
            candidates = [root] if (root / "robot.yaml").exists() else sorted(
                p for p in root.iterdir() if p.is_dir() and (p / "robot.yaml").exists()) if root.is_dir() else []
            for path in candidates:
                base = _PID.sub("-", path.name) or "project"
                pid, n = base, 2
                while pid in found:
                    pid, n = f"{base}-{n}", n + 1
                found[pid] = ProjectRef(pid, path.resolve(), root)
        return found

    def get(self, pid: str) -> ProjectRef:
        ref = self.scan().get(pid)
        if ref is None:
            raise ApiError("project_not_found", f"no project {pid!r} under {', '.join(map(str, self.roots))}", {"project": pid})
        return ref

    def id_of(self, path: Path) -> str | None:
        path = path.resolve()
        return next((ref.id for ref in self.scan().values() if ref.root == path), None)

    @property
    def create_root(self) -> Path:
        """Where ``POST /api/projects`` scaffolds: the first root (a project root's parent if it is one)."""
        root = self.roots[0]
        return root.parent if (root / "robot.yaml").exists() else root


def load(ref: ProjectRef) -> Project:
    try:
        return Project.load(ref.root)
    except Exception as error:  # schema errors, YAML errors, assembly compile errors
        raise ApiError("project_invalid", f"{ref.root / 'robot.yaml'} does not load: {error}",
                       {"project": ref.id, "exception": type(error).__name__}) from error


# ------------------------------------------------------------------ helpers
def artifact_url(pid: str, relative: str | Path) -> str:
    return f"/api/projects/{pid}/artifacts/{str(relative).lstrip('/')}"


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _rel(build: Path, path: Path) -> str:
    return str(path.relative_to(build))


def _files(build: Path, folder: str, suffixes: tuple[str, ...]) -> list[str]:
    base = build / folder
    if not base.is_dir():
        return []
    return sorted(_rel(build, p) for p in base.rglob("*") if p.is_file() and p.suffix.lower() in suffixes)


def _urls(pid: str, paths: list[str]) -> list[dict[str, str]]:
    return [{"path": p, "url": artifact_url(pid, p), "name": Path(p).stem.replace("_", " ")} for p in paths]


def _result(project: Project, stage: str, fingerprint: dict[str, str]) -> dict[str, Any] | None:
    path = stage_runner.result_path(project, stage)
    data = _read_json(path)
    if not isinstance(data, dict):
        return None
    data["status"] = "unavailable" if any(d.get("code") == "stage_unavailable" for d in data.get("diagnostics", [])) else \
        ("ok" if data.get("ok") else "failed")
    data["stale"] = release.is_stale(data.get("inputs"), fingerprint)
    return data


def _stage_block(project: Project, pid: str, stage: str, fingerprint: dict[str, str]) -> dict[str, Any] | None:
    """A stage's result with diagnostics carrying hints and artifact URLs."""
    data = _result(project, stage, fingerprint)
    if data is None:
        return None
    from ..hints import hint_for

    for d in data.get("diagnostics", []):
        d["hint"] = hint_for(d.get("code", ""), d.get("hint", ""))
    for artifact in data.get("artifacts", []):
        artifact["url"] = artifact_url(pid, artifact["path"])
    return data


def _csv_rows(path: Path) -> list[dict[str, Any]] | None:
    if not path.exists():
        return None
    with open(path, newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        for key, value in row.items():
            try:
                row[key] = int(value) if value and value.lstrip("-").isdigit() else float(value) if value and re.fullmatch(
                    r"-?\d+\.\d*(e-?\d+)?", value) else value
            except (TypeError, ValueError):
                pass
    return rows


def _finite(value: Any) -> Any:
    """JSON cannot carry NaN/inf: map them to None recursively."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_finite(v) for v in value]
    return value


# -------------------------------------------------------------------- views
def summary_entry(ref: ProjectRef, runs: Any = None) -> dict[str, Any]:
    """One row of ``GET /api/projects``. Unloadable projects are listed with ``error``."""
    entry: dict[str, Any] = {"id": ref.id, "path": str(ref.root), "projects_root": str(ref.projects_root)}
    try:
        project = Project.load(ref.root)
    except Exception as error:  # noqa: BLE001 - list it anyway so the UI can show the problem
        return {**entry, "name": ref.id, "description": "", "version": "", "error": f"{type(error).__name__}: {error}",
                "last_run": None}
    fingerprint = project.fingerprint()
    results = stage_runner.load_results(project)
    stage_status = {name: stage_runner.status_of(results.get(name)) for name in stage_runner.stage_names()}
    finished = [r.started_at + r.duration_s for r in results.values()]
    any_stale = any(release.is_stale(r.inputs, fingerprint) for r in results.values())
    last_run: dict[str, Any] | None = None
    if results:
        failed = [n for n, s in stage_status.items() if s == "failed"]
        last_run = {"status": "failed" if failed else "ok", "finished_at": max(finished), "failed_stages": failed,
                    "stale": any_stale, "counts": stage_runner.counts([d for r in results.values() for d in r.diagnostics])}
    latest = runs.latest(ref.id) if runs is not None else None
    if latest is not None:
        last_run = {**(last_run or {}), "run_id": latest.id, "run_status": latest.status}
    spec = project.spec
    return {**entry, "name": spec.robot.name, "description": spec.robot.description, "version": spec.robot.version,
            "robot_yaml_sha256": fingerprint.get("robot_yaml_sha256"), "stages": stage_status, "last_run": last_run,
            "onshape_url": (project.lock.get("cad") or {}).get("url"), "error": None}


def detail(ref: ProjectRef) -> dict[str, Any]:
    project = load(ref)
    payload = api.status(ref.root)
    fingerprint = payload["inputs"]
    results = {name: _stage_block(project, ref.id, name, fingerprint) for name in stage_runner.ALL_STAGES}
    report = _read_json(ref.root / "build" / "report" / "summary.json")
    cad_lock = project.lock.get("cad") or {}
    git_remote = None
    if (ref.root / ".git").exists() or payload.get("git"):
        import subprocess

        proc = subprocess.run(["git", "-C", str(ref.root), "remote", "get-url", "origin"], capture_output=True, text=True)
        git_remote = proc.stdout.strip() or None if proc.returncode == 0 else None
    return {**payload, "id": ref.id, "fingerprint": fingerprint,
            "results": {k: v for k, v in results.items() if v is not None},
            "report": report, "report_url": artifact_url(ref.id, "report/index.html") if (ref.root / "build/report/index.html").exists() else None,
            "release": release.evaluate(project),
            "links": {"onshape": cad_lock.get("url"), "github": _github_url(git_remote)}}


def _github_url(remote: str | None) -> str | None:
    if not remote:
        return None
    match = re.match(r"(?:git@github\.com:|https://github\.com/)(.+?)(?:\.git)?$", remote)
    return f"https://github.com/{match.group(1)}" if match else remote


def spec_document(ref: ProjectRef) -> dict[str, Any]:
    spec = spec_ops.get_spec(ref.root)
    return {"yaml": (ref.root / "robot.yaml").read_text(), "sha256": spec["sha256"], "spec": spec["spec"], "path": spec["path"]}


def model_view(ref: ProjectRef) -> dict[str, Any]:
    project = load(ref)
    build = ref.root / "build"
    fingerprint = project.fingerprint()
    graph = _read_json(build / "model" / "graph.json")
    assembly = _read_json(build / "model" / "assembly.json")
    catalog = project.catalog
    components = {}
    for cid, instance in project.spec.components.items():  # poses are compiled from the assembly when declared
        entry = catalog.components.get(instance.type)
        components[cid] = {"type": instance.type, "pose": instance.pose.model_dump(mode="json"),
                           "mount": instance.mount, "label": instance.label,
                           "kind": entry.kind if entry else None, "name": entry.name if entry else None,
                           "shape": entry.mechanical.shape if entry else None, "size": entry.mechanical.size if entry else None,
                           "mass": entry.mechanical.mass if entry else None}
    compiled = project.assembly
    derived_joints = [j.model_dump(mode="json") if hasattr(j, "model_dump") else j for j in (compiled.joints if compiled else [])]
    joints = [j.model_dump(mode="json") for j in project.spec.mechanical.joints]
    return _finite({"graph": graph, "assembly": assembly, "components": components, "joints": joints,
                    "derived_joints": derived_joints,
                    "compiled": compiled is not None,
                    "images": _urls(ref.id, [p for p in ("model/layout.png", "model/assembly_graph.png") if (build / p).exists()]),
                    "results": {s: _stage_block(project, ref.id, s, fingerprint) for s in ("assembly", "model")},
                    "missing": [name for name, value in (("graph", graph), ("assembly", assembly)) if value is None]})


def cad_view(ref: ProjectRef) -> dict[str, Any]:
    project = load(ref)
    build = ref.root / "build"
    model = _read_json(build / "cad" / "cad_model.json")
    meshes: dict[str, str] = {}
    if isinstance(model, dict):
        for cid, part in (model.get("parts") or {}).items():
            mesh = part.get("mesh") if isinstance(part, dict) else None
            if mesh and (build / "cad" / mesh).exists():
                meshes[cid] = artifact_url(ref.id, f"cad/{mesh}")
    for path in _files(build, "cad/meshes", (".stl", ".glb", ".obj")):
        meshes.setdefault(Path(path).stem, artifact_url(ref.id, path))
    lock = project.lock.get("cad") or {}
    source = (model or {}).get("source") or {}
    urls = {"document": lock.get("url")}
    did = source.get("document_id") or lock.get("document_id")
    wid = source.get("workspace_id") or lock.get("workspace_id")
    if did and wid:
        urls["document"] = urls["document"] or f"https://cad.onshape.com/documents/{did}/w/{wid}"
        if source.get("element_id"):
            urls["part_studio"] = f"https://cad.onshape.com/documents/{did}/w/{wid}/e/{source['element_id']}"
    return {"cad_model": model, "meshes": meshes, "onshape": {**urls, "lock": lock},
            "views": _urls(ref.id, _files(build, "cad", IMAGE_SUFFIXES)),
            "result": _stage_block(project, ref.id, "cad", project.fingerprint()),
            "missing": [] if model else ["cad_model"]}


def wiring_view(ref: ProjectRef) -> dict[str, Any]:
    project = load(ref)
    build = ref.root / "build" / "wiring"
    routes = _read_json(build / "routes.json")
    detail_ = _read_json(build / "routing_detail.json")
    slack = _read_json(build / "slack_sim.json")
    result = _stage_block(project, ref.id, "wiring", project.fingerprint())
    metrics = (result or {}).get("metrics", {})
    timeline = (slack or {}).get("timeline") or _read_json(build / "slack_timeline.json")
    # ``index`` locates the harness in ``electrical.harnesses``; a merge patch replaces lists whole, so a
    # route-hint edit PATCHes the complete harness list (see docs/architecture/api.md).
    harnesses = {h.id: {"index": i, "wires": [w.id for w in h.wires], "connectors": list(h.connectors),
                        "route": h.route.model_dump(mode="json"), "jacket": h.jacket}
                 for i, h in enumerate(project.spec.electrical.harnesses)}
    return _finite({"routes": routes, "routing_detail": detail_, "bom": _csv_rows(build / "harness_bom.csv"),
                    "bom_url": artifact_url(ref.id, "wiring/harness_bom.csv") if (build / "harness_bom.csv").exists() else None,
                    "clearances": {"static": (routes or {}).get("clearances", []), "moving": metrics.get("wire_min_clearance_moving"),
                                   "worst_static": metrics.get("worst_static_clearance"),
                                   "worst_dynamic": metrics.get("worst_dynamic_clearance"),
                                   "requirement": float(project.spec.requirements.min_wire_clearance_moving)},
                    "slack_sim": slack, "slack_timeline": timeline, "harnesses": harnesses,
                    "images": _urls(ref.id, _files(ref.root / "build", "wiring", IMAGE_SUFFIXES)), "result": result,
                    "missing": [n for n, v in (("routes", routes), ("routing_detail", detail_), ("slack_sim", slack),
                                               ("slack_timeline", timeline)) if v is None]})


def electrical_view(ref: ProjectRef) -> dict[str, Any]:
    project = load(ref)
    build = ref.root / "build"
    points = _read_json(build / "electrical" / "operating_points.json")
    graph = _read_json(build / "model" / "graph.json") or {}
    worst = (points or {}).get("worst_case") or {}
    wires = [{"id": wid, **info, "burst_ratio": info.get("burst_ratio"),
              "continuous_ratio": (info["continuous_current"] / info["ampacity"]) if info.get("ampacity") else None}
             for wid, info in (worst.get("wires") or {}).items()]
    connectors = [{"id": cid, **info,
                   "continuous_ratio": (info["continuous_current"] / info["rating"]) if info.get("rating") else None,
                   "burst_ratio": (info["burst_current"] / info["burst_rating"]) if info.get("burst_rating") and info.get("burst_current") is not None else None}
                  for cid, info in (worst.get("connectors") or {}).items()]
    table = []
    for key, point in ((points or {}).get("operating_points") or {}).items():
        components = point.get("components") or {}
        table.append({"id": key, "operating_point": point.get("operating_point"), "battery_state": point.get("battery_state"),
                      "kind": point.get("kind"), "soc": point.get("soc"),
                      "battery_current": (components.get("battery") or {}).get("current"),
                      "battery_voltage": (components.get("battery") or {}).get("v_terminal"),
                      "motor_current": (components.get("motor") or {}).get("current"),
                      "rails": point.get("nets") or point.get("rails")})
    power_tree = {"rails": graph.get("rails", []), "fuses": (points or {}).get("fuses"), "brownout": (points or {}).get("brownout"),
                  "harness_voltage_drop": worst.get("harness_voltage_drop")}
    return _finite({"operating_points": points, "table": table, "wires": wires, "connectors": connectors, "power_tree": power_tree,
                    "runtime": (points or {}).get("runtime"), "thermal": (points or {}).get("thermal"),
                    "images": _urls(ref.id, _files(build, "electrical", IMAGE_SUFFIXES)),
                    "result": _stage_block(project, ref.id, "electrical", project.fingerprint()),
                    "missing": [] if points else ["operating_points"]})


def pcb_view(ref: ProjectRef) -> dict[str, Any]:
    project = load(ref)
    build = ref.root / "build"
    boards = []
    for board in project.spec.pcb.boards:
        name = Path(board.source).name
        folder = build / "pcb" / name
        data = _read_json(folder / "board.json")
        files = sorted(_rel(build, p) for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else []
        gerbers = next((f for f in files if f.endswith(".zip") and "gerber" in f.lower()), None) or \
            next((f for f in files if f.endswith(".zip")), None)
        bom_file = next((f for f in files if f.lower().endswith(".csv") and "bom" in f.lower()), None)
        catalog_entry = project.catalog.components.get(project.spec.components[board.component].type) \
            if board.component in project.spec.components else None
        # Catalog connector pin number -> component pin name, to compare with board.json's pin -> net map.
        catalog_pins = {conn: dict(instance.pins) for conn, instance in (catalog_entry.connectors.items() if catalog_entry else [])}
        boards.append({"board": name, "component": board.component, "spec": board.model_dump(mode="json"), "board_json": data,
                       "renders": _urls(ref.id, [f for f in files if f.lower().endswith(IMAGE_SUFFIXES)]),
                       "erc": (data or {}).get("erc"), "drc": (data or {}).get("drc"),
                       "connectors": (data or {}).get("connectors"), "catalog_connectors": catalog_pins,
                       "bom": _csv_rows(build / bom_file) if bom_file else (data or {}).get("bom"),
                       "gerbers_url": artifact_url(ref.id, gerbers) if gerbers else None,
                       "files": _urls(ref.id, files), "missing": [] if data else ["board_json"]})
    return _finite({"boards": boards, "result": _stage_block(project, ref.id, "pcb", project.fingerprint())})


def software_view(ref: ProjectRef) -> dict[str, Any]:
    project = load(ref)
    graph = project.graph
    names = (_read_json(ref.root / "build" / "model" / "graph.json") or {}).get("net_names", {})
    bindings = []
    for handle, binding in project.spec.software.bindings.items():
        pins = []
        for pin in binding.pins:
            entry: dict[str, Any] = {"pin": pin}
            try:
                node = graph._pin_node(pin)
                root = graph.net_of(node)
                reach = graph._reach.find(node)  # continuity through internal links (series resistors, level shifters)
                entry.update(node=node, net=root, net_name=names.get(root, root),
                             devices=[n.removeprefix("C:") for n in graph.component_pins_on_net(node)
                                      if not n.startswith(f"C:{binding.computer}.")],
                             reaches=[n.removeprefix("C:") for n in sorted(graph.nodes) if n.startswith("C:")
                                      and not n.startswith(f"C:{binding.computer}.") and graph._reach.find(n) == reach])
            except KeyError as error:
                entry["error"] = str(error).strip("'\"")
            pins.append(entry)
        expects = []
        for ref_ in binding.expects:
            reached = False
            for pin in binding.pins:
                try:
                    reached = reached or graph.connected(pin, ref_, through_internal=True)
                except KeyError:
                    pass
            expects.append({"pin": ref_, "reached": reached})
        bindings.append({"handle": handle, **binding.model_dump(mode="json"), "pin_map": pins, "expects_reached": expects})
    modules = []
    for module in project.spec.software.modules:
        folder = ref.root / "backend" / module.language / module.name
        files = sorted(str(p.relative_to(ref.root)) for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts) \
            if folder.is_dir() else []
        modules.append({**module.model_dump(mode="json"), "path": str(folder.relative_to(ref.root)), "exists": folder.is_dir(),
                        "files": files})
    logs = []
    for path in sorted((ref.root / "build" / "sim").glob("*/summary.json")):
        summary = _read_json(path) or {}
        log = path.parent / "software.log"
        logs.append({"scenario": summary.get("scenario", path.parent.name), "firmware_logs": summary.get("firmware_logs", []),
                     "software": summary.get("software"),
                     "software_log_url": artifact_url(ref.id, _rel(ref.root / "build", log)) if log.exists() else None,
                     "software_log_tail": log.read_text(errors="replace")[-8000:] if log.exists() else ""})
    deploy_log = ref.root / "build" / "deploy" / "fleet.log"
    return {"bindings": bindings, "modules": modules, "config": project.spec.software.config, "firmware_logs": logs,
            "deploy_log_url": artifact_url(ref.id, "deploy/fleet.log") if deploy_log.exists() else None}


# -------------------------------------------------------------- simulation
def _scenario_summary(ref: ProjectRef, scenario_id: str, fingerprint: dict[str, str]) -> dict[str, Any] | None:
    path = ref.root / "build" / "sim" / scenario_id / "summary.json"
    data = _read_json(path)
    if not isinstance(data, dict):
        return None
    build = ref.root / "build"
    folder = path.parent
    return {"passed": data.get("passed"), "checks": data.get("checks"), "metrics": data.get("metrics"),
            "fallbacks": data.get("fallbacks"), "stale": release.is_stale(data.get("inputs"), fingerprint),
            "inputs": data.get("inputs"), "summary_url": artifact_url(ref.id, _rel(build, path)),
            "plots": _urls(ref.id, sorted(_rel(build, p) for p in folder.glob("*.png"))),
            "renders": _urls(ref.id, sorted(_rel(build, p) for p in (folder / "renders").glob("*.png"))),
            "clip_url": next((artifact_url(ref.id, _rel(build, p)) for p in sorted((folder / "renders").glob("*")) if p.suffix in VIDEO_SUFFIXES), None),
            "recording": (folder / "recording.npz").exists(), "diagnostics": data.get("diagnostics", [])}


def sim_list(ref: ProjectRef) -> dict[str, Any]:
    project = load(ref)
    fingerprint = project.fingerprint()
    scenarios = [{**s.model_dump(mode="json"), "last": _scenario_summary(ref, s.id, fingerprint)} for s in project.spec.simulation.scenarios]
    return _finite({"scenarios": scenarios, "result": _stage_block(project, ref.id, "sim", fingerprint),
                    "overview_url": artifact_url(ref.id, "sim/overview.png") if (ref.root / "build/sim/overview.png").exists() else None})


_RECORDING_LOCK = threading.Lock()


def sim_detail(ref: ProjectRef, scenario_id: str, hz: float = 30.0, cables: bool = True) -> dict[str, Any]:
    project = load(ref)
    scenario = next((s for s in project.spec.simulation.scenarios if s.id == scenario_id), None)
    folder = ref.root / "build" / "sim" / scenario_id
    if scenario is None and not folder.is_dir():
        raise ApiError("scenario_not_found", f"no scenario {scenario_id!r} in robot.yaml or build/sim",
                       {"scenarios": [s.id for s in project.spec.simulation.scenarios]})
    last = _scenario_summary(ref, scenario_id, project.fingerprint())
    payload: dict[str, Any] = {"scenario": scenario.model_dump(mode="json") if scenario else {"id": scenario_id}, "last": last,
                               "summary": _read_json(folder / "summary.json"), "recording": None}
    npz = folder / "recording.npz"
    if npz.exists():
        with _RECORDING_LOCK:
            payload["recording"] = decode_recording(str(npz), npz.stat().st_mtime_ns, float(hz), bool(cables))
    return _finite(payload)


@lru_cache(maxsize=8)
def decode_recording(npz_path: str, mtime_ns: int, hz: float, cables: bool) -> dict[str, Any]:
    """Downsample a scenario recording for playback.

    ``series`` holds every 1-D channel of the 100 Hz recording resampled to
    ``hz`` (linear interpolation on a uniform grid). ``frames`` holds world
    poses ``[x, y, z, qw, qx, qy, qz]`` per rigid body, obtained by forward
    kinematics (``mj_kinematics``) of ``vehicle.xml`` at each 30 Hz
    ``snapshot_qpos``, plus cable polylines (body positions in chain order).
    """
    import numpy as np

    data = np.load(npz_path)
    t = data["t"] if "t" in data.files else np.zeros(0)
    hz = min(max(hz, 0.5), 200.0)
    duration = float(t[-1]) if len(t) else 0.0
    grid = np.arange(0.0, duration + 1e-9, 1.0 / hz) if len(t) else np.zeros(0)
    series: dict[str, list[float]] = {"t": [round(float(v), 4) for v in grid]}
    for key in data.files:
        array = data[key]
        if key == "t" or array.ndim != 1 or array.shape[0] != t.shape[0] or not np.issubdtype(array.dtype, np.number):
            continue
        values = np.interp(grid, t, array.astype(float))
        series[key] = [round(float(v), 6) if math.isfinite(v) else None for v in values]
    logs = [json.loads(x) for x in data["firmware_logs"] if x] if "firmware_logs" in data.files else []
    events = [json.loads(x) for x in data["events"] if x] if "events" in data.files else []
    out: dict[str, Any] = {"hz": hz, "duration": duration, "channels": sorted(k for k in series if k != "t"), "series": series,
                           "firmware_logs": logs[:2000], "events": events[:2000], "frames": None}
    xml = Path(npz_path).with_name("vehicle.xml")
    if "snapshot_qpos" in data.files and xml.exists():
        out["frames"] = _body_frames(xml, data["snapshot_t"], data["snapshot_qpos"], hz, cables)
    return out


def _body_frames(xml: Path, snapshot_t: Any, snapshot_qpos: Any, hz: float, cables: bool) -> dict[str, Any] | None:
    import numpy as np

    try:
        import mujoco
    except ImportError:
        return None
    try:
        model = mujoco.MjModel.from_xml_path(str(xml))
    except Exception as error:  # noqa: BLE001 - a recording without a loadable model still plays its series
        return {"error": f"vehicle.xml does not load: {error}"}
    if snapshot_qpos.ndim != 2 or snapshot_qpos.shape[1] != model.nq or not len(snapshot_t):
        return {"error": f"qpos width {snapshot_qpos.shape} does not match model nq={model.nq}"}
    data = mujoco.MjData(model)
    step = max(1, int(round((len(snapshot_t) / max(float(snapshot_t[-1] - snapshot_t[0]), 1e-9)) / hz))) if len(snapshot_t) > 1 else 1
    indices = range(0, len(snapshot_t), step)
    names = [mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, i) or f"body{i}" for i in range(model.nbody)]
    cable_ids: dict[str, list[int]] = {}
    rigid: list[int] = []
    for i, name in enumerate(names[1:], start=1):
        match = re.match(r"^(cable\w*?)_B_", name)
        if match:
            cable_ids.setdefault(match.group(1), []).append(i)
        else:
            rigid.append(i)
    components: dict[str, str] = {}
    for g in range(model.ngeom):
        gname = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, g) or ""
        if gname.startswith("vis_"):
            components[gname[4:]] = names[model.geom_bodyid[g]]
    cable_colors = {}
    for cable, ids in cable_ids.items():
        geoms = [g for g in range(model.ngeom) if model.geom_bodyid[g] == ids[0]]
        cable_colors[cable] = [round(float(c), 3) for c in model.geom_rgba[geoms[0]]] if geoms else None
    times: list[float] = []
    poses: dict[str, list[list[float]]] = {names[i]: [] for i in rigid}
    cable_frames: dict[str, list[list[list[float]]]] = {c: [] for c in cable_ids}
    for index in indices:
        data.qpos[:] = snapshot_qpos[index]
        mujoco.mj_kinematics(model, data)
        times.append(round(float(snapshot_t[index]), 4))
        for i in rigid:
            poses[names[i]].append([round(float(v), 5) for v in (*data.xpos[i], *data.xquat[i])])
        if cables:
            for cable, ids in cable_ids.items():
                cable_frames[cable].append([[round(float(v), 4) for v in data.xpos[i]] for i in ids])
    return {"t": times, "format": "x y z qw qx qy qz (world frame)", "bodies": poses, "component_bodies": components,
            "cables": {c: {"rgba": cable_colors[c], "points": frames} for c, frames in cable_frames.items()} if cables else None}


def diagnostics_view(ref: ProjectRef, stage: str | None = None, severity: str | None = None, subject: str | None = None) -> dict[str, Any]:
    payload = api.stored_diagnostics(ref.root, stage, severity, subject)
    groups: dict[str, dict[str, int]] = {}
    for d in payload["diagnostics"]:
        groups.setdefault(d["stage"] or "general", {"error": 0, "warning": 0, "info": 0})[d["severity"]] += 1
    payload["by_stage"] = groups
    return payload

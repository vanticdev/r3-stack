"""``r3 doctor``: which external tools are available here, and which stages can run headless.

Every probe is local and bounded by a timeout: subprocess version queries, a
32x32 MuJoCo offscreen render per GL backend (in a child process, because the
backend is fixed at import), library lookups, and the local Onshape bridge
health endpoint. Nothing is sent to Onshape, GitHub or a robot.
"""

from __future__ import annotations

import ctypes.util
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .environment import r3_pythonpath
from .stages import ALL_STAGES

PROBE_TIMEOUT = 60.0


@dataclass
class Tool:
    name: str
    available: bool
    version: str | None = None
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


def _run(command: list[str], timeout: float = 20.0, env: dict[str, str] | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as error:
        return -1, str(error)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _dist_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


# ------------------------------------------------------------------ probes
_RENDER_PROBE = """
import mujoco, numpy
m = mujoco.MjModel.from_xml_string('<mujoco><worldbody><light pos="0 0 1"/><geom type="sphere" size=".1"/></worldbody></mujoco>')
d = mujoco.MjData(m); mujoco.mj_forward(m, d)
with mujoco.Renderer(m, 32, 32) as r:
    r.update_scene(d); img = r.render()
print("OK", img.shape, int(img.sum() > 0))
"""


def _gl_candidates() -> list[str]:
    explicit = os.environ.get("MUJOCO_GL")
    if sys.platform == "darwin":
        defaults = ["cgl", "glfw"]
    elif sys.platform.startswith("linux"):
        defaults = ["egl", "osmesa"] + (["glfw"] if os.environ.get("DISPLAY") else [])
    else:
        defaults = ["glfw", "wgl"]
    return [explicit] + [c for c in defaults if c != explicit] if explicit else defaults


def probe_mujoco() -> Tool:
    version = _dist_version("mujoco")
    if version is None:
        return Tool("mujoco", False, detail="python package mujoco is not installed")
    backends: dict[str, str] = {}
    working: str | None = None
    for backend in _gl_candidates():
        env = {**os.environ, "MUJOCO_GL": backend}
        if backend in ("egl", "osmesa"):
            env["PYOPENGL_PLATFORM"] = backend
        code, out = _run([sys.executable, "-c", _RENDER_PROBE], timeout=PROBE_TIMEOUT, env=env)
        ok = code == 0 and "OK" in out
        backends[backend] = "ok" if ok else (out.strip().splitlines() or ["failed"])[-1][:200]
        if ok and working is None:
            working = backend
            break  # the first working backend is the one R3 uses; skip slower fallbacks
    renderer = None
    if working:
        renderer = _gl_renderer(working)
    detail = f"offscreen rendering via MUJOCO_GL={working}" if working else "no working offscreen GL backend (renders are skipped)"
    return Tool("mujoco", True, version, detail, {"gl_backend": working, "gl_renderer": renderer, "backends": backends,
                                                  "MUJOCO_GL": os.environ.get("MUJOCO_GL")})


def _gl_renderer(backend: str) -> str | None:
    """GL_RENDERER string of the backend (e.g. llvmpipe = CPU, or the GPU name)."""
    script = ("import mujoco\nfrom mujoco import gl_context\nfrom OpenGL import GL\n"
              "c = mujoco.GLContext(16, 16); c.make_current(); print(GL.glGetString(GL.GL_RENDERER).decode()); c.free()")
    env = {**os.environ, "MUJOCO_GL": backend}
    if backend in ("egl", "osmesa"):
        env["PYOPENGL_PLATFORM"] = backend
    code, out = _run([sys.executable, "-c", script], timeout=PROBE_TIMEOUT, env=env)
    return out.strip().splitlines()[-1][:120] if code == 0 and out.strip() else None


def probe_matplotlib() -> Tool:
    version = _dist_version("matplotlib")
    if version is None:
        return Tool("matplotlib", False, detail="not installed")
    backend = os.environ.get("MPLBACKEND") or "Agg (forced by every R3 plot module)"
    return Tool("matplotlib", True, version, f"backend {backend}")


NGSPICE_LIBRARIES = ("/opt/homebrew/lib/libngspice.dylib", "/usr/local/lib/libngspice.dylib", "/usr/lib/x86_64-linux-gnu/libngspice.so.0",
                     "/usr/lib/aarch64-linux-gnu/libngspice.so.0", "/usr/lib/libngspice.so.0", "/usr/local/lib/libngspice.so")


def probe_ngspice() -> list[Tool]:
    found = ctypes.util.find_library("ngspice")
    library = next((p for p in (os.environ.get("NGSPICE_LIBRARY_PATH"), *NGSPICE_LIBRARIES) if p and Path(p).exists()), None)
    library = library or found
    executable = shutil.which("ngspice") or next((p for p in ("/opt/homebrew/bin/ngspice", "/usr/local/bin/ngspice") if Path(p).exists()), None)
    version = None
    if executable:
        code, out = _run([executable, "--version"])
        match = re.search(r"ngspice-(\d+(?:\.\d+)*)", out) or re.search(r"ngspice\D+(\d+(?:\.\d+)*)", out)
        version = match.group(1) if match else None
    lib = Tool("libngspice", library is not None, version if library else None,
               library or "shared library not found (set NGSPICE_LIBRARY_PATH)", {"path": library})
    exe = Tool("ngspice", executable is not None, version, executable or "not on PATH")
    return [lib, exe]


def probe_kicad_cli() -> Tool:
    executable = shutil.which("kicad-cli") or next(
        (p for p in ("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",) if Path(p).exists()), None)
    if executable is None:
        return Tool("kicad-cli", False, detail="not on PATH")
    code, out = _run([executable, "version"])
    version = out.splitlines()[0].strip() if code == 0 and out else None
    return Tool("kicad-cli", code == 0, version, executable)


def _kicad_python() -> str | None:
    """Interpreter that can import KiCad's pcbnew module (system python3 on Linux
    with the KiCad PPA; KiCad's bundled Python on macOS)."""
    candidates = [os.environ.get("R3_KICAD_PYTHON", ""), "python3",
                  str(Path.home() / "Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3"),
                  "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3"]
    for candidate in candidates:
        if candidate and (shutil.which(candidate) or Path(candidate).exists()):
            code, _ = _run([candidate, "-c", "import pcbnew"], timeout=30)
            if code == 0:
                return candidate
    return None


def probe_kicad_pcbnew() -> Tool:
    """KiCad's board engine as a Python module: headless board editing, never a GUI."""
    python = _kicad_python()
    if python is None:
        return Tool("kicad-pcbnew", False, detail="no interpreter can import pcbnew (install KiCad / python3-pcbnew)")
    code, out = _run([python, "-c", "import pcbnew; print(pcbnew.Version())"], timeout=30)
    version = out.strip().splitlines()[-1] if code == 0 and out.strip() else None
    return Tool("kicad-pcbnew", code == 0, version, python)


def _jar_version(jar: Path) -> str | None:
    """Release version: a VERSION file beside the jar (the worker image writes one;
    release jars report "unspecified"), the manifest, or the file name."""
    marker = jar.parent / "VERSION"
    if marker.exists():
        return marker.read_text().strip() or None
    try:
        with zipfile.ZipFile(jar) as archive:
            manifest = archive.read("META-INF/MANIFEST.MF").decode(errors="replace")
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    match = re.search(r"^Implementation-Version:\s*(\S+)", manifest, re.MULTILINE)
    if match and match.group(1) != "unspecified":
        return match.group(1)
    match = re.search(r"(\d+\.\d+\.\d+)", jar.name)
    return match.group(1) if match else None


def probe_java() -> Tool:
    java = shutil.which("java")
    if java is None:
        return Tool("java", False, detail="not on PATH")
    code, out = _run([java, "-version"])
    match = re.search(r'version "([^"]+)"', out)
    return Tool("java", code == 0, match.group(1) if match else None, java)


def probe_freerouting(java: Tool) -> Tool:
    candidates = [os.environ.get("R3_FREEROUTING_JAR"), "/opt/freerouting/freerouting.jar"]
    jar = next((Path(p) for p in candidates if p and Path(p).exists()), None)
    if jar is None:
        return Tool("freerouting", False, detail="jar not found (set R3_FREEROUTING_JAR)")
    return Tool("freerouting", java.available, _jar_version(jar),
                f"{jar} (CLI: freerouting -de board.dsn -do board.ses)" if java.available else f"{jar}, but java is missing",
                {"jar": str(jar), "wrapper": shutil.which("freerouting")})


def probe_docker() -> Tool:
    docker = shutil.which("docker")
    if docker is None:
        return Tool("docker", False, detail="docker CLI not on PATH")
    code, client = _run([docker, "version", "--format", "{{.Client.Version}}"])
    code_info, info = _run([docker, "info", "--format", "{{json .}}"], timeout=20)
    server: dict[str, Any] = {}
    if code_info == 0:
        try:
            data = json.loads(info.splitlines()[-1])
            server = {"version": data.get("ServerVersion"), "arch": data.get("Architecture"), "os": data.get("OperatingSystem")}
        except (ValueError, IndexError):
            server = {}
    reachable = bool(server.get("version"))
    endpoint = os.environ.get("DOCKER_HOST") or "/var/run/docker.sock"
    detail = f"daemon {server.get('version')} ({server.get('arch')}) via {endpoint}" if reachable \
        else f"CLI only; no daemon reachable at {endpoint} (mount the socket or set DOCKER_HOST)"
    return Tool("docker", reachable, client.splitlines()[-1] if code == 0 and client else None, detail,
                {"cli": docker, "daemon": server or None, "endpoint": endpoint})


def probe_onshape() -> Tool:
    try:
        from r3_onshape.client import describe_transport
    except ImportError as error:
        return Tool("onshape", False, detail=f"r3_onshape not importable: {error}")
    try:
        info = describe_transport()
    except ValueError as error:
        return Tool("onshape", False, detail=str(error))
    selected = info["selected"]
    if selected is None:
        detail = "no transport: set ONSHAPE_ACCESS_KEY/ONSHAPE_SECRET_KEY (headless) or start the onshape-native bridge (local only)"
    elif selected == "onshape-session":
        detail = "headless Onshape session (stored cookies; validity is checked on first use)"
    elif info["headless"]:
        detail = f"API keys ({info['api_keys']['auth']}) to {info['api_keys']['base']}"
    else:
        detail = "onshape-native browser bridge (local development only, not headless)"
    return Tool("onshape", selected is not None, None, detail, info)


_SIONNA_PROBE = """
import mitsuba
import sionna.rt  # selects cuda_ad_mono_polarized when a GPU is usable, else llvm_ad_mono_polarized
print("VARIANT", mitsuba.variant() or "")
"""


def probe_sionna() -> Tool:
    version = _dist_version("sionna-rt")
    if version is None or importlib.util.find_spec("sionna") is None:
        return Tool("sionna-rt", False, detail="not installed (optional: radio uses the analytic link model)")
    code, out = _run([sys.executable, "-c", _SIONNA_PROBE], timeout=PROBE_TIMEOUT)
    match = re.search(r"VARIANT (\S*)", out)
    variant = match.group(1) if match else ""
    if code == 0 and variant:
        return Tool("sionna-rt", True, version, f"Mitsuba variant {variant} ({'CPU/LLVM' if variant.startswith('llvm') else 'GPU/CUDA'})",
                    {"variant": variant, "mitsuba": _dist_version("mitsuba"), "drjit": _dist_version("drjit")})
    return Tool("sionna-rt", False, version, f"installed but no usable Mitsuba variant: {(out.splitlines() or [''])[-1][:200]}",
                {"mitsuba": _dist_version("mitsuba"), "drjit": _dist_version("drjit")})


def probe_simple(name: str, executable: str, args: list[str], pattern: str) -> Tool:
    path = shutil.which(executable)
    if path is None:
        return Tool(name, False, detail="not on PATH")
    code, out = _run([path, *args])
    match = re.search(pattern, out)
    return Tool(name, True, match.group(1) if match else None, path)


# ---------------------------------------------------------- stage readiness
def probe_stage_imports() -> dict[str, dict[str, Any]]:
    """Actually import every stage in a child with the agent session environment.

    A foreign cwd prevents the checkout or pytest's sys.path from masking missing
    package roots. Import failures include transitive dependencies, not just specs.
    """
    modules = {name: stage.module for name, stage in ALL_STAGES.items()}
    script = """
import importlib, json
results = {}
for name, module in MODULES.items():
    try:
        loaded = importlib.import_module(module)
        if name == 'tests':
            importlib.import_module('pytest')
        if not callable(getattr(loaded, 'run', None)):
            raise ImportError(f'{module} has no callable run')
        results[name] = {'available': True, 'module': module, 'detail': ''}
    except Exception as error:
        results[name] = {'available': False, 'module': module, 'detail': f'{type(error).__name__}: {error}'}
print(json.dumps(results))
""".replace("MODULES", repr(modules))
    try:
        with tempfile.TemporaryDirectory(prefix="r3-doctor-") as directory:
            process = subprocess.run([sys.executable, "-c", script], cwd=directory,
                                     env={**os.environ, "PYTHONPATH": r3_pythonpath()},
                                     capture_output=True, text=True, timeout=PROBE_TIMEOUT)
        if process.returncode == 0:
            return json.loads(process.stdout.splitlines()[-1])
        detail = (process.stderr or process.stdout)[-2000:]
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as error:
        detail = str(error)
    return {name: {"available": False, "module": module, "detail": detail} for name, module in modules.items()}


@dataclass
class StageReadiness:
    stage: str
    headless: bool
    level: str  # full | degraded | unavailable
    missing: list[str]
    note: str


def _stages(tools: dict[str, Tool]) -> list[StageReadiness]:
    def ok(name: str) -> bool:
        return tools[name].available

    gl = bool(tools["mujoco"].data.get("gl_backend"))
    out = [StageReadiness("assembly", True, "full", [], "Python assembly compiler."),
           StageReadiness("model", True, "full", [], "Python only; plots use matplotlib Agg.")]
    onshape = tools["onshape"].data or {}
    if onshape.get("headless"):
        out.append(StageReadiness("cad", True, "full", [], "Onshape API keys; views from the REST shaded-view endpoint."))
    elif onshape.get("selected"):
        out.append(StageReadiness("cad", False, "degraded", ["onshape API keys"],
                                  "Uses the browser bridge (needs a logged-in browser); reuses build/cad/cad_model.json when current."))
    else:
        out.append(StageReadiness("cad", True, "degraded", ["onshape API keys"],
                                  "Reuses a current build/cad/cad_model.json, else downstream stages use catalog geometry."))
    pcb_missing = [n for n in ("kicad-cli",) if not ok(n)]
    pcb_missing += [n for n in ("kicad-pcbnew",) if not ok(n)]
    extras = [n for n in ("freerouting",) if not ok(n)]
    out.append(StageReadiness("pcb", not pcb_missing, "unavailable" if pcb_missing else "degraded" if extras else "full",
                              pcb_missing + extras, "kicad-cli for ERC/DRC/exports, KiCad's pcbnew module for board edits, "
                              "Freerouting CLI for autorouting. KiCad never opens a window."))
    out.append(StageReadiness("wiring", True, "full", [], "Python + MuJoCo cable physics (no GL); plots use Agg."))
    out.append(StageReadiness("connectors", True, "full", [], "Connector models and pin geometry."))
    out.append(StageReadiness("fabrication", True, "full", [], "Mesh printability and finite-element strength checks."))
    out.append(StageReadiness("electrical", True, "full" if ok("ngspice") or ok("libngspice") else "degraded",
                              [] if ok("ngspice") or ok("libngspice") else ["ngspice"], "ngspice cross-check runs when available."))
    out.append(StageReadiness("software", ok("docker"), "full" if ok("docker") else "unavailable", [] if ok("docker") else ["docker daemon"],
                              "BLITZ firmware image build and container runs need a Docker daemon (socket or DinD)."))
    sim_missing = [n for n, present in (("mujoco GL backend", gl), ("docker daemon", ok("docker")), ("sionna-rt", ok("sionna-rt"))) if not present]
    out.append(StageReadiness("sim", ok("mujoco"), "unavailable" if not ok("mujoco") else "full" if not sim_missing else "degraded", sim_missing,
                              "Physics always; renders need a GL backend; container software mode needs Docker; "
                              "ray-traced radio needs sionna-rt (else analytic link model)."))
    out.append(StageReadiness("report", True, "full", [], "Static HTML + JSON."))
    out.append(StageReadiness("tests", True, "full", [], "Project-authored pytest checks; no display."))
    return out


def run_doctor(only: list[str] | None = None) -> dict[str, Any]:
    probes: list[Callable[[], Tool | list[Tool]]] = [probe_mujoco, probe_matplotlib, probe_ngspice, probe_kicad_cli, probe_kicad_pcbnew,
                                                     probe_java, probe_docker, probe_onshape, probe_sionna,
                                                     lambda: probe_simple("ffmpeg", "ffmpeg", ["-version"], r"ffmpeg version (\S+)")]
    tools: dict[str, Tool] = {}
    for probe in probes:
        found = probe()
        for tool in found if isinstance(found, list) else [found]:
            tools[tool.name] = tool
    tools["freerouting"] = probe_freerouting(tools["java"])
    stages = _stages(tools)
    imports = probe_stage_imports()
    for stage in stages:
        imported = imports.get(stage.stage)
        if imported is not None and not imported["available"]:
            stage.headless, stage.level = False, "unavailable"
            stage.missing.append(imported["module"])
            stage.note += " Import failed: " + imported["detail"]
    try:
        from r3_sim.headless import has_display
    except ImportError:
        def has_display() -> bool:
            return sys.platform != "linux" or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

    payload: dict[str, Any] = {
        "ok": True,
        "environment": {"python": platform.python_version(), "platform": f"{platform.system()} {platform.machine()}",
                        "display": has_display(), "worker_image": os.environ.get("R3_WORKER_IMAGE") == "1",
                        "ci": os.environ.get("CI") == "true"},
        "tools": {name: asdict(tool) for name, tool in tools.items()},
        "stages": [asdict(s) for s in stages],
        "stage_imports": imports,
    }
    if only:
        blocked = [s.stage for s in stages if s.stage in only and not s.headless]
        unknown = sorted(set(only) - {s.stage for s in stages})
        if unknown:
            return {"ok": False, "code": "usage_error", "message": f"unknown stage(s): {', '.join(unknown)}"}
        payload["required"] = only
        if blocked:
            payload.update(ok=False, code="stage_not_headless", message=f"cannot run headless here: {', '.join(blocked)}")
    failed_imports = [name for name, imported in imports.items() if not imported["available"] and (not only or name in only)]
    if failed_imports:
        payload.update(ok=False, code="stage_not_importable", message=f"cannot import pipeline stages: {', '.join(failed_imports)}")
    return payload


def print_doctor(payload: dict[str, Any]) -> None:
    env = payload["environment"]
    print(f"r3 doctor  Python {env['python']} on {env['platform']}, display: {'yes' if env['display'] else 'no'}"
          f"{', R3 worker image' if env['worker_image'] else ''}")
    print("tools:")
    for name, tool in payload["tools"].items():
        mark = "ok " if tool["available"] else "-- "
        print(f"  {mark}{name:<12} {tool['version'] or '':<12} {tool['detail']}")
    print("stages (headless):")
    for stage in payload["stages"]:
        missing = f"  missing: {', '.join(stage['missing'])}" if stage["missing"] else ""
        print(f"  {stage['stage']:<11} {'yes' if stage['headless'] else 'NO ':<4} {stage['level']:<12}{missing}")
    if not payload["ok"]:
        print(f"FAIL: {payload.get('message', '')}")

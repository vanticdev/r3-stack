"""Headless environment: rendering defaults, ``r3 doctor`` (CLI + MCP), CI workflows."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
import yaml

from r3_orchestrator import cli, doctor
from r3_orchestrator import mcp_server as m
from r3_sim import headless

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def linux_no_display(monkeypatch):
    monkeypatch.setattr(headless.sys, "platform", "linux")
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "MUJOCO_GL", "PYOPENGL_PLATFORM", "MPLBACKEND"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delitem(sys.modules, "mujoco", raising=False)
    return monkeypatch


def test_configure_selects_backend(linux_no_display):
    monkeypatch = linux_no_display
    libraries = {"EGL": "libEGL.so.1", "OSMesa": "libOSMesa.so.8"}
    gpu = {"present": True}
    monkeypatch.setattr(headless.ctypes.util, "find_library", lambda name: libraries.get(name))
    monkeypatch.setattr(headless, "gpu_present", lambda: gpu["present"])

    def fresh() -> dict[str, str]:
        for key in ("MUJOCO_GL", "PYOPENGL_PLATFORM", "MPLBACKEND"):
            monkeypatch.delenv(key, raising=False)
        return headless.configure()

    assert fresh() == {"MPLBACKEND": "Agg", "MUJOCO_GL": "egl", "PYOPENGL_PLATFORM": "egl"}  # GPU: vendor EGL
    gpu["present"] = False
    assert fresh()["MUJOCO_GL"] == "osmesa"  # CPU only: OSMesa renders cleaner than llvmpipe via EGL
    del libraries["OSMesa"]
    assert fresh()["MUJOCO_GL"] == "egl"
    del libraries["EGL"]
    assert "MUJOCO_GL" not in fresh()


def test_configure_never_overrides(linux_no_display):
    monkeypatch = linux_no_display
    monkeypatch.setenv("MUJOCO_GL", "osmesa")
    monkeypatch.setenv("MPLBACKEND", "svg")
    assert headless.configure() == {}
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("MUJOCO_GL")
    assert headless.configure() == {}  # a display exists: MuJoCo's default (GLFW) works


def test_configure_after_mujoco_import_is_a_noop(linux_no_display):
    monkeypatch = linux_no_display
    monkeypatch.setitem(sys.modules, "mujoco", object())
    monkeypatch.setattr(headless.ctypes.util, "find_library", lambda name: "lib")
    assert "MUJOCO_GL" not in headless.configure()  # too late: the backend is fixed at import


def _fake_tools(monkeypatch, **available: bool) -> None:
    def tool(name: str, ok: bool = True, **data):
        return doctor.Tool(name, ok, "1.0" if ok else None, "fake", data)

    flags = {"gl": True, "kicad": True, "docker": True, "keys": True, "ngspice": True, "sionna": False, **available}
    monkeypatch.setattr(doctor, "probe_mujoco", lambda: tool("mujoco", gl_backend="egl" if flags["gl"] else None))
    monkeypatch.setattr(doctor, "probe_matplotlib", lambda: tool("matplotlib"))
    monkeypatch.setattr(doctor, "probe_ngspice", lambda: [tool("libngspice", flags["ngspice"]), tool("ngspice", flags["ngspice"])])
    monkeypatch.setattr(doctor, "probe_kicad_cli", lambda: tool("kicad-cli", flags["kicad"]))
    monkeypatch.setattr(doctor, "probe_kicad_pcbnew", lambda: tool("kicad-pcbnew", flags["kicad"]))
    monkeypatch.setattr(doctor, "probe_java", lambda: tool("java"))
    monkeypatch.setattr(doctor, "probe_freerouting", lambda java: tool("freerouting"))
    monkeypatch.setattr(doctor, "probe_docker", lambda: tool("docker", flags["docker"]))
    keys = {"selected": "onshape-api-keys" if flags["keys"] else None, "headless": flags["keys"]}
    monkeypatch.setattr(doctor, "probe_onshape", lambda: tool("onshape", flags["keys"], **keys))
    monkeypatch.setattr(doctor, "probe_sionna", lambda: tool("sionna-rt", flags["sionna"]))
    monkeypatch.setattr(doctor, "probe_simple", lambda name, *args: tool(name))
    monkeypatch.setattr(doctor, "probe_stage_imports", lambda: {
        name: {"available": True, "module": stage.module, "detail": ""} for name, stage in doctor.ALL_STAGES.items()})


def test_doctor_stage_matrix(monkeypatch):
    _fake_tools(monkeypatch)
    payload = doctor.run_doctor()
    stages = {s["stage"]: s for s in payload["stages"]}
    assert payload["ok"] and set(stages) == set(doctor.ALL_STAGES) | {"software"}
    assert stages["cad"]["level"] == "full" and stages["pcb"]["level"] == "full"
    assert stages["sim"]["level"] == "degraded" and stages["sim"]["missing"] == ["sionna-rt"]

    _fake_tools(monkeypatch, kicad=False, docker=False, keys=False, gl=False)
    payload = doctor.run_doctor(["model", "pcb", "software"])
    stages = {s["stage"]: s for s in payload["stages"]}
    assert stages["pcb"]["headless"] is False and stages["software"]["level"] == "unavailable"
    assert stages["cad"]["headless"] is True and stages["cad"]["level"] == "full"  # validates pinned pulls without native access
    assert "mujoco GL backend" in stages["sim"]["missing"]
    assert payload["ok"] is False and payload["code"] == "stage_not_headless" and "pcb" in payload["message"]


def test_doctor_cli(monkeypatch, capsys):
    _fake_tools(monkeypatch, docker=False)
    assert cli.main(["doctor", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tools"]["mujoco"]["data"]["gl_backend"] == "egl"
    assert cli.main(["doctor", "--require", "model,software"]) == 1
    text = capsys.readouterr().out
    assert "stages (headless):" in text and "FAIL: cannot run headless here: software" in text
    assert cli.main(["doctor", "--require", "nonsense", "--json"]) == 2


def test_doctor_mcp_tool(monkeypatch):
    _fake_tools(monkeypatch)
    tools = {t.name: t for t in asyncio.run(m.mcp.list_tools())}
    assert tools["r3_doctor"].annotations.read_only_hint is True
    assert m.r3_doctor()["ok"] is True
    assert m.r3_doctor(["software"])["ok"] is True


def test_doctor_real_probes_run():
    """The real probes complete and report a consistent shape on this machine."""
    payload = doctor.run_doctor()
    assert {"mujoco", "libngspice", "kicad-cli", "kicad-pcbnew", "freerouting", "java", "docker", "onshape", "sionna-rt"} <= set(payload["tools"])
    for tool in payload["tools"].values():
        assert isinstance(tool["available"], bool) and isinstance(tool["detail"], str)
    assert next(s for s in payload["stages"] if s["stage"] == "model")["headless"] is True


def test_platform_workflow_runs_pytest_in_worker_image():
    workflow = yaml.safe_load((ROOT / ".github/workflows/r3-platform.yml").read_text())
    test = workflow["jobs"]["test"]
    steps = {s.get("name", s.get("uses")): s for s in test["steps"]}
    build = steps["Build worker image (linux/amd64)"]
    assert build["with"]["file"] == "docker/r3-worker/Dockerfile" and build["with"]["load"] is True
    assert "python -m pytest tests" in steps["pytest"]["run"] and "r3-worker:ci" in steps["pytest"]["run"]
    publish = workflow["jobs"]["publish"]
    push = next(s for s in publish["steps"] if s.get("name", "").startswith("Build and push"))
    assert push["with"]["platforms"] == "linux/amd64,linux/arm64" and publish["permissions"]["packages"] == "write"


def test_worker_dockerfile_contents():
    text = (ROOT / "docker/r3-worker/Dockerfile").read_text()
    for needle in ("kicad-10.0-releases", "kicad-cli version", "libngspice0", "libosmesa6", "libegl1", "openjdk-25-jre-headless",
                   "freerouting", "docker-ce-cli", "WITH_SIONNA", "import pcbnew", "requirements.txt"):
        assert needle in text, needle
    requirements = (ROOT / "docker/r3-worker/requirements.txt").read_text()
    for package in ("mujoco", "scipy", "numpy", "matplotlib", "manifold3d", "trimesh", "mcp"):
        assert f"\n{package}==" in requirements, package


def test_firmware_container_joins_worker_network(monkeypatch, tmp_path):
    """Inside a container on the host daemon, firmware containers are siblings: they join
    the worker's network namespace (twin on 127.0.0.1) and bind mounts use host paths."""
    from r3_hal import container

    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("R3_DOCKER_NETWORK", raising=False)
    monkeypatch.setattr(container, "own_container_id", lambda: "abc123")
    monkeypatch.setattr(container, "host_path", lambda path, cid: Path("/host/workspace") / path.name)
    command = container.run_command("img", 7600, config=tmp_path / "config.json")
    assert command[command.index("--network") + 1] == "container:abc123" and "--add-host" not in command
    assert "R3_HAL_ENDPOINT=tcp://127.0.0.1:7600" in command
    assert f"/host/workspace/config.json:{container.CONFIG_MOUNT}:ro" in command

    for env in ({"DOCKER_HOST": "tcp://dind:2375"}, {"R3_DOCKER_NETWORK": "host-gateway"}):
        with monkeypatch.context() as patch:
            for key, value in env.items():
                patch.setenv(key, value)
            command = container.run_command("img", 7600)
            assert "--network" not in command and "R3_HAL_ENDPOINT=tcp://host.docker.internal:7600" in command
    monkeypatch.setattr(container, "own_container_id", lambda: None)  # plain host: unchanged behaviour
    assert "--add-host" in container.run_command("img", 7600)
    monkeypatch.setenv("R3_DOCKER_NETWORK", "r3-net")
    command = container.run_command("img", 7600, host="twin")
    assert command[command.index("--network") + 1] == "r3-net" and "R3_HAL_ENDPOINT=tcp://twin:7600" in command

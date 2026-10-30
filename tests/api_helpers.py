"""Fixtures for the HTTP API tests: an bench project copy, stage hand-off files and SSE parsing."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "tests" / "fixtures" / "bench"
TESTS = Path(__file__).resolve().parent


def copy_bench(parent: Path, name: str = "bench") -> Path:
    """A temporary copy of ``tests/fixtures/bench`` (sources only, no build/)."""
    target = parent / name
    shutil.copytree(BENCH, target, ignore=shutil.ignore_patterns("build", "__pycache__", ".venv", "*.pyc"))
    return target


# A small MuJoCo world shaped like the sim stage's vehicle.xml: a free base carrying
# component visuals, one hinged wheel and one elastic cable.
VEHICLE_XML = """<mujoco model="fake-vehicle">
  <extension><plugin plugin="mujoco.elasticity.cable"/></extension>
  <worldbody>
    <body name="base" pos="0 0 0.05">
      <freejoint name="base_free"/>
      <geom name="vis_base" type="box" size="0.06 0.06 0.003" mass="1"/>
      <geom name="vis_battery" type="box" pos="0.15 0 0.02" size="0.07 0.023 0.0125" mass="0.3"/>
      <body name="arm" pos="0 0.1 0">
        <joint name="pivot" type="hinge" axis="0 1 0"/>
        <geom name="vis_arm" type="cylinder" size="0.015 0.01" mass="0.08"/>
      </body>
      <composite type="cable" prefix="cable00s0_" vertex="0.1 0 0.02 0.12 0 0.03 0.14 0 0.03 0.16 0 0.02" initial="none">
        <plugin plugin="mujoco.elasticity.cable"><config key="bend" value="1e6"/><config key="twist" value="1e6"/></plugin>
        <geom type="capsule" size="0.002" rgba="0.85 0.1 0.1 1" density="1500"/>
      </composite>
    </body>
  </worldbody>
</mujoco>
"""


def write_sim_scenario(root: Path, scenario: str = "full_throttle", passed: bool = False, fingerprint: dict | None = None) -> Path:
    """A scenario result as the sim stage writes it: summary.json, recording.npz, vehicle.xml, a plot and a render."""
    import mujoco

    folder = root / "build" / "sim" / scenario
    (folder / "renders").mkdir(parents=True, exist_ok=True)
    (folder / "vehicle.xml").write_text(VEHICLE_XML)
    model = mujoco.MjModel.from_xml_string(VEHICLE_XML)
    t = np.arange(0.0, 2.0 + 1e-9, 0.01)
    snapshot_t = np.arange(0.0, 2.0 + 1e-9, 1 / 30)
    qpos = np.tile(model.qpos0, (len(snapshot_t), 1))
    qpos[:, 0] = 2.0 * snapshot_t  # base drives along +x at 2 m/s
    np.savez_compressed(folder / "recording.npz", t=t, x=2.0 * t, y=np.zeros_like(t), speed=np.full_like(t, 2.0),
                        battery_v=8.4 - 0.1 * t, rssi=np.where(t < 1.0, -60.0, np.nan), snapshot_t=snapshot_t,
                        snapshot_qpos=qpos, firmware_logs=np.asarray([json.dumps({"t": 0.5, "msg": "armed"})]),
                        events=np.asarray([""]))
    (folder / "speed.png").write_bytes(b"\x89PNG fake")
    (folder / "renders" / "render_t0000.50s.png").write_bytes(b"\x89PNG fake")
    (folder / "renders" / "clip.mp4").write_bytes(b"fake mp4")
    (folder / "software.log").write_text("controller started\n")
    summary = {"scenario": scenario, "passed": passed, "checks": [
        {"key": "min_top_speed_mps", "expected": 8.0, "measured": 7.4, "pass": False, "detail": "100 ms average"},
        {"key": "brownouts", "expected": 0, "measured": 0, "pass": True, "detail": ""}],
        "metrics": {"top_speed_mps": 7.4}, "fallbacks": {"electrical": False, "cables": False},
        "firmware_logs": [{"t": 0.5, "msg": "armed"}], "software": {"command": ["python", "main.py"]},
        "diagnostics": [], "inputs": fingerprint or {}}
    (folder / "summary.json").write_text(json.dumps(summary))
    return folder


def write_stage_outputs(root: Path) -> None:
    """Minimal wiring, electrical and PCB hand-off files in the documented formats."""
    wiring = root / "build" / "wiring"
    wiring.mkdir(parents=True, exist_ok=True)
    (wiring / "routes.json").write_text(json.dumps({
        "wires": {"W_BAT_P": {"harness": "supply", "awg": 12, "path": [[0, 0, 0], [0.05, 0, 0]], "routed_length": 0.05,
                              "cut_length": 0.08}},
        "harnesses": {"supply": {"bundle_path": [[0, 0, 0], [0.05, 0, 0]], "length": 0.05, "tie_points": []}},
        "clearances": [{"wire": "W_BAT_P", "component": "sensor", "min_distance": 0.012}]}))
    (wiring / "harness_bom.csv").write_text("section,harness,item,awg,quantity,cut_length_mm\nwire,supply,W_BAT_P,12,1,79\n")
    (wiring / "slack_sim.json").write_text(json.dumps({"requirement": 0.008, "wires": {"W_BAT_P": {"min_distance": 0.02}}}))
    electrical = root / "build" / "electrical"
    electrical.mkdir(parents=True, exist_ok=True)
    (electrical / "operating_points.json").write_text(json.dumps({
        "schema": "r3/electrical-operating-points@1",
        "operating_points": {"cruise@full": {"operating_point": "cruise", "battery_state": "full", "kind": "continuous", "soc": 1.0,
                                             "components": {"battery": {"current": 1.8, "v_terminal": 8.37}, "motor": {"current": 2.3}},
                                             "nets": {"VBAT": 8.37, "5V": 5.1}}},
        "worst_case": {"wires": {"W_BAT_P": {"continuous_current": 4.5, "ampacity": 35.0, "burst_current": 59.0, "burst_ratio": 1.69}},
                       "connectors": {"distribution.P1": {"continuous_current": 4.5, "rating": 30.0, "burst_current": 59.0, "burst_rating": 60.0}},
                       "harness_voltage_drop": {"distribution": {"fraction": 0.02}}},
        "wire_lengths": {"W_BAT_P": {"source": "routed", "length": 0.05}},
        "runtime": {"runtime": 1800.0}, "thermal": {"esc": {"temperature_c": 60.0}}, "fuses": {}, "brownout": {}}))
    board = root / "build" / "pcb" / "test-board"
    board.mkdir(parents=True, exist_ok=True)
    (board / "board.json").write_text(json.dumps({"board": "test-board", "component": "controller", "connectors": {"J_BATT": {"1": "VBAT", "2": "GND"}},
                                                  "erc": {"errors": 0, "warnings": 1}, "drc": {"errors": 0}}))
    (board / "top.png").write_bytes(b"\x89PNG fake")
    (board / "gerbers.zip").write_bytes(b"PK fake")
    cad = root / "build" / "cad"
    (cad / "meshes").mkdir(parents=True, exist_ok=True)
    (cad / "meshes" / "base.stl").write_text("solid base\nendsolid base\n")
    (cad / "cad_model.json").write_text(json.dumps({"source": {"provider": "onshape", "document_id": "d1", "workspace_id": "w1",
                                                               "element_id": "e1"},
                                                    "parts": {"base": {"mass": 0.33, "mesh": "meshes/base.stl", "mass_status": "cad"}}}))


def parse_sse(lines: Any) -> list[dict[str, Any]]:
    """Events from an SSE line iterator, until the server closes the stream (after the final event)."""
    events: list[dict[str, Any]] = []
    current: dict[str, str] = {}
    for line in lines:
        if line == "":
            if "data" in current:
                event = json.loads(current["data"])
                assert event["type"] == current.get("event") and str(event["seq"]) == current.get("id")
                events.append(event)
            current = {}
            continue
        if line.startswith(":"):
            continue
        key, _, value = line.partition(":")
        current[key] = value.lstrip(" ")
    return events


def sse(client: Any, url: str, headers: dict[str, str] | None = None) -> list[dict[str, Any]]:
    with client.stream("GET", url, headers=headers or {}) as response:
        assert response.status_code == 200, response.read()
        assert response.headers["content-type"].startswith("text/event-stream")
        return parse_sse(response.iter_lines())


def fake_cli_bin(directory: Path) -> Path:
    """``claude`` and ``codex`` wrappers that run ``api_fake_agent_cli.py``."""
    directory.mkdir(parents=True, exist_ok=True)
    for provider in ("claude", "codex"):
        path = directory / provider
        path.write_text(f"#!/bin/sh\nexec {sys.executable} {TESTS / 'api_fake_agent_cli.py'} {provider} \"$@\"\n")
        path.chmod(0o755)
    return directory

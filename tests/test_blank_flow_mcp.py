"""From nothing to a tiny 3D-printed-frame robot through MCP tool calls only.

The test plays an outside agent: it creates a blank project, reads the guide and the catalog, writes its
own printed frame into the project catalog, grows robot.yaml with merge patches and runs the stages,
reacting to diagnostics. Every stage must handle the partial project gracefully (``not_configured`` /
``not_applicable`` info, never a crash)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from r3_orchestrator import mcp_server as m

FIXTURE = Path(__file__).parent / "fixtures" / "printbot"


def frame_entry() -> dict[str, Any]:
    return yaml.safe_load((FIXTURE / "catalog" / "components" / "printbot.yaml").read_text())["components"][0]


def patch(project: str, change: dict[str, Any]) -> dict[str, Any]:
    spec = m.r3_get_spec(project=project)
    reply = m.r3_patch_spec(change, spec["sha256"], project=project)
    assert reply["ok"], reply
    return reply


def stage(project: str, name: str) -> dict[str, Any]:
    reply = m.r3_run_stage(name, project=project)
    (summary,) = reply["stages"]
    assert not any(d["code"] == "stage_crashed" for d in reply["diagnostics"]), reply["diagnostics"]
    return {**summary, "diagnostics": reply["diagnostics"]}


@pytest.fixture
def project(tmp_path: Path) -> str:
    created = m.r3_create_project("printbot", parent=str(tmp_path), template="blank")
    assert created["ok"] and "github" not in created
    return created["path"]


def test_blank_project_is_graceful_everywhere(project: str) -> None:
    root = Path(project)
    assert "components: {}" in (root / "robot.yaml").read_text()
    assert "r3_guide" in (root / "AGENTS.md").read_text() and "FeatureScript" in (root / "AGENTS.md").read_text()
    assert "FeatureScript" not in (root / "cad" / "README.md").read_text().replace("never FeatureScript", "")
    result = m.r3_validate(project=project)
    assert result["ok"] and result["counts"]["error"] == 0
    statuses = {s["stage"]: s["status"] for s in result["stages"]}
    assert statuses["model"] == statuses["cad"] == statuses["sim"] == "not_configured"
    assert statuses["pcb"] == "not_applicable"
    hints = {d["stage"]: d["hint"] for d in result["diagnostics"] if d["code"] == "not_configured"}
    assert "r3_list_catalog" in hints["model"]
    release = m.r3_release_status(project=project)
    assert release["fabrication_ready"] is False
    items = {i["id"]: i for i in release["items"]}
    assert items["stage.cad"]["status"] == "not_run" and items["stage.pcb"]["status"] == "verified"


def test_grow_a_printed_frame_robot(project: str) -> None:
    guide = m.r3_guide()
    assert guide["ok"] and "puzzle" in guide["content"].lower() and "r3_put_catalog_component" in guide["content"]

    cameras = m.r3_list_catalog(project=project, kind="camera")["components"]
    assert "arducam-b0445-imx296" in [c["id"] for c in cameras]
    camera = m.r3_get_catalog_component("arducam-b0445-imx296", project=project)
    assert camera["defined_in"] == "platform" and {"mount", "csi"} <= set(camera["component"]["interfaces"])
    assert m.r3_get_catalog_component("arducam-uc-717", project=project)["code"] == "catalog_entry_missing"
    pis = [c["id"] for c in m.r3_list_catalog(project=project, kind="computer", query="raspberry pi 5")["components"]]
    assert {"raspberry-pi-5-2gb", "raspberry-pi-5-4gb", "raspberry-pi-5-8gb", "raspberry-pi-5-16gb"} <= set(pis)

    # The agent's own printed frame, validated by the catalog schema.
    broken = {**frame_entry(), "mechanical": {"shape": "custom", "mass": 0.09}}
    rejected = m.r3_put_catalog_component(broken, project=project)
    assert rejected["code"] == "catalog_entry_invalid" and any("size" in e for e in rejected["errors"])
    written = m.r3_put_catalog_component(frame_entry(), project=project)
    assert written["ok"] and written["path"] == "catalog/components/printbot-frame-petg.yaml"
    assert m.r3_get_catalog_component("printbot-frame-petg", project=project)["defined_in"].startswith("catalog/")

    # Step 1: frame + computer, joined at the Pi's mounting holes.
    patch(project, {"components": {"frame": {"type": "printbot-frame-petg", "pose": {"xyz": [0, 0, 0.03]}},
                                   "pi": {"type": "raspberry-pi-5-4gb"}},
                    "assembly": {"root": "frame", "joins": [
                        {"a": "frame.pi_1", "b": "pi.h1", "fasteners": {"type": "M2.5x5"}},
                        {"a": "frame.pi_2", "b": "pi.h2", "fasteners": {"type": "M2.5x5"}},
                        {"a": "frame.pi_3", "b": "pi.h3", "fasteners": {"type": "M2.5x5"}},
                        {"a": "frame.pi_4", "b": "pi.h4", "fasteners": {"type": "M2.5x5"}}]}})
    assert stage(project, "assembly")["status"] == "ok"
    model = stage(project, "model")
    # An unpowered Pi is a real error; its spare header inputs are not.
    assert {d["code"] for d in model["diagnostics"] if d["severity"] == "error"} == {"input_without_driver"}
    assert any(d["code"] == "computer_inputs_unused" for d in model["diagnostics"])
    assert stage(project, "electrical")["status"] == "not_configured"

    # Step 2: power (2S LiPo -> Pololu 5 V buck -> Pi J8), camera on CAM0 through the 22-22 flat cable.
    spec = yaml.safe_load((FIXTURE / "robot.yaml").read_text())
    patch(project, {"components": {k: v for k, v in spec["components"].items() if k not in ("frame", "pi")},
                    "assembly": spec["assembly"], "electrical": spec["electrical"], "software": spec["software"]})
    for name in ("assembly", "model", "electrical"):
        result = stage(project, name)
        assert result["status"] == "ok", (name, [d for d in result["diagnostics"] if d["severity"] == "error"])
    assert not [d for d in stage(project, "fabrication")["diagnostics"] if d["code"] == "stage_unavailable"]

    # Step 3: a mistake the checks must catch: the camera binding on the empty CAM1 port.
    bindings = spec["software"]["bindings"]
    wrong = {**bindings["front_camera"], "pins": ["pi.CAM1.21", "pi.CAM1.20"], "device": "cam1"}
    patch(project, {"software": {"bindings": {"front_camera": wrong}}})
    errors = [d for d in stage(project, "model")["diagnostics"] if d["severity"] == "error"]
    assert {d["code"] for d in errors} == {"binding_not_connected"}
    diagnosis = m.r3_diagnose(project=project)
    assert diagnosis["errors"] >= 1 and any(g["subject"] == "front_camera" for g in diagnosis["subjects"])
    patch(project, {"software": {"bindings": {"front_camera": bindings["front_camera"]}}})
    assert stage(project, "model")["status"] == "ok"

    # A cable too short for where the computer sits is reported with numbers.
    short = {**m.r3_get_catalog_component("arducam-fpc-22-22-150mm", project=project)["component"]}
    short["behavior"] = {**short["behavior"], "params": {"length": "20 mm"}}
    assert m.r3_put_catalog_component(short, project=project)["overrides_platform_entry"] is True
    cable = next(d for d in stage(project, "model")["diagnostics"] if d["code"] == "cable_too_short")
    assert cable["data"]["span"] > cable["data"]["length"] == pytest.approx(0.02)

    status = m.r3_status(project=project)
    assert status["summary"]["components"] == 6 and status["summary"]["bindings"] == 1
    assert m.r3_release_status(project=project)["fabrication_ready"] is False

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
    assert "Onshape" in (root / "cad" / "README.md").read_text()
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


def test_grow_a_native_pulled_project(project: str) -> None:
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from pulled_fixture import write_pull
    guide = m.r3_guide()
    assert guide["ok"] and "Onshape" in guide["content"] and "r3_cad_pull" in guide["content"]
    entry = {"id": "synthetic-coupon", "name": "Synthetic coupon", "kind": "structure",
             "mechanical": {"mass": .02, "size": [.04,.03,.004], "fabricated": True, "material": "PLA"}}
    broken = {**entry, "mechanical": {**entry["mechanical"], "solids": [{"kind": "box"}]}}
    assert m.r3_put_catalog_component(broken, project=project)["code"] == "catalog_entry_invalid"
    assert m.r3_put_catalog_component(entry, project=project)["ok"]
    patch(project, {"components": {"coupon": {"type": "synthetic-coupon"}}})
    assert {d["code"] for d in stage(project, "cad")["diagnostics"] if d["severity"] == "error"} == {"cad_pull_missing"}
    write_pull(Path(project), {"coupon": BRepPrimAPI_MakeBox(40,30,4).Shape()})
    assert stage(project, "model")["status"] == "ok"
    assert stage(project, "cad")["status"] == "ok"
    current = m.r3_get_spec(project=project)
    refused = m.r3_patch_spec({"components": {"coupon": {"pulled": {"mass": 5}}}}, current["sha256"], project=project)
    assert not refused["ok"] and refused["code"] == "spec_read_only"
    assert m.r3_release_status(project=project)["fabrication_ready"] is False


def test_source_a_catalog_part_by_requirements(tmp_path: Path) -> None:
    """`source:` slots also find the bought parts R3 already models (provider `catalog`); choosing one pins the
    existing entry (no generated file), with its datasheet provenance in the candidate parameters."""
    import shutil

    root = tmp_path / "printbot"
    shutil.copytree(FIXTURE, root)
    text = (root / "robot.yaml").read_text().replace(
        "  camera: {type: arducam-b0445-imx296}",
        "  camera: {source: {requires: {kind: camera, shutter: global}, prefer: {}}}")
    (root / "robot.yaml").write_text(text)
    project = str(root)
    before = m.r3_validate(project=project, stages=["assembly", "model"])
    assert "cad_pull_missing" in {d["code"] for d in before["diagnostics"]}
    found = m.r3_source_search("camera", project=project, providers=["catalog"], offline=True)
    best = found["ranked"][0]
    assert best["candidate"]["id"] == "catalog-arducam-b0445-imx296" and best["tier"] != "rejected"
    params = best["candidate"]["params"]
    assert params["shutter"]["value"] == "global" and params["sensor"]["status"] == "datasheet"
    chosen = m.r3_source_choose("camera", "1", project=project)
    assert chosen["ok"] and chosen["pinned"]["catalog_id"] == "arducam-b0445-imx296" and chosen["catalog_file"] == ""
    assert not list((root / "catalog" / "components").glob("sourced_*.yaml"))
    for name in ("assembly", "model"):
        result = stage(project, name)
        assert "cad_pull_missing" in {d["code"] for d in result["diagnostics"]}

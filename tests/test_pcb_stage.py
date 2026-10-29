"""The ``pcb`` stage on the RC car: board.json hand-off, KiCad outputs, catalog consistency."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from r3_catalog.schema import ComponentType
from r3_kicad.board_json import catalog_check, connectors_map
from r3_kicad.flow import load_intent_module
from r3_kicad.library import find_install
from r3_kicad.netlist import read_netlist
from r3_model.project import Project

ROOT = Path(__file__).resolve().parents[1]
CAR = ROOT / "examples/robots/rc-car"
REQUEST = ROOT / ".r3-agents/requests/pcb.md"
pytestmark = pytest.mark.skipif(find_install() is None, reason="KiCad (kicad-cli + pcbnew) not installed")


@pytest.fixture(scope="module")
def stage_run():
    import os

    from r3_kicad import stage

    os.environ.setdefault("R3_KICAD_REGENERATE", "0")      # check the committed project; the flow has its own test
    project = Project.load(CAR)
    result = stage.run(project)
    board = json.loads((CAR / "build/pcb/carrier-hat/board.json").read_text())
    return project, result, board


def test_board_json_contract(stage_run):
    _, result, board = stage_run
    assert board["board"] == "carrier-hat" and board["component"] == "hat"
    assert board["outline"]["width"] == pytest.approx(0.065) and board["outline"]["height"] == pytest.approx(0.0565)
    assert board["erc"]["errors"] == 0
    assert board["drc"]["errors"] == 0 and board["drc"]["unconnected"] == 0
    assert set(board["connectors"]) == {"J_GPIO", "J_BATT", "J_ESC", "J_SERVO", "J_RX", "J_GPS", "J_ENC"}
    assert board["connectors"]["J_SERVO"]["2"] == "SERVO_6V" and "2" not in board["connectors"]["J_ESC"]
    assert board["connectors"]["J_GPIO"]["13"] == "HEARTBEAT"
    assert "J_BATT.1" in board["nets"]["VBAT"]
    traces = board["traces"]
    assert traces and all(t["max_current"] > 0 and t["width"] > 0 for t in traces)
    deliberate = [t for t in traces if t["origin"] == "deliberate"]
    assert any(t["net"] == "VIN_HAT" and t["max_current"] >= 5.0 for t in deliberate)
    for key in ("kicad_pcb", "kicad_sch", "gerbers", "board_step", "step", "bom", "pos", "netlist"):
        assert (CAR / board["files"][key]).exists(), key
    for side in ("render_top", "render_bottom"):
        assert (CAR / board["renders"][side]).stat().st_size > 50_000
    assert not [d for d in result.diagnostics if d.code in ("kicad_erc_errors", "kicad_drc_errors", "kicad_gui_opened",
                                                            "pcb_netlist_mismatch", "pcb_flow_failed")]


def test_mounting_envelopes_handed_to_cad(stage_run):
    _, _, board = stage_run
    mounting = board["mounting"]
    assert len(mounting["holes"]) == 4
    assert {tuple(h["position"]) for h in mounting["holes"]} == {(-0.029, 0.02475), (0.029, 0.02475),
                                                                 (-0.029, -0.02425), (0.029, -0.02425)}
    assert mounting["printed_cap"]["inner_diameter"] == pytest.approx(mounting["head"]["diameter"] + 0.0006)
    assert mounting["keepouts"]["top_components_radius"] > mounting["head"]["diameter"] / 2


def test_stage_reports_catalog_mismatch_until_the_request_is_applied(stage_run):
    _, result, _ = stage_run
    mismatch = [d for d in result.diagnostics if d.code == "board_catalog_mismatch"]
    # The catalog still feeds the servo from the ESC BEC and has no heartbeat pin: the board differs on purpose.
    assert {s for d in mismatch for s in d.subjects} >= {"J_ESC", "J_SERVO", "J_GPIO"}


def _requested_component() -> ComponentType:
    text = REQUEST.read_text()
    block = re.search(r"```yaml\n(  - id: r3-carrier-hat.*?)```", text, re.S).group(1)
    entry = yaml.safe_load(block)[0]
    for link in entry["internal_links"]:
        if link["kind"] == "failsafe_arbiter":      # requested schema literal; validate with the interim kind
            link["kind"] = "switch"
    return ComponentType.model_validate(entry)


def test_board_matches_catalog_with_requested_patch(stage_run):
    project, _, _ = stage_run
    source = CAR / "pcb/carrier-hat"
    intent = load_intent_module(source).build()
    netlist = read_netlist(CAR / "build/pcb/carrier-hat/carrier-hat.xml")
    connectors = connectors_map(netlist, intent)
    diags = catalog_check("hat", _requested_component(), connectors, netlist.nets, intent)
    assert diags == [], [d.message for d in diags]

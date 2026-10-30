"""The ``pcb`` stage on the bench: board.json hand-off, KiCad outputs, catalog consistency."""

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
COUPON = ROOT / "tests/fixtures/pcb"
pytestmark = pytest.mark.skipif(find_install() is None, reason="KiCad (kicad-cli + pcbnew) not installed")


@pytest.fixture(scope="module")
def stage_run(pcb_project):
    from r3_kicad import stage
    project, _ = pcb_project
    result = stage.run(project)
    board = json.loads((project.root / "build/pcb/test-board/board.json").read_text())
    return project, result, board


def test_board_json_contract(stage_run):
    project, result, board = stage_run
    assert board["board"] == "test-board" and board["component"] == "filter"
    assert board["outline"]["width"] == pytest.approx(.038) and board["outline"]["height"] == pytest.approx(.028)
    assert board["erc"]["errors"] == 0
    assert board["drc"]["errors"] == 0 and board["drc"]["unconnected"] == 0
    assert set(board["connectors"]) == {"INPUT", "OUTPUT"}
    assert board["connectors"]["OUTPUT"] == {"1": "SIGNAL_OUT", "2": "SUPPLY", "3": "GND"}
    assert "INPUT.2" in board["nets"]["SUPPLY"]
    assert board["regulators"] == {} and board["arbiter"] is None and "spice" not in board["files"]
    assert board["traces"] and all(t["max_current"] > 0 and t["width"] > 0 for t in board["traces"])
    for key in ("kicad_pcb", "kicad_sch", "gerbers", "board_step", "step", "bom", "pos", "netlist"):
        assert (project.root / board["files"][key]).exists(), key
    for side in ("render_top", "render_bottom"):
        assert (project.root / board["renders"][side]).stat().st_size > 50_000
    assert not [d for d in result.diagnostics if d.code in ("kicad_erc_errors", "kicad_drc_errors", "kicad_gui_opened", "pcb_netlist_mismatch", "pcb_flow_failed")]


def test_mounting_envelopes_handed_to_cad(stage_run):
    _, _, board = stage_run
    mounting = board["mounting"]
    assert len(mounting["holes"]) == 2
    assert {tuple(h["position"]) for h in mounting["holes"]} == {(-.015, -.01), (.015, -.01)}
    assert mounting["printed_cap"]["inner_diameter"] == pytest.approx(mounting["head"]["diameter"] + .0006)
    assert mounting["keepouts"]["top_components_radius"] > mounting["head"]["diameter"] / 2


def test_catalog_matches_the_board(stage_run):
    """The requested catalog patch is applied: the KiCad netlist matches test-controller exactly, the
    connector positions are the measured ones and the TVS on the board is the catalog's."""
    _, result, board = stage_run
    assert not [d for d in result.diagnostics if d.code == "board_catalog_mismatch"], \
        [d.message for d in result.diagnostics if d.code == "board_catalog_mismatch"]
    assert not [d for d in result.diagnostics if d.code == "pcb_connector_position_mismatch"]
    assert all(entry["deviation_xy"] <= 0.002 for entry in board["connector_geometry"].values())
    assert not [d for d in result.diagnostics if d.code == "pcb_tvs_clamp_exceeds_abs_max"]


def _declared_component():
    return Project.load(COUPON).catalog.component("test-filter")


def test_board_matches_declared_catalog(stage_run):
    project, _, _ = stage_run
    source = project.root / "pcb/test-board"
    intent = load_intent_module(source).build()
    netlist = read_netlist(project.root / "build/pcb/test-board/test-board.xml")
    connectors = connectors_map(netlist, intent)
    assert catalog_check("filter", _declared_component(), connectors, netlist.nets, intent) == []

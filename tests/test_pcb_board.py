"""The committed carrier-HAT KiCad project, checked with KiCad's own tools (headless)."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from r3_kicad.board_read import read_board
from r3_kicad.cli import KicadCli, report_counts
from r3_kicad.flow import inputs_digest, load_intent_module
from r3_kicad.headless import GUI_PATTERN, GuiWatch, gui_processes
from r3_kicad.library import find_install
from r3_kicad.netlist import compare, read_netlist

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "examples/robots/rc-car/pcb/carrier-hat"
needs_kicad = pytest.mark.skipif(find_install() is None, reason="KiCad (kicad-cli + pcbnew) not installed")


@pytest.fixture(scope="module")
def intent():
    return load_intent_module(BOARD).build()


@pytest.fixture(scope="module")
def checked(tmp_path_factory):
    """ERC, DRC (schematic parity, zones refilled), netlist and a pcbnew read-back of a copy of the project,
    with the process table watched for KiCad GUI programs the whole time."""
    work = tmp_path_factory.mktemp("carrier-hat")
    for suffix in (".kicad_pro", ".kicad_sch", ".kicad_pcb", ".kicad_dru"):   # .kicad_dru: Board Setup custom rules
        shutil.copy2(BOARD / f"carrier-hat{suffix}", work / f"carrier-hat{suffix}")
    cli = KicadCli.find()
    with GuiWatch(interval=0.1) as watch:
        cli.erc(work / "carrier-hat.kicad_sch", work / "erc.json")
        cli.drc(work / "carrier-hat.kicad_pcb", work / "drc.json")
        cli.netlist(work / "carrier-hat.kicad_sch", work / "net.xml")
        board = read_board(work / "carrier-hat.kicad_pcb", work)
    return {"work": work, "watch": watch, "board": board, "erc": report_counts(work / "erc.json"),
            "drc": report_counts(work / "drc.json"), "netlist": read_netlist(work / "net.xml")}


def test_gui_pattern_matches_editors_not_the_cli():
    assert GUI_PATTERN.search("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad ")
    assert GUI_PATTERN.search("/Applications/KiCad/KiCad.app/Contents/Applications/pcbnew.app/Contents/MacOS/pcbnew ")
    assert GUI_PATTERN.search("/usr/bin/eeschema ")
    assert not GUI_PATTERN.search("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli ")
    assert not GUI_PATTERN.search("/bin/zsh ")


@needs_kicad
def test_no_kicad_window_while_checking(checked):
    assert checked["watch"].samples > 3
    assert checked["watch"].seen == [] and checked["watch"].windows == []
    assert gui_processes() == []


@needs_kicad
def test_erc_and_drc_are_clean(checked):
    assert checked["erc"]["errors"] == 0
    drc = checked["drc"]
    assert drc["errors"] == 0 and drc["unconnected"] == 0 and drc["parity"] == 0, drc


@needs_kicad
def test_netlist_equals_design_intent(checked, intent):
    assert compare(intent.nets(), checked["netlist"]) == []


@needs_kicad
def test_board_geometry(checked, intent):
    board = checked["board"]
    assert board.size == pytest.approx((65.0, 56.5), abs=0.01)
    pads = {(p.ref, p.number): (p.x, p.y) for p in board.pads}
    # HAT specification: header pin 1 inboard at (8.37, 4.77), pin 2 at the edge, pin 40 at the far end.
    assert pads[("J1", "1")] == pytest.approx((8.37, 4.77), abs=1e-3)
    assert pads[("J1", "2")] == pytest.approx((8.37, 2.23), abs=1e-3)
    assert pads[("J1", "40")] == pytest.approx((56.63, 2.23), abs=1e-3)
    assert next(f for f in board.footprints if f.ref == "J1").layer == "B.Cu"
    # Mounting envelopes: no other pad within the screw-head (top) / standoff (bottom) contact circles.
    m = intent.mounting
    for hx, hy in intent.holes:
        for pad in board.pads:
            if pad.ref.startswith("H"):
                continue
            assert math.hypot(pad.x - hx, pad.y - hy) > m.top_copper_radius, (pad.ref, pad.number)
    # Hand-routed power copper survived the autorouter round trip as locked copper.
    locked = [s for s in board.segments if s.locked]
    assert len(locked) >= len([t for t in intent.power_tracks]) and any(s.net == "VIN_HAT" for s in locked)
    ground = [z for z in board.zones if z.net == "GND"]
    assert len(ground) == 2 and all(z.area > 500 for z in ground)
    # Power pours at the regulator pins, the FET -> shunt gap and the servo buck-boost power row, filled by KiCad on
    # the top layer.
    power = {z.net: z for z in board.zones if z.net != "GND"}
    assert set(power) == {"SW_5V", "VIN_HAT", "VBAT_PROT", "SW1_6V", "SW2_6V", "CS_6V", "SERVO_6V"}
    assert all(z.layer == "F.Cu" and z.area > 5 and z.polygons for z in power.values())


def test_flow_log_records_a_headless_run():
    record = json.loads((BOARD / "flow-log.json").read_text())
    assert record["headless"]["gui_processes_started"] == []
    assert record["headless"]["tool_windows_opened"] == [] and record["headless"]["window_samples"] > 10
    assert record["steps"]["drc"]["result"]["errors"] == 0
    assert record["steps"]["drc"]["result"]["unconnected"] == 0
    assert record["steps"]["erc"]["result"]["errors"] == 0


def test_committed_project_is_current():
    """The committed KiCad files were produced by the current intent and automation (else the stage regenerates)."""
    record = json.loads((BOARD / "flow-log.json").read_text())
    assert record["inputs_digest"] == inputs_digest(BOARD)


@needs_kicad
def test_pcbnew_module_runs_without_a_display(tmp_path):
    """KiCad's bundled Python imports pcbnew and saves a board with no GUI process appearing."""
    install = find_install()
    script = tmp_path / "probe.py"
    script.write_text("import pcbnew\nb = pcbnew.NewBoard(%r)\npcbnew.SaveBoard(%r, b)\nprint('ok')\n"
                      % (str(tmp_path / "p.kicad_pcb"), str(tmp_path / "p.kicad_pcb")))
    with GuiWatch(interval=0.05) as watch:
        out = subprocess.run([str(install.python), "-I", str(script)], capture_output=True, text=True, timeout=120)
    assert "ok" in out.stdout and (tmp_path / "p.kicad_pcb").exists()
    assert watch.clean, watch.summary()

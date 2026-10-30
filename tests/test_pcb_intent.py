"""Carrier-HAT design intent: official KiCad library parts, schematic plan, placement plan."""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

from r3_kicad.library import find_install, footprint_exists, load_symbol, pin_outward, pin_position
from r3_kicad.schematic_layout import plan_sheet

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "tests/fixtures/pcb/pcb/test-board"
needs_kicad = pytest.mark.skipif(find_install() is None, reason="KiCad libraries not installed")


def _module():
    spec = importlib.util.spec_from_file_location("carrier_hat_test", BOARD / "board.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def module():
    return _module()


@pytest.fixture(scope="module")
def intent(module):
    return module.build()


def test_intent_is_consistent(intent):
    assert intent.validate() == []
    assert {p.ref for p in intent.parts} == {"J1", "J2", "R1", "C1", "H1", "H2"}
    assert intent.connectors()["INPUT"].pins == {"1": "SIGNAL_IN", "2": "SUPPLY", "3": "GND"}
    assert intent.nets()["SIGNAL_IN"] == ["J1.1", "R1.1"]
    assert intent.nets()["SIGNAL_OUT"] == ["C1.1", "J2.1", "R1.2"]


@needs_kicad
def test_every_part_is_an_official_library_part(intent):
    for part in intent.parts:
        symbol = load_symbol(part.lib_id)
        numbers = {p.number for p in symbol.pins}
        assert set(part.pins) | set(part.no_connect) <= numbers, part.ref
        unused = [p for p in symbol.pins if p.number not in part.pins and p.number not in part.no_connect]
        # Unused pins must be the library's no-connect pins (type no_connect, or the hidden NC/RESV pins: MPU-6050 datasheet "do not connect").
        assert all(p.etype == "no_connect" or p.name in ("NC", "RESV") for p in unused), (part.ref, unused)
        footprint = part.footprint or symbol.footprint
        if part.lib_id.startswith("power:"):
            continue
        assert footprint_exists(footprint), (part.ref, footprint)
    # Pad numbers are checked by the pcbnew board build (netlist pins without a pad fail it) and by DRC parity.


@needs_kicad
def test_pin_geometry_matches_kicad_convention():
    r = load_symbol("Device:R")
    pin1 = r.pin("1")
    # Device:R pin 1 is at the top; rotated 90 deg (counter-clockwise) it is on the left, pointing left.
    assert pin_position(pin1, (100.0, 100.0), 0) == pytest.approx((100.0, 96.19))
    assert pin_position(pin1, (101.6, 101.6), 90) == pytest.approx((97.79, 101.6))
    assert pin_outward(pin1, 0) == pytest.approx(90.0)
    assert pin_outward(pin1, 90) == pytest.approx(180.0)


@needs_kicad
def test_schematic_plan_has_no_overlaps_or_shorts(module, intent):
    plan = plan_sheet(intent, module.schematic_groups())
    assert plan.problems == []
    assert len(plan.symbols) == len(intent.parts)
    labelled = {(lab.ref, pin) for lab in plan.labels for pin in lab.pins}
    for part in intent.parts:
        symbol = load_symbol(part.lib_id)
        connected = {p.number for p in symbol.pins if p.number in part.pins}
        assert connected <= {pin for ref, pin in labelled if ref == part.ref}, part.ref
    assert intent.sheet == "A3"
    for sym in plan.symbols:   # inside the A2 frame (594 x 420 mm), clear of the title block
        x0, y0, x1, y1 = sym.box
        assert 10 <= x0 and x1 <= 584 and 10 <= y0 and y1 <= 410, sym
        assert not (x1 > 470 and y1 > 375), sym


def test_placement_covers_every_footprint(intent):
    placed = {p.ref for p in intent.parts if p.pcb is not None}
    on_board = {p.ref for p in intent.parts if not p.ref.startswith("#")}
    assert on_board == placed
    j1 = intent.part("J1").pcb
    assert j1.side == "F" and (j1.x, j1.y) == pytest.approx((8, 8))
    # Every hand-routed track starts or ends on a pad of its own net.
    nets = intent.nets()
    for track in intent.power_tracks:
        for point in track.points:
            if isinstance(point, str):
                assert point in nets[track.net], (track.net, point)

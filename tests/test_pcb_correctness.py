import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from r3_kicad.board_json import _value
from r3_kicad.board_read import BoardFile, BoardPad
from r3_kicad.flow import Flow, FlowError, load_intent_module
from r3_kicad.intent import Placement, PowerTrack
from r3_kicad.netlist import Netlist
from r3_kicad.validation import board_netlist_checks
from r3_model.work_units import debug_work_units


def intent():
    return load_intent_module(Path(__file__).parent / 'fixtures/pcb/pcb/test-board').build()


def test_missing_placement_and_wrong_pad_net_are_rejected():
    board = intent()
    board.parts[0] = replace(board.parts[0], pcb=None)
    board.power_tracks.append(PowerTrack('GND', .8, ('J1.2', 'J2.2')))
    errors = board.validate()
    assert any('placement is missing' in e for e in errors)
    assert any('belongs to' in e and 'J1.2' in e for e in errors)


def test_placement_and_via_geometry_fail_closed():
    board = intent()
    board.parts[0] = replace(board.parts[0], pcb=Placement(float('nan'), 2))
    assert any('finite' in e for e in board.validate())
    board.parts[0] = replace(board.parts[0], pcb=Placement(999, 2))
    assert any('outside' in e for e in board.validate())


def test_pad_net_assignment_is_checked_independently_of_rendering():
    board = BoardFile((20, 20), pads=[BoardPad('J1', '1', 'GND', 5, 5, ('F.Cu',))])
    schematic = Netlist(nets={'SUPPLY': [('J1', '1', '')]})
    findings = board_netlist_checks(board, schematic, 'coupon')
    assert len(findings) == 1 and findings[0].code == 'pcb_pad_net_mismatch'
    assert findings[0].data['expected_net'] == 'SUPPLY'
    board.pads[0] = replace(board.pads[0], net='/SUPPLY')
    assert board_netlist_checks(board, schematic, 'coupon') == []
    # Same pad number can occur more than once in a real footprint.
    board.pads.append(replace(board.pads[0], x=6))
    assert board_netlist_checks(board, schematic, 'coupon') == []


def test_drc_is_a_gate_not_a_successful_report_export(tmp_path):
    flow = Flow.__new__(Flow)
    flow.work, flow.pcb = tmp_path, tmp_path / 'coupon.kicad_pcb'
    def check(_pcb, target, **kwargs):
        target.write_text(json.dumps({'violations': [], 'unconnected_items': [{'severity': 'error', 'description': 'pad disconnected'}]}))
        return SimpleNamespace(ok=True, stderr='')
    flow.cli = SimpleNamespace(drc=check)
    with pytest.raises(FlowError, match='DRC failed'):
        flow.drc()
    # A stale clean report cannot certify a failed current invocation.
    (tmp_path / 'drc.json').write_text('{"violations": []}')
    flow.cli.drc = lambda *a, **kw: SimpleNamespace(ok=False, stderr='engine crashed')
    with pytest.raises(FlowError, match='did not complete'):
        flow.drc()


def test_embedded_resistor_value_and_unknown_value():
    assert _value('4k7') == pytest.approx(4700)
    assert _value('0R47') == pytest.approx(.47)
    assert _value('1M0') == pytest.approx(1e6)
    import math
    assert math.isnan(_value('not-a-value'))


def test_causal_groups_preserve_evidence_and_do_not_infer_causes_from_text():
    d = {'code': 'pad_net', 'severity': 'error', 'stage': 'pcb', 'message': 'wrong pad',
         'subjects': ['board', 'J1'], 'data': {'cause_id': 'pad:J1.1', 'cause_status': 'confirmed'}}
    groups = debug_work_units([d, d])
    assert len(groups) == 1 and groups[0]['evidence'][0]['occurrences'] == 2
    other = {**d, 'data': {}, 'subjects': ['J2']}
    groups = debug_work_units([d, other])
    assert len(groups) == 2
    assert {g['relationship'] for g in groups} == {'explicit_cause', 'related_context'}

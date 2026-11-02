"""Independent data-integrity checks; KiCad DRC remains copper-geometry authority.

Matching net names does NOT prove conductive contact. These checks compare
schematic pad identities with saved-board assignments, never screenshot pixels
or pad bounding boxes. Unknown geometry must stay explicitly unsupported.
"""
from __future__ import annotations

from r3_model.diagnostics import Diagnostic

from .netlist import net_name


def board_netlist_checks(board, netlist, name: str) -> list[Diagnostic]:
    expected = {(ref, pin): net for (ref, pin), net in netlist.pin_nets().items()
                if not ref.startswith('#') and not net.startswith('unconnected-')}
    actual = {}
    for pad in board.pads:
        if pad.number:
            actual.setdefault((pad.ref, pad.number), set()).add(net_name(pad.net))
    diagnostics = []
    for key, net in sorted(expected.items()):
        assigned = actual.get(key, set())
        if assigned != {net}:
            diagnostics.append(Diagnostic.error(
                'pcb_pad_net_mismatch', f'{name}: {key[0]} pad {key[1]} must connect to {net}; '
                f'saved board has {", ".join(sorted(assigned)) or "no pad"}.',
                subjects=[name, key[0], net],
                data={'subsystem': 'pcb.connectivity', 'cause_id': f'{name}:pad:{key[0]}.{key[1]}',
                      'cause_status': 'confirmed', 'expected_net': net, 'actual_nets': sorted(assigned),
                      'reference': key[0], 'pad': key[1], 'consequence': 'Schematic and board disagree.'},
                hint='Correct the footprint pin mapping or pad net assignment, then rerun schematic parity and DRC.'))
    for key, assigned in sorted(actual.items()):
        live = {n for n in assigned if n and not n.startswith('unconnected-')}
        if key not in expected and live:
            diagnostics.append(Diagnostic.error('pcb_unexpected_pad_net',
                f'{name}: pad {key[0]}.{key[1]} has copper nets absent from the schematic: {sorted(live)}.',
                subjects=[name, key[0]], data={'subsystem': 'pcb.connectivity', 'actual_nets': sorted(live),
                'cause_id': f'{name}:pad:{key[0]}.{key[1]}', 'cause_status': 'confirmed'},
                hint='Check no-connect pins and stale footprint assignments; do not join them merely to clear a warning.'))
    return diagnostics

"""Shared mount patterns and screw-cap seats, resolved before assembly compilation.

A fixture may copy one catalog interface's holes and specify its own hole kind,
thread diameter and depth. The ordinary assembly join still checks the match.
"""
from __future__ import annotations

import copy

from r3_model.assembly import ResolvedInterface, frame_matrix

from .schema import Catalog


def resolve_patterns(catalog: Catalog) -> None:
    resolved: set[str] = set()

    def holes(ref: str, chain: tuple[str, ...] = ()) -> list:
        if ref in chain:
            raise ValueError(f"hole-pattern reference cycle: {' -> '.join((*chain, ref))}")
        component, name = ref.rsplit('.', 1)
        iface = catalog.component(component).interfaces[name]
        params = iface.params
        if ref not in resolved and params.get('holes_from'):
            source = holes(str(params['holes_from']), (*chain, ref))
            derived = []
            for row in source:
                x, y, diameter = row[:3]
                kind = row[3] if len(row) > 3 else 'clearance'
                depth = row[4] if len(row) > 4 else 0
                derived.append([x, y, params.get('hole_diameter', diameter),
                                params.get('hole_kind', kind), params.get('hole_depth', depth)])
            params['holes'] = derived
        resolved.add(ref)
        return copy.deepcopy(params.get('holes', []))

    for component in catalog.components.values():
        for name in component.interfaces:
            holes(f'{component.id}.{name}')
    for component in catalog.components.values():
        for iface in component.interfaces.values():
            seat = iface.params.get('hole_seat')
            if seat is None:
                continue
            source = component.interfaces[seat['interface']]
            frame = frame_matrix(source.origin, source.z, source.x)
            pattern = ResolvedInterface(component.id, seat['interface'], source.kind, frame, source.params)
            hole = pattern.holes[int(seat['index'])]
            iface.origin = (frame[:3, 3] + frame[:3, 0] * hole.x + frame[:3, 1] * hole.y
                            - frame[:3, 2] * hole.depth).tolist()
            iface.z = (-frame[:3, 2]).tolist()
            iface.x = frame[:3, 0].tolist()

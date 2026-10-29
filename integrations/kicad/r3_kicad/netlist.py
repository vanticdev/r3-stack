"""Read KiCad's own netlist export (``kicad-cli sch export netlist --format kicadxml``)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Component:
    ref: str
    value: str
    footprint: str
    lib_id: str
    fields: dict[str, str]
    dnp: bool
    exclude_from_bom: bool


@dataclass
class Netlist:
    components: dict[str, Component] = field(default_factory=dict)
    nets: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)   # net -> [(ref, pin, pinfunction)]

    def pin_nets(self) -> dict[tuple[str, str], str]:
        return {(ref, pin): net for net, nodes in self.nets.items() for ref, pin, _ in nodes}


def net_name(raw: str) -> str:
    """Local labels on the root sheet are reported as "/NAME"."""
    return raw[1:] if raw.startswith("/") and raw.count("/") == 1 else raw


def read_netlist(path: Path) -> Netlist:
    root = ET.parse(path).getroot()
    out = Netlist()
    for comp in root.iter("comp"):
        lib = comp.find("libsource")
        fields = {f.get("name", ""): (f.text or "") for f in comp.iter("field")}
        props = {p.get("name", ""): p.get("value", "") for p in comp.iter("property")}
        out.components[comp.get("ref", "")] = Component(
            ref=comp.get("ref", ""), value=(comp.findtext("value") or ""), footprint=(comp.findtext("footprint") or ""),
            lib_id=f"{lib.get('lib')}:{lib.get('part')}" if lib is not None else "", fields=fields,
            dnp="dnp" in props, exclude_from_bom="exclude_from_bom" in props)
    for net in root.iter("net"):
        name = net_name(net.get("name", ""))
        out.nets[name] = [(n.get("ref", ""), n.get("pin", ""), n.get("pinfunction", "")) for n in net.iter("node")]
    return out


def compare(intent_nets: dict[str, list[str]], netlist: Netlist) -> list[str]:
    """Differences between the intended nets ("REF.PIN" members) and KiCad's netlist."""
    problems = []
    actual = {name: sorted(f"{r}.{p}" for r, p, _ in nodes) for name, nodes in netlist.nets.items()
              if not name.startswith("unconnected-") and not name.startswith("Net-(")}
    by_pin = {member: name for name, members in actual.items() for member in members}
    for net, members in intent_nets.items():
        members = [m for m in members if not m.startswith("#")]     # power symbols are not in netlists
        if not members:
            continue
        if net not in actual:
            problems.append(f"net {net} missing from the schematic netlist")
            continue
        missing = sorted(set(members) - set(actual[net]))
        extra = sorted(set(actual[net]) - set(members))
        if missing:
            problems.append(f"net {net}: pins not connected {missing} (found on {[by_pin.get(m) for m in missing]})")
        if extra:
            problems.append(f"net {net}: unexpected pins {extra}")
    for net in actual:
        if net not in intent_nets:
            problems.append(f"unexpected net {net}: {actual[net]}")
    for name, nodes in netlist.nets.items():
        if name.startswith("Net-("):
            problems.append(f"unnamed net {name}: {[f'{r}.{p}' for r, p, _ in nodes]}")
    return problems

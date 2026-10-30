"""``model`` stage: static checks on the derived component/net graph.

Runs before any external tool. Every check reads only ``robot.yaml``, the
catalog and (when present) upstream artifacts, so it is cheap enough to run on
every edit. Writes ``build/model/graph.json`` and ``build/model/layout.png``.

Checks (stable diagnostic codes):

* connectors: graph diagnostics (``connector_mate_mismatch``, ...),
  ``connector_multiply_mated``, ``harness_connector_unmated``,
  ``wire_gauge_connector_mismatch``
* bindings: ``binding_not_connected``, ``binding_expects_mismatch``,
  ``binding_pin_unresolved``, ``binding_pin_not_on_computer``,
  ``binding_pin_conflict``, ``binding_pin_function_missing``,
  ``binding_pwm_channel_mismatch``, ``binding_gpio_line_mismatch``,
  ``binding_device_mismatch``
* pin roles: ``power_conflict``, ``power_ground_short``,
  ``uart_tx_tx_conflict``, ``pwm_output_conflict``, ``input_without_driver``
* voltages: ``power_in_voltage_out_of_range``, ``power_in_overvoltage``,
  ``power_in_undervoltage``, ``logic_level_overvoltage``, ``logic_level_marginal``
* buses: ``i2c_address_conflict``
* mechanics: ``component_interference``, ``mass_exceeds_requirement``
* sourcing (``source:`` slots, see :mod:`r3_orchestrator.sourcing`): ``component_unsourced``,
  ``sourcing_catalog_missing``, ``sourcing_digest_mismatch``, ``sourcing_pin_stale``,
  ``source_requirement_unmet``, ``source_requirement_unverified``
"""

from __future__ import annotations

import itertools
import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np

from r3_model.diagnostics import Diagnostic, StageResult
from r3_model.graph import RobotGraph, _UnionFind
from r3_model.project import Project

from r3_model.units import to_si

from .geometry import Solid, make_solid, outline_solid, overlap, solids_solid

STAGE = "model"

SIGNAL_ROLES = {"gpio", "pwm_in", "pwm_out", "i2c_sda", "i2c_scl", "spi_mosi", "spi_miso", "spi_sck", "spi_cs",
                "uart_tx", "uart_rx", "analog"}
# Inputs and the roles that can drive them.
INPUT_DRIVERS: dict[str, set[str]] = {
    "uart_rx": {"uart_tx"},
    "pwm_in": {"pwm_out", "gpio"},
    "power_in": {"power_out"},
}
OUTPUT_ROLES = {"uart_tx", "pwm_out", "gpio", "i2c_sda", "i2c_scl", "spi_mosi", "spi_sck", "spi_cs", "spi_miso"}
INPUT_ROLES = {"uart_rx", "pwm_in", "gpio", "i2c_sda", "i2c_scl", "spi_miso", "spi_mosi", "spi_sck", "spi_cs", "analog"}
# Internal links that keep a supply rail at the same voltage (current limiting elements are ignored).
POWER_LINKS = {"fuse", "switch", "shunt", "wire"}
# Internal links a logic level passes through unchanged (a level shifter translates it). A failsafe
# arbiter re-drives its input at the same logic level (or neutral pulses when the heartbeat stops).
SIGNAL_LINKS = {"series_resistor", "switch", "wire", "failsafe_arbiter"}  # graph.SIGNAL_LINK_KINDS minus level shifters
# CMOS inputs: absolute maximum ~ VDD + 0.3 V, minimum high threshold V_IH ~ 0.7 VDD.
CMOS_ABS_MAX_MARGIN = 0.3
CMOS_VIH_FRACTION = 0.7
TOLERANT_5V_MAX = 5.5
# Wire length estimate when no routed length exists: straight line x 1.3 + slack.
ROUTE_FACTOR_ESTIMATE = 1.3
DEFAULT_WIRE_LENGTH = 0.15  # m, when an end has no known position


@dataclass
class PinInfo:
    node: str  # C:<component>.<pin>
    component: str
    name: str
    role: str
    spec: Any  # r3_catalog.schema.Pin
    connector_pins: list[str] = field(default_factory=list)  # <component>.<connector>.<num>
    passthrough: bool = False

    @property
    def exposed(self) -> bool:
        return bool(self.connector_pins)


class ModelChecker:
    """Holds the project, graph and derived lookup tables for all checks."""

    def __init__(self, project: Project, result: StageResult) -> None:
        self.project = project
        self.spec = project.spec
        self.catalog = project.catalog
        self.graph: RobotGraph = project.graph
        self.result = result
        self.pins: dict[str, PinInfo] = {}
        self._index_pins()
        self.power_domains = self._domains(POWER_LINKS)
        self.signal_domains = self._domains(SIGNAL_LINKS)

    # ------------------------------------------------------------ lookup tables
    def _index_pins(self) -> None:
        passthrough_seen = False
        for cid, comp in self.spec.components.items():
            if comp.type not in self.catalog.components:
                continue
            ctype = self.catalog.component(comp.type)
            exposure: dict[str, list[str]] = defaultdict(list)
            connectors_of: dict[str, set[str]] = defaultdict(set)
            for cname, inst in ctype.connectors.items():
                for number, pin in inst.pins.items():
                    exposure[pin].append(f"{cid}.{cname}.{number}")
                    connectors_of[pin].add(cname)
            for pname, pin in ctype.pins.items():
                explicit = getattr(pin, "passthrough", None)
                if explicit is None:
                    # A board signal routed between two of its own connectors is a trace;
                    # its role names the signal, it does not drive or sink it.
                    passthrough = ctype.kind == "pcb" and pin.role in SIGNAL_ROLES and len(connectors_of[pname]) >= 2
                    passthrough_seen |= passthrough
                else:
                    passthrough = bool(explicit)
                self.pins[f"C:{cid}.{pname}"] = PinInfo(f"C:{cid}.{pname}", cid, pname, pin.role, pin,
                                                        exposure.get(pname, []), passthrough)
        if passthrough_seen:
            self.result.assume("electrical", "PCB signal pins exposed on two or more of the board's connectors are treated as "
                                             "pass-through traces (neither driver nor receiver) in pin-role checks.")

    def _domains(self, link_kinds: set[str]) -> _UnionFind:
        finder = _UnionFind()
        for node in self.graph.nodes:
            finder.add(node)
        for edge in self.graph.edges:
            if edge.kind != "internal" or edge.data.get("link_kind") in link_kinds:
                finder.union(edge.a, edge.b)
        return finder

    def _groups(self, finder: _UnionFind) -> dict[str, list[PinInfo]]:
        groups: dict[str, list[PinInfo]] = defaultdict(list)
        for node, info in sorted(self.pins.items()):
            groups[finder.find(node)].append(info)
        return groups

    def net_members(self, node: str, through_internal: bool = False) -> list[str]:
        finder = self.graph._reach if through_internal else self.graph._nets
        root = finder.find(node)
        return [n.removeprefix("C:") for n in sorted(self.graph.nodes) if n.startswith("C:") and finder.find(n) == root]

    def add(self, diagnostic: Diagnostic) -> None:
        self.result.add(diagnostic)

    # ------------------------------------------------------------ connectors
    def check_connectors(self) -> None:
        for diagnostic in self.graph.diagnostics:
            self.add(diagnostic.model_copy(deep=True))
        mated_by: dict[str, list[str]] = defaultdict(list)
        for harness in self.spec.electrical.harnesses:
            for name, hconn in harness.connectors.items():
                if hconn.mates:
                    mated_by[hconn.mates].append(f"{harness.id}.{name}")
                    mated_by[f"{harness.id}.{name}"].append(hconn.mates)
        for mate in self.spec.electrical.direct_mates:
            mated_by[mate.a].append(mate.b)
            mated_by[mate.b].append(mate.a)
        for ref, partners in sorted(mated_by.items()):
            if len(set(partners)) > 1:
                self.add(Diagnostic.error(
                    "connector_multiply_mated", f"{ref} is mated with {len(set(partners))} connectors: {', '.join(sorted(set(partners)))}",
                    subjects=[ref, *sorted(set(partners))], data={"mates": len(set(partners))},
                    hint="A physical connector accepts one mating half; split the harness with a splice instead."))
        for harness in self.spec.electrical.harnesses:
            for name, hconn in harness.connectors.items():
                ref = f"{harness.id}.{name}"
                if ref not in mated_by and hconn.position is None:
                    self.add(Diagnostic.warning(
                        "harness_connector_unmated", f"{ref} ({hconn.type}) is not mated and has no free-end position",
                        subjects=[ref], hint="Set `mates:` to the connector it plugs into, or give a `position:` for a loose end."))
            for wire in harness.wires:
                for end in (wire.from_, wire.to):
                    connector = end.partition(".")[0]
                    hconn = harness.connectors.get(connector)
                    if hconn is None or hconn.type not in self.catalog.connectors:
                        continue
                    ctype = self.catalog.connector(hconn.type)
                    if not ctype.awg_min <= wire.awg <= ctype.awg_max:
                        self.add(Diagnostic.error(
                            "wire_gauge_connector_mismatch",
                            f"wire {wire.id} is AWG {wire.awg} but {harness.id}.{connector} ({hconn.type}) accepts AWG {ctype.awg_min}-{ctype.awg_max}",
                            subjects=[wire.id, f"{harness.id}.{connector}"],
                            data={"awg": wire.awg, "awg_min": ctype.awg_min, "awg_max": ctype.awg_max},
                            hint="Change the wire gauge or pick a connector whose contacts accept it."))

    # ------------------------------------------------------------ bindings
    def _resolve(self, ref: str) -> str | None:
        try:
            return self.graph._pin_node(ref)
        except KeyError:
            return None

    def check_bindings(self) -> None:
        users: dict[str, list[str]] = defaultdict(list)
        for name, binding in self.spec.software.bindings.items():
            nodes: list[str | None] = []
            for ref in binding.pins:
                node = self._resolve(ref)
                nodes.append(node)
                if node is None:
                    self.add(Diagnostic.error("binding_pin_unresolved", f"binding {name}: pin {ref!r} does not exist",
                                              subjects=[name, ref], hint="Use <component>.<connector>.<pin> of the computer header."))
                    continue
                users[node].append(name)
                if not node.startswith(f"C:{binding.computer}."):
                    self.add(Diagnostic.error("binding_pin_not_on_computer",
                                              f"binding {name}: pin {ref} is not a pin of computer {binding.computer}",
                                              subjects=[name, ref]))
            self._check_reach(name, binding, nodes)
            self._check_functions(name, binding, nodes)
        for node, names in sorted(users.items()):
            if len(names) > 1:
                self.add(Diagnostic.error("binding_pin_conflict", f"{node[2:]} is claimed by bindings {', '.join(names)}",
                                          subjects=[node[2:], *names], data={"bindings": len(names)}))

    def _check_reach(self, name: str, binding: Any, nodes: list[str | None]) -> None:
        expects = list(binding.expects)
        if not expects:
            return
        if len(expects) == 1 and len(nodes) > 1:
            expects = expects * len(nodes)
        if len(expects) != len(nodes):
            self.add(Diagnostic.error("binding_expects_mismatch",
                                      f"binding {name}: {len(binding.pins)} pins but {len(binding.expects)} expected targets",
                                      subjects=[name], data={"pins": len(binding.pins), "expects": len(binding.expects)}))
            return
        for ref, node, target in zip(binding.pins, nodes, expects):
            target_node = self._resolve(target)
            if node is None:
                continue
            if target_node is None:
                self.add(Diagnostic.error("binding_pin_unresolved", f"binding {name}: expected pin {target!r} does not exist",
                                          subjects=[name, target]))
                continue
            if not self.graph.connected(node, target_node, through_internal=True):
                actual = self.net_members(node, through_internal=True)
                self.add(Diagnostic.error(
                    "binding_not_connected",
                    f"binding {name}: {ref} ({node[2:]}) does not reach {target} ({target_node[2:]}); it reaches {', '.join(actual) or 'nothing'}",
                    subjects=[name, ref, target],
                    data={"pin": node[2:], "expected": target_node[2:], "net_members": actual,
                          "expected_net_members": self.net_members(target_node, through_internal=True)},
                    hint="Fix the harness wire or connector mapping, or point the binding at the pin that is actually wired."))

    def _computer_params(self, computer: str) -> dict[str, Any]:
        comp = self.spec.components.get(computer)
        if comp is None or comp.type not in self.catalog.components:
            return {}
        params: dict[str, Any] = {}
        for interface in self.catalog.component(comp.type).software:
            params.update(interface.params)
        return params

    def _check_functions(self, name: str, binding: Any, nodes: list[str | None]) -> None:
        infos = [self.pins.get(node) if node else None for node in nodes]
        if any(info is None for info in infos):
            return
        params = self._computer_params(binding.computer)
        interface = binding.interface

        def missing(info: PinInfo, wanted: str) -> None:
            self.add(Diagnostic.error(
                "binding_pin_function_missing",
                f"binding {name}: {info.node[2:]} has no {wanted} function (has: {', '.join(info.spec.functions) or 'none'})",
                subjects=[name, info.node[2:]], data={"interface": interface, "wanted": wanted, "functions": list(info.spec.functions)},
                hint=f"Move the binding to a pin that provides {wanted}, and rewire the harness to match."))

        if interface == "pwm":
            for info in infos:
                match = next((m for f in info.spec.functions if (m := re.fullmatch(r"pwm(\d+)_ch(\d+)", f))), None)
                if match is None:
                    missing(info, "pwmX_chY")
                elif binding.channel is not None and int(match.group(2)) != binding.channel:
                    self.add(Diagnostic.error(
                        "binding_pwm_channel_mismatch",
                        f"binding {name}: {info.node[2:]} is PWM{match.group(1)} channel {match.group(2)}, binding says channel {binding.channel}",
                        subjects=[name, info.node[2:]], data={"binding_channel": binding.channel, "pin_channel": int(match.group(2))},
                        hint=f"Set channel: {match.group(2)}."))
            chip = params.get("pwm_chip")
            if chip and binding.device and binding.device != chip:
                self._device_mismatch(name, binding.device, chip)
        elif interface in ("uart", "i2c"):
            kinds = ("tx", "rx") if interface == "uart" else ("sda", "scl")
            prefix = "uart" if interface == "uart" else "i2c"
            if len(infos) != 2:
                self.add(Diagnostic.error("binding_expects_mismatch", f"binding {name}: {interface} needs exactly 2 pins ({'/'.join(kinds)})",
                                          subjects=[name], data={"pins": len(infos)}))
                return
            instances = []
            for info, kind in zip(infos, kinds):
                match = next((m for f in info.spec.functions if (m := re.fullmatch(rf"{prefix}(\d+)_{kind}", f))), None)
                if match is None:
                    missing(info, f"{prefix}N_{kind}")
                else:
                    instances.append(match.group(1))
            if len(set(instances)) > 1:
                self.add(Diagnostic.error("binding_pin_function_missing",
                                          f"binding {name}: pins belong to different {prefix} controllers ({', '.join(prefix + i for i in instances)})",
                                          subjects=[name], data={"interface": interface, "controllers": instances}))
            device_map = params.get(interface, {})
            if binding.device and isinstance(device_map, dict) and device_map:
                expected = device_map.get(binding.device)
                names = [info.name for info in infos]
                if expected is None:
                    self._device_mismatch(name, binding.device, ", ".join(sorted(device_map)))
                elif list(expected) != names:
                    owner = next((dev for dev, pins in device_map.items() if list(pins) == names), None)
                    self._device_mismatch(name, binding.device, owner or f"pins {expected}")
        elif interface == "gpio":
            for info in infos:
                line = re.fullmatch(r"GPIO(\d+)", info.name)
                if line is None and "gpio" not in info.spec.functions:
                    missing(info, "gpio")
                elif line is not None and binding.channel is not None and int(line.group(1)) != binding.channel:
                    self.add(Diagnostic.error(
                        "binding_gpio_line_mismatch", f"binding {name}: {info.node[2:]} is line {line.group(1)}, binding says {binding.channel}",
                        subjects=[name, info.node[2:]], data={"binding_channel": binding.channel, "pin_line": int(line.group(1))},
                        hint=f"Set channel: {line.group(1)}."))
        elif interface == "spi":
            for info in infos:
                if not any(re.fullmatch(r"spi\d+_(mosi|miso|sck|ce\d+|cs\d*)", f) for f in info.spec.functions):
                    missing(info, "spiN_*")

    def _device_mismatch(self, name: str, device: str, expected: str) -> None:
        self.add(Diagnostic.error("binding_device_mismatch", f"binding {name}: device {device} does not match the pins (expected {expected})",
                                  subjects=[name], data={"device": device, "expected": expected},
                                  hint="Use the Linux device that the selected pins are muxed to."))

    # ------------------------------------------------------------ pin roles
    def check_roles(self) -> None:
        for root, members in self._groups(self.power_domains).items():
            power_out = [p for p in members if p.role == "power_out"]
            grounds = [p for p in members if p.role == "ground"]
            supplies = [p for p in members if p.role in ("power_out", "power_in")]
            if len(power_out) > 1:
                self.add(Diagnostic.error(
                    "power_conflict", f"{len(power_out)} power outputs drive one rail: {', '.join(p.node[2:] for p in power_out)}",
                    subjects=[p.node[2:] for p in power_out],
                    data={"outputs": len(power_out), "voltages": [p.spec.voltage_nominal for p in power_out]},
                    hint="Only one source may drive a rail; isolate one (cut the red wire of a servo lead, add an ideal diode)."))
            if grounds and supplies:
                self.add(Diagnostic.error(
                    "power_ground_short", f"supply pins share a net with ground: {', '.join(p.node[2:] for p in supplies + grounds)}",
                    subjects=[p.node[2:] for p in supplies + grounds], data={"supply_pins": len(supplies), "ground_pins": len(grounds)}))
        for root, members in self._groups(self.graph._nets).items():
            active = [p for p in members if not p.passthrough]
            tx = [p for p in active if p.role == "uart_tx"]
            if len(tx) > 1:
                self.add(Diagnostic.error(
                    "uart_tx_tx_conflict", f"UART transmitters connected together: {', '.join(p.node[2:] for p in tx)}",
                    subjects=[p.node[2:] for p in tx], data={"transmitters": len(tx), "net_members": [p.node[2:] for p in members]},
                    hint="TX must cross to RX: swap the TX/RX wires in the harness."))
            pwm = [p for p in active if p.role == "pwm_out"]
            if len(pwm) > 1:
                self.add(Diagnostic.error("pwm_output_conflict", f"PWM outputs connected together: {', '.join(p.node[2:] for p in pwm)}",
                                          subjects=[p.node[2:] for p in pwm], data={"outputs": len(pwm)}))
        unused: list[str] = []
        for info in self.pins.values():
            if info.passthrough or info.role not in INPUT_DRIVERS or not info.exposed:
                continue
            finder = self.power_domains if info.role == "power_in" else self.signal_domains
            root = finder.find(info.node)
            drivers = [p for p in self.pins.values() if p.role in INPUT_DRIVERS[info.role] and not p.passthrough
                       and p.node != info.node and finder.find(p.node) == root]
            if not drivers and self._unused_computer_input(info):
                unused.append(info.node[2:])
                continue
            if not drivers:
                self.add(Diagnostic.error(
                    "input_without_driver", f"{info.node[2:]} ({info.role}) has no {'/'.join(sorted(INPUT_DRIVERS[info.role]))} on its net",
                    subjects=[info.node[2:]],
                    data={"role": info.role, "net_members": self.net_members(info.node, through_internal=True)},
                    hint="Connect the pin to a source, or check for swapped wires."))
        if unused:
            self.add(Diagnostic.info(
                "computer_inputs_unused", f"{len(unused)} unconnected, unbound computer input pins (spare header pins)",
                subjects=unused, data={"pins": len(unused)}))

    def _unused_computer_input(self, info: PinInfo) -> bool:
        """A spare signal input on a computer header: nothing else on its net and no software binding
        uses it. Its pin muxing leaves it an unused input (no driver needed); a bound or wired one is checked."""
        comp = self.spec.components.get(info.component)
        ctype = self.catalog.components.get(comp.type) if comp is not None else None
        if ctype is None or ctype.kind != "computer" or info.role == "power_in":
            return False
        if any(m != info.node[2:] for m in self.net_members(info.node, through_internal=True)):
            return False
        bound = {self._resolve(ref) for b in self.spec.software.bindings.values() for ref in b.pins}
        return info.node not in bound

    # ------------------------------------------------------------ voltages
    def check_voltages(self) -> list[dict[str, Any]]:
        rails: list[dict[str, Any]] = []
        for root, members in self._groups(self.power_domains).items():
            sources = [p for p in members if p.role == "power_out" and p.spec.voltage_nominal is not None]
            if len(sources) != 1:
                continue
            source = sources[0].spec
            v_nom = source.voltage_nominal
            v_low = source.voltage_min if source.voltage_min is not None else v_nom
            v_high = source.voltage_max if source.voltage_max is not None else v_nom
            sinks = [p for p in members if p.role == "power_in"]
            rails.append({"source": sources[0].node[2:], "nominal": v_nom, "min": v_low, "max": v_high,
                          "sinks": [p.node[2:] for p in sinks]})
            for sink in sinks:
                pin = sink.spec
                lo = pin.voltage_min if pin.voltage_min is not None else -np.inf
                hi = pin.voltage_max if pin.voltage_max is not None else np.inf
                subjects = [sink.node[2:], sources[0].node[2:]]
                data = {"rail_nominal": v_nom, "rail_min": v_low, "rail_max": v_high,
                        "pin_min": pin.voltage_min, "pin_max": pin.voltage_max}
                if not lo <= v_nom <= hi:
                    self.add(Diagnostic.error(
                        "power_in_voltage_out_of_range",
                        f"{sink.node[2:]} accepts {pin.voltage_min}-{pin.voltage_max} V but its rail ({sources[0].node[2:]}) is {v_nom} V nominal",
                        subjects=subjects, data=data, hint="Feed the pin from a regulator matching its input range."))
                    continue
                if v_high > hi:
                    self.add(Diagnostic.error(
                        "power_in_overvoltage", f"{sink.node[2:]} absolute max {pin.voltage_max} V is exceeded by its rail maximum {v_high} V",
                        subjects=subjects, data=data))
                if v_low < lo:
                    self.add(Diagnostic.warning(
                        "power_in_undervoltage", f"{sink.node[2:]} needs {pin.voltage_min} V but its rail can fall to {v_low} V",
                        subjects=subjects, data=data, hint="Expect a brownout near the end of discharge; the electrical stage quantifies it under load."))
        for root, members in self._groups(self.signal_domains).items():
            active = [p for p in members if not p.passthrough and p.spec.logic_level is not None]
            drivers = [p for p in active if p.role in OUTPUT_ROLES]
            receivers = [p for p in active if p.role in INPUT_ROLES]
            for driver, receiver in itertools.product(drivers, receivers):
                if driver.node == receiver.node or driver.component == receiver.component:
                    continue
                level, pin = driver.spec.logic_level, receiver.spec
                if pin.voltage_max is not None:
                    limit, basis = pin.voltage_max, "absolute maximum"
                elif pin.tolerant_5v:
                    limit, basis = TOLERANT_5V_MAX, "5 V tolerant"
                else:
                    limit, basis = pin.logic_level + CMOS_ABS_MAX_MARGIN, "VDD + 0.3 V (CMOS estimate)"
                data = {"driver_level": level, "receiver_limit": limit, "receiver_logic_level": pin.logic_level}
                if level > limit + 1e-9:
                    self.add(Diagnostic.error(
                        "logic_level_overvoltage", f"{driver.node[2:]} drives {level} V into {receiver.node[2:]} ({basis} {limit:.2f} V)",
                        subjects=[driver.node[2:], receiver.node[2:]], data=data,
                        hint="Add a level shifter or a resistor divider, or use a 3.3 V device."))
                elif receiver.role in ("uart_rx", "pwm_in") and level < CMOS_VIH_FRACTION * pin.logic_level:
                    self.add(Diagnostic.warning(
                        "logic_level_marginal", f"{driver.node[2:]} high level {level} V is below 70 % of {receiver.node[2:]} logic level {pin.logic_level} V",
                        subjects=[driver.node[2:], receiver.node[2:]], data=data))
        return rails

    # ------------------------------------------------------------ buses
    def check_i2c(self) -> list[dict[str, Any]]:
        buses: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
        for cid, comp in self.spec.components.items():
            if comp.type not in self.catalog.components:
                continue
            ctype = self.catalog.component(comp.type)
            sda = [name for name, pin in ctype.pins.items() if pin.role == "i2c_sda"]
            for interface in ctype.software:
                if interface.bus != "i2c" or interface.address is None:
                    continue
                if not sda:
                    continue
                bus = self.signal_domains.find(f"C:{cid}.{sda[0]}")
                buses[bus].append((cid, interface.address, interface.protocol))
        summary = []
        for bus, devices in buses.items():
            counts = Counter(address for _, address, _ in devices)
            bus_name = self.net_members(bus)[:1]
            summary.append({"bus": bus[2:], "devices": [{"component": c, "address": a, "protocol": p} for c, a, p in devices]})
            for address, count in counts.items():
                if count > 1:
                    clash = [f"{c}:{p}" for c, a, p in devices if a == address]
                    self.add(Diagnostic.error(
                        "i2c_address_conflict", f"I2C address 0x{address:02x} is used by {count} devices on bus {bus_name[0] if bus_name else bus}: {', '.join(clash)}",
                        subjects=sorted({c for c, a, _ in devices if a == address}), data={"address": address, "devices": count},
                        hint="Strap one device to an alternate address or move it to a second I2C bus."))
        return summary

    def check_mount_estimates(self) -> None:
        """Owned parts with estimated mounting holes still require measurement."""
        for cid, comp in self.spec.components.items():
            if not comp.owned or comp.type not in self.catalog.components:
                continue
            mechanical = self.catalog.component(comp.type).mechanical
            holes = [f for f in mechanical.features if f.kind == "clearance_hole"
                     and f.provenance.status == "estimated"]
            if mechanical.fabricated or not holes:
                continue
            self.add(Diagnostic.warning(
                "mount_geometry_estimated",
                f"{cid}: {len(holes)} mounting holes use estimated geometry; measure the owned part before releasing its mount",
                subjects=[cid], data={"holes": [f.model_dump(mode="json") for f in holes]},
                hint="Confirm the hole centres and diameters, then update their catalog provenance."))

    # ------------------------------------------------------------ flat cables
    def _connector_point(self, ref: str) -> np.ndarray | None:
        cid, _, name = ref.partition(".")
        comp = self.spec.components.get(cid)
        ctype = self.catalog.components.get(comp.type) if comp is not None else None
        inst = ctype.connectors.get(name) if ctype is not None else None
        if inst is None:
            return None
        position, rotation = self.graph.component_pose(cid)
        return position + rotation @ np.asarray([to_si(v) for v in inst.position], dtype=float)

    def check_cable_reach(self) -> None:
        """A `cable` component (flat CSI cable) must span the sockets its two ends mate: the straight distance
        between them may not exceed its length (``behavior.params.length``); less than 15 % slack is a warning
        (a flat cable needs room for its bends)."""
        mates: dict[str, str] = {}
        for mate in self.spec.electrical.direct_mates:
            mates[mate.a], mates[mate.b] = mate.b, mate.a
        for cid, comp in self.spec.components.items():
            ctype = self.catalog.components.get(comp.type)
            if ctype is None or ctype.kind != "cable" or ctype.behavior is None:
                continue
            length = ctype.behavior.params.get("length")
            ends = [mates.get(f"{cid}.{name}") for name in ctype.connectors]
            if length is None or len(ends) != 2 or None in ends:
                continue
            points = [self._connector_point(ref) for ref in ends]
            if any(p is None for p in points):
                continue
            span, limit = float(np.linalg.norm(points[0] - points[1])), to_si(length)
            data = {"span": span, "length": limit, "slack_fraction": (limit - span) / limit}
            if any(c.flexible_end for c in ctype.connectors.values()):
                self.add(Diagnostic.warning(
                    "flat_cable_route_estimated", f"{cid}: flexible ends are seated, but socket separation alone "
                    "does not verify the ribbon's installed bends, restraints or swept clearance",
                    subjects=[cid, *ends], data=data,
                    hint="Validate the installed ribbon route and its supports before fabrication release."))
            if span > limit:
                self.add(Diagnostic.error("cable_too_short", f"{cid} ({limit * 1e3:.0f} mm) cannot reach from {ends[0]} to {ends[1]} "
                                                             f"({span * 1e3:.0f} mm straight)", subjects=[cid, *ends], data=data,
                                          hint="Use a longer cable (e.g. the 300/500 mm variant) or move the computer closer."))
            elif span > 0.85 * limit:
                self.add(Diagnostic.warning("cable_slack_low", f"{cid}: only {100 * (limit - span) / limit:.0f} % slack between "
                                                               f"{ends[0]} and {ends[1]}", subjects=[cid, *ends], data=data))

    # ------------------------------------------------------------ mechanics
    def solids(self) -> dict[str, Solid]:
        result: dict[str, Solid] = {}
        for cid, comp in self.spec.components.items():
            if comp.type not in self.catalog.components:
                continue
            mech = self.catalog.component(comp.type).mechanical
            outline = comp.params.get("outline") if mech.shape == "custom" else None
            if outline:
                # Plates authored as an outline polygon: extrude it (convex hull) instead of using the bounding box.
                result[cid] = outline_solid(cid, [[to_si(a), to_si(b)] for a, b in outline], float(mech.size[2]),
                                            comp.pose.xyz, comp.pose.rpy)
            elif mech.shape == "custom" and mech.solids and (piecewise := solids_solid(
                    cid, mech.solids, comp.params, comp.pose.xyz, comp.pose.rpy, comp.params.get("mirror"))) is not None:
                result[cid] = piecewise  # union of the catalog primitives, not the bounding box
            else:
                result[cid] = make_solid(cid, mech.shape, list(mech.size), mech.axis, comp.pose.xyz, comp.pose.rpy, mech.envelope_offset)
        return result

    def check_interference(self, solids: dict[str, Solid]) -> list[dict[str, Any]]:
        # Exact B-rep interference from the CAD stage supersedes envelope estimates,
        # but only when it was computed for this exact component set. The direct-CAD
        # stage records it in its ``fit`` report (allowed thread/press-fit
        # intersections, every other pair checked); older builds wrote ``contacts``.
        from .release import is_stale

        fingerprint = self.project.fingerprint()
        cad = self.project.read_build_json("cad/cad_model.json") or {}
        checked = isinstance(cad, dict) and ("fit" in cad or "contacts" in cad)
        if checked and not is_stale(cad.get("inputs"), fingerprint) and set(cad.get("parts", {})) >= set(self.spec.components):
            fit = cad.get("fit") or {}
            self.add(Diagnostic.info("interference_checked_by_cad",
                                     "static interference evaluated on exact Onshape B-rep by the cad stage; envelope check skipped",
                                     data={"document": cad.get("source", {}).get("url"), "parts": len(cad.get("parts", {})),
                                           "verified_fasteners": len(fit.get("verified_fasteners", [])),
                                           "verified_interfaces": len(fit.get("verified_interfaces", []))}))
            return []
        assembly = self.project.read_build_json("model/assembly.json") or {}
        current = not is_stale(assembly.get("inputs"), fingerprint)
        if (assembly.get("interference_method") == "exact" and current
                and set(assembly.get("poses", {})) >= set(self.spec.components)):
            # The assembly stage intersected the exact part plans (solids, datasheet features, interface-cut
            # holes and bores, fasteners) at the compiled poses; its object_interference errors are the result.
            self.add(Diagnostic.info("interference_checked_by_assembly",
                                     "static interference evaluated on the exact part plans by the assembly stage; envelope check skipped",
                                     data={"pairs_in_contact": len(assembly.get("interference", []))}))
            return []
        self.result.assume("mechanical", "Interference uses catalog envelopes: boxes exact, cylinders as 48-gon prisms, 'custom' "
                                         "shapes as the union of their catalog solids (concave profiles as their hull). Overlaps thinner than 0.1 mm count as contact.")
        self.result.assume("mechanical", "Static interference at the authored pose only; steering and wheel sweep are checked in simulation.")
        overlaps = []
        for a, b in itertools.combinations(sorted(solids), 2):
            hit = overlap(solids[a], solids[b])
            if hit is None:
                continue
            mount_related = self.spec.components[a].mount == b or self.spec.components[b].mount == a
            overlaps.append({"a": a, "b": b, **hit, "mount_related": mount_related})
            extent_mm = " x ".join(f"{v * 1e3:.1f}" for v in hit["extent"])
            self.add(Diagnostic.error(
                "component_interference",
                f"{a} and {b} overlap by {hit['volume'] * 1e9:.0f} mm^3 (region {extent_mm} mm at "
                f"[{', '.join(f'{v * 1e3:.1f}' for v in hit['center'])}] mm)",
                subjects=[a, b],
                data={"overlap_volume": hit["volume"], "depth": hit["depth"], "extent": hit["extent"], "center": hit["center"],
                      "mount_related": mount_related},
                hint="Move one component (pose.xyz) by at least `depth` along the thinnest overlap axis, or resize the envelope."))
        return overlaps

    def check_mass(self) -> dict[str, Any]:
        cad = self.project.read_build_json("cad/cad_model.json") or {}
        cad_parts = cad.get("parts", {}) if isinstance(cad, dict) else {}
        routes = self.project.read_build_json("wiring/routes.json") or {}
        routed = routes.get("wires", {}) if isinstance(routes, dict) else {}
        components: dict[str, dict[str, Any]] = {}
        for cid, comp in self.spec.components.items():
            if comp.type not in self.catalog.components:
                continue
            mech = self.catalog.component(comp.type).mechanical
            if cid in cad_parts and cad_parts[cid].get("mass"):
                components[cid] = {"mass": float(cad_parts[cid]["mass"]), "source": "cad"}
            elif "mass" in comp.params:
                from r3_model.units import to_si

                components[cid] = {"mass": to_si(comp.params["mass"]), "source": "spec_override"}
            else:
                components[cid] = {"mass": mech.mass, "source": mech.provenance.status}
        wires: dict[str, dict[str, Any]] = {}
        estimated_wires = 0
        for harness in self.spec.electrical.harnesses:
            for wire in harness.wires:
                if wire.id in routed and routed[wire.id].get("cut_length"):
                    length, source = float(routed[wire.id]["cut_length"]), "routed"
                elif wire.length is not None:
                    length, source = wire.length, "spec"
                else:
                    length, source = self._wire_length_estimate(harness, wire), "estimated"
                    estimated_wires += 1
                per_m = self.catalog.wires[wire.awg].mass_per_m if wire.awg in self.catalog.wires else 0.0
                wires[wire.id] = {"length": length, "mass": length * per_m, "source": source}
        if estimated_wires:
            self.result.assume("mechanical", f"{estimated_wires} wire lengths are straight-line estimates x {ROUTE_FACTOR_ESTIMATE} + slack "
                                             "(no build/wiring/routes.json); connector masses are not counted.")
        if not any(name not in self.spec.components for name in cad_parts):
            self.result.assume("mechanical", "Fastener masses are not counted (no CAD fasteners in build/cad/cad_model.json).")
        # CAD-only parts (fasteners, nuts) carry real mass once the cad stage has modeled them.
        hardware_mass = sum(float(part.get("mass") or 0.0) for name, part in cad_parts.items()
                            if name not in self.spec.components and isinstance(part, dict))
        component_mass = sum(c["mass"] for c in components.values())
        wire_mass = sum(w["mass"] for w in wires.values())
        total = component_mass + wire_mass + hardware_mass
        limit = self.spec.requirements.max_mass
        estimated_mass = sum(c["mass"] for c in components.values() if c["source"] == "estimated")
        summary = {"total": total, "components": component_mass, "wires": wire_mass, "hardware": hardware_mass, "limit": limit,
                   "estimated_fraction": estimated_mass / total if total else 0.0,
                   "by_component": components, "by_wire": wires}
        if limit is not None and total > limit:
            heaviest = sorted(components, key=lambda c: -components[c]["mass"])[:3]
            self.add(Diagnostic.error(
                "mass_exceeds_requirement", f"total mass {total:.3f} kg exceeds requirements.max_mass {limit:.3f} kg",
                subjects=heaviest, data={"mass": total, "limit": limit, "excess": total - limit},
                hint="Heaviest components first: " + ", ".join(f"{c} {components[c]['mass']:.3f} kg" for c in heaviest)))
        return summary

    def _connector_position(self, ref: str, seen: set[str]) -> np.ndarray | None:
        if ref in seen:
            return None
        seen.add(ref)
        if ref in self.graph.endpoints:
            return self.graph.endpoints[ref].position
        owner, _, name = ref.partition(".")
        for harness in self.spec.electrical.harnesses:
            if harness.id == owner and name in harness.connectors:
                hconn = harness.connectors[name]
                if hconn.position is not None:
                    return np.asarray(hconn.position)
                if hconn.mates:
                    return self._connector_position(hconn.mates, seen)
            for other, oconn in harness.connectors.items():
                if oconn.mates == ref:
                    found = self._connector_position(f"{harness.id}.{other}", seen)
                    if found is not None:
                        return found
        return None

    def _far_ends(self, ref: str) -> list[np.ndarray]:
        """Known positions at the far end of every wire attached to harness connector ``ref``."""
        owner, _, name = ref.partition(".")
        harness = next((h for h in self.spec.electrical.harnesses if h.id == owner), None)
        if harness is None:
            return []
        points = []
        for wire in harness.wires:
            for near, far in ((wire.from_, wire.to), (wire.to, wire.from_)):
                if near.partition(".")[0] != name:
                    continue
                if far in harness.splices:
                    points.append(np.asarray(harness.splices[far].position))
                else:
                    found = self._connector_position(f"{owner}.{far.partition('.')[0]}", set())
                    if found is not None:
                        points.append(found)
        return points

    def _free_pair_estimate(self, ref: str) -> np.ndarray | None:
        """An in-line connector pair with no authored position (e.g. XT60 battery plug) is placed at the
        centroid of the known far ends of both mating halves (estimate)."""
        partners = [ref]
        owner, _, name = ref.partition(".")
        for harness in self.spec.electrical.harnesses:
            for other, hconn in harness.connectors.items():
                if hconn.mates == ref:
                    partners.append(f"{harness.id}.{other}")
                if harness.id == owner and other == name and hconn.mates:
                    partners.append(hconn.mates)
        points = [p for partner in partners for p in self._far_ends(partner)]
        return np.mean(points, axis=0) if points else None

    def wire_ends(self, harness: Any, wire: Any) -> tuple[list[np.ndarray | None], bool]:
        """Robot-frame positions of a wire's two ends (connector or splice) and whether any is estimated."""
        ends: list[np.ndarray | None] = []
        estimated = False
        for end in (wire.from_, wire.to):
            if end in harness.splices:
                ends.append(np.asarray(harness.splices[end].position))
                continue
            ref = f"{harness.id}.{end.partition('.')[0]}"
            position = self._connector_position(ref, set())
            if position is None:
                position = self._free_pair_estimate(ref)
                estimated |= position is not None
            ends.append(position)
        return ends, estimated

    def _wire_length_estimate(self, harness: Any, wire: Any) -> float:
        ends, _ = self.wire_ends(harness, wire)
        if any(e is None for e in ends):
            return DEFAULT_WIRE_LENGTH
        return float(np.linalg.norm(ends[0] - ends[1])) * ROUTE_FACTOR_ESTIMATE + harness.route.slack


def check_failsafe_chain(checker: ModelChecker) -> dict[str, Any]:
    """Failsafe timeouts, innermost to outermost, must be strictly increasing:

    1. hardware arbiter (board ``failsafe``: heartbeat ``timeout`` + one output ``frame``): hung software;
    2. software radio failsafe (``software.config.radio_failsafe_timeout_s``): link loss, active brake;
    3. ESC signal-loss timeout (``signal_timeout``): no pulses at all (computer or arbiter unpowered);
    4. receiver link-lost timeout (``failsafe_timeout``): the receiver declares failsafe.

    The receiver must stop sending frames when packets stop (``failsafe_mode: no_pulses``); a receiver that
    holds the last channels hides link loss from layer 2. The heartbeat period (one edge per control cycle) must
    leave room for the arbiter's re-arm edges within its timeout."""
    spec, catalog = checker.spec, checker.catalog
    layers: list[tuple[str, str, float]] = []
    config = spec.software.config
    rate = float(config.get("control_rate_hz", 0) or 0)
    report: dict[str, Any] = {"layers": [], "control_period": 1.0 / rate if rate else None}
    arbiter = esc = receiver = None
    for cid, comp in spec.components.items():
        ctype = catalog.components.get(comp.type)
        if ctype is None or ctype.behavior is None:
            continue
        params = {**ctype.behavior.params, **comp.params}
        failsafe = params.get("failsafe")
        if isinstance(failsafe, dict) and any(link.kind == "failsafe_arbiter" for link in ctype.internal_links):
            arbiter = (cid, failsafe)
            layers.append((cid, "arbiter neutral (heartbeat timeout + frame)",
                           float(to_si(failsafe.get("timeout", 0.1))) + float(to_si(failsafe.get("frame", 0.02)))))
        elif ctype.behavior.model == "esc.brushed" and params.get("signal_timeout") is not None:
            esc = cid
        elif ctype.behavior.model == "receiver.crsf":
            receiver = (cid, params)
    if config.get("radio_failsafe_timeout_s") is not None:
        layers.append(("software", "software radio failsafe (brake)", float(config["radio_failsafe_timeout_s"])))
    if esc is not None:
        esc_params = {**catalog.component(spec.components[esc].type).behavior.params, **spec.components[esc].params}
        layers.append((esc, "ESC signal-loss failsafe (coast)", float(to_si(esc_params["signal_timeout"]))))
    if receiver is not None and receiver[1].get("failsafe_timeout") is not None:
        layers.append((receiver[0], "receiver link lost", float(to_si(receiver[1]["failsafe_timeout"]))))
    report["layers"] = [{"component": c, "layer": name, "timeout": value} for c, name, value in layers]
    for (ca, na, va), (cb, nb, vb) in zip(layers, layers[1:]):
        if va >= vb:
            checker.add(Diagnostic.error(
                "failsafe_chain_order", f"{na} ({ca}, {va:g} s) is not faster than {nb} ({cb}, {vb:g} s)",
                subjects=[ca, cb], data={"inner": va, "outer": vb},
                hint="each failsafe layer must act before the next outer one"))
    if receiver is not None:
        mode = str(receiver[1].get("failsafe_mode", "unspecified"))
        report["receiver_failsafe_mode"] = mode
        if mode not in ("no_pulses", "unspecified"):
            checker.add(Diagnostic.error(
                "receiver_failsafe_not_no_pulses", f"{receiver[0]}: failsafe mode {mode!r}; the receiver must send no frames on "
                "link loss so the software sees the gap", subjects=[receiver[0]], data={},
                hint="set the receiver's failsafe to 'no pulses' (ELRS: failsafe mode 'No Pulses')"))
    if arbiter is not None and rate:
        failsafe = arbiter[1]
        timeout = float(to_si(failsafe.get("timeout", 0.1)))
        edges = int(failsafe.get("rearm_edges", 3))
        report["heartbeat_edges_per_timeout"] = timeout * rate
        if timeout * rate < 2 * edges:
            checker.add(Diagnostic.error(
                "heartbeat_too_slow", f"{arbiter[0]}: {rate:g} Hz heartbeat gives {timeout * rate:.1f} edges per {timeout:g} s "
                f"arbiter timeout; need >= {2 * edges} (re-arm takes {edges})", subjects=[arbiter[0], "software"],
                data={"edges_per_timeout": timeout * rate, "required": 2 * edges}))
    if layers:
        checker.add(Diagnostic.info(
            "failsafe_chain", "failsafe timeouts: " + " < ".join(f"{name} {value:g} s" for _, name, value in layers),
            subjects=["failsafe_chain"], data={name: value for _, name, value in layers}))
    return report


def _net_names(checker: ModelChecker) -> dict[str, str]:
    """Human net names: the most common harness signal name on the net, else a component pin."""
    signals: dict[str, Counter] = defaultdict(Counter)
    for harness in checker.spec.electrical.harnesses:
        for wire in harness.wires:
            if not wire.signal:
                continue
            for edge in checker.graph.edges:
                if edge.kind == "wire" and edge.ref == wire.id:
                    signals[checker.graph.net_of(edge.a)][wire.signal] += 1
    names = {}
    for root, members in checker.graph.nets().items():
        if signals[root]:
            names[root] = signals[root].most_common(1)[0][0]
        else:
            pins = [m for m in members if m.startswith("C:")]
            names[root] = pins[0][2:] if pins else root
    return names


def _component_geometry(checker: ModelChecker, solids: dict[str, Solid]) -> dict[str, Any]:
    out = {}
    for cid, comp in checker.spec.components.items():
        if comp.type not in checker.catalog.components:
            continue
        ctype = checker.catalog.component(comp.type)
        mech = ctype.mechanical
        lo, hi = solids[cid].aabb
        out[cid] = {"type": comp.type, "kind": ctype.kind, "name": ctype.name, "mount": comp.mount, "shape": mech.shape,
                    "size": list(mech.size), "axis": mech.axis, "xyz": list(comp.pose.xyz), "rpy": list(comp.pose.rpy),
                    "color": mech.color, "moving": mech.moving, "mass": mech.mass,
                    "aabb_min": lo.tolist(), "aabb_max": hi.tolist(), "volume": solids[cid].volume}
    return out


def _wires(checker: ModelChecker) -> dict[str, Any]:
    out = {}
    for harness in checker.spec.electrical.harnesses:
        for wire in harness.wires:
            ends, estimated = checker.wire_ends(harness, wire)
            out[wire.id] = {"harness": harness.id, "awg": wire.awg, "color": wire.color, "signal": wire.signal,
                            "from": wire.from_, "to": wire.to, "ends_estimated": estimated,
                            "ends": [e.tolist() if e is not None else None for e in ends]}
    return out


def _plot_layout(path: Any, geometry: dict[str, Any], overlaps: list[dict[str, Any]]) -> bool:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Polygon, Rectangle
    except ImportError:
        return False
    clashing = {o["a"] for o in overlaps} | {o["b"] for o in overlaps}
    fig, axes = plt.subplots(1, 2, figsize=(15, 6.5))
    views = (("Top view (x forward, y left)", 0, 1), ("Side view (x forward, z up)", 0, 2))
    order = sorted(geometry, key=lambda c: geometry[c]["aabb_min"][2])
    for ax, (title, i, j) in zip(axes, views):
        for cid in order:
            g = geometry[cid]
            lo, hi = g["aabb_min"], g["aabb_max"]
            bad = cid in clashing
            ax.add_patch(Rectangle((lo[i] * 1e3, lo[j] * 1e3), (hi[i] - lo[i]) * 1e3, (hi[j] - lo[j]) * 1e3,
                                   facecolor=g["color"], alpha=0.35, edgecolor="#d62728" if bad else "#333333",
                                   linewidth=1.8 if bad else 0.6))
            ax.annotate(cid, ((lo[i] + hi[i]) / 2 * 1e3, (lo[j] + hi[j]) / 2 * 1e3), fontsize=6, ha="center", va="center")
        for o in overlaps:
            c, e = o["center"], o["extent"]
            ax.add_patch(Rectangle(((c[i] - e[i] / 2) * 1e3, (c[j] - e[j] / 2) * 1e3), e[i] * 1e3, e[j] * 1e3,
                                   facecolor="#d62728", alpha=0.8, edgecolor="none"))
        ax.set_title(title)
        ax.set_xlabel(f"{'xyz'[i]} (mm)")
        ax.set_ylabel(f"{'xyz'[j]} (mm)")
        ax.set_aspect("equal")
        ax.autoscale_view()
        ax.grid(alpha=0.3)
    fig.suptitle(f"R3 model stage: component envelopes ({len(overlaps)} interference region(s) in red)")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return True


def run(project: Project) -> StageResult:
    start = time.time()
    result = StageResult(stage=STAGE, inputs=project.fingerprint())
    from r3_model.connections import compile_connections

    connections = compile_connections(project)
    result.extend(connections.diagnostics)
    result.metrics["connections"] = len(connections.connections)
    result.artifact("model/connections.json", "json", "Sourced assembly connection capacities and explicit estimates")
    project.build_dir("model").joinpath("connections.json").write_text(json.dumps(
        {"connections": [c.as_dict() for c in connections.connections], "estimated": connections.estimated}, indent=2))
    checker = ModelChecker(project, result)
    checker.check_connectors()
    checker.check_bindings()
    checker.check_roles()
    rails = checker.check_voltages()
    buses = checker.check_i2c()
    checker.check_mount_estimates()
    checker.check_cable_reach()
    solids = checker.solids()
    overlaps = checker.check_interference(solids)
    mass = checker.check_mass()
    if project.spec.source_slots():
        from .sourcing import check_pins

        result.extend(check_pins(project))
    failsafe_chain = check_failsafe_chain(checker)
    result.assume("electrical", "Rail voltages are the single power_out source's nominal/min/max; fuses, switches and shunts "
                                "are treated as zero-drop. Load-dependent drops are computed by the electrical stage.")

    geometry = _component_geometry(checker, solids)
    out_dir = project.build_dir(STAGE)
    graph_json = checker.graph.to_json()
    graph_json.update({
        "net_names": _net_names(checker),
        "components": geometry,
        "wires": _wires(checker),
        "rails": rails,
        "i2c_buses": buses,
        "interference": overlaps,
        "mass": mass,
        "failsafe_chain": failsafe_chain,
        "pins": {node[2:]: {"role": p.role, "passthrough": p.passthrough, "connector_pins": p.connector_pins}
                 for node, p in checker.pins.items()},
    })
    (out_dir / "graph.json").write_text(json.dumps(graph_json, indent=2, default=float))
    result.artifact("model/graph.json", "json", "Derived net graph, component envelopes, rails, buses and mass budget")
    if _plot_layout(out_dir / "layout.png", geometry, overlaps):
        result.artifact("model/layout.png", "plot_png", "Top and side view of component envelopes with interference")
    result.metrics.update({
        "components": len(geometry), "nets": len(graph_json["nets"]), "bindings": len(project.spec.software.bindings),
        "interferences": len(overlaps), "total_mass": mass["total"], "mass_limit": mass["limit"],
        "mass_margin": (mass["limit"] - mass["total"]) if mass["limit"] is not None else None,
    })
    result.duration_s = time.time() - start
    return result

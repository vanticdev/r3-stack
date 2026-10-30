"""``electrical`` pipeline stage: static worst-case operating points and checks.

Operating points (ngspice ``op`` solves of the full nonlinear network, see
:mod:`r3_sim.electrical.system`), each at three battery states (full, nominal,
minimum = SOC 0):

* ``idle``                 — throttle neutral, computer idle.
* ``cruise``               — steady speed (default 4 m/s); motor torque from rolling
  resistance + aero, F = C_rr m g + 1/2 rho C_d A v^2; throttle solved so the
  motor delivers that torque at w = v / r * ratio.
* ``full_throttle``        — throttle 1 at the traction-limited acceleration point:
  motor torque = mu m_driven g r / (ratio eta) (w solved), burst.
* ``motor_stall``          — throttle 1, w = 0, burst.
* ``servo_stall_compute``  — servos stalled (demand above stall torque, w = 0) and
  computers at 100 % while driving at cruise; treated as continuous.

Continuous points use steady conductor temperatures; burst points use ambient
(cold, highest current) conductors and a transient temperature check over the
burst duration. Diagnostics use stable codes; every number is in SI units.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
from scipy.optimize import brentq

from r3_model.diagnostics import Diagnostic, StageResult
from r3_model.graph import ACTIVE_SIGNAL_LINKS
from r3_model.units import to_si

from .models.battery import BatteryLipo
from .models.board import BoardPcb
from .models.esc import BrushedEsc
from .models.fuse import FuseBlade
from .models.loads import BuckRegulator, ComputerLoad, ServoPwm
from .models.motor import DcBrushedMotor
from .network import SILICONE_INSULATION_MAX_C, adiabatic_i2t_withstand
from .system import ElectricalSystem

STAGE = "electrical"
DEFAULT_CRUISE_SPEED = 1.0  # m/s
AIR_DENSITY = 1.204  # kg/m^3 at 20 C
DRAG_COEFFICIENT = 1.0  # conservative generic bluff-body estimate
FRONTAL_FILL = 1.0  # conservative projected bounding-envelope fill estimate
SERVO_STALL_DEMAND = 1.2  # x stall torque demanded (servo held against its stop)
CRUISE_ACTIVITY = 0.7
BURST_DURATION = {"full_throttle": 2.0, "motor_stall": 2.0}  # s (assumptions)
FUSE_DERATING = 0.75  # continuous load / rating (Littelfuse blade fuse application guidance)
DEFAULT_DUTY_CYCLE = {"cruise": 0.85, "full_throttle": 0.05, "idle": 0.10}
LOGIC_INPUT_RESISTANCE = 10e3  # ohm, receiver input / pull-down estimate for PWM inputs
DRIVER_OUTPUT_RESISTANCE = 40.0  # ohm, 3.3 V CMOS GPIO output impedance (estimate)
CONTINUOUS = ("idle", "cruise", "servo_stall_compute")
BURST = ("full_throttle", "motor_stall")


# ------------------------------------------------------------------ helpers
def _models(system: ElectricalSystem, kind: type) -> dict[str, Any]:
    return {cid: m for cid, m in system.models.items() if isinstance(m, kind)}


def _net_of_label(system: ElectricalSystem) -> dict[str, str]:
    mapping = {}
    for name, members in system.nets.items():
        for member in members:
            mapping[member] = name
    return mapping


def vehicle_model(project: Any, result: StageResult) -> dict[str, Any]:
    from ..vehicle.roles import wheel_joints
    from ..load_cases import is_mobile

    wheel_owners = wheel_joints(project)
    spec, catalog = project.spec, project.catalog
    motors = [cid for cid, c in spec.components.items()
              if (ct := catalog.components.get(c.type)) and ct.kind == "motor" and ct.behavior]
    transmissions = [t for t in spec.mechanical.transmissions if t.actuator in motors and t.type in ("direct", "differential")]
    ground_transmissions = [t for t in transmissions if any(j.id in t.joints for j in wheel_owners.values())]
    transmission = next(iter(ground_transmissions or transmissions), None)
    ratio = transmission.ratio if transmission else 1.0
    efficiency = transmission.efficiency if transmission else 1.0
    radius = None
    if transmission:
        for wheel, joint in wheel_owners.items():
            ctype = catalog.components.get(spec.components[wheel].type) if joint.id in transmission.joints else None
            params = {**(ctype.behavior.params if ctype and ctype.behavior else {}), **spec.components[wheel].params}
            if ctype and "radius" in params:
                radius = to_si(params["radius"])
                break
    if radius is None:
        radius = 0.05
        if ground_transmissions:
            result.assume("vehicle", "No wheel behavior radius found; using 0.05 m.")
    from r3_model.pulled_view import cad_view
    cad = cad_view(project, mass_required=True)
    cad_parts = cad["parts"]
    mass = sum(p["mass"] for p in cad_parts.values())
    mass_source = "pulled"
    lows = [np.asarray(p["bbox_min"]) for p in cad_parts.values()]
    highs = [np.asarray(p["bbox_max"]) for p in cad_parts.values()]
    extent = np.max(highs, axis=0) - np.min(lows, axis=0) if highs else np.zeros(3)
    frontal_area = FRONTAL_FILL * extent[1] * extent[2]
    wheels = {cid for cid, c in spec.components.items() if (ct := catalog.components.get(c.type)) and ct.kind == "wheel"}
    driven = {wheel for wheel, joint in wheel_owners.items() if any(joint.id in t.joints for t in ground_transmissions)}
    ground_drive = is_mobile(project) and bool(driven)
    if ground_drive:
        characteristics = {(float(t.ratio), float(t.efficiency)) for t in ground_transmissions}
        radii = set()
        for wheel in driven:
            behavior = catalog.component(spec.components[wheel].type).behavior
            params = {**(behavior.params if behavior else {}), **spec.components[wheel].params}
            if "radius" in params:
                radii.add(to_si(params["radius"]))
        if len(characteristics) > 1 or len(radii) > 1:
            result.add(Diagnostic.error("electrical_drive_layout_unsupported",
                "Steady road-load estimation requires equal driven radii, transmission ratios and efficiencies; heterogeneous drives need individual load allocation"))
    if not ground_drive and motors:
        result.assume("motor_load", "Non-wheeled operating points use component operating_speed and load_torque; unspecified values are estimated as zero.")
    loads = [{**catalog.component(spec.components[cid].type).behavior.params, **spec.components[cid].params} for cid in motors]
    env = spec.simulation.environment
    return {"mass": mass, "mass_source": mass_source, "catalog_component_mass": None, "wheel_radius": radius,
            "ratio": ratio, "drivetrain_efficiency": efficiency, "rolling_resistance": env.ground_rolling_resistance,
            "friction": env.ground_friction, "gravity": env.gravity, "frontal_area": frontal_area,
            "frontal_area_source": "pulled",
            "drag_coefficient": DRAG_COEFFICIENT, "air_density": AIR_DENSITY,
            "driven_axle_load_fraction": len(driven) / len(wheels) if wheels else 0.0,
            "ground_drive": ground_drive,
            "motor_operating_speed": sum(to_si(p.get("operating_speed", 0)) for p in loads) / max(len(loads), 1),
            "motor_load_torque": sum(to_si(p.get("load_torque", 0)) for p in loads) / max(len(loads), 1)}


def road_load(vehicle: dict[str, Any], speed: float) -> float:
    """F = C_rr m g + 1/2 rho C_d A v^2 (N)."""
    rolling = vehicle["rolling_resistance"] * vehicle["mass"] * vehicle["gravity"]
    aero = 0.5 * vehicle["air_density"] * vehicle["drag_coefficient"] * vehicle["frontal_area"] * speed ** 2
    return rolling + aero


class OperatingPoints:
    """Builds generic operating-point inputs and solves them."""

    def __init__(self, system: ElectricalSystem, vehicle: dict[str, Any], cruise_speed: float) -> None:
        self.system = system
        self.vehicle = vehicle
        self.cruise_speed = cruise_speed
        self.motors = _models(system, DcBrushedMotor)
        self.escs = _models(system, BrushedEsc)
        self.servos = _models(system, ServoPwm)
        self.computers = _models(system, ComputerLoad)
        graph = system.project.graph
        self.esc_for_motor: dict[str, str] = {}
        for mid, motor in self.motors.items():
            for eid, esc in self.escs.items():
                if graph.net_of(f"C:{eid}.{esc.m_a.name}") == graph.net_of(f"C:{mid}.{motor.pos.name}"):
                    self.esc_for_motor[mid] = eid
        if vehicle["ground_drive"]:
            self.motor_omega_cruise = cruise_speed / vehicle["wheel_radius"] * vehicle["ratio"]
            wheel_torque = road_load(vehicle, cruise_speed) * vehicle["wheel_radius"]
            self.motor_torque_cruise = wheel_torque / (vehicle["ratio"] * vehicle["drivetrain_efficiency"]) / max(len(self.motors), 1)
            traction = vehicle["friction"] * vehicle["driven_axle_load_fraction"] * vehicle["mass"] * vehicle["gravity"]
            self.motor_torque_traction = traction * vehicle["wheel_radius"] / (vehicle["ratio"] * vehicle["drivetrain_efficiency"]) / max(len(self.motors), 1)
        else:
            self.motor_omega_cruise = vehicle["motor_operating_speed"]
            self.motor_torque_cruise = vehicle["motor_load_torque"]
            self.motor_torque_traction = self.motor_torque_cruise

    def inputs(self, throttle: float, omega: float, servo: dict[str, float] | None, activity: float) -> dict[str, Any]:
        inputs: dict[str, Any] = {}
        for mid in self.motors:
            inputs[mid] = {"omega": omega}
            if mid in self.esc_for_motor:
                inputs[self.esc_for_motor[mid]] = {"throttle": throttle}
        for sid in self.servos:
            params = {**self.system.project.catalog.component(self.system.project.spec.components[sid].type).behavior.params,
                      **self.system.project.spec.components[sid].params}
            inputs[sid] = dict(servo) if servo is not None else {
                "horn_torque": to_si(params.get("load_torque", 0)),
                "horn_speed": to_si(params.get("operating_speed", 0))}
        for cid in self.computers:
            inputs[cid] = {"activity": activity}
        return inputs

    def motor_torque(self, out: dict[str, Any]) -> float:
        return sum(out[mid]["torque"] for mid in self.motors) / max(len(self.motors), 1)

    def solve(self, inputs: dict[str, Any], soc: float, thermal: str) -> dict[str, Any]:
        return self.system.operating_point(inputs, soc=soc, thermal=thermal)

    def cruise_throttle(self, soc: float, activity: float = CRUISE_ACTIVITY) -> float:
        def residual(t: float) -> float:
            out = self.solve(self.inputs(t, self.motor_omega_cruise, None, activity), soc, "ambient")
            return self.motor_torque(out) - self.motor_torque_cruise

        if residual(0.0) >= 0:
            return 0.0
        if residual(1.0) < 0:
            return 1.0
        return float(brentq(residual, 0.0, 1.0, xtol=1e-5))

    def traction_omega(self, soc: float) -> float:
        def residual(w: float) -> float:
            out = self.solve(self.inputs(1.0, w, None, 1.0), soc, "ambient")
            return self.motor_torque(out) - self.motor_torque_traction

        if residual(0.0) <= 0:
            return 0.0
        high = max(m.max_rpm * 2 * math.pi / 60 if math.isfinite(m.max_rpm) else 3000.0 for m in self.motors.values())
        if residual(high) > 0:
            return high
        return float(brentq(residual, 0.0, high, xtol=1e-3))

    def definitions(self, soc: float) -> dict[str, tuple[dict[str, Any], str]]:
        if not self.motors:
            servo_stall = {"horn_torque": 0.0, "horn_speed": 0.0}
            return {"idle": (self.inputs(0.0, 0.0, {}, 0.0), "steady"),
                    "servo_stall_compute": (self.inputs(0.0, 0.0, servo_stall, 1.0), "steady")}
        t_cruise = self.cruise_throttle(soc)
        w_full = self.traction_omega(soc)
        stall_torque = max((s.stall_torque * SERVO_STALL_DEMAND for s in self.servos.values()), default=0.0)
        return {
            "idle": (self.inputs(0.0, 0.0, {"horn_torque": 0.0, "horn_speed": 0.0}, 0.0), "steady"),
            "cruise": (self.inputs(t_cruise, self.motor_omega_cruise, None, CRUISE_ACTIVITY), "steady"),
            "full_throttle": (self.inputs(1.0, w_full, None, 1.0), "ambient"),
            "motor_stall": (self.inputs(1.0, 0.0, None, 1.0), "ambient"),
            "servo_stall_compute": (self.inputs(t_cruise, self.motor_omega_cruise,
                                                {"horn_torque": stall_torque, "horn_speed": 0.0}, 1.0), "steady"),
        }


def harness_drops(system: ElectricalSystem, out: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Per harness: sum over its nets of (max - min) node voltage across the
    harness's own nodes plus the mated far-side contacts, divided by the highest
    node voltage in the harness (the supply it carries)."""
    graph, spec = system.project.graph, system.project.spec
    voltage = {label: system.voltage(out["_x"], label) for label in graph.nodes}
    result = {}
    for harness in spec.electrical.harnesses:
        prefix = (f"H:{harness.id}.", f"S:{harness.id}.")
        nodes = {n for n in graph.nodes if n.startswith(prefix)}
        for edge in graph.edges:
            if edge.kind == "mate" and (edge.a in nodes or edge.b in nodes):
                nodes |= {edge.a, edge.b}
        by_net: dict[str, list[float]] = {}
        for node in nodes:
            by_net.setdefault(graph.net_of(node), []).append(voltage[node])
        drop = sum(max(v) - min(v) for v in by_net.values())
        supply = max(abs(v) for vs in by_net.values() for v in vs)
        result[harness.id] = {"drop": drop, "supply": supply, "fraction": drop / supply if supply > 0.1 else 0.0}
    return result


def pin_currents(system: ElectricalSystem, out: dict[str, Any]) -> dict[str, float]:
    """Current entering each component pin from wires/contacts (A)."""
    currents: dict[str, float] = {}
    branches = out["_branches"]
    for key, branch in system.network.branches.items():
        if branch.kind == "internal":
            continue
        i = branches[key]["current"]
        for node, sign in ((branch.node_b, 1.0), (branch.node_a, -1.0)):
            if node.startswith("C:"):
                currents[node[2:]] = currents.get(node[2:], 0.0) + sign * i
    return currents


def summarize_point(system: ElectricalSystem, name: str, state: str, soc: float, kind: str, out: dict[str, Any]) -> dict[str, Any]:
    components = {}
    for cid, values in out.items():
        if cid.startswith("_"):
            continue
        components[cid] = {k: v for k, v in values.items() if isinstance(v, (int, float, bool, str))}
        for key, nested in values.items():  # converter states (board.pcb: ``regulator``, ``servo_regulator``, ...)
            if isinstance(nested, dict) and "power_loss" in nested:
                components[cid][key] = {k: v for k, v in nested.items() if isinstance(v, (int, float, bool))}
    branches = out["_branches"]
    wires = {k: {"current": b["current"], "voltage_drop": b["voltage_drop"], "power_loss": b["power_loss"],
                 "temperature_rise": b["temperature_rise"], "ampacity": b["rating"], "awg": b["awg"], "harness": b["harness"],
                 "length": b["length"]}
             for k, b in branches.items() if b["kind"] == "wire"}
    contacts = {k: {"current": b["current"], "rating": b["rating"], "burst_rating": b["burst_rating"],
                    "voltage_drop": b["voltage_drop"], "power_loss": b["power_loss"]}
                for k, b in branches.items() if b["kind"] == "contact"}
    return {"operating_point": name, "battery_state": state, "soc": soc, "kind": kind, "components": components,
            "nets": out["_nets"], "wires": wires, "contacts": contacts, "harness_drop": harness_drops(system, out),
            "pin_currents": pin_currents(system, out), "events": out["_events"]}


# ------------------------------------------------------------------- checks
def _op_label(point: dict[str, Any]) -> str:
    return f"{point['operating_point']}@{point['battery_state']}"


def check_wires(system: ElectricalSystem, points: list[dict[str, Any]], result: StageResult) -> dict[str, Any]:
    ambient = system.ambient_c
    worst: dict[str, Any] = {}
    cruise = next((p for p in points if p["operating_point"] == "cruise" and p["battery_state"] == "nominal"), None)
    for key, branch in system.network.branches.items():
        if branch.kind != "wire":
            continue
        cont = max((p for p in points if p["kind"] == "continuous"), key=lambda p: abs(p["wires"][key]["current"]))
        burst = max((p for p in points if p["kind"] == "burst"), key=lambda p: abs(p["wires"][key]["current"]), default=None)
        i_cont = abs(cont["wires"][key]["current"])
        entry = {"continuous_current": i_cont, "continuous_point": _op_label(cont), "ampacity": branch.rating,
                 "awg": branch.meta["awg"], "harness": branch.meta["harness"], "length": branch.meta["length"],
                 "length_source": branch.meta["length_source"], "steady_rise": branch.steady_rise(i_cont, ambient)}
        if i_cont > branch.rating:
            result.add(Diagnostic.error(
                "wire_overcurrent", f"{key} ({branch.meta['awg']} AWG) carries {i_cont:.2f} A continuous at {_op_label(cont)} > ampacity {branch.rating} A",
                subjects=[key, branch.meta["harness"], f"op:{_op_label(cont)}"],
                data={"current": i_cont, "ampacity": branch.rating, "awg": branch.meta["awg"]}))
        if burst is not None:
            i_burst = abs(burst["wires"][key]["current"])
            start = cruise["wires"][key]["temperature_rise"] if cruise else 0.0
            duration = BURST_DURATION.get(burst["operating_point"], 2.0)
            rise = branch.transient_rise(i_burst, duration, ambient, start)
            temperature = ambient + rise
            entry.update(burst_current=i_burst, burst_point=_op_label(burst), burst_duration=duration,
                         burst_temperature_c=temperature, burst_ratio=i_burst / branch.rating)
            if temperature > SILICONE_INSULATION_MAX_C:
                result.add(Diagnostic.error(
                    "wire_burst_overtemperature",
                    f"{key}: {i_burst:.1f} A for {duration:.1f} s heats the conductor to {temperature:.0f} C > {SILICONE_INSULATION_MAX_C:.0f} C",
                    subjects=[key, f"op:{_op_label(burst)}"],
                    data={"current": i_burst, "duration": duration, "temperature_c": temperature, "limit_c": SILICONE_INSULATION_MAX_C}))
            elif i_burst > branch.rating:
                result.add(Diagnostic.warning(
                    "wire_burst_above_ampacity",
                    f"{key} ({branch.meta['awg']} AWG): {i_burst:.1f} A burst at {_op_label(burst)} exceeds the {branch.rating} A continuous "
                    f"ampacity; transient model gives {temperature:.1f} C after {duration:.1f} s (within insulation rating)",
                    subjects=[key, f"op:{_op_label(burst)}"],
                    data={"current": i_burst, "ampacity": branch.rating, "duration": duration, "temperature_c": temperature}))
        worst[key] = entry
    return worst


def check_connectors(system: ElectricalSystem, points: list[dict[str, Any]], result: StageResult) -> dict[str, Any]:
    worst = {}
    for key, branch in system.network.branches.items():
        if branch.kind != "contact":
            continue
        cont = max((p for p in points if p["kind"] == "continuous"), key=lambda p: abs(p["contacts"][key]["current"]))
        burst = max((p for p in points if p["kind"] == "burst"), key=lambda p: abs(p["contacts"][key]["current"]), default=None)
        i_cont = abs(cont["contacts"][key]["current"])
        entry = {"continuous_current": i_cont, "continuous_point": _op_label(cont), "rating": branch.rating,
                 "burst_rating": branch.burst_rating, "burst_rating_source": branch.meta["burst_source"],
                 "types": branch.meta["types"]}
        if i_cont > branch.rating:
            result.add(Diagnostic.error(
                "connector_overcurrent", f"{key}: {i_cont:.2f} A continuous at {_op_label(cont)} > {branch.rating} A per pin",
                subjects=[*branch.meta["connectors"], f"op:{_op_label(cont)}"],
                data={"current": i_cont, "rating": branch.rating, "continuous": 1.0}))
        if burst is not None:
            i_burst = abs(burst["contacts"][key]["current"])
            entry.update(burst_current=i_burst, burst_point=_op_label(burst))
            if i_burst > branch.burst_rating:
                result.add(Diagnostic.error(
                    "connector_overcurrent", f"{key}: {i_burst:.1f} A burst at {_op_label(burst)} > {branch.burst_rating:.0f} A burst rating",
                    subjects=[*branch.meta["connectors"], f"op:{_op_label(burst)}"],
                    data={"current": i_burst, "rating": branch.burst_rating, "continuous": 0.0}))
            elif i_burst > branch.rating:
                result.add(Diagnostic.warning(
                    "connector_burst_above_continuous",
                    f"{key}: {i_burst:.1f} A at {_op_label(burst)} exceeds the {branch.rating:.0f} A continuous rating "
                    f"(burst limit {branch.burst_rating:.0f} A, {branch.meta['burst_source']})",
                    subjects=[*branch.meta["connectors"], f"op:{_op_label(burst)}"],
                    data={"current": i_burst, "rating": branch.rating, "burst_rating": branch.burst_rating}))
        worst[key] = entry
    return worst


def check_voltage_drop(system: ElectricalSystem, points: list[dict[str, Any]], result: StageResult) -> dict[str, Any]:
    limit = system.project.spec.requirements.max_wire_voltage_drop_fraction
    worst = {}
    for harness in system.project.spec.electrical.harnesses:
        hid = harness.id
        for kind in ("continuous", "burst"):
            candidates = [p for p in points if p["kind"] == kind]
            if not candidates:
                continue
            point = max(candidates, key=lambda p: p["harness_drop"][hid]["fraction"])
            drop = point["harness_drop"][hid]
            worst.setdefault(hid, {})[kind] = {**drop, "point": _op_label(point)}
            if drop["fraction"] > limit:
                make = Diagnostic.error if kind == "continuous" else Diagnostic.warning
                result.add(make(
                    "voltage_drop_exceeded",
                    f"harness {hid}: {drop['drop'] * 1e3:.0f} mV drop ({100 * drop['fraction']:.2f} % of {drop['supply']:.2f} V) at "
                    f"{_op_label(point)} > {100 * limit:.1f} % ({kind})",
                    subjects=[hid, f"op:{_op_label(point)}"],
                    data={"drop": drop["drop"], "fraction": drop["fraction"], "limit": limit, "supply": drop["supply"]}))
    return worst


def _load_side_wires(system: ElectricalSystem, fuse: FuseBlade) -> tuple[list[str], list[str], str]:
    """(wires on the fuse's load side, wires feeding the fuse that it cannot
    protect, the fuse's load-side terminal node)."""
    graph = system.project.graph
    batteries = [f"C:{cid}.{m.pos.name}" for cid, m in _models(system, BatteryLipo).items()]
    source_nets = {graph.net_of(b) for b in batteries}
    a, b = fuse.branch.node_a, fuse.branch.node_b
    source_terminal, load_terminal = (a, b) if graph.net_of(a) in source_nets else (b, a)
    load_net = graph.net_of(load_terminal)
    source_net = graph.net_of(source_terminal)
    load, feed = [], []
    wire_edges = [e for e in graph.edges if e.kind == "wire"]
    for edge in wire_edges:
        if graph.net_of(edge.a) == load_net:
            load.append(edge.ref)
    # Wires of the source net whose removal disconnects the fuse from the battery
    # and that are thinner than the source net's main conductors are protected by nothing.
    net_edges = [e for e in graph.edges if e.kind in ("wire", "mate") and graph.net_of(e.a) == source_net]
    thickest = min((e.data["awg"] for e in net_edges if e.kind == "wire"), default=0)
    for edge in net_edges:
        if edge.kind != "wire" or edge.data["awg"] <= thickest:
            continue
        adjacency: dict[str, set[str]] = {}
        for other in net_edges:
            if other is edge:
                continue
            adjacency.setdefault(other.a, set()).add(other.b)
            adjacency.setdefault(other.b, set()).add(other.a)
        seen, stack = {source_terminal}, [source_terminal]
        while stack:
            for nxt in adjacency.get(stack.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        if not any(battery in seen for battery in batteries):
            feed.append(edge.ref)
    return load, feed, load_terminal


def check_fuses(system: ElectricalSystem, points: list[dict[str, Any]], result: StageResult) -> dict[str, Any]:
    report = {}
    ambient = system.ambient_c
    for cid, fuse in system.fuse_models().items():
        if fuse.branch is None:
            continue
        cont = max((p for p in points if p["kind"] == "continuous"), key=lambda p: abs(p["components"][cid]["current"]))
        i_cont = abs(cont["components"][cid]["current"])
        entry: dict[str, Any] = {"rating": fuse.rating, "i2t_melt": fuse.i2t_melt, "min_melt_current": fuse.min_melt_ratio * fuse.rating,
                                 "continuous_current": i_cont, "continuous_point": _op_label(cont), "derated_limit": FUSE_DERATING * fuse.rating}
        if i_cont > FUSE_DERATING * fuse.rating:
            result.add(Diagnostic.error(
                "fuse_undersized", f"{cid}: {i_cont:.2f} A continuous at {_op_label(cont)} > {FUSE_DERATING:.0%} of the {fuse.rating:.0f} A rating",
                subjects=[cid, f"op:{_op_label(cont)}"], data={"current": i_cont, "rating": fuse.rating, "derating": FUSE_DERATING}))
        for point in (p for p in points if p["kind"] == "burst"):
            i = abs(point["components"][cid]["current"])
            duration = BURST_DURATION.get(point["operating_point"], 2.0)
            t_blow = fuse.time_to_blow(i)
            entry.setdefault("burst", {})[_op_label(point)] = {"current": i, "time_to_blow": t_blow if math.isfinite(t_blow) else None}
            if t_blow < duration:
                result.add(Diagnostic.error(
                    "fuse_undersized", f"{cid}: {i:.1f} A at {_op_label(point)} melts the fuse in {t_blow:.2f} s (< {duration} s burst)",
                    subjects=[cid, f"op:{_op_label(point)}"], data={"current": i, "time_to_blow": t_blow, "duration": duration}))
        load_wires, feed_wires, load_terminal = _load_side_wires(system, fuse)
        entry["protected_wires"] = load_wires
        entry["unprotected_feed_wires"] = feed_wires
        # Prospective bolted short at the fuse's load terminal (battery full, cold conductors).
        ground = system.project.spec.electrical.ground_net
        short = system.operating_point({"_faults": [{"type": "short", "a": load_terminal, "b": ground, "resistance": 0.001}]},
                                       soc=1.0, thermal="ambient")
        i_sc = abs(short[cid]["current"])
        t_sc = fuse.time_to_blow(i_sc)
        entry.update(short_circuit_current=i_sc, short_circuit_clear_time=t_sc)
        for wire_id in load_wires:
            branch = system.network.branches[wire_id]
            area = branch.meta["area"]
            withstand = adiabatic_i2t_withstand(area, ambient, SILICONE_INSULATION_MAX_C)
            hold_temperature = ambient + branch.steady_rise(fuse.min_melt_ratio * fuse.rating, ambient)
            let_through = min(fuse.i2t_melt, i_sc * i_sc * t_sc) if math.isfinite(t_sc) else math.inf
            entry.setdefault("wires", {})[wire_id] = {"ampacity": branch.rating, "awg": branch.meta["awg"], "withstand_i2t": withstand,
                                                       "temperature_at_min_melt_current_c": hold_temperature, "let_through_i2t": let_through}
            problems = []
            if fuse.rating > branch.rating:
                problems.append(f"rating {fuse.rating:.0f} A > ampacity {branch.rating} A")
            if hold_temperature > SILICONE_INSULATION_MAX_C:
                problems.append(f"wire reaches {hold_temperature:.0f} C at the fuse's {fuse.min_melt_ratio * fuse.rating:.1f} A minimum melting current")
            if let_through > withstand:
                problems.append(f"let-through {let_through:.0f} A2s > wire withstand {withstand:.0f} A2s")
            if problems:
                result.add(Diagnostic.error(
                    "fuse_does_not_protect_wire", f"{cid} does not protect {wire_id} ({branch.meta['awg']} AWG): " + "; ".join(problems),
                    subjects=[cid, wire_id], data={"rating": fuse.rating, "ampacity": branch.rating, "withstand_i2t": withstand,
                                                   "let_through_i2t": let_through, "temperature_c": hold_temperature}))
        for wire_id in feed_wires:
            branch = system.network.branches[wire_id]
            result.add(Diagnostic.warning(
                "wire_unprotected_by_fuse",
                f"{wire_id} ({branch.meta['awg']} AWG, {branch.meta['length'] * 1e3:.0f} mm) feeds {cid} from the battery side; "
                f"a short on it is not cleared by any fuse",
                subjects=[wire_id, cid], data={"awg": branch.meta["awg"], "length": branch.meta["length"], "ampacity": branch.rating,
                                               "short_circuit_current": i_sc},
                hint="Place the fuse at the splice/source end of the thin branch, or size that segment for the battery's short-circuit current."))
        report[cid] = entry
    return report


def check_regulators_and_thermal(system: ElectricalSystem, points: list[dict[str, Any]], result: StageResult) -> dict[str, Any]:
    ambient = system.ambient_c
    requirement = system.project.spec.requirements.max_component_temperature_c
    report: dict[str, Any] = {}
    continuous = [p for p in points if p["kind"] == "continuous"]
    for cid, model in system.models.items():
        for key, core in _cores(model).items():
            name = cid if key is None else f"{cid}.{key}"

            def values(p: dict[str, Any]) -> dict[str, Any]:
                return p["components"][cid][key] if key is not None else p["components"][cid]

            point = max(continuous, key=lambda p: values(p)["power_loss"])
            reg = values(point)
            temperature = ambient + core.theta * reg["power_loss"]
            limit = min(core.max_temp_c, requirement)
            # A converter's rated output current: the catalog design current (continuous). Short peaks up to
            # the current limit are reported as regulator_current_limit when the limit is reached.
            peak = max(points, key=lambda p: values(p)["i_out"])
            report[name] = {"part": core.part, "power_loss": reg["power_loss"], "theta_ja": core.theta, "temperature_c": temperature,
                            "limit_c": limit, "point": _op_label(point), "efficiency": reg["efficiency"], "i_out": reg["i_out"],
                            "i_max": core.i_max, "current_limit": core.current_limit, "peak_i_out": values(peak)["i_out"],
                            "peak_point": _op_label(peak), "v_out_at_peak": values(peak)["v_out"],
                            "dropout_input_at_i_max": core.dropout_input(core.i_max)}
            if temperature > limit:
                result.add(Diagnostic.error(
                    "regulator_overtemperature", f"{name} buck: {reg['power_loss']:.2f} W x {core.theta} C/W -> {temperature:.0f} C > {limit:.0f} C",
                    subjects=[cid, f"op:{_op_label(point)}"], data={"temperature_c": temperature, "limit_c": limit, "power_loss": reg["power_loss"]}))
            if reg["i_out"] > core.i_max:
                result.add(Diagnostic.error("regulator_overcurrent", f"{name}: {reg['i_out']:.2f} A > {core.i_max} A",
                                            subjects=[cid], data={"current": reg["i_out"], "limit": core.i_max}))
            if values(peak)["i_out"] >= core.current_limit * 0.98:
                result.add(Diagnostic.warning(
                    "regulator_current_limit", f"{name}: output reaches the {core.current_limit:g} A current limit at "
                    f"{_op_label(peak)} ({values(peak)['v_out']:.2f} V)", subjects=[cid, f"op:{_op_label(peak)}"],
                    data={"current": values(peak)["i_out"], "limit": core.current_limit, "v_out": values(peak)["v_out"]}))
        if isinstance(model, BrushedEsc):
            point = max(continuous, key=lambda p: p["components"][cid]["power_loss"])
            loss = point["components"][cid]["power_loss"]
            temperature = ambient + model.theta * loss
            limit = min(model.max_temp_c, requirement)
            entry = {"power_loss": loss, "temperature_c": temperature, "limit_c": limit, "point": _op_label(point)}
            if temperature > limit:
                result.add(Diagnostic.error(
                    "esc_overtemperature", f"{cid}: {loss:.2f} W x {model.theta} C/W -> {temperature:.0f} C > {limit:.0f} C at {_op_label(point)}",
                    subjects=[cid, f"op:{_op_label(point)}"], data={"temperature_c": temperature, "limit_c": limit, "power_loss": loss}))
            if model.bec_reg is not None:
                bec_point = max(points, key=lambda p: p["components"][cid]["bec_current"])
                i_bec = bec_point["components"][cid]["bec_current"]
                entry.update(bec_current=i_bec, bec_rating=model.bec_current, bec_point=_op_label(bec_point),
                             bec_voltage=bec_point["components"][cid]["bec_voltage"], bec_dissipation=bec_point["components"][cid]["bec_dissipation"])
                if i_bec > model.bec_current:
                    result.add(Diagnostic.error(
                        "bec_overload", f"{cid} BEC supplies {i_bec:.2f} A at {_op_label(bec_point)} > {model.bec_current} A rating "
                        f"(output {bec_point['components'][cid]['bec_voltage']:.2f} V, dissipating {bec_point['components'][cid]['bec_dissipation']:.1f} W)",
                        subjects=[cid, f"op:{_op_label(bec_point)}"],
                        data={"current": i_bec, "rating": model.bec_current, "voltage": bec_point["components"][cid]["bec_voltage"],
                              "dissipation": bec_point["components"][cid]["bec_dissipation"]}))
            burst = [p for p in points if p["kind"] == "burst"]
            if burst:
                peak = max(burst, key=lambda p: abs(p["components"][cid]["motor_current"]))
                i_peak = abs(peak["components"][cid]["motor_current"])
                entry.update(peak_motor_current=i_peak, peak_point=_op_label(peak), current_continuous=model.i_cont, current_burst=model.i_burst)
                if i_peak > model.i_burst:
                    result.add(Diagnostic.error("esc_overcurrent", f"{cid}: {i_peak:.0f} A > {model.i_burst:.0f} A burst",
                                                subjects=[cid], data={"current": i_peak, "limit": model.i_burst}))
            report[cid] = entry
        if isinstance(model, DcBrushedMotor):
            point = max(continuous, key=lambda p: abs(p["components"][cid]["current"]))
            i = abs(point["components"][cid]["current"])
            heat = i * i * model.resistance + model.brush_drop * i
            report[cid] = {"steady_temperature_c": ambient + heat * model.theta, "limit_c": model.max_temp_c, "point": _op_label(point)}
        if isinstance(model, BatteryLipo):
            peak = max(points, key=lambda p: abs(p["components"][cid]["current"]))
            i = abs(peak["components"][cid]["current"])
            report[cid] = {"peak_current": i, "peak_point": _op_label(peak), "c_rating_limit": model.max_current}
            if i > model.max_current:
                result.add(Diagnostic.error("battery_overcurrent", f"{cid}: {i:.0f} A > {model.max_current:.0f} A",
                                            subjects=[cid], data={"current": i, "limit": model.max_current}))
    return report


def _cores(model: Any) -> dict[str | None, Any]:
    """Buck converters of a model keyed by their output key (``None``: the model's own outputs)."""
    if isinstance(model, BoardPcb):
        return dict(model.cores)
    if isinstance(model, BuckRegulator):
        return {None: model.core}
    return {}


def check_tvs(system: ElectricalSystem, result: StageResult) -> dict[str, Any]:
    """Input TVS clamp against the converters' input absolute maximum.

    Expected surge: with a 2S pack nothing in the robot drives the bus above the pack voltage for long (the
    brushed ESC does not boost); surges are inductive kicks when the pack is unplugged or a fuse opens under
    load, and the TVS current is at most the current interrupted in the board's supply branch. That branch is
    fused, so the expected surge current is 2 x the fuse rating (a blade fuse carries 200 % for at least
    ~0.15 s), and never more than the TVS's rated I_PP. The clamp is also checked at I_PP (the worst case the
    part is specified for)."""
    report: dict[str, Any] = {}
    graph = system.project.graph
    fuses = {cid: m for cid, m in system.models.items() if isinstance(m, FuseBlade)}
    for cid, model in system.models.items():
        if not isinstance(model, BoardPcb) or model.vin_pin is None:
            continue
        tvs = model.params.values.get("tvs")
        if not isinstance(tvs, dict) or model.tvs_clamp(0.0) is None:
            continue
        abs_max = min((core.v_in_abs_max for core in model.cores.values()), default=math.inf)
        if not math.isfinite(abs_max):
            continue
        board_net = graph.net_of(model.vin_pin.label)
        feeding = [(fid, f) for fid, f in fuses.items()
                   if any(graph.net_of(pin.label) == board_net for pin in f.pins.values())]
        i_pp = float(to_si(tvs["i_pp"]))
        surge = min(i_pp, 2.0 * min(f.rating for _, f in feeding)) if feeding else i_pp
        v_surge, v_pp = model.tvs_clamp(surge), model.tvs_clamp(i_pp)
        entry = {"part": tvs.get("part"), "abs_max": abs_max, "expected_surge_current": surge,
                 "surge_source": f"2 x {feeding[0][0]} rating" if feeding else "TVS rated I_PP",
                 "clamp_at_expected_surge": v_surge, "clamp_at_i_pp": v_pp, "i_pp": i_pp,
                 "margin_at_expected_surge": abs_max - v_surge, "pack_max": max(
                     (m.cells * m.ocv_cell(1.0) for m in system.models.values() if isinstance(m, BatteryLipo)), default=None),
                 "v_standoff": float(to_si(tvs["v_standoff"])) if tvs.get("v_standoff") is not None else None}
        report[cid] = entry
        if v_surge > abs_max or v_pp > abs_max:
            worst = max(v_surge, v_pp)
            result.add(Diagnostic.error(
                "tvs_clamp_exceeds_abs_max", f"{cid}: {tvs.get('part')} clamps at {v_surge:.1f} V at the expected "
                f"{surge:.1f} A surge ({v_pp:.1f} V at I_PP {i_pp:g} A), above the converters' {abs_max:g} V VIN absolute maximum",
                subjects=[cid, str(tvs.get("part"))],
                data={"clamp_voltage": worst, "abs_max": abs_max, "surge_current": surge, "i_pp": i_pp},
                hint="choose a TVS with a lower breakdown voltage that still stands off the full pack voltage"))
        if entry["v_standoff"] is not None and entry["pack_max"] is not None and entry["v_standoff"] < entry["pack_max"]:
            result.add(Diagnostic.error(
                "tvs_standoff_below_pack", f"{cid}: {tvs.get('part')} stand-off {entry['v_standoff']:g} V is below the full pack "
                f"{entry['pack_max']:.2f} V (the TVS would conduct in normal use)", subjects=[cid, str(tvs.get("part"))],
                data={"v_standoff": entry["v_standoff"], "pack_max": entry["pack_max"]}))
    return report


def check_brownout(system: ElectricalSystem, points: list[dict[str, Any]], result: StageResult) -> dict[str, Any]:
    report: dict[str, Any] = {}
    regulators = {cid: m for cid, m in system.models.items() if isinstance(m, (BoardPcb, BuckRegulator)) and m.core}
    for cid, model in _models(system, ComputerLoad).items():
        rows = []
        for point in points:
            comp = point["components"][cid]
            row = {"point": _op_label(point), "voltage": comp["voltage"], "margin": comp["brownout_margin"]}
            for rid, reg in regulators.items():
                values = point["components"][rid].get("regulator", point["components"][rid])
                row[f"{rid}_v_in"] = values["v_in"]
                row[f"{rid}_uvlo_margin"] = values["v_in"] - reg.core.uvlo_falling
                row[f"{rid}_enabled"] = values["enabled"]
            rows.append(row)
            if comp["brownout_margin"] < 0:
                cause = [rid for rid in regulators if not row[f"{rid}_enabled"]]
                result.add(Diagnostic.error(
                    "brownout_risk",
                    f"{cid} supply {comp['voltage']:.2f} V < brownout {model.brownout_voltage} V at {_op_label(point)}"
                    + (f" (regulator {', '.join(cause)} in UVLO: input below {regulators[cause[0]].core.uvlo_falling} V)" if cause else ""),
                    subjects=[cid, *cause, f"op:{_op_label(point)}"],
                    data={"voltage": comp["voltage"], "threshold": model.brownout_voltage, "margin": comp["brownout_margin"],
                          "battery_voltage": next(iter(point["components"][b]["v_terminal"] for b in _models(system, BatteryLipo))),
                          **{k: v for k, v in row.items() if isinstance(v, float)}}))
        worst = min(rows, key=lambda r: r["margin"])
        at_min = [r for r in rows if r["point"].endswith("@minimum")]
        report[cid] = {"worst": worst, "at_battery_minimum": at_min, "brownout_voltage": model.brownout_voltage}
        for row in at_min:
            uvlo_margins = [row[k] for k in row if k.endswith("_uvlo_margin")]
            if row["margin"] >= 0 and uvlo_margins and min(uvlo_margins) < 0.2:
                result.add(Diagnostic.warning(
                    "brownout_margin_low", f"{cid}: regulator input only {min(uvlo_margins) * 1e3:.0f} mV above UVLO at {row['point']}",
                    subjects=[cid, f"op:{row['point']}"], data={"uvlo_margin": min(uvlo_margins), "voltage": row["voltage"]}))
    return report


def check_pins(system: ElectricalSystem, points: list[dict[str, Any]], result: StageResult) -> None:
    spec, catalog = system.project.spec, system.project.catalog
    reported: set[tuple[str, str]] = set()
    for cid, inst in spec.components.items():
        ctype = catalog.components.get(inst.type)
        if not ctype:
            continue
        ground = next((n for n, p in ctype.pins.items() if p.role == "ground"), None)
        for name, pin in ctype.pins.items():
            ref = f"{cid}.{name}"
            if pin.role == "ground":
                continue
            for point in points:
                x_pin = _pin_voltage(point, ref) - (_pin_voltage(point, f"{cid}.{ground}") if ground else 0.0)
                current = abs(point["pin_currents"].get(ref, 0.0))
                burst = point["kind"] == "burst"
                if pin.current_max and current > pin.current_max * 1.001 and ("pin_overcurrent", ref) not in reported:
                    reported.add(("pin_overcurrent", ref))
                    result.add((Diagnostic.warning if burst else Diagnostic.error)(
                        "pin_overcurrent", f"{ref}: {current:.2f} A at {_op_label(point)} > pin rating {pin.current_max} A",
                        subjects=[ref, f"op:{_op_label(point)}"], data={"current": current, "limit": pin.current_max}))
                if pin.voltage_max is not None and x_pin > pin.voltage_max + 1e-3 and ("pin_overvoltage", ref) not in reported:
                    reported.add(("pin_overvoltage", ref))
                    result.add(Diagnostic.error("pin_overvoltage", f"{ref}: {x_pin:.2f} V at {_op_label(point)} > {pin.voltage_max} V",
                                                subjects=[ref, f"op:{_op_label(point)}"], data={"voltage": x_pin, "limit": pin.voltage_max}))
                if (pin.role == "power_in" and pin.voltage_min is not None and x_pin < pin.voltage_min
                        and ("supply_undervoltage", ref, burst) not in reported):
                    reported.add(("supply_undervoltage", ref, burst))
                    result.add((Diagnostic.warning if burst else Diagnostic.error)(
                        "supply_undervoltage", f"{ref}: {x_pin:.2f} V at {_op_label(point)} < minimum {pin.voltage_min} V",
                        subjects=[ref, f"op:{_op_label(point)}"], data={"voltage": x_pin, "limit": pin.voltage_min}))


def _pin_voltage(point: dict[str, Any], ref: str) -> float:
    return float(point["_pins"].get(ref, 0.0))


def check_logic_levels(system: ElectricalSystem, result: StageResult) -> list[dict[str, Any]]:
    """3.3 V outputs into actuator thresholds and 5 V tolerance, per signal group
    (nets joined through series resistors). A receiver's ``logic_level`` <= 2.5 V
    is read as its V_IH threshold; higher values name its logic family (V_IH =
    0.7 x level, CMOS). The driver's V_OH at the receiver includes the series
    resistor divider against an estimated input resistance."""
    project = system.project
    spec, catalog, graph = project.spec, project.catalog, project.graph
    groups: dict[str, set[str]] = {}
    parent: dict[str, str] = {}

    def find(n: str) -> str:
        while parent.setdefault(n, n) != n:
            n = parent[n]
        return n

    series: dict[tuple[str, str], float] = {}
    for edge in graph.edges:
        if edge.kind == "internal" and edge.data.get("link_kind") in ("series_resistor", *ACTIVE_SIGNAL_LINKS):
            ra, rb = find(graph.net_of(edge.a)), find(graph.net_of(edge.b))
            parent[ra] = rb
            # Active links re-drive the signal at their own logic level: no series resistance (their
            # ``value`` is not an ohmic value, e.g. the arbiter's heartbeat timeout).
            resistance = float(edge.data.get("value") or 0.0) if edge.data.get("link_kind") == "series_resistor" else 0.0
            series[(graph.net_of(edge.a), graph.net_of(edge.b))] = resistance
    for node in graph.nodes:
        groups.setdefault(find(graph.net_of(node)), set()).add(node)
    pins: dict[str, Any] = {}
    kinds: dict[str, str] = {}
    for cid, inst in spec.components.items():
        ctype = catalog.components.get(inst.type)
        if ctype:
            for name, pin in ctype.pins.items():
                pins[f"C:{cid}.{name}"] = pin
                kinds[f"C:{cid}.{name}"] = ctype.kind
    drivers_roles = {"pwm_out", "uart_tx"}
    receiver_roles = {"pwm_in", "uart_rx"}
    rows = []
    for members in groups.values():
        endpoints = [m for m in members if m in pins and kinds[m] != "pcb"]
        drivers = [m for m in endpoints if pins[m].role in drivers_roles and pins[m].logic_level]
        receivers = [m for m in endpoints if pins[m].role in receiver_roles]
        for driver in drivers:
            for receiver in receivers:
                d, r = pins[driver], pins[receiver]
                pair = {graph.net_of(driver), graph.net_of(receiver)}
                r_series = sum(v for (a, b), v in series.items() if a in pair and b in pair)
                v_oh = d.logic_level * LOGIC_INPUT_RESISTANCE / (LOGIC_INPUT_RESISTANCE + r_series + DRIVER_OUTPUT_RESISTANCE)
                level = r.logic_level or 3.3
                v_ih = level if level <= 2.5 else 0.7 * level
                row = {"driver": driver[2:], "receiver": receiver[2:], "v_oh": v_oh, "v_ih": v_ih, "margin": v_oh - v_ih,
                       "series_resistance": r_series, "receiver_voltage_max": r.voltage_max, "tolerant_5v": r.tolerant_5v}
                rows.append(row)
                if v_oh < v_ih:
                    result.add(Diagnostic.error(
                        "logic_level_incompatible", f"{driver[2:]} drives {v_oh:.2f} V into {receiver[2:]} which needs {v_ih:.2f} V",
                        subjects=[driver[2:], receiver[2:]], data={"v_oh": v_oh, "v_ih": v_ih, "series_resistance": r_series}))
                over_abs = r.voltage_max is not None and d.logic_level > r.voltage_max
                # A family level (> 2.5 V) without 5 V tolerance limits the input to ~VDD + 0.3 V.
                over_family = bool(r.logic_level and 2.5 < r.logic_level <= 3.6 and not r.tolerant_5v
                                   and d.logic_level > r.logic_level + 0.3)
                if over_abs or over_family:
                    result.add(Diagnostic.error(
                        "logic_level_overvoltage", f"{driver[2:]} ({d.logic_level} V) into {receiver[2:]} (max {r.voltage_max or r.logic_level} V, not 5 V tolerant)",
                        subjects=[driver[2:], receiver[2:]], data={"v_oh": d.logic_level, "limit": float(r.voltage_max or r.logic_level)}))
    return rows


def estimate_runtime(system: ElectricalSystem, ops: OperatingPoints, duty: dict[str, float], result: StageResult) -> dict[str, Any]:
    """Quasi-static discharge: at each SOC the duty-cycle-weighted battery
    current I_avg = sum f_k I_k is solved; dt = dSOC Q / I_avg. Ends when the
    cruise-loaded pack voltage reaches the cutoff (max of battery minimum,
    ESC low-voltage cutoff and a software ``low_voltage_cutoff_v`` if configured)."""
    batteries = _models(system, BatteryLipo)
    if not batteries or not ops.motors:
        return {}
    bid, battery = next(iter(batteries.items()))
    cutoffs = {"battery_min_cell": battery.min_cell_voltage * battery.cells}
    for eid, esc in ops.escs.items():
        if esc.lvc_per_cell:
            cutoffs[f"{eid}_lvc"] = esc.lvc_per_cell * battery.cells
    software_cutoff = system.project.spec.software.config.get("low_voltage_cutoff_v")
    if software_cutoff:
        cutoffs["software_cutoff"] = float(software_cutoff)
    cutoff = max(cutoffs.values())
    total = sum(duty.values())
    weights = {k: v / total for k, v in duty.items()}
    elapsed, curve = 0.0, []
    socs = np.linspace(1.0, 0.0, 41)
    previous = None
    for soc in socs:
        defs = ops.definitions(float(soc))
        currents, voltages = {}, {}
        for name in weights:
            out = system.operating_point(defs[name][0], soc=float(soc), thermal="ambient")
            currents[name] = out[bid]["current"]
            voltages[name] = out[bid]["v_terminal"]
        i_avg = sum(weights[n] * currents[n] for n in weights)
        if previous is not None:
            d_soc = previous[0] - soc
            elapsed += d_soc * battery.capacity / (0.5 * (previous[1] + i_avg))
        curve.append({"soc": float(soc), "time": elapsed, "average_current": i_avg, "cruise_voltage": voltages.get("cruise", 0.0),
                      "full_throttle_voltage": voltages.get("full_throttle", 0.0)})
        if voltages.get("cruise", math.inf) < cutoff:
            break
        previous = (soc, i_avg)
    runtime = curve[-1]["time"]
    if len(curve) >= 2 and curve[-1]["cruise_voltage"] < cutoff:  # interpolate the crossing
        a, b = curve[-2], curve[-1]
        frac = (a["cruise_voltage"] - cutoff) / max(a["cruise_voltage"] - b["cruise_voltage"], 1e-9)
        runtime = a["time"] + frac * (b["time"] - a["time"])
    requirement = system.project.spec.requirements.min_runtime
    report = {"runtime": runtime, "requirement": requirement, "duty_cycle": weights, "cutoff_voltage": cutoff,
              "cutoffs": cutoffs, "battery": bid, "capacity_ah": battery.capacity / 3600.0, "curve": curve,
              "average_current_nominal": float(np.interp(0.5, [c["soc"] for c in curve[::-1]], [c["average_current"] for c in curve[::-1]]))}
    if requirement and runtime < requirement:
        result.add(Diagnostic.error("runtime_insufficient", f"estimated runtime {runtime / 60:.1f} min < required {requirement / 60:.1f} min",
                                    subjects=[bid], data={"runtime": runtime, "requirement": requirement}))
    else:
        result.add(Diagnostic.info("runtime_estimate", f"estimated runtime {runtime / 60:.1f} min at the duty cycle (cutoff {cutoff:.2f} V)",
                                   subjects=[bid], data={"runtime": runtime, "requirement": requirement or 0.0, "cutoff_voltage": cutoff}))
    return report


def full_throttle_transient(project: Any, routes: Any, ops: OperatingPoints, duration: float = 2.0, dt: float = 1e-3) -> dict[str, list[float]]:
    """Full throttle from rest with a constant motor-speed ramp to the
    traction-limited speed reached after ``duration`` (no vehicle dynamics)."""
    system = ElectricalSystem(project, routes, initial={"battery_soc": 1.0})
    w_end = ops.cruise_speed * 2.0 / ops.vehicle["wheel_radius"] * ops.vehicle["ratio"]
    trace: dict[str, list[float]] = {"time": [], "omega": [], "motor_current": [], "torque": [], "battery_voltage": [], "pi_voltage": []}
    motor = next(iter(ops.motors))
    esc = ops.esc_for_motor.get(motor)
    battery = next(iter(_models(system, BatteryLipo)))
    computer = next(iter(ops.computers), None)
    for k in range(int(round(duration / dt))):
        omega = w_end * (k + 1) * dt / duration
        inputs = ops.inputs(1.0, omega, None, 1.0)
        inputs[esc] = {"pulse_us": 2000.0} if esc else {}
        out = system.step(dt, inputs)
        trace["time"].append(system.time)
        trace["omega"].append(omega)
        trace["motor_current"].append(out[motor]["current"])
        trace["torque"].append(out[motor]["torque"])
        trace["battery_voltage"].append(out[battery]["v_terminal"])
        trace["pi_voltage"].append(out[computer]["voltage"] if computer else 0.0)
    system.close()
    return trace


# --------------------------------------------------------------------- run
def run(project: Any, routes: dict[str, Any] | None = None, *, plots: bool = True,
        cruise_speed: float = DEFAULT_CRUISE_SPEED) -> StageResult:
    started = time.time()
    result = StageResult(stage=STAGE, inputs=project.fingerprint())
    build = project.build_dir(STAGE)
    if routes is None:
        routes = project.read_build_json("wiring/routes.json")
    system = ElectricalSystem(project, routes)
    result.extend(system.diagnostics)
    from r3_model.pulled import PullError
    try:
        vehicle = vehicle_model(project, result)
    except PullError as error:
        result.add(Diagnostic.error(error.code, str(error)))
        return result
    ops = OperatingPoints(system, vehicle, cruise_speed)
    battery = next(iter(_models(system, BatteryLipo).values()), None)
    states = {"full": 1.0, "nominal": 0.5, "minimum": 0.0}

    points: list[dict[str, Any]] = []
    for state, soc in states.items():
        for name, (inputs, thermal) in ops.definitions(soc).items():
            out = system.operating_point(inputs, soc=soc, thermal=thermal)
            point = summarize_point(system, name, state, soc, "burst" if name in BURST else "continuous", out)
            point["_x"] = out["_x"]
            point["_pins"] = out["_pins"]
            point["inputs"] = inputs
            points.append(point)

    worst_wires = check_wires(system, points, result)
    worst_contacts = check_connectors(system, points, result)
    drops = check_voltage_drop(system, points, result)
    fuses = check_fuses(system, points, result)
    thermal = check_regulators_and_thermal(system, points, result)
    brownout = check_brownout(system, points, result)
    tvs = check_tvs(system, result)
    check_pins(system, points, result)
    logic = check_logic_levels(system, result)
    duty = dict(project.spec.requirements.extra.get("duty_cycle", DEFAULT_DUTY_CYCLE))
    runtime = estimate_runtime(system, ops, duty, result)

    estimated = {cid: m.params.estimated for cid, m in system.models.items() if m.params.estimated}
    _assumptions(result, vehicle, ops, estimated, routes is not None, routes)
    result.metrics.update({
        "vehicle_mass": vehicle["mass"], "cruise_motor_torque": ops.motor_torque_cruise, "cruise_motor_omega": ops.motor_omega_cruise,
        "traction_motor_torque": ops.motor_torque_traction, "runtime": runtime.get("runtime"),
        "unknowns": system.unknowns, "behavior_models": len(system.models), "solver": "ngspice",
        "operating_point_solves": system.stats["operating_points"],
        "max_battery_current": max(abs(p["components"][battery.id]["current"]) for p in points) if battery else 0.0,
    })
    for p in points:
        if battery:
            result.metrics[f"battery_current.{_op_label(p)}"] = p["components"][battery.id]["current"]

    document = {
        "schema": "r3/electrical-operating-points@1", "robot": project.spec.robot.name, "ambient_c": system.ambient_c,
        "vehicle": vehicle, "battery_states": states,
        "operating_points": {_op_label(p): {k: v for k, v in p.items() if not k.startswith("_")} for p in points},
        "worst_case": {"wires": worst_wires, "connectors": worst_contacts, "harness_voltage_drop": drops},
        "fuses": fuses, "thermal": thermal, "brownout": brownout, "tvs": tvs, "logic_levels": logic, "runtime": runtime,
        "estimated_parameters": estimated, "wire_lengths": system.network.wire_lengths,
        "diagnostics": [d.model_dump(mode="json") for d in result.diagnostics],
    }
    path = build / "operating_points.json"
    path.write_text(json.dumps(_clean(document), indent=2, default=_json_default, allow_nan=False))
    result.artifact("electrical/operating_points.json", "json", "Static worst-case electrical operating points and checks")
    for mode, description in (("op", "ngspice deck for the operating points (EXTERNAL sources set per point)"),
                              ("tran", "ngspice deck of the lockstep co-simulation transient")):
        (build / f"circuit_{mode}.cir").write_text(system.to_spice(mode) + "\n")
        result.artifact(f"electrical/circuit_{mode}.cir", "spice", description)

    if plots:
        from . import plots as plotting

        cruise = next(p for p in points if p["operating_point"] == ("cruise" if ops.motors else "idle") and p["battery_state"] == "nominal")
        plotting.power_tree(system, cruise, build / "power_tree.png")
        result.artifact("electrical/power_tree.png", "plot_png", "Power tree at cruise, nominal battery")
        plotting.wire_currents(worst_wires, build / "wire_currents.png")
        result.artifact("electrical/wire_currents.png", "plot_png", "Worst-case current per wire vs ampacity")
        if runtime:
            plotting.runtime_curve(runtime, build / "runtime.png")
            result.artifact("electrical/runtime.png", "plot_png", "Quasi-static discharge at the duty cycle")
        if ops.motors:
            started_transient = time.perf_counter()
            trace = full_throttle_transient(project, routes, ops)
            result.metrics["transient_real_time_factor"] = 2.0 / (time.perf_counter() - started_transient)
            plotting.transient(trace, build / "full_throttle_transient.png")
            result.artifact("electrical/full_throttle_transient.png", "plot_png", "2 s full-throttle transient (constant motor-speed ramp)")
            result.metrics["transient_peak_motor_current"] = max(trace["motor_current"])
            result.metrics["transient_min_battery_voltage"] = min(trace["battery_voltage"])
    system.close()
    result.duration_s = time.time() - started
    result.write(build / "result.json")
    return result


def _assumptions(result: StageResult, vehicle: dict[str, Any], ops: OperatingPoints, estimated: dict[str, Any], routed: bool,
                 routes: dict[str, Any] | None = None) -> None:
    a = result.assume
    a("electrical", "Circuit solved by ngspice (shared library): operating points with `op`, the transient in lockstep with the "
                    "1 ms co-simulation clock; every component is a SPICE subcircuit generated from robot.yaml and the catalog.")
    a("electrical", "ESC H-bridge is a state-space averaged switch inside ngspice (no PWM ripple); its cycle-by-cycle current limit "
                    "acts with one co-simulation step (one 1 kHz PWM period) of latency. The PWM-switched bridge "
                    "(ElectricalSystem(esc_model='switched'), gate edges resolved by ngspice) runs at ~0.1x real time versus "
                    "~2x averaged on the robot; its step-mean motor torque was ~4 % below the averaged model at 1 kHz PWM, "
                    "50 % throttle, where the current ripple is large (L/R ~ T/2).")
    a("electrical", "Wire resistance from the AWG table with copper tempco 0.00393/K; contact resistance per mated pin from the catalog; PCB trace resistance not modeled.")
    if not routed:
        a("wiring", "No build/wiring/routes.json: wire lengths are 1.3 x straight-line connector distance (wire_length_estimated).")
    installed = bool(routes) and any(w.get("installed_ampacity") for w in (routes or {}).get("wires", {}).values())
    if installed:
        a("thermal", "Wire ratings are the wiring stage's installed ampacity (bundle derating NEC 310.15(C)(1), enclosure "
                     "temperature); wire thermal resistance calibrated to a 30 K rise at bundle factor x catalog ampacity; "
                     "contacts at their per-pin rating.")
    else:
        a("thermal", "Wire and contact thermal resistance calibrated to a 30 K rise at the catalog ampacity / per-pin rating; free air, no bundling derating.")
    a("thermal", f"Burst durations: {BURST_DURATION} s; continuous points use steady conductor temperatures.")
    a("connectors", "Burst rating = 2 x continuous unless the catalog provides current_rating_burst (XT60/XT30 datasheet ratio).")
    a("vehicle", f"Mass {vehicle['mass']:.2f} kg ({vehicle['mass_source']}), C_rr {vehicle['rolling_resistance']}, C_d {DRAG_COEFFICIENT}, "
                 f"frontal area {vehicle['frontal_area']:.4f} m^2 ({vehicle['frontal_area_source']} bounding box x {FRONTAL_FILL}), "
                 f"driven-wheel load fraction {vehicle['driven_axle_load_fraction']} (equal wheel-load estimate).")
    a("vehicle", f"Cruise {ops.cruise_speed} m/s -> motor {ops.motor_torque_cruise * 1e3:.2f} mN m at {ops.motor_omega_cruise:.0f} rad/s; "
                 f"traction-limited acceleration torque {ops.motor_torque_traction * 1e3:.1f} mN m.")
    a("loads", f"Servo loads use declared load_torque and operating_speed (zero when unspecified); servo stall = {SERVO_STALL_DEMAND} x stall torque demand at zero speed; computer activity {CRUISE_ACTIVITY} at cruise, 1.0 at peaks.")
    a("regulators", "Buck UVLO falling threshold = catalog v_in_min unless uvlo_falling is given; ESC linear BEC dropout 1.0 V and 1.5 x current-limit foldback (estimates).")
    a("fuses", "Fuse melting integral with tau set by a 1.25 x rating minimum melting current; contacts and fuse resistance temperature-independent.")
    a("thermal", "Operating points: power-net conductors at their electrothermal steady state (continuous points) or ambient (burst "
                 "points), solved inside ngspice; component temperatures at ambient. Signal-net conductors stay at ambient.")
    a("logic", f"Logic-level check uses {LOGIC_INPUT_RESISTANCE:.0f} ohm receiver input and {DRIVER_OUTPUT_RESISTANCE:.0f} ohm driver output (estimates).")
    a("runtime", "Runtime is quasi-static (DC points weighted by duty cycle); no regenerative braking; capacity independent of rate (no Peukert).")
    if estimated:
        a("parameters", "Estimated behavior parameters (no catalog value): " + json.dumps(estimated, default=_json_default))


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return str(value)


def _clean(value: Any) -> Any:
    """Recursively replace non-finite floats (e.g. a fuse that never melts) by None."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    return value

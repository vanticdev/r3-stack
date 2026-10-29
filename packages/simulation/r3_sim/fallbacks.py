"""FALLBACK models used only when the real domain models are not importable.

* :class:`FallbackElectricalSystem` stands in for ``r3_sim.electrical.ElectricalSystem``:
  LiPo OCV(SOC) + R0 + one RC pair, brushed DC motor driven by an ESC duty map,
  a linear BEC for the servo, and a *constant* regulated 5 V rail that is either
  on (battery above the buck's minimum input) or off. Wires, connectors and the
  fuse are ideal; there are no temperatures.
* :class:`FallbackCableSimulator` stands in for ``r3_wiring.CableSimulator``:
  wires are rigid straight polylines (from ``routes.json`` when present, else
  between connector endpoints) fixed to the chassis, and clearance is measured
  to the primitive shapes of moving bodies. There is no slack dynamics.

Both emit ``*_model_fallback`` warnings from the kernel. They exist so the
co-simulation loop can run end to end; results that depend on them are labeled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from r3_model.project import Project
from r3_model.units import to_si

from .geometry import quat_to_rotation


def _si(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        return to_si(value)
    except ValueError:
        return default


def _params(project: Project, cid: str) -> dict[str, Any]:
    ctype = project.catalog.component(project.spec.components[cid].type)
    params = dict(ctype.behavior.params) if ctype.behavior else {}
    params.update(project.spec.components[cid].params)
    return params


def _find(project: Project, prefix: str) -> list[str]:
    out = []
    for cid, comp in project.spec.components.items():
        ctype = project.catalog.component(comp.type)
        if ctype.behavior and ctype.behavior.model.startswith(prefix):
            out.append(cid)
    return out


# ---------------------------------------------------------------------- electrical
@dataclass
class _Battery:
    cells: int
    capacity_as: float
    r0: float
    r1: float
    c1: float
    ocv_soc: np.ndarray
    ocv_v: np.ndarray
    soc: float = 1.0
    v_rc: float = 0.0

    def ocv(self) -> float:
        return self.cells * float(np.interp(self.soc, self.ocv_soc, self.ocv_v))


class FallbackElectricalSystem:
    """FALLBACK electrical twin (see module docstring). Interface matches
    ``ElectricalSystem.step(dt, inputs) -> outputs`` from the pipeline contract."""

    is_fallback = True

    def __init__(self, project: Project, routes: dict[str, Any] | None = None, initial_soc: float = 1.0) -> None:
        self.project = project
        batteries = _find(project, "battery")
        self.battery_id = batteries[0] if batteries else "battery"
        bp = _params(project, self.battery_id) if batteries else {}
        cells = int(bp.get("cells_series", 2))
        curve = np.asarray(bp.get("ocv_curve", [[0, 3.3], [1, 4.2]]), dtype=float)
        rc = bp.get("rc_pair", {}) or {}
        self.battery = _Battery(
            cells=cells, capacity_as=_si(bp.get("capacity"), 18000.0),
            r0=cells * _si(bp.get("r_internal_per_cell"), 0.004), r1=cells * _si(rc.get("r"), 0.0025),
            c1=_si(rc.get("c"), 2000.0) / cells, ocv_soc=curve[:, 0], ocv_v=curve[:, 1], soc=float(initial_soc))
        motors = _find(project, "motor.dc")
        escs = _find(project, "esc")
        servos = _find(project, "servo")
        computers = _find(project, "load.computer")
        self.motor_id = motors[0] if motors else "motor"
        self.esc_id = escs[0] if escs else "esc"
        self.servo_id = servos[0] if servos else "servo"
        self.computer_id = computers[0] if computers else "pi"
        mp = _params(project, self.motor_id) if motors else {}
        kv = float(mp.get("kv_rpm_per_v", 2000)) * 2 * math.pi / 60.0
        self.ke = 1.0 / kv  # V s/rad == N m/A
        self.r_motor = _si(mp.get("resistance"), 0.1)
        self.l_motor = _si(mp.get("inductance"), 60e-6)
        self.brush_drop = _si(mp.get("brush_drop"), 0.2)
        self.friction_torque = self.ke * _si(mp.get("no_load_current"), 1.5)
        ep = _params(project, self.esc_id) if escs else {}
        self.esc_neutral = _si(ep.get("neutral_pulse"), 1500e-6) * 1e6
        self.esc_min = _si(ep.get("input_min_pulse"), 1000e-6) * 1e6
        self.esc_max = _si(ep.get("input_max_pulse"), 2000e-6) * 1e6
        self.esc_deadband = float(ep.get("deadband", 0.03))
        self.esc_timeout = _si(ep.get("signal_timeout"), 0.5)
        self.esc_r_on = _si(ep.get("r_on"), 0.002)
        self.esc_quiescent = _si(ep.get("quiescent_current"), 0.04)
        self.esc_reverse_delay = _si(ep.get("reverse_delay"), 0.1)
        self.bec_voltage = _si(ep.get("bec_voltage"), 6.0)
        sp = _params(project, self.servo_id) if servos else {}
        self.servo_stall_current = _si(sp.get("stall_current_at_6v"), 2.5)
        self.servo_stall_torque = _si(sp.get("stall_torque_at_6v"), 2.0)
        self.servo_idle = _si(sp.get("idle_current"), 0.01)
        cp = _params(project, self.computer_id) if computers else {}
        self.pi_idle = _si(cp.get("power_idle"), 3.0)
        self.pi_active = _si(cp.get("power_active"), 6.0)
        self.pi_brownout = _si(cp.get("brownout_voltage"), 4.63)
        # Regulator: the first board with a buck regulator.
        self.regulator: dict[str, Any] = {}
        self.board_id = None
        for cid in _find(project, "board"):
            reg = _params(project, cid).get("regulator")
            if reg:
                self.regulator, self.board_id = reg, cid
                break
        self.v_rail = _si(self.regulator.get("v_out"), 5.0)
        self.v_in_min = _si(self.regulator.get("v_in_min"), 6.0)
        eff = self.regulator.get("efficiency", [[0, 0.9], [10, 0.9]])
        self.eff_i = np.asarray(eff, dtype=float)[:, 0]
        self.eff_v = np.asarray(eff, dtype=float)[:, 1]
        # Constant 5 V loads other than the computer (receivers, GPS) from their catalog current.
        self.rail_loads = 0.0
        for cid in _find(project, "receiver") + _find(project, "gps"):
            p = _params(project, cid)
            self.rail_loads += _si(p.get("current_active", p.get("current")), 0.05)
        self.i_motor = 0.0
        self.i_battery = 0.0
        self.mode = "neutral"
        self.no_signal_time = 1e9
        self.neutral_time = 0.0
        self.saw_neutral_after_brake = False

    def _throttle(self, pulse: float) -> float:
        if pulse > self.esc_neutral:
            d = (pulse - self.esc_neutral) / (self.esc_max - self.esc_neutral)
        else:
            d = (pulse - self.esc_neutral) / (self.esc_neutral - self.esc_min)
        d = max(-1.0, min(1.0, d))
        return 0.0 if abs(d) < self.esc_deadband else d

    def step(self, dt: float, inputs: dict[str, Any]) -> dict[str, Any]:
        battery = self.battery
        esc_in = inputs.get(self.esc_id, {})
        pulse = float(esc_in.get("pulse_us") or 0.0)
        omega = float(inputs.get(self.motor_id, {}).get("omega", 0.0))
        servo_in = inputs.get(self.servo_id, {})
        activity = float(inputs.get(self.computer_id, {}).get("activity", 0.5))

        valid = self.esc_min * 0.8 <= pulse <= self.esc_max * 1.2
        self.no_signal_time = 0.0 if valid else self.no_signal_time + dt
        d = self._throttle(pulse) if valid else 0.0
        if self.no_signal_time > self.esc_timeout:
            d = 0.0
        # Forward / brake / reverse state machine ("Fwd/Br/Rev"): reverse stick while
        # rolling forward brakes; neutral then reverse again at standstill reverses.
        if d > 0:
            self.mode, self.saw_neutral_after_brake = "forward", False
        elif d < 0:
            reverse_ok = self.saw_neutral_after_brake and abs(omega) < 1.0 and self.neutral_time >= self.esc_reverse_delay
            self.mode = "reverse" if self.mode == "reverse" or reverse_ok else "brake"
        else:
            if self.mode in ("brake", "reverse"):
                self.saw_neutral_after_brake = True
            self.mode = "neutral"
        self.neutral_time = self.neutral_time + dt if d == 0 else 0.0

        v_term = battery.ocv() - battery.v_rc - self.i_battery * battery.r0
        # Motor: L di/dt = V_m - R i - Ke w - V_brush sign(i); exact exponential update.
        r_total = self.r_motor + 2 * self.esc_r_on
        if self.mode in ("forward", "reverse"):
            v_m = d * v_term
            i_ss = (v_m - self.ke * omega - self.brush_drop * math.tanh(self.i_motor / 0.5)) / r_total
            battery_share = abs(d)
        elif self.mode == "brake":
            i_ss = -abs(d) * self.ke * omega / r_total  # PWM'd short-circuit brake, no battery current
            v_m = 0.0
            battery_share = 0.0
        else:
            i_ss, v_m, battery_share = 0.0, 0.0, 0.0
        decay = math.exp(-r_total * dt / self.l_motor)
        self.i_motor = i_ss + (self.i_motor - i_ss) * decay
        if self.mode == "neutral" and abs(self.i_motor) < 1e-3:
            self.i_motor = 0.0
        torque = self.ke * self.i_motor - self.friction_torque * math.tanh(omega / 5.0)

        # Servo through the linear BEC: input current = output current.
        horn_torque = abs(float(servo_in.get("horn_torque", 0.0)))
        i_servo = self.servo_idle + self.servo_stall_current * min(1.0, horn_torque / max(self.servo_stall_torque, 1e-6))
        if not servo_in.get("pulse_us"):
            i_servo = self.servo_idle
        bec_v = min(self.bec_voltage, max(0.0, v_term - 0.3))

        # 5 V rail (constant) via the buck: P_in = P_out / eta.
        rail_on = v_term >= self.v_in_min
        p_pi = self.pi_idle + max(0.0, min(1.0, activity)) * (self.pi_active - self.pi_idle)
        i_rail = p_pi / self.v_rail + self.rail_loads
        eta = float(np.interp(i_rail, self.eff_i, self.eff_v))
        i_hat = (i_rail * self.v_rail / eta) / max(v_term, 1.0) + 0.002 if rail_on else 0.0
        i_esc = battery_share * self.i_motor + self.esc_quiescent + i_servo
        self.i_battery = i_esc + i_hat

        # Battery: SOC -= I dt / Q; RC pair dv/dt = I/C - v/(R C).
        battery.soc = max(0.0, battery.soc - self.i_battery * dt / battery.capacity_as)
        tau = battery.r1 * battery.c1
        battery.v_rc = self.i_battery * battery.r1 + (battery.v_rc - self.i_battery * battery.r1) * math.exp(-dt / tau)
        v_term = battery.ocv() - battery.v_rc - self.i_battery * battery.r0
        v5 = self.v_rail if rail_on else 0.0
        return {
            self.battery_id: {"v_terminal": v_term, "current": self.i_battery, "soc": battery.soc, "v_ocv": battery.ocv(),
                              "cell_voltage": v_term / battery.cells},
            self.motor_id: {"torque": torque, "current": self.i_motor, "voltage": v_m},
            self.esc_id: {"duty": d, "mode": self.mode, "current_in": i_esc, "bec_voltage": bec_v},
            self.servo_id: {"voltage": bec_v, "current": i_servo},
            self.computer_id: {"powered": v5 >= self.pi_brownout, "v_in": v5, "power": p_pi},
            **({self.board_id: {"v_in": v_term, "current_in": i_hat}} if self.board_id else {}),
            "_nets": {"VBAT": v_term, "5V": v5, "3V3": 3.3 if v5 >= self.pi_brownout else 0.0, "BEC_6V": bec_v},
            "_branches": {},
            "_brownouts": [],
            "_temperatures": {},
        }


# ---------------------------------------------------------------------- cables
@dataclass
class CableReport:
    min_clearance: dict[str, tuple[float, str]] = field(default_factory=dict)
    diagnostics: list[Any] = field(default_factory=list)


def _segment_points(path: list[np.ndarray], spacing: float = 0.005) -> np.ndarray:
    points = [path[0]]
    for a, b in zip(path[:-1], path[1:]):
        n = max(1, int(np.linalg.norm(b - a) / spacing))
        for k in range(1, n + 1):
            points.append(a + (b - a) * k / n)
    return np.asarray(points)


class FallbackCableSimulator:
    """FALLBACK cable model: rigid wire polylines fixed to the chassis frame."""

    is_fallback = True

    def __init__(self, project: Project, routes: dict[str, Any] | None = None, moving_geometry: dict[str, dict[str, Any]] | None = None) -> None:
        self.project = project
        self.moving_geometry = moving_geometry or {}
        self.estimated_endpoints: list[str] = []
        self.paths = self._paths_from_routes(routes) if routes and routes.get("wires") else self._paths_from_endpoints()
        self.wire_ids: list[str] = []
        self.wire_radius: list[float] = []
        clouds = []
        for wire_id, path in self.paths.items():
            pts = _segment_points([np.asarray(p, dtype=float) for p in path])
            clouds.append(pts)
            self.wire_ids.extend([wire_id] * len(pts))
            self.wire_radius.extend([self._wire_radius(wire_id)] * len(pts))
        self.points = np.vstack(clouds) if clouds else np.zeros((0, 3))
        self.point_wire = np.asarray(self.wire_ids)
        self.radius = np.asarray(self.wire_radius)

    def _wire_radius(self, wire_id: str) -> float:
        for harness in self.project.spec.electrical.harnesses:
            for wire in harness.wires:
                if wire.id == wire_id:
                    try:
                        return self.project.catalog.wire(wire.awg).outer_diameter / 2.0
                    except KeyError:
                        return 0.001
        return 0.001

    @staticmethod
    def _paths_from_routes(routes: dict[str, Any]) -> dict[str, list[list[float]]]:
        return {wid: w["path"] for wid, w in routes["wires"].items() if w.get("path")}

    def _paths_from_endpoints(self) -> dict[str, list[list[float]]]:
        spec, graph = self.project.spec, self.project.graph
        known: dict[str, np.ndarray] = {}
        for harness in spec.electrical.harnesses:
            for name, conn in harness.connectors.items():
                ref = f"{harness.id}.{name}"
                if conn.position is not None:
                    known[ref] = np.asarray(conn.position, dtype=float)
                elif conn.mates and conn.mates in graph.endpoints:
                    known[ref] = graph.endpoints[conn.mates].position
            for sid, splice in harness.splices.items():
                known[f"{harness.id}.{sid}"] = np.asarray(splice.position, dtype=float)
        # Free connectors mated by another harness: midpoint of both harnesses' known centroids.
        mates: dict[str, str] = {}
        for harness in spec.electrical.harnesses:
            for name, conn in harness.connectors.items():
                if conn.mates and conn.mates not in graph.endpoints:
                    mates[f"{harness.id}.{name}"] = conn.mates
                    mates[conn.mates] = f"{harness.id}.{name}"

        def centroid(harness_id: str) -> np.ndarray | None:
            pts = [p for ref, p in known.items() if ref.split(".")[0] == harness_id]
            return np.mean(pts, axis=0) if pts else None

        for _ in range(3):
            for a, b in mates.items():
                if a in known and b not in known:
                    known[b] = known[a]
                elif a not in known and b not in known:
                    ca, cb = centroid(a.split(".")[0]), centroid(b.split(".")[0])
                    if ca is not None and cb is not None:
                        known[a] = known[b] = 0.5 * (ca + cb)
                        self.estimated_endpoints.extend([a, b])
        paths = {}
        for harness in spec.electrical.harnesses:
            for wire in harness.wires:
                ends = []
                for ref in (wire.from_, wire.to):
                    key = f"{harness.id}.{ref}" if ref in harness.splices else f"{harness.id}.{ref.split('.')[0]}"
                    ends.append(known.get(key))
                if all(e is not None for e in ends):
                    paths[wire.id] = [ends[0].tolist(), ends[1].tolist()]
        return paths

    def step(self, dt: float, chassis: dict[str, Any], moving: dict[str, dict[str, Any]]) -> CableReport:
        report = CableReport()
        if len(self.points) == 0:
            return report
        rotation = quat_to_rotation(chassis["quat"])
        world = self.points @ rotation.T + np.asarray(chassis["pos"])
        best = np.full(len(self.points), np.inf)
        best_part = np.full(len(self.points), "", dtype=object)
        for part, pose in moving.items():
            geo = self.moving_geometry.get(part)
            if not geo:
                continue
            r_part = quat_to_rotation(pose["quat"])
            center = np.asarray(pose["pos"]) + r_part @ np.asarray(geo["center_offset"])
            rel = world - center
            if geo["shape"] == "cylinder":
                axis = r_part @ np.asarray(geo["axis_vector"])
                t = rel @ axis
                radial = np.linalg.norm(rel - np.outer(t, axis), axis=1) - geo["size"][0] / 2.0
                axial = np.abs(t) - geo["size"][1] / 2.0
                outside = np.sqrt(np.maximum(radial, 0) ** 2 + np.maximum(axial, 0) ** 2)
                dist = np.where((radial <= 0) & (axial <= 0), np.maximum(radial, axial), outside)
            else:
                local = rel @ (r_part @ np.asarray(geo["rotation"]))
                q = np.abs(local) - np.asarray(geo["size"]) / 2.0
                dist = np.linalg.norm(np.maximum(q, 0), axis=1) + np.minimum(np.max(q, axis=1), 0)
            dist = dist - self.radius
            closer = dist < best
            best[closer] = dist[closer]
            best_part[closer] = part
        for wire_id in self.paths:
            mask = self.point_wire == wire_id
            if not mask.any():
                continue
            idx = int(np.argmin(np.where(mask, best, np.inf)))
            report.min_clearance[wire_id] = (float(best[idx]), str(best_part[idx]))
        return report

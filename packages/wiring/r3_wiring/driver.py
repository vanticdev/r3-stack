"""Standalone vibration / bump / steering test drive on the MuJoCo cable shaker.

The harnesses are MuJoCo elastic cables on the robot's bodies (chassis on a
shaker table, :class:`r3_wiring.cable_sim.CableSimulator`). Load case
(chassis frame, world-vertical excitation):

* continuous 0.5 g, 25 Hz sinusoidal vertical vibration (motor/terrain);
* one 3 g half-sine bump of 20 ms, timed to coincide with full left lock;
* lock-to-lock steering as a triangle wave (Ackermann joint angles from
  ``mechanical.transmissions``), wheels spinning at ``speed``.

Reports the minimum clearance of each wire to any moving part over the run and
whether any cable sags into a moving part (clearance < 0).

Run: ``python -m r3_wiring.driver examples/robots/rc-car``
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from r3_model.project import Project

from .bodies import Kinematics
from .cable_sim import CableSimulator
from .geometry import matrix_to_quat
from .obstacles import ObstacleSet

G = 9.80665


@dataclass
class DriveProfile:
    duration: float = 2.6
    dt: float = 0.001
    vibration_g: float = 0.5
    vibration_hz: float = 25.0
    bump_g: float = 3.0
    bump_duration: float = 0.02
    steer_period: float = 1.0  # lock-to-lock-and-back, s (servo ~0.15 s/60 deg allows ~0.3 s)
    speed: float = 2.0  # m/s wheel surface speed

    def steer_fraction(self, t: float) -> float:
        """Triangle wave in [-1, 1]: 0 at t=0, +1 at T/4, -1 at 3T/4."""
        phase = (t / self.steer_period + 0.25) % 1.0
        return 4 * phase - 1 if phase < 0.5 else 3 - 4 * phase

    @property
    def bump_time(self) -> float:
        return 1.25 * self.steer_period  # full left lock

    def accel_z(self, t: float) -> float:
        a = self.vibration_g * G * math.sin(2 * math.pi * self.vibration_hz * t)
        if self.bump_time <= t <= self.bump_time + self.bump_duration:
            a += self.bump_g * G * math.sin(math.pi * (t - self.bump_time) / self.bump_duration)
        return a


@dataclass
class DriveResult:
    min_clearance: dict[str, dict[str, Any]]
    sags_into_moving: dict[str, bool]
    below_requirement: dict[str, bool]
    worst: dict[str, Any]
    snapshot: dict[str, np.ndarray]
    snapshot_time: float
    timeline: dict[str, np.ndarray]
    requirement: float
    max_speed: float
    max_strain: float  # largest pin give (m)
    profile: DriveProfile = field(default_factory=DriveProfile)
    assumptions: list[tuple[str, str]] = field(default_factory=list)
    # wire -> {"end_tension": [start, end] peak N, "time": [s, s]}: cable force at the terminations
    terminal_loads: dict[str, dict[str, Any]] = field(default_factory=dict)
    cap_clearance: dict[str, dict[str, Any]] = field(default_factory=dict)  # printed screw cap -> worst cable surface gap

    def summary(self) -> dict[str, Any]:
        return {"requirement": self.requirement, "worst": self.worst, "snapshot_time": self.snapshot_time,
                "cap_clearance": self.cap_clearance,
                "max_segment_speed": self.max_speed, "max_pin_error_m": self.max_strain,
                "profile": self.profile.__dict__, "terminal_loads": self.terminal_loads,
                "wires": {w: {**v, "sags_into_moving": self.sags_into_moving[w],
                              "below_requirement": self.below_requirement[w]} for w, v in self.min_clearance.items()}}


def cap_obstacles(project: Project, obstacles: ObstacleSet | None) -> list[Any]:
    """Obstacles of the printed screw caps (components with a ``cap_for`` interface)."""
    if obstacles is None:
        return []
    out = []
    for cid, comp in project.spec.components.items():
        ctype = project.catalog.component(comp.type)
        if any("cap_for" in (i.params or {}) for i in ctype.interfaces.values()):
            obstacle = obstacles.by_component(cid)
            if obstacle is not None:
                out.append(obstacle)
    return out


def wire_radii(project: Project, routes: dict[str, Any]) -> dict[str, float]:
    radii = {}
    for harness in project.spec.electrical.harnesses:
        for wire in harness.wires:
            radii[wire.id] = float(project.catalog.wire(wire.awg).outer_diameter) / 2
    return radii


def track_caps(positions: dict[str, np.ndarray], caps: list[Any], radii: dict[str, float], t: float,
               out: dict[str, dict[str, Any]]) -> None:
    """Worst surface gap between each cap and every simulated cable (centerline distance minus wire radius)."""
    for cap in caps:
        lo, hi = cap.shape.aabb()
        for wire, points in positions.items():
            if not len(points):
                continue
            pts = np.atleast_2d(points)
            r = radii.get(wire, 0.0)
            # Distance to the cap's bounding box bounds the distance to the cap from below: only near points get the mesh SDF.
            box_gap = np.linalg.norm(np.maximum(np.maximum(lo - pts, pts - hi), 0.0), axis=1) - r
            near = box_gap < 2e-3
            gap = float(box_gap.min())
            if near.any():
                gap = min(float(cap.shape.sdf_bounded(pts[near], 0.05).min()) - r, float(box_gap[~near].min()) if (~near).any() else np.inf)
            if cap.component not in out or gap < out[cap.component]["min_gap"]:
                out[cap.component] = {"min_gap": gap, "wire": wire, "time": t}


def moving_poses(project: Project, kin: Kinematics, steer: float, spin: float) -> dict[str, dict[str, list[float]]]:
    """World poses of every component on a jointed body (chassis at the origin)."""
    q = kin.ackermann(steer)
    for joint in kin.joints.values():
        if joint.type == "continuous":
            q[joint.id] = spin
    out = {}
    for component in project.spec.components:
        body = kin.body_of(component)
        if not kin.is_moving(body):
            continue
        rotation, translation = kin.body_transform(body, q)
        p0, r0 = project.graph.component_pose(component)
        out[component] = {"pos": (rotation @ p0 + translation).tolist(), "quat": matrix_to_quat(rotation @ r0).tolist()}
    return out


def drive_joints(kin: Kinematics, steer: float, spin: float) -> dict[str, float]:
    """Joint values for a centre steer angle (Ackermann) and a wheel spin angle."""
    q = kin.ackermann(steer)
    for joint in kin.joints.values():
        if joint.type == "continuous":
            q[joint.id] = spin
    return q


def run_drive(project: Project, routes: dict[str, Any], obstacles: ObstacleSet | None = None,
              profile: DriveProfile | None = None, simulator: CableSimulator | None = None) -> DriveResult:
    profile = profile or DriveProfile()
    sim = simulator or CableSimulator(project, routes, obstacles, timestep=profile.dt)
    kin = sim.kin
    max_steer = kin.steering_limit()
    wheel_radius = float(project.spec.software.config.get("wheel_radius_m", 0.055))
    requirement = float(project.spec.requirements.min_wire_clearance_moving)
    steps = int(round(profile.duration / profile.dt))
    times, clearance, accel, steer_log = [], [], [], []
    pre_bump: dict[str, np.ndarray] | None = None
    best_sag, snapshot, snapshot_time = -math.inf, sim.positions(), 0.0
    max_speed = max_strain = 0.0
    bundles = len(sim.plan.bundles)
    peak_tension, peak_time = np.zeros((bundles, 2)), np.zeros((bundles, 2))
    caps = cap_obstacles(project, sim.obstacles if hasattr(sim, "obstacles") else obstacles)
    radii = wire_radii(project, routes)
    cap_clearance: dict[str, dict[str, Any]] = {}
    track_caps(sim.positions(), caps, radii, 0.0, cap_clearance)
    for k in range(steps):
        t = (k + 1) * profile.dt
        steer = max_steer * profile.steer_fraction(t)
        a_z = profile.accel_z(t)
        joints = drive_joints(kin, steer, profile.speed / wheel_radius * t)
        report = sim.step(profile.dt, {"pos": [0, 0, 0], "quat": [1, 0, 0, 0], "lin_acc": [0.0, 0.0, a_z],
                                       "ang_vel": [0, 0, 0], "ang_acc": [0, 0, 0]}, joints=joints)
        times.append(t)
        clearance.append(min(d for d, _ in report.min_clearance.values()))
        accel.append(a_z)
        steer_log.append(steer)
        tension = sim.monitor.end_tension()
        if tension.size:
            better = tension > peak_tension
            peak_tension[better], peak_time[better] = tension[better], t
        max_speed = max(max_speed, report.max_speed)
        max_strain = max(max_strain, report.max_strain)
        if caps and k % 25 == 0:
            track_caps(sim.positions(), caps, radii, t, cap_clearance)
        if pre_bump is None and t >= profile.bump_time - profile.dt:
            pre_bump = sim.positions()
        if pre_bump is not None and profile.bump_time <= t <= profile.bump_time + 0.08:
            current = sim.positions()
            sag = float(np.mean(np.concatenate([pre_bump[w][:, 2] - current[w][:, 2] for w in current]))) if current else 0.0
            if sag > best_sag:
                best_sag, snapshot, snapshot_time = sag, current, t
    history = sim.min_history
    min_clearance = {w: {"min_distance": d, "component": c, "time": tm} for w, (d, c, tm) in history.items()}
    worst_wire = min(history, key=lambda w: history[w][0]) if history else ""
    worst = {"wire": worst_wire, "min_distance": history[worst_wire][0] if worst_wire else math.inf,
             "component": history[worst_wire][1] if worst_wire else "",
             "time": history[worst_wire][2] if worst_wire else 0.0}
    index = {b.id: i for i, b in enumerate(sim.plan.bundles)}
    terminal_loads = {wire: {"end_tension": peak_tension[index[b]].tolist(), "time": peak_time[index[b]].tolist()}
                      for wire, b in sim.plan.wire_bundle.items()}
    return DriveResult(min_clearance, {w: v["min_distance"] < 0 for w, v in min_clearance.items()},
                       {w: v["min_distance"] < requirement for w, v in min_clearance.items()}, worst, snapshot,
                       snapshot_time, {"time": np.asarray(times), "min_clearance": np.asarray(clearance),
                                       "accel_z": np.asarray(accel), "steer": np.asarray(steer_log)},
                       requirement, max_speed, max_strain, profile, list(sim.plan.assumptions), terminal_loads, cap_clearance)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cable slack vibration/steering test drive (MuJoCo cable shaker)")
    parser.add_argument("project", type=Path)
    parser.add_argument("--duration", type=float, default=DriveProfile.duration)
    args = parser.parse_args(argv)
    project = Project.load(args.project)
    routes = project.read_build_json("wiring/routes.json")
    if routes is None:
        from .stage import route_and_check

        routes = route_and_check(project)["routes"]
    result = run_drive(project, routes, profile=DriveProfile(duration=args.duration))
    print(json.dumps(result.summary(), indent=2, default=float))
    print("cable sags into a moving part:", any(result.sags_into_moving.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

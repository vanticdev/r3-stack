"""Steering kinematics from verified geometry and CAD motion sweeps.

The linkage (servo axis, horn balls, tie rods, knuckle balls, kingpins) is read
from the *verified* interfaces (measured axes and sphere centers), solved by
:mod:`r3_onshape.kinematics`, and the solved transforms are applied to the
tessellated meshes. At every sample the moving subtrees are checked against all
static parts with a clearance margin, and against each other for interference.
Wheel-spin sweeps rotate each wheel's rigid subtree (including D-flat axles).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from r3_model.diagnostics import Diagnostic, StageResult
from r3_model.project import Project
from r3_model.units import to_si

from .fit import VOLUME_TOL, FitReport, Link, rotor_crossing_links, unit
from .kinematics import Linkage, Side, rotation, steering_table
from .model import GeometryModel

MARGIN = 5e-4  # m, clearance margin moving vs static
SWEEP_SAMPLES = 11
SPIN_ANGLES = [0.0, 30.0, 60.0, 90.0, 120.0, 150.0]


def servo_travel(project: Project) -> tuple[float, float]:
    """Horn travel from the software steering end points and the servo's pulse/angle map."""
    spec = project.spec
    steering = next(t for t in spec.mechanical.transmissions if t.type == "ackermann_steering")
    behavior = project.catalog.component(spec.components[steering.actuator].type).behavior
    params = behavior.params if behavior else {}
    span = to_si(params.get("max_pulse", 2.5e-3)) - to_si(params.get("min_pulse", 0.5e-3))
    travel = to_si(params.get("travel", math.radians(270)))
    pulses = spec.software.config.get("steering_pulse_us", {"left": 1878, "center": 1500, "right": 1122})
    per_us = travel / (span * 1e6)
    return ((pulses["right"] - pulses["center"]) * per_us, (pulses["left"] - pulses["center"]) * per_us)


def _subtree(start: str, links: list[Link], cut: set[str]) -> set[str]:
    """Parts reachable from ``start`` without crossing links whose ``via`` is in ``cut``."""
    adjacency: dict[str, set[str]] = {}
    for link in links:
        if link.via in cut:
            continue
        adjacency.setdefault(link.a, set()).add(link.b)
        adjacency.setdefault(link.b, set()).add(link.a)
    seen, stack = {start}, [start]
    while stack:
        for neighbor in adjacency.get(stack.pop(), ()):
            if neighbor not in seen:
                seen.add(neighbor)
                stack.append(neighbor)
    return seen


def _matrix(rot: np.ndarray, center: np.ndarray, translate: np.ndarray | None = None) -> np.ndarray:
    """3x4 affine: x -> rot (x - center) + center (+ translate)."""
    m = np.zeros((3, 4))
    m[:, :3] = rot
    m[:, 3] = center - rot @ center + (translate if translate is not None else 0.0)
    return m


def _align(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Minimal rotation taking unit vector a onto unit vector b."""
    a, b = unit(a), unit(b)
    v = np.cross(a, b)
    s, c = float(np.linalg.norm(v)), float(a @ b)
    if s < 1e-12:
        return np.eye(3)
    return rotation(v / s, math.atan2(s, c))


class Motion:
    def __init__(self, project: Project, model: GeometryModel, fit: FitReport, result: StageResult) -> None:
        self.project = project
        self.spec = project.spec
        self.model = model
        self.fit = fit
        self.result = result
        crossing = rotor_crossing_links(self.spec, fit.links)
        self.links = [l for i, l in enumerate(fit.links) if i not in crossing]
        self.partners = {(l.a, l.b) for l in fit.links} | {(l.b, l.a) for l in fit.links}
        for interface in self.spec.mechanical.interfaces:
            for thrust in interface.params.get("thrust", []) or []:
                for part in interface.parts:
                    self.partners |= {(part, thrust), (thrust, part)}
            if interface.kind == "drive_flat" and interface.params.get("rotating_bore"):
                a, b = interface.parts
                self.partners |= {(a, b), (b, a)}

    # ------------------------------------------------------------------ steering
    def linkage(self) -> tuple[Linkage, dict[str, Any]] | None:
        spec = self.spec
        steering = next((t for t in spec.mechanical.transmissions if t.type == "ackermann_steering"), None)
        if steering is None:
            return None
        axes = self.fit.interface_axes
        interfaces = {i.id: i for i in spec.mechanical.interfaces}
        spline = next((i for i in spec.mechanical.interfaces if i.kind == "spline" and steering.actuator in i.parts), None)
        if spline is None or spline.id not in axes:
            self.result.add(Diagnostic.error("steering_linkage_unverified", "servo spline interface not verified", subjects=[steering.id]))
            return None
        servo_point, servo_axis = np.asarray(axes[spline.id]["point"]), unit(axes[spline.id]["axis"])
        if float(servo_axis @ np.array([0.0, 0.0, 1.0])) < 0:
            servo_axis = -servo_axis
        joints = {j.id: j for j in spec.mechanical.joints}
        sides: list[Side] = []
        groups: dict[str, set[str]] = {}
        ball_joints = [i for i in spec.mechanical.interfaces if i.kind == "ball_joint"]
        cut_all = {i.id for i in ball_joints} | {spline.id} | {joints[j].interface for j in steering.joints}
        horn_group = _subtree(spline.parts[1], self.links, cut_all)
        groups["horn"] = horn_group
        for jid in steering.joints:
            joint = joints[jid]
            kingpin = interfaces[joint.interface]
            if kingpin.id not in axes:
                self.result.add(Diagnostic.error("steering_linkage_unverified", f"{jid}: kingpin interface not verified", subjects=[jid]))
                return None
            knuckle_group = _subtree(joint.child, self.links, cut_all)
            groups[jid] = knuckle_group
            knuckle_ball = next((b for b in ball_joints if b.parts[0] in knuckle_group), None)
            if knuckle_ball is None or knuckle_ball.id not in axes:
                self.result.add(Diagnostic.error("steering_linkage_unverified", f"{jid}: no verified ball joint on the knuckle", subjects=[jid]))
                return None
            rod = knuckle_ball.parts[1]
            horn_ball = next((b for b in ball_joints if b.parts[1] == rod and b.id != knuckle_ball.id and b.parts[0] in horn_group), None)
            if horn_ball is None or horn_ball.id not in axes:
                self.result.add(Diagnostic.error("steering_linkage_unverified", f"{jid}: tie rod {rod} has no verified horn ball",
                                                 subjects=[jid, rod]))
                return None
            groups[f"rod:{jid}"] = _subtree(rod, self.links, cut_all)
            k_axis = unit(axes[kingpin.id]["axis"])
            k_axis = k_axis if float(k_axis @ unit(joint.axis)) >= 0 else -k_axis
            sides.append(Side(name=jid, joint=jid, kingpin_point=np.asarray(axes[kingpin.id]["point"]), kingpin_axis=k_axis,
                              knuckle_ball=np.asarray(axes[knuckle_ball.id]["point"]), horn_ball=np.asarray(axes[horn_ball.id]["point"]),
                              limits=(float(joint.limits[0]), float(joint.limits[1])) if joint.limits else (-math.pi, math.pi)))
        params = steering.params
        linkage = Linkage(servo_point=servo_point, servo_axis=servo_axis, sides=sides, wheelbase=to_si(params.get("wheelbase", 0.26)),
                          track=to_si(params.get("kingpin_track", 0.135)))
        return linkage, groups

    def steering(self) -> dict[str, Any]:
        built = self.linkage()
        if built is None:
            return {}
        linkage, groups = built
        travel = servo_travel(self.project)
        table = steering_table(linkage, travel, samples=41)
        steering_id = next(t.id for t in self.spec.mechanical.transmissions if t.type == "ackermann_steering")
        out: dict[str, Any] = {"transmission": steering_id, "servo_travel_rad": list(travel), "sides": {}, "table": [],
                               "servo_axis": {"point": linkage.servo_point.tolist(), "axis": linkage.servo_axis.tolist()}}
        for side in linkage.sides:
            solution = table["solutions"][side.joint]
            angles = np.array(solution.phi, dtype=float)
            finite = angles[np.isfinite(angles)]
            info = {"status": solution.status, "failed_at_servo_rad": solution.failed_at, "rod_length_m": side.rod_length,
                    "min_transmission_angle_deg": float(np.nanmin(solution.transmission_deg)) if finite.size else None,
                    "wheel_angle_range_rad": [float(finite.min()), float(finite.max())] if finite.size else None,
                    "limits_rad": list(side.limits), "kingpin": side.kingpin_point.tolist(), "knuckle_ball": side.knuckle_ball.tolist(),
                    "horn_ball": side.horn_ball.tolist()}
            out["sides"][side.joint] = info
            if solution.status == "lockup":
                self.result.add(Diagnostic.error("steering_range_unreachable", f"{side.joint}: linkage locks up at servo "
                                                 f"{math.degrees(solution.failed_at):.1f} deg (tie rod cannot reach)",
                                                 subjects=[side.joint], data={"servo_angle": solution.failed_at}))
            elif solution.status == "toggle":
                self.result.add(Diagnostic.error("steering_linkage_toggle", f"{side.joint}: tie rod toggles (transmission angle "
                                                 f"{info['min_transmission_angle_deg']:.1f} deg) within servo travel",
                                                 subjects=[side.joint], data={"servo_angle": solution.failed_at,
                                                                              "transmission_angle_deg": info["min_transmission_angle_deg"]}))
            if finite.size:
                lo, hi = side.limits
                if finite.min() < lo - 1e-6 or finite.max() > hi + 1e-6:
                    self.result.add(Diagnostic.error("steering_range_unreachable", f"{side.joint}: servo travel drives the knuckle to "
                                                     f"[{finite.min():.3f}, {finite.max():.3f}] rad, beyond its limits [{lo}, {hi}] rad",
                                                     subjects=[side.joint], data={"range": [float(finite.min()), float(finite.max())],
                                                                                  "limits": [lo, hi]}))
                elif max(abs(finite.min()), abs(finite.max())) < 0.9 * max(abs(lo), abs(hi)):
                    self.result.add(Diagnostic.warning("steering_range_unreachable", f"{side.joint}: full servo travel reaches only "
                                                       f"{max(abs(finite.min()), abs(finite.max())):.3f} rad of the {max(abs(lo), abs(hi)):.3f} rad limit",
                                                       subjects=[side.joint]))
        for row in table["rows"]:
            out["table"].append({k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in row.items()})
        # Linearized ratio at center and Ackermann at full lock.
        rows = table["rows"]
        full = rows[-1]
        out["ackermann_pct_full_lock"] = full.get("ackermann_pct")
        center = min(range(len(rows)), key=lambda i: abs(rows[i]["servo_rad"]))
        if 0 < center < len(rows) - 1:
            d_servo = rows[center + 1]["servo_rad"] - rows[center - 1]["servo_rad"]
            out["center_ratio"] = {side.joint: (rows[center + 1][side.joint] - rows[center - 1][side.joint]) / d_servo for side in linkage.sides}
        out["sweep"] = self.sweep(linkage, groups, travel)
        return out

    # ------------------------------------------------------------------- sweeps
    def _transformed(self, names: set[str], matrix: np.ndarray):
        return {n: self.model[n].manifold().transform(matrix) for n in names if n in self.model.parts}

    def _check(self, moving: dict[str, Any], static: list[str], margin: float, label: str, sample: dict[str, float],
               failures: list[dict[str, Any]], min_gap: dict[str, float]) -> None:
        for name, solid in moving.items():
            box = solid.bounding_box()
            lo, hi = np.array(box[:3]) - margin, np.array(box[3:]) + margin
            for other in static:
                if (name, other) in self.partners:
                    continue
                geometry = self.model[other]
                if np.any(geometry.hi < lo) or np.any(hi < geometry.lo):
                    continue
                target = geometry.manifold()
                volume = (solid ^ target).volume()
                gap = solid.min_gap(target, margin * 4) if volume <= VOLUME_TOL and margin > 0 else (0.0 if volume > VOLUME_TOL else None)
                if gap is not None and gap < margin * 4:
                    key = f"{name}|{other}"
                    min_gap[key] = min(min_gap.get(key, 1.0), float(gap))
                if volume > VOLUME_TOL or (margin > 0 and gap is not None and gap < margin):
                    failures.append({"moving": name, "other": other, "volume_mm3": round(volume * 1e9, 4),
                                     "gap_mm": None if gap is None else round(float(gap) * 1e3, 4), "sample": sample, "sweep": label})

    def sweep(self, linkage: Linkage, groups: dict[str, set[str]], travel: tuple[float, float]) -> dict[str, Any]:
        thetas = np.linspace(travel[0], travel[1], SWEEP_SAMPLES).tolist()
        table = steering_table(linkage, travel, samples=SWEEP_SAMPLES)
        moving_names = set().union(*groups.values())
        static = sorted(set(self.model.parts) - moving_names)
        failures: list[dict[str, Any]] = []
        min_gap: dict[str, float] = {}
        poses = []
        lock_poses: dict[str, dict[str, Any]] = {}
        for i, theta in enumerate(table["thetas"]):
            row = table["rows"][i]
            sample = {"servo_rad": theta, **{s.joint: row[s.joint] for s in linkage.sides}}
            if any(not math.isfinite(row[s.joint]) for s in linkage.sides):
                continue
            solids: dict[str, dict[str, Any]] = {}
            horn_rot = rotation(linkage.servo_axis, theta)
            matrices = {"horn": _matrix(horn_rot, linkage.servo_point)}
            solids["horn"] = self._transformed(groups["horn"], matrices["horn"])
            for side in linkage.sides:
                phi = row[side.joint]
                matrices[side.joint] = _matrix(rotation(side.kingpin_axis, phi), side.kingpin_point)
                solids[side.joint] = self._transformed(groups[side.joint], matrices[side.joint])
                h0, k0 = side.horn_ball, side.knuckle_ball
                h1 = linkage.servo_point + horn_rot @ (h0 - linkage.servo_point)
                k1 = side.kingpin_point + rotation(side.kingpin_axis, phi) @ (k0 - side.kingpin_point)
                rot = _align(k0 - h0, k1 - h1)
                matrices[f"rod:{side.joint}"] = _matrix(rot, h0, h1 - h0)
                solids[f"rod:{side.joint}"] = self._transformed(groups[f"rod:{side.joint}"], matrices[f"rod:{side.joint}"])
            if i in (0, len(table["thetas"]) - 1):
                # Full-lock pose of every moving part, used to pose and cross-check the Onshape assembly.
                lock_poses["left" if theta > 0 else "right"] = {
                    "sample": sample, "parts": {n: m.tolist() for g, m in matrices.items() for n in groups[g] if n in self.model.parts}}
            all_moving = {n: s for group in solids.values() for n, s in group.items()}
            for label, group in solids.items():
                self._check(group, static, MARGIN, "steer_vs_static", sample, failures, min_gap)
                others = [n for g, members in solids.items() if g != label for n in members]
                for name, solid in group.items():
                    for other in others:
                        if (name, other) in self.partners:
                            continue
                        target = all_moving[other]
                        volume = (solid ^ target).volume()
                        if volume > VOLUME_TOL:
                            failures.append({"moving": name, "other": other, "volume_mm3": round(volume * 1e9, 4), "gap_mm": None,
                                             "sample": sample, "sweep": "steer_vs_moving"})
            poses.append(sample)
        spin = self.spin_sweep(failures)
        unique: dict[tuple[str, str, str], dict[str, Any]] = {}
        for failure in failures:
            key = (failure["sweep"], *sorted((failure["moving"], failure["other"])))
            if key not in unique or (failure["volume_mm3"] or 0) > (unique[key]["volume_mm3"] or 0):
                unique[key] = failure
        for failure in unique.values():
            self.result.add(Diagnostic.error("cad_motion_interference",
                                             f"{failure['sweep']}: {failure['moving']} vs {failure['other']} "
                                             f"({'intersects ' + str(failure['volume_mm3']) + ' mm^3' if failure['volume_mm3'] else 'gap ' + str(failure['gap_mm']) + ' mm < ' + str(MARGIN * 1e3) + ' mm margin'})",
                                             subjects=[failure["moving"], failure["other"]], data=failure))
        tight = sorted(min_gap.items(), key=lambda kv: kv[1])[:10]
        return {"samples": poses, "lock_poses": lock_poses, "margin_m": MARGIN, "failures": list(unique.values()), "closest_static_gaps_mm": {k: round(v * 1e3, 3) for k, v in tight},
                "spin": spin}

    def spin_sweep(self, failures: list[dict[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for joint in self.spec.mechanical.joints:
            if joint.type != "continuous":
                continue
            cut = {joint.interface} | {i.id for i in self.spec.mechanical.interfaces if i.kind in ("revolute_fit", "ball_joint", "spline")
                                       or (i.kind == "drive_flat" and i.params.get("rotating_bore"))}
            group = _subtree(joint.child, self.links, cut)
            static = sorted(set(self.model.parts) - group)
            axis, origin = unit(joint.axis), np.asarray(joint.origin, dtype=float)
            before = len(failures)
            for degrees in SPIN_ANGLES[1:]:
                moving = self._transformed(group, _matrix(rotation(axis, math.radians(degrees)), origin))
                self._check(moving, static, 0.0, f"spin_{joint.id}", {"angle_deg": degrees}, failures, {})
            out[joint.id] = {"rotating_parts": sorted(group), "angles_deg": SPIN_ANGLES, "failures": len(failures) - before}
        return out


def run_motion(project: Project, model: GeometryModel, fit: FitReport, result: StageResult) -> dict[str, Any]:
    result.assume("mechanical", f"Motion sweep: linkage solved in Python from verified CAD axes; {SWEEP_SAMPLES} servo samples, "
                                f"{MARGIN * 1e3:.1f} mm clearance margin moving vs static; wheel spin at {SPIN_ANGLES} deg.")
    return Motion(project, model, fit, result).steering()

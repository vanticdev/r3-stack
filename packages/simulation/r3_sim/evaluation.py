"""Scenario ``expect`` evaluation on the recording (twin-side ground truth).

Every metric is computed from the physical state recorded by the twin, never
from what the robot software believes, so a controller cannot pass by
reporting success. Firmware log events are only used for
``low_voltage_cutoff_engaged`` and are cross-checked against the ESC pulse.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from r3_model.units import to_si

#: Speed below which the car counts as stopped (m/s).
STOP_SPEED = 0.05
#: Waypoint capture radius for lap counting (m).
CAPTURE_RADIUS = 2.0
#: Max distance from the course for progress to count towards a lap (m).
LAP_CORRIDOR = 4.0


@dataclass
class Check:
    key: str
    expected: Any
    measured: Any
    passed: bool
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "expected": self.expected, "measured": self.measured, "pass": self.passed,
                "detail": self.detail, **({"data": self.data} if self.data else {})}


def _segments(waypoints: list[list[float]], closed: bool) -> np.ndarray:
    pts = np.asarray(waypoints, dtype=float)[:, :2]
    if closed:
        pts = np.vstack([pts, pts[:1]])
    return np.stack([pts[:-1], pts[1:]], axis=1)


def distance_to_path(xy: np.ndarray, segments: np.ndarray) -> np.ndarray:
    a, b = segments[:, 0], segments[:, 1]
    ab = b - a
    length2 = np.maximum((ab * ab).sum(axis=1), 1e-12)
    rel = xy[:, None, :] - a[None, :, :]
    s = np.clip((rel * ab[None]).sum(axis=2) / length2[None], 0.0, 1.0)
    closest = a[None] + s[..., None] * ab[None]
    return np.linalg.norm(xy[:, None, :] - closest, axis=2).min(axis=1)


def _project(xy: np.ndarray, segments: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Arc length of the closest course point and the distance to it, per sample."""
    a, b = segments[:, 0], segments[:, 1]
    ab = b - a
    lengths = np.linalg.norm(ab, axis=1)
    offsets = np.concatenate([[0.0], np.cumsum(lengths)[:-1]])
    rel = xy[:, None, :] - a[None]
    u = np.clip((rel * ab[None]).sum(axis=2) / np.maximum(lengths ** 2, 1e-12)[None], 0.0, 1.0)
    closest = a[None] + u[..., None] * ab[None]
    dist = np.linalg.norm(xy[:, None, :] - closest, axis=2)
    k = np.argmin(dist, axis=1)
    rows = np.arange(len(xy))
    return offsets[k] + u[rows, k] * lengths[k], dist[rows, k]


def lap_progress(xy: np.ndarray, t: np.ndarray, waypoints: list[list[float]], closed: bool) -> tuple[int, float | None, float | None]:
    """(laps completed, mission start time, mission end time).

    Progress is the unwrapped arc length of the car's projection onto the course,
    counted from the first approach to the first waypoint and only while the car is
    within ``LAP_CORRIDOR`` of the course (shortcuts do not count). A lap completes
    when progress reaches the course length minus ``CAPTURE_RADIUS``."""
    if len(waypoints) < 2:
        return 0, None, None
    segments = _segments(waypoints, closed)
    perimeter = float(np.linalg.norm(segments[:, 1] - segments[:, 0], axis=1).sum())
    s, dist = _project(xy, segments)
    near_start = np.nonzero(np.hypot(*(xy - np.asarray(waypoints[0], dtype=float)[:2]).T) <= CAPTURE_RADIUS)[0]
    if not len(near_start):
        return 0, None, None
    k0 = int(near_start[0])
    progress, laps, end = 0.0, 0, None
    for k in range(k0 + 1, len(t)):
        ds = s[k] - s[k - 1]
        if closed:
            ds = (ds + perimeter / 2) % perimeter - perimeter / 2
        if dist[k] <= LAP_CORRIDOR:
            progress += ds
        if progress >= (laps + 1) * perimeter - CAPTURE_RADIUS:
            laps += 1
            end = float(t[k])
            if not closed:
                break
    return laps, float(t[k0]), end


def rc_gaps(frame_times: list[float], end_t: float, threshold: float) -> list[tuple[float, float]]:
    """Gaps between consecutive RC frames (and to the end of the run) longer than ``threshold``."""
    gaps = []
    times = list(frame_times) + [end_t]
    for a, b in zip(times[:-1], times[1:]):
        if b - a > threshold:
            gaps.append((a, b - a))
    return gaps


def _first_stop_after(t: np.ndarray, speed: np.ndarray, t0: float) -> int | None:
    idx = np.nonzero((t >= t0) & (np.abs(speed) < STOP_SPEED))[0]
    return int(idx[0]) if len(idx) else None


def evaluate(expect: dict[str, Any], rec: dict[str, np.ndarray], context: dict[str, Any]) -> list[Check]:
    t, x, y, speed = rec["t"], rec["x"], rec["y"], rec["speed"]
    xy = np.column_stack([x, y])
    mission = context.get("mission") or {}
    waypoints = mission.get("waypoints") or []
    closed = len(waypoints) > 2
    laps, start, end = lap_progress(xy, t, waypoints, closed)
    smooth = np.convolve(speed, np.ones(10) / 10.0, mode="same") if len(speed) >= 10 else speed
    checks: list[Check] = []
    for key, expected in expect.items():
        if key == "laps_completed":
            checks.append(Check(key, expected, laps, laps >= int(expected), f"{laps} laps of the {len(waypoints)}-waypoint course "
                                f"(progress along the course, start t={start}, end t={end})"))
        elif key == "max_cross_track_error_m":
            if start is None:
                checks.append(Check(key, expected, None, False, "car never reached the first waypoint"))
                continue
            window = (t >= start) & (t <= (end if end is not None and laps >= int(mission.get("laps", 1)) else t[-1]))
            err = distance_to_path(xy[window], _segments(waypoints, closed)) if window.any() else np.array([math.inf])
            k = int(np.argmax(err))
            worst = float(err[k])
            checks.append(Check(key, expected, round(worst, 3), worst <= float(expected),
                                f"worst at t={t[window][k]:.2f}s ({xy[window][k][0]:.2f}, {xy[window][k][1]:.2f})",
                                {"t": float(t[window][k]), "x": float(xy[window][k][0]), "y": float(xy[window][k][1])}))
        elif key == "failsafe_events":
            gaps = context["failsafe_gaps"]
            checks.append(Check(key, expected, len(gaps), len(gaps) <= int(expected),
                                f"RC frame gaps > {context['failsafe_timeout']} s", {"gaps": [[round(a, 3), round(b, 3)] for a, b in gaps[:20]]}))
        elif key == "min_battery_voltage_v":
            k = int(np.argmin(rec["battery_v"]))
            value = float(rec["battery_v"][k])
            checks.append(Check(key, expected, round(value, 3), value >= float(expected), f"at t={t[k]:.2f}s",
                                {"t": float(t[k]), "current": float(rec["battery_i"][k])}))
        elif key == "brownouts":
            n = len(context["brownouts"])
            checks.append(Check(key, expected, n, n <= int(expected), "", {"times": context["brownouts"][:20]}))
        elif key == "min_top_speed_mps":
            k = int(np.argmax(smooth))
            value = float(smooth[k])
            checks.append(Check(key, expected, round(value, 3), value >= float(expected), f"100 ms average, at t={t[k]:.2f}s"))
        elif key == "link_lost_beyond_m":
            lost = context.get("link_lost")
            if lost is not None:
                checks.append(Check(key, expected, round(lost["distance"], 1), lost["distance"] >= to_si(expected),
                                    f"last packet at t={lost['t']:.2f}s", lost))
            else:
                reach = float(np.max(rec["tx_distance"]))
                checks.append(Check(key, expected, None, reach >= to_si(expected),
                                    f"link never lost; max distance {reach:.1f} m", {"max_distance": reach}))
        elif key == "stop_within_m_of_link_loss":
            lost = context.get("link_lost")
            if lost is None:
                checks.append(Check(key, expected, None, False, "link was never lost; stop distance not exercised"))
                continue
            # Measure from the last RC frame before the first frame gap longer than the software's failsafe
            # timeout that ends the run or precedes the declared loss: the software brakes on that gap, which can
            # come well before the receiver declares the link lost (its own, longer timeout).
            t_loss = lost["t"]
            gaps = [g for g in context.get("failsafe_gaps", []) if g[0] <= t_loss + 1e-6]
            moving = [g for g in gaps if abs(float(np.interp(g[0], t, speed))) > STOP_SPEED]
            t0 = moving[0][0] if moving else t_loss
            k0 = int(np.searchsorted(t, t0))
            k1 = _first_stop_after(t, speed, t0)
            if k1 is None:
                checks.append(Check(key, expected, None, False, "car never stopped after link loss"))
                continue
            path = float(np.sum(np.hypot(np.diff(x[k0:k1 + 1]), np.diff(y[k0:k1 + 1])))) if k1 > k0 else 0.0
            checks.append(Check(key, expected, round(path, 3), path <= to_si(expected),
                                f"last frame before the failsafe gap at t={t0:.2f}s (receiver declared loss at t={t_loss:.2f}s); "
                                f"stopped at t={t[k1]:.2f}s, {t[k1] - t0:.2f}s later",
                                {"speed_at_loss": float(speed[min(k0, len(speed) - 1)]), "stop_t": float(t[k1]),
                                 "gap_start_t": float(t0)}))
        elif key == "stop_within_m_of_fault":
            fault = context.get("fault")
            if fault is None:
                checks.append(Check(key, expected, None, False, "no fault was injected; stop distance not exercised"))
                continue
            k0 = int(np.searchsorted(t, fault["t"]))
            k1 = _first_stop_after(t, speed, fault["t"])
            if k1 is None:
                k_end = len(t) - 1
                path = float(np.sum(np.hypot(np.diff(x[k0:]), np.diff(y[k0:])))) if k_end > k0 else 0.0
                checks.append(Check(key, expected, None, False,
                                    f"car never stopped after the {fault['type']} fault at t={fault['t']:.2f}s "
                                    f"({path:.1f} m travelled, {float(speed[-1]):.2f} m/s at the end)",
                                    {"speed_at_fault": float(speed[min(k0, len(speed) - 1)]), "distance_to_end": path,
                                     "final_speed": float(speed[-1])}))
                continue
            path = float(np.sum(np.hypot(np.diff(x[k0:k1 + 1]), np.diff(y[k0:k1 + 1])))) if k1 > k0 else 0.0
            checks.append(Check(key, expected, round(path, 3), path <= to_si(expected),
                                f"{fault['type']} at t={fault['t']:.2f}s; stopped at t={t[k1]:.2f}s, {t[k1] - fault['t']:.2f}s later",
                                {"speed_at_fault": float(speed[min(k0, len(speed) - 1)]), "stop_t": float(t[k1]),
                                 "fault_t": float(fault["t"])}))
        elif key == "arbiter_neutral_within_s":
            fault = context.get("fault")
            events = [e for e in context.get("events", []) if e.get("event") == "arbiter_failsafe"
                      and (fault is None or e["t"] >= fault["t"] - 1e-9)]
            if not events:
                checks.append(Check(key, expected, None, False, "the arbiter never switched to failsafe"))
                continue
            event = events[0]
            edge = event.get("last_heartbeat_edge_t")
            latency = float(event["t"] - edge) if edge is not None else math.inf
            after = t >= event["t"] + 0.02
            neutral = float(context.get("esc_neutral_us", 1500.0))
            # The arbiter's own output from then on: its active brake (``brake_us`` until ``brake_until``, when the
            # ESC was brake-armed), then neutral. The Pi's command must never reach the ESC again.
            brake_until, brake_us = event.get("brake_until"), event.get("brake_us")
            target = np.full(len(t), neutral)
            if brake_until is not None and brake_us is not None:
                target[t < float(brake_until) - 0.02] = float(brake_us)
            settled = after & ~((brake_until is not None) & (np.abs(t - float(brake_until or 0.0)) < 0.021))
            held = bool(np.all(np.abs(rec["esc_pulse"][settled] - target[settled]) < 1.0)) if settled.any() else False
            phase = (f"brake {brake_us:.0f} us until t={float(brake_until):.2f}s, then neutral" if brake_until is not None
                     else "neutral")
            checks.append(Check(key, expected, round(latency, 4), latency <= float(expected) and held,
                                f"last heartbeat edge t={edge}, failsafe from t={event['t']:.3f}s ({phase}); ESC held at the "
                                f"arbiter's output afterwards: {held}", {"last_edge_t": edge if edge is not None else -1.0,
                                                                          "failsafe_t": float(event["t"])}))
        elif key == "min_cell_voltage_v":
            k = int(np.argmin(rec["cell_v"]))
            value = float(rec["cell_v"][k])
            checks.append(Check(key, expected, round(value, 3), value >= float(expected), f"under load at t={t[k]:.2f}s"))
        elif key == "low_voltage_cutoff_engaged":
            events = [log for log in context["logs"] if "low_voltage_cutoff" in (str(log.get("fields", {}).get("event", "")) + log["message"])]
            engaged = False
            detail = "no low_voltage_cutoff event logged by the software"
            if events:
                t_cut = float(events[0]["t"])
                after = (t > t_cut + 0.1)
                neutral = float(context.get("esc_neutral_us", 1500.0))
                max_after = float(np.max(rec["esc_pulse"][after])) if after.any() else neutral
                engaged = max_after <= neutral + 15.0
                detail = f"cutoff logged at t={t_cut:.2f}s; max ESC pulse afterwards {max_after:.0f} us"
            checks.append(Check(key, expected, engaged, engaged == bool(expected), detail))
        elif key == "min_wire_clearance_m":
            distance, wire, part = context["min_wire_clearance"]
            ok = distance >= float(expected)
            checks.append(Check(key, expected, round(distance, 5) if math.isfinite(distance) else None, ok,
                                f"{wire} to {part}", {"wire": wire, "component": part}))
        else:
            checks.append(Check(key, expected, None, False, "unknown expectation key (not evaluated)"))
    return checks

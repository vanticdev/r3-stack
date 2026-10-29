"""Cut length, slack and termination allowances, from the termination catalog.

The cut length of a conductor is built up from physical terms instead of a flat
``route.slack``:

``L_cut = L_centreline + e * sum|theta| + sum(span slack) + sum(end: insertion + strip + rework)``

* ``L_centreline``: the planned bundle centreline between the two termination
  points (where the conductor leaves the cable-side housing rear).
* **Inner/outer bend length.** In a bundle of diameter ``D`` a conductor of
  diameter ``d`` sits up to ``e = (D - d) / 2`` off the centreline; over a total
  turning angle ``sum|theta|`` its length differs by up to ``+- e sum|theta|``
  (arc length ``(R +- e) theta``). Loose conductors migrate, so every conductor
  is cut for the outside of every bend; the spread is reported.
* **Span slack.** Every span between two holding points (connector, support,
  grommet) carries :data:`SPAN_SLACK_RATIO` of its length (at least
  :data:`SPAN_SLACK_MIN`) so that no span is preloaded under vibration
  (IPC/WHMA-A-620: no stress on terminations; the ratio is an estimate). Spans
  between holding points on bodies that move relative to each other carry at
  least the extra length the joint travel needs (``1.05 x`` the largest pin
  distance over the travel minus the routed span).
* **Ends.** Strip length and the insulated length inside the housing (insertion
  depth), per connector type from ``data/terminations.yaml``, plus a service
  loop of one re-termination (``rework``) at every removable end.
* **Tolerance.** ``+CUT_TOLERANCE / -0`` (a conductor may be long, never short;
  drawing default chosen by R3). The bend spread and strip tolerances are listed
  separately.

Terminal pull limits are the UL 486A-486B pull-out force of the conductor size
and the contact retention in the housing, divided by :data:`PULL_SAFETY`. The
MuJoCo slack drive measures the cable tension at every connector end at the
steering extremes and the bump; :func:`terminal_diagnostics` compares them.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from r3_model.diagnostics import Diagnostic
from r3_model.units import to_si

from .bodies import ROOT
from .geometry import polyline_length, project_to_polyline

DATA = Path(__file__).with_name("data") / "terminations.yaml"
SPAN_SLACK_RATIO = 0.02
SPAN_SLACK_MIN = 0.002  # m
MOTION_FACTOR = 1.05
PULL_SAFETY = 2.0
CUT_TOLERANCE = {0.3: 0.003, 1.0: 0.005, math.inf: 0.010}  # length up to -> +tolerance (m)


@dataclass(frozen=True)
class Termination:
    connector_type: str
    method: str
    housing: dict[str, Any] | None
    contact: dict[str, Any] | None
    strip: float
    strip_tolerance: float
    insertion: float
    rework: float
    contact_retention: float | None
    tool: dict[str, Any] | None
    heat_shrink: dict[str, Any] | None
    provenance: dict[str, Any]


@lru_cache(maxsize=1)
def load_terminations(path: str = str(DATA)) -> tuple[dict[str, Termination], dict[int, float]]:
    raw = yaml.safe_load(Path(path).read_text())
    out = {}
    for key, t in raw["terminations"].items():
        out[key] = Termination(key, t["method"], t.get("housing"), t.get("contact"), to_si(t["strip"][0]),
                               to_si(t["strip"][1]), to_si(t["insertion"]), to_si(t["rework"]),
                               to_si(t["contact_retention"]) if t.get("contact_retention") else None,
                               t.get("tool"), t.get("heat_shrink"), t.get("provenance", {}))
    pull = {int(k): float(v) for k, v in raw["pull_out_by_awg"].items()}
    return out, pull


def termination(connector_type: str | None) -> Termination:
    table, _ = load_terminations()
    if connector_type is None:
        return table["splice"]
    if connector_type in table:
        return table[connector_type]
    return Termination(connector_type, "unknown", None, None, 0.005, 0.001, 0.0, 0.005, None, None, None,
                       {"status": "estimated", "source": "no termination data for this connector type"})


def pull_out_limit(awg: int) -> float:
    _, table = load_terminations()
    if awg in table:
        return table[awg]
    keys = sorted(table)
    return table[min(keys, key=lambda k: abs(k - awg))]


def cut_tolerance(length: float) -> float:
    for limit, tol in CUT_TOLERANCE.items():
        if length <= limit:
            return tol
    return CUT_TOLERANCE[math.inf]


@dataclass
class CutLength:
    wire: str
    harness: str
    awg: int
    centreline: float
    turning: float  # sum |theta|, rad
    offset: float  # max conductor offset from the bundle centreline, m
    bend_extra: float  # outside-of-bend allowance = offset * turning
    spans: list[dict[str, Any]]
    span_slack: float
    ends: list[dict[str, Any]]
    nominal: float
    tolerance_plus: float
    pull_limits: list[float]  # N, per end (inf where no terminal)
    notes: list[str] = field(default_factory=list)

    @property
    def end_allowance(self) -> float:
        return sum(e["strip"] + e["insertion"] for e in self.ends)

    @property
    def rework(self) -> float:
        return sum(e["rework"] for e in self.ends)

    def as_dict(self) -> dict[str, Any]:
        return {"wire": self.wire, "harness": self.harness, "awg": self.awg, "nominal": self.nominal,
                "tolerance": [self.tolerance_plus, 0.0], "centreline": self.centreline,
                "bend_turning_rad": self.turning, "conductor_offset": self.offset,
                "bend_spread": [-self.bend_extra, self.bend_extra], "outside_bend_allowance": self.bend_extra,
                "span_slack": self.span_slack, "spans": self.spans, "ends": self.ends,
                "service_loops": self.rework, "terminal_pull_limit": self.pull_limits, "notes": self.notes,
                "units": "m, rad, N"}


def _turning(path: np.ndarray) -> float:
    e = np.diff(path, axis=0)
    n = np.linalg.norm(e, axis=1)
    keep = n > 1e-9
    u = e[keep] / n[keep][:, None]
    if len(u) < 2:
        return 0.0
    return float(np.sum(np.arccos(np.clip(np.einsum("ij,ij->i", u[:-1], u[1:]), -1.0, 1.0))))


def _end(harness: Any, ref: str, awg: int, catalog: Any) -> dict[str, Any]:
    if ref in harness.splices:
        connector_type, label = None, f"{harness.id}.{ref}"
    else:
        name = ref.split(".")[0]
        connector_type, label = harness.connectors[name].type, f"{harness.id}.{ref}"
    term = termination(connector_type)
    removable = term.method not in ("component_lead",)
    pull = pull_out_limit(awg)
    limit = min(pull, term.contact_retention) if term.contact_retention else (pull if term.method != "component_lead"
                                                                               else math.inf)
    return {"end": label, "connector_type": connector_type or "splice", "method": term.method,
            "housing": (term.housing or {}).get("mpn"), "contact": (term.contact or {}).get("mpn"),
            "strip": term.strip, "strip_tolerance": term.strip_tolerance, "insertion": term.insertion,
            "rework": term.rework if removable else 0.0, "pull_out": pull, "contact_retention": term.contact_retention,
            "pull_limit": limit / PULL_SAFETY if math.isfinite(limit) else math.inf,
            "provenance": term.provenance.get("status", "estimated")}


def compute_cut_lengths(project: Any, routing: Any, spans: list[dict[str, Any]], kinematics: Any) -> dict[str, CutLength]:
    """Cut length of every routed conductor from its planned path and support spans."""
    catalog = project.catalog
    harnesses = {h.id: h for h in project.spec.electrical.harnesses}
    by_wire: dict[str, list[dict[str, Any]]] = {}
    for span in spans:
        by_wire.setdefault(span["wire"], []).append(span)
    out: dict[str, CutLength] = {}
    for wire_id, route in routing.wires.items():
        harness = harnesses[route.harness]
        wire = next(w for w in harness.wires if w.id == wire_id)
        path = route.path
        centreline = polyline_length(path)
        turning = _turning(path)
        bundle_od = 2 * float(np.max(route.bundle_radius)) if len(route.bundle_radius) else route.od
        offset = max(0.0, (bundle_od - route.od) / 2)
        bend_extra = offset * turning
        ends = [_end(harness, ref, wire.awg, catalog) for ref in (wire.from_, wire.to)]
        span_list = []
        slack = 0.0
        nodes = routing.nodes[route.harness]
        bodies = (nodes[route.from_node].body, nodes[route.to_node].body)
        for span in sorted(by_wire.get(wire_id, []), key=lambda s: s["start_arc"]):
            length = span["length"]
            base = max(SPAN_SLACK_MIN, SPAN_SLACK_RATIO * length)
            motion = _motion_slack(kinematics, path, span, bodies, centreline)
            s = max(base, motion)
            slack += s
            span_list.append({"from": span["from"], "to": span["to"], "length": length, "slack": s,
                              "motion_requirement": motion})
        if not span_list:  # no span data: one span end to end
            s = max(SPAN_SLACK_MIN, SPAN_SLACK_RATIO * centreline)
            slack = s
            span_list.append({"from": "end", "to": "end", "length": centreline, "slack": s, "motion_requirement": 0.0})
        nominal = centreline + bend_extra + slack + sum(e["strip"] + e["insertion"] + e["rework"] for e in ends)
        notes = []
        if wire.length is not None:
            notes.append(f"robot.yaml fixes the length at {float(wire.length) * 1e3:.0f} mm")
        if any(e["method"] == "component_lead" for e in ends):
            notes.append("one end is the component's own lead: order/trim the lead to this length")
        out[wire_id] = CutLength(wire_id, route.harness, wire.awg, centreline, turning, offset, bend_extra, span_list,
                                 slack, ends, nominal, cut_tolerance(nominal), [e["pull_limit"] for e in ends], notes)
    return out


def _motion_slack(kin: Any, path: np.ndarray, span: dict[str, Any], bodies: tuple[str, str], total: float) -> float:
    """Extra length a span needs when its holding points move relative to each other
    (only spans that end on a jointed body; supports are on the chassis)."""
    body_a = bodies[0] if span["start_arc"] <= 1e-6 else ROOT
    body_b = bodies[1] if span["end_arc"] >= total - 1e-6 else ROOT
    if body_a == body_b:
        return 0.0
    joints = kin.relative_joints(body_a, body_b)
    if not joints or any(j.type == "continuous" for j in joints):
        return 0.0
    from .geometry import point_at_arc

    pa, pb = point_at_arc(path, span["start_arc"]), point_at_arc(path, span["end_arc"])
    worst = 0.0
    for values in itertools.product(*[j.samples(math.radians(2.0), 0.001) for j in joints]):
        q = dict(zip([j.id for j in joints], values))
        ta, tb = kin.body_transform(body_a, q), kin.body_transform(body_b, q)
        worst = max(worst, float(np.linalg.norm((ta[0] @ pa + ta[1]) - (tb[0] @ pb + tb[1]))))
    return max(0.0, MOTION_FACTOR * worst - span["length"])


def apply_cut_lengths(routing: Any, cuts: dict[str, CutLength]) -> None:
    """Make ``WireRoute.cut_length`` the computed nominal: slack = everything that is
    not centreline or termination (bend allowance, span slack, service loops)."""
    for wire_id, cut in cuts.items():
        route = routing.wires[wire_id]
        route.termination_allowance = cut.end_allowance
        route.slack = cut.nominal - route.routed_length - cut.end_allowance


def terminal_diagnostics(cuts: dict[str, CutLength], loads: dict[str, dict[str, Any]]) -> list[Diagnostic]:
    """Compare the slack drive's peak cable tension at each connector end with the
    terminal pull limit; report spans the drive pulled taut."""
    out = []
    for wire_id, load in loads.items():
        cut = cuts.get(wire_id)
        if cut is None:
            continue
        for index, (tension, when) in enumerate(zip(load.get("end_tension", []), load.get("time", []))):
            limit = cut.pull_limits[index] if index < len(cut.pull_limits) else math.inf
            end = cut.ends[index]["end"] if index < len(cut.ends) else str(index)
            if tension > limit:
                out.append(Diagnostic.error(
                    "terminal_overload", f"{wire_id} pulls {tension:.1f} N on {end} during the slack drive "
                                         f"(limit {limit:.1f} N = pull-out / {PULL_SAFETY:.0f})",
                    subjects=[cut.harness, wire_id, end],
                    data={"tension": tension, "limit": limit, "time": when, "pull_out": cut.ends[index]["pull_out"],
                          "contact_retention": cut.ends[index]["contact_retention"], "units": "N, s"},
                    hint="Add a support (strain relief) next to this connector or add slack to the span."))
    return out


def cut_report(cuts: dict[str, CutLength]) -> dict[str, Any]:
    return {w: c.as_dict() for w, c in cuts.items()}


__all__ = ["CutLength", "Termination", "apply_cut_lengths", "compute_cut_lengths", "cut_report", "cut_tolerance",
           "load_terminations", "pull_out_limit", "termination", "terminal_diagnostics", "project_to_polyline"]

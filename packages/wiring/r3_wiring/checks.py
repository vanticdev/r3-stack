"""Physical harness checks on routed geometry.

Every finding is a :class:`Diagnostic` with a stable code and numeric data
(SI units). Codes:

* ``wire_bend_radius_violation``: bend radius below ``factor * OD_bundle``.
* ``wire_clearance_violation``: wire surface closer than
  ``requirements.min_wire_clearance_moving`` to a moving swept envelope.
* ``connector_clearance_violation``: same, for a connector body.
* ``wire_intersects_component``: wire penetrates a component (outside its own
  termination zone; drilled pass-throughs excepted).
* ``wire_intersects_connector``: bundle surface penetrates a routed cable-side
  housing or splice sleeve, outside its own local conductor-entry aperture.
* ``connector_intersects_component``: a cable-side body penetrates a static
  component other than its connector owner/mating half.
* ``wire_intersects_wire``: bundle surfaces of different harnesses or separate
  branches overlap; shared cores and local physical joins/splices are excepted.
  Penetrations, radii, arc lengths and witness positions are in metres.
* ``wire_too_short`` / ``connector_strain``: a span between pins on bodies that
  move relative to each other cannot cover the full joint travel (or is
  within 5 % of taut), or a spec cut length is shorter than the route.
* ``excess_slack_loop``: unsupported slack between pins could reach a moving
  part (reach envelope: |x - p1| + |x - p2| <= L_span, an ellipsoid).
* ``connector_wire_gauge_mismatch``: AWG outside the connector's range.
* ``harness_mass`` (info), ``wire_pass_through`` (info).
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from r3_model.diagnostics import Diagnostic
from r3_model.project import Project

from .bodies import ROOT, Kinematics
from .geometry import polyline_length, project_to_polyline, resample_polyline
from .obstacles import CableBody, Obstacle, ObstacleSet, cable_obstacles
from .router import NodePoint, RoutingResult, WireRoute
from .terminations import connector_mass, mated_pair_length

TIE_MASS = 0.0002  # 100 mm nylon zip tie, ~0.2 g (estimate)
TIE_MATCH = 0.0015  # a tie pins a wire if within bundle radius + this


def _polyline_distances(points: np.ndarray, path: np.ndarray) -> np.ndarray:
    """Exact point-to-segment distances (bounded memory for long shared cores)."""
    best = np.full(len(points), math.inf)
    for a, b in zip(path[:-1], path[1:]):
        v = b - a
        t = np.clip((points - a) @ v / max(float(v @ v), 1e-18), 0, 1)
        np.minimum(best, np.linalg.norm(points - (a + t[:, None] * v), axis=1), out=best)
    return best


@dataclass
class Pin:
    arc: float
    position: np.ndarray
    body: str
    kind: str  # end | tie
    exempt: set[str] = field(default_factory=set)


@dataclass
class CheckReport:
    diagnostics: list[Diagnostic]
    clearances: list[dict[str, Any]]
    wire_min_clearance: dict[str, dict[str, Any]]
    connector_clearances: list[dict[str, Any]]
    pins: dict[str, list[Pin]]
    harness_mass: dict[str, dict[str, float]]


def wire_pins(wire: WireRoute, ties: list[np.ndarray], start: NodePoint, end: NodePoint) -> list[Pin]:
    """Endpoints plus the harness ties that hold this wire, sorted by arc length."""
    total = polyline_length(wire.path)
    pins = [Pin(0.0, wire.path[0], start.body, "end", start.exempt), Pin(total, wire.path[-1], end.body, "end", end.exempt)]
    radius = float(np.max(wire.bundle_radius)) if len(wire.bundle_radius) else wire.od / 2
    for tie in ties:
        distance, arc = project_to_polyline(wire.path, np.asarray(tie)[None])
        if distance <= radius + TIE_MATCH and 0.003 < arc < total - 0.003:
            pins.append(Pin(arc, np.asarray(tie, dtype=float), ROOT, "tie"))
    pins.sort(key=lambda p: p.arc)
    return pins


class HarnessChecker:
    def __init__(self, project: Project, obstacles: ObstacleSet, routing: RoutingResult) -> None:
        self.project = project
        self.spec = project.spec
        self.catalog = project.catalog
        self.obstacles = obstacles
        self.kin: Kinematics = obstacles.kinematics
        self.routing = routing
        self.obstacles.cable_bodies = cable_obstacles(project, routing.nodes)
        self.c_req = float(self.spec.requirements.min_wire_clearance_moving)
        self.diagnostics: list[Diagnostic] = []
        self._surface_cache: dict[str, np.ndarray] = {}

    # ---------------------------------------------------------------- helpers
    def _nodes(self, wire: WireRoute) -> tuple[NodePoint, NodePoint]:
        nodes = self.routing.nodes[wire.harness]
        return nodes[wire.from_node], nodes[wire.to_node]

    def _samples(self, wire: WireRoute, spacing: float = 0.001) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        points, arc = resample_polyline(wire.path, spacing)
        vertex_arc = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(wire.path, axis=0), axis=1))])
        radius = np.interp(arc, vertex_arc, wire.bundle_radius)
        return points, arc, radius

    def _exempt_mask(self, arc: np.ndarray, component: str, start: NodePoint, end: NodePoint) -> np.ndarray:
        total = arc[-1]
        mask = np.zeros(len(arc), dtype=bool)
        if component in start.exempt:
            mask |= arc <= start.termination_length
        if component in end.exempt:
            mask |= total - arc <= end.termination_length
        return mask

    def _hole_mask(self, points: np.ndarray, obstacle: Obstacle, harness: str) -> np.ndarray:
        mask = np.zeros(len(points), dtype=bool)
        for hole in self.routing.holes:
            if hole.plate == obstacle.component and harness in hole.harnesses:
                in_plane = [i for i in range(3) if i != hole.axis]
                mask |= np.linalg.norm((points - hole.center)[:, in_plane], axis=1) <= max(hole.diameter / 2, 0.004) + 0.002
        return mask

    def _surface(self, obstacle: Obstacle) -> np.ndarray:
        if obstacle.component not in self._surface_cache:
            self._surface_cache[obstacle.component] = obstacle.swept.surface_points(0.004)
        return self._surface_cache[obstacle.component]

    # ----------------------------------------------------------------- checks
    def run(self) -> CheckReport:
        clearances, wire_min = self._clearance_and_intersection()
        connector_clearances = self._connector_clearance()
        self._wire_intersections()
        self._bend_radius()
        pins = {w.wire_id: wire_pins(w, self.routing.harnesses[w.harness].tie_points, *self._nodes(w))
                for w in self.routing.wires.values()}
        self._relative_motion(pins)
        self._slack_reach(pins)
        self._gauge()
        mass = self._mass()
        self._pass_throughs()
        return CheckReport(self.diagnostics, clearances, wire_min, connector_clearances, pins, mass)

    def _clearance_and_intersection(self) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        clearances: list[dict[str, Any]] = []
        wire_min: dict[str, dict[str, Any]] = {}
        violations: dict[tuple[str, str], dict[str, Any]] = {}
        intersections: dict[tuple[str, str], dict[str, Any]] = {}
        for wire in self.routing.wires.values():
            start, end = self._nodes(wire)
            points, arc, radius = self._samples(wire)
            best = {"distance": math.inf, "component": ""}
            for obstacle in self.obstacles.moving:
                d = obstacle.swept.sdf_bounded(points, 0.2) - radius
                d[self._exempt_mask(arc, obstacle.component, start, end)] = math.inf
                k = int(np.argmin(d))
                if not np.isfinite(d[k]):
                    continue
                entry = {"wire": wire.wire_id, "component": obstacle.component, "min_distance": float(d[k]),
                         "position": points[k].tolist(), "arc_length": float(arc[k])}
                clearances.append(entry)
                if d[k] < best["distance"]:
                    best = {"distance": float(d[k]), "component": obstacle.component, "position": points[k].tolist()}
                if d[k] < self.c_req:
                    key = (wire.harness, obstacle.component)
                    current = violations.setdefault(key, {"wires": {}, "worst": entry})
                    current["wires"][wire.wire_id] = float(d[k])
                    if d[k] < current["worst"]["min_distance"]:
                        current["worst"] = entry
            wire_min[wire.wire_id] = best
            for obstacle in [*self.obstacles.obstacles, *self.obstacles.cable_bodies]:
                d = obstacle.shape.sdf_bounded(points, 0.05) - radius
                if isinstance(obstacle, CableBody):
                    incident = np.zeros(len(arc), dtype=bool)
                    if start.ref == obstacle.component:
                        incident |= arc <= start.termination_length
                    if end.ref == obstacle.component:
                        incident |= arc[-1] - arc <= end.termination_length
                    # The splice joins bundle surfaces, not just centrelines:
                    # its local entry cap is the sleeve entry Minkowski-summed
                    # with this run's radius. Non-incident wires get no aperture.
                    entry_radius = radius if obstacle.kind == "splice" else 0.0
                    d[incident & obstacle.entry_mask(points, entry_radius)] = math.inf
                else:
                    d[self._exempt_mask(arc, obstacle.component, start, end)] = math.inf
                if obstacle.plate_axis is not None:
                    d[self._hole_mask(points, obstacle, wire.harness)] = math.inf
                k = int(np.argmin(d))
                if d[k] < -0.0005:
                    key = (wire.harness, obstacle.component)
                    current = intersections.setdefault(key, {"wires": {}, "depth": 0.0, "position": None})
                    current["wires"][wire.wire_id] = float(-d[k])
                    if -d[k] > current["depth"]:
                        current.update(depth=float(-d[k]), position=points[k].tolist(), arc=float(arc[k]))
        for (harness, component), item in violations.items():
            worst = item["worst"]
            self.diagnostics.append(Diagnostic.error(
                "wire_clearance_violation",
                f"{harness} passes {worst['min_distance'] * 1e3:.1f} mm from moving {component} "
                f"(required {self.c_req * 1e3:.0f} mm)",
                subjects=[harness, *sorted(item["wires"]), component],
                data={"min_distance": worst["min_distance"], "required": self.c_req, "component": component,
                      "position": worst["position"], "arc_length": worst["arc_length"], "worst_wire": worst["wire"],
                      "per_wire_min_distance": item["wires"], "units": "m"},
                hint="Reroute or add a tie point to hold the bundle away from the swept volume."))
        for (harness, component), item in intersections.items():
            body = next((b for b in self.obstacles.cable_bodies if b.component == component), None)
            self.diagnostics.append(Diagnostic.error(
                "wire_intersects_connector" if body else "wire_intersects_component",
                f"{harness} penetrates {component} by {item['depth'] * 1e3:.1f} mm",
                subjects=list(dict.fromkeys([harness, *sorted(item["wires"]), component,
                                             *([body.node.harness] if body else [])])),
                data={"penetration": item["depth"], "component": component, "position": item["position"],
                      "arc_length": item.get("arc"), "per_wire_penetration": item["wires"],
                      **({"connector": component, "geometry_source": body.source} if body else {}), "units": "m"}))
        return clearances, wire_min

    def _connector_clearance(self) -> list[dict[str, Any]]:
        out = []
        for body in self.obstacles.cable_bodies:
            node = body.node
            points = body.shape.surface_points(0.0005)
            best = (math.inf, "")
            for obstacle in self.obstacles.obstacles:
                if obstacle.component == body.owner or obstacle.component == body.mates:
                    continue
                shape = obstacle.swept if obstacle.moving else obstacle.shape
                distance = shape.sdf_bounded(points, 0.05)
                k = int(np.argmin(distance))
                d, position = float(distance[k]), points[k]
                if obstacle.moving:
                    if d < best[0]:
                        best = (d, obstacle.component)
                    continue
                # Both directions cover containment of either solid in the other.
                lo, hi = body.shape.aabb()
                olo, ohi = shape.aabb()
                if np.any(hi < olo) or np.any(ohi < lo):
                    continue
                other = shape.surface_points(0.001)
                reverse = body.shape.sdf_bounded(other, 0.005)
                j = int(np.argmin(reverse))
                if reverse[j] < d:
                    d, position = float(reverse[j]), other[j]
                if d < -0.0005:
                    self.diagnostics.append(Diagnostic.error(
                        "connector_intersects_component",
                        f"connector {body.component} penetrates {obstacle.component} by {-d * 1e3:.1f} mm",
                        subjects=[node.harness, body.component, obstacle.component],
                        data={"penetration": -d, "connector": body.component, "component": obstacle.component,
                              "connector_type": node.connector_type, "position": position.tolist(),
                              "geometry_source": body.source, "units": "m"}))
            if np.isfinite(best[0]):
                out.append({"connector": body.component, "component": best[1], "min_distance": best[0]})
                if best[0] < self.c_req:
                    self.diagnostics.append(Diagnostic.error(
                        "connector_clearance_violation",
                        f"connector {body.component} body is {best[0] * 1e3:.1f} mm from moving {best[1]} "
                        f"(required {self.c_req * 1e3:.0f} mm)",
                        subjects=[node.harness, body.component, best[1]],
                        data={"min_distance": best[0], "required": self.c_req, "component": best[1],
                              "connector_type": node.connector_type, "position": node.exit.tolist(), "units": "m"}))
        return out

    def _wire_intersections(self) -> None:
        """Compare swept bundle surfaces; shared runs are physical single objects.

        0.5 mm centreline sampling bounds the distance error by 0.5 mm; findings
        below use a 0.5 mm penetration threshold, as for component checks.
        A shared graph segment, or the local merge at an incident junction, is
        exempt. The rest of those same wires are still checked against each other.
        """
        from scipy.spatial import cKDTree

        samples = {w.wire_id: self._samples(w, 0.0005) for w in self.routing.wires.values()}
        found: dict[tuple[str, str], dict[str, Any]] = {}
        for a, b in itertools.combinations(sorted(self.routing.wires.values(), key=lambda w: w.wire_id), 2):
            pa, aa, ra = samples[a.wire_id]
            pb, ab, rb = samples[b.wire_id]
            pairs = cKDTree(pa).query_ball_tree(cKDTree(pb), float(ra.max() + rb.max()))
            ia = np.repeat(np.arange(len(pa)), [len(p) for p in pairs])
            ib = np.asarray([j for p in pairs for j in p], dtype=int)
            if not len(ia):
                continue
            gap = np.linalg.norm(pa[ia] - pb[ib], axis=1) - ra[ia] - rb[ib]
            valid = gap < -0.0005
            if a.harness == b.harness:
                segments = self.routing.harnesses[a.harness].segments
                shared = [np.asarray(s["points"], float) for s in segments
                          if a.wire_id in s["wires"] and b.wire_id in s["wires"]]
                for core in shared:
                    # Both witnesses must be in the shared bundle's local tube.
                    da = _polyline_distances(pa, core)
                    db = _polyline_distances(pb, core)
                    join = ra[ia] + rb[ib] + 0.001
                    valid &= ~((da[ia] <= join) & (db[ib] <= join))
                nodes = self.routing.nodes[a.harness]
                common = {a.from_node, a.to_node} & {b.from_node, b.to_node}
                for name in common:
                    node = nodes[name]
                    # Fan-outs into one connector/core and actual solder splices
                    # merge locally. A crossing farther down either branch remains.
                    reach = (0.003 + node.od) if node.kind == "splice" else max(node.fan_length, 2 * node.od)
                    valid &= ~((np.linalg.norm(pa[ia] - node.exit, axis=1) <= reach) &
                               (np.linalg.norm(pb[ib] - node.exit, axis=1) <= reach))
            if not valid.any():
                continue
            k = int(np.argmin(np.where(valid, gap, math.inf)))
            key = tuple(sorted((a.harness, b.harness)))
            item = found.setdefault(key, {"wires": set(), "penetration": 0.0})
            item["wires"].update((a.wire_id, b.wire_id))
            if -gap[k] > item["penetration"]:
                item.update(penetration=float(-gap[k]), wire_a=a.wire_id, wire_b=b.wire_id,
                            radius_a=float(ra[ia[k]]), radius_b=float(rb[ib[k]]),
                            arc_length_a=float(aa[ia[k]]), arc_length_b=float(ab[ib[k]]),
                            witness=[pa[ia[k]].tolist(), pb[ib[k]].tolist()],
                            position=((pa[ia[k]] + pb[ib[k]]) / 2).tolist())
        for harnesses, item in sorted(found.items()):
            wires = sorted(item.pop("wires"))
            self.diagnostics.append(Diagnostic.error(
                "wire_intersects_wire", f"{' / '.join(dict.fromkeys(harnesses))} bundle surfaces overlap "
                f"by {item['penetration'] * 1e3:.1f} mm",
                subjects=list(dict.fromkeys([*harnesses, *wires])), data={**item, "units": "m"},
                hint="Separate the branches or route one bundle around the other; preserve real splice joins."))

    def _bend_radius(self) -> None:
        for harness_id, harness in self.routing.harnesses.items():
            clusters: list[dict[str, Any]] = []
            for wire_id in harness.wires:
                wire = self.routing.wires.get(wire_id)
                if wire is None:
                    continue
                for bend in wire.bends:
                    if bend.radius >= 0.98 * bend.required:
                        continue
                    for cluster in clusters:
                        if np.linalg.norm(cluster["position"] - bend.position) < 0.008:
                            cluster["wires"].add(wire_id)
                            if bend.radius / bend.required < cluster["radius"] / cluster["required"]:
                                cluster.update(radius=bend.radius, required=bend.required, conductors=bend.conductors,
                                               od=bend.od, position=bend.position)
                            break
                    else:
                        clusters.append({"position": bend.position, "radius": bend.radius, "required": bend.required,
                                         "conductors": bend.conductors, "od": bend.od, "wires": {wire_id}})
            for cluster in clusters:
                ratio = cluster["radius"] / cluster["required"]
                make = Diagnostic.error if ratio < 0.75 else Diagnostic.warning
                self.diagnostics.append(make(
                    "wire_bend_radius_violation",
                    f"{harness_id} bends at {cluster['radius'] * 1e3:.1f} mm radius; minimum "
                    f"{cluster['required'] * 1e3:.1f} mm for a {cluster['conductors']}-conductor "
                    f"{cluster['od'] * 1e3:.1f} mm bundle",
                    subjects=[harness_id, *sorted(cluster["wires"])],
                    data={"radius": cluster["radius"], "min_radius": cluster["required"], "ratio": ratio,
                          "conductors": cluster["conductors"], "bundle_od": cluster["od"],
                          "position": np.asarray(cluster["position"]).tolist(), "units": "m"},
                    hint="Give the bundle more room (move the connector or rotate its exit) or split the bundle."))

    def _relative_motion(self, pins: dict[str, list[Pin]]) -> None:
        for wire in self.routing.wires.values():
            stretch = wire.free_length / max(wire.routed_length, 1e-9)
            routed_needed = wire.routed_length + wire.termination_allowance
            if wire.spec_length is not None and wire.spec_length < routed_needed:
                self.diagnostics.append(Diagnostic.error(
                    "wire_too_short", f"{wire.wire_id} spec length {wire.spec_length * 1e3:.0f} mm is shorter than "
                                      f"its route plus terminations ({routed_needed * 1e3:.0f} mm)",
                    subjects=[wire.harness, wire.wire_id],
                    data={"spec_length": wire.spec_length, "required_length": routed_needed, "units": "m"}))
            sequence = pins[wire.wire_id]
            for a, b in zip(sequence[:-1], sequence[1:]):
                if a.body == b.body:
                    continue
                joints = self.kin.relative_joints(a.body, b.body)
                if not joints:
                    continue
                available = (b.arc - a.arc) * stretch
                if any(j.type == "continuous" for j in joints):
                    self.diagnostics.append(Diagnostic.error(
                        "wire_too_short", f"{wire.wire_id} spans continuous joint(s) {[j.id for j in joints]}",
                        subjects=[wire.harness, wire.wire_id, *[j.id for j in joints]],
                        data={"available_length": available, "required_length": math.inf, "units": "m"}))
                    continue
                grids = [j.samples(math.radians(2.0), 0.001) for j in joints]
                worst, worst_q = 0.0, {}
                for values in itertools.product(*grids):
                    q = dict(zip([j.id for j in joints], values))
                    ta, tb = self.kin.body_transform(a.body, q), self.kin.body_transform(b.body, q)
                    pa, pb = ta[0] @ a.position + ta[1], tb[0] @ b.position + tb[1]
                    distance = float(np.linalg.norm(pa - pb))
                    if distance > worst:
                        worst, worst_q = distance, q
                data = {"available_length": available, "max_pin_distance": worst, "joints": [j.id for j in joints],
                        "worst_configuration": worst_q, "units": "m"}
                if worst > available:
                    self.diagnostics.append(Diagnostic.error(
                        "wire_too_short", f"{wire.wire_id}: {available * 1e3:.0f} mm between pins on {a.body} and "
                                          f"{b.body} cannot reach {worst * 1e3:.0f} mm over the joint travel",
                        subjects=[wire.harness, wire.wire_id], data=data))
                elif worst > 0.95 * available:
                    self.diagnostics.append(Diagnostic.warning(
                        "connector_strain", f"{wire.wire_id} is pulled taut (>{0.95 * 100:.0f} %) at full joint travel",
                        subjects=[wire.harness, wire.wire_id], data=data))

    def _slack_reach(self, pins: dict[str, list[Pin]]) -> None:
        found: dict[tuple[str, str], dict[str, Any]] = {}
        for wire in self.routing.wires.values():
            stretch = wire.free_length / max(wire.routed_length, 1e-9)
            points, arc, radius = self._samples(wire)
            sequence = pins[wire.wire_id]
            for a, b in zip(sequence[:-1], sequence[1:]):
                available = (b.arc - a.arc) * stretch
                span = (arc >= a.arc) & (arc <= b.arc)
                r = float(radius[span].max()) if span.any() else wire.od / 2
                for obstacle in self.obstacles.moving:
                    if obstacle.component in a.exempt or obstacle.component in b.exempt:
                        continue
                    own = obstacle.swept.sdf_bounded(points[span], 0.2) - radius[span]
                    if own.size and own.min() <= 0:
                        continue  # the routed path itself already touches: reported as a clearance violation
                    surface = self._surface(obstacle)
                    focal = np.linalg.norm(surface - a.position, axis=1) + np.linalg.norm(surface - b.position, axis=1)
                    margin = available + 2 * r - float(focal.min())
                    if margin > 0:
                        key = (wire.harness, obstacle.component)
                        current = found.setdefault(key, {"wires": set(), "margin": -1.0})
                        current["wires"].add(wire.wire_id)
                        if margin > current["margin"]:
                            current.update(margin=margin, available=available, span=(b.arc - a.arc),
                                           pins=[a.position.tolist(), b.position.tolist()],
                                           pin_kinds=[a.kind, b.kind], wire=wire.wire_id)
        for (harness, component), item in found.items():
            self.diagnostics.append(Diagnostic.warning(
                "excess_slack_loop",
                f"{harness}: {item['available'] * 1e3:.0f} mm of cable between pins can reach moving {component} "
                f"(excess reach {item['margin'] * 1e3:.1f} mm)",
                subjects=[harness, *sorted(item["wires"]), component],
                data={"reach_margin": item["margin"], "available_length": item["available"],
                      "routed_span": item["span"], "pins": item["pins"], "pin_kinds": item["pin_kinds"],
                      "component": component, "worst_wire": item["wire"], "units": "m"},
                hint="Add a tie point in this span or reduce the slack."))

    def _gauge(self) -> None:
        for harness in self.spec.electrical.harnesses:
            for wire in harness.wires:
                for ref in (wire.from_, wire.to):
                    if ref in harness.splices:
                        continue
                    name = ref.split(".")[0]
                    conn = harness.connectors.get(name)
                    if conn is None:
                        continue
                    ctype = self.catalog.connector(conn.type)
                    if not ctype.awg_min <= wire.awg <= ctype.awg_max:
                        self.diagnostics.append(Diagnostic.error(
                            "connector_wire_gauge_mismatch",
                            f"{wire.id} is AWG {wire.awg} but {harness.id}.{name} ({conn.type}) accepts AWG "
                            f"{ctype.awg_min}-{ctype.awg_max}",
                            subjects=[harness.id, wire.id, f"{harness.id}.{name}"],
                            data={"awg": wire.awg, "awg_min": ctype.awg_min, "awg_max": ctype.awg_max,
                                  "connector_type": conn.type}))

    def _mass(self) -> dict[str, dict[str, float]]:
        out = {}
        for harness in self.spec.electrical.harnesses:
            wire_mass = sum(self.routing.wires[w.id].cut_length * float(self.catalog.wire(w.awg).mass_per_m)
                            for w in harness.wires if w.id in self.routing.wires)
            conn_mass = sum(connector_mass(self.catalog, c.type) for c in harness.connectors.values())
            ties = len(self.routing.harnesses[harness.id].tie_points) if harness.id in self.routing.harnesses else 0
            total = wire_mass + conn_mass + ties * TIE_MASS
            out[harness.id] = {"mass": total, "wire_mass": wire_mass, "connector_mass_estimated": conn_mass,
                               "tie_count": ties}
            self.diagnostics.append(Diagnostic.info(
                "harness_mass", f"{harness.id}: {total * 1e3:.1f} g ({wire_mass * 1e3:.1f} g copper+insulation)",
                subjects=[harness.id], data={**out[harness.id], "units": "kg"}))
        return out

    def _pass_throughs(self) -> None:
        for hole in self.routing.holes:
            cad = getattr(hole, "kind", "drilled") == "cad_slot"
            self.diagnostics.append(Diagnostic.info(
                "wire_pass_through",
                (f"{', '.join(sorted(hole.harnesses))} pass through the {hole.label} lead slot in {hole.plate} (grommet edge strip)"
                 if cad else
                 f"drill a {hole.diameter * 1e3:.1f} mm grommeted hole in {hole.plate} for {', '.join(sorted(hole.harnesses))}"),
                subjects=[hole.plate, *sorted(hole.harnesses)],
                data={"position": hole.center.tolist(), "diameter": hole.diameter, "plate": hole.plate,
                      "axis": "xyz"[hole.axis], "units": "m"}))


def run_checks(project: Project, obstacles: ObstacleSet, routing: RoutingResult) -> CheckReport:
    return HarnessChecker(project, obstacles, routing).run()


__all__ = ["CheckReport", "HarnessChecker", "Pin", "run_checks", "wire_pins", "mated_pair_length"]

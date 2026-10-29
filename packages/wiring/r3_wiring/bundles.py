"""Harnesses as one installation: shared occupancy, crossings, EMC segregation,
edge protection and installed (derated) ampacity.

**Occupancy.** Every planned bundle segment of every harness is sampled at
1 mm. Two harnesses *interfere* where their bundle surfaces overlap
(``harness_interference``, an error with witness points). Where they run
within ``r1 + r2 + SHARED_GAP`` of each other for at least
:data:`PARALLEL_MIN` they share a corridor: they are installed as one bundle
(tied together), which matters for derating and segregation. Shorter contacts
are crossings; a crossing must not rub (``harness_crossing_contact`` when the
surfaces touch at less than :data:`CROSSING_MIN_ANGLE`).

**Segregation.** Conductors are classed ``motor`` (PWM-switched motor
phases: the aggressor), ``power`` (battery / regulator supply and returns,
AWG <= 18 or a supply signal) and ``signal`` (UART, PWM command, encoder, RF).
SAE AS50881 requires wiring of different EMC categories to be separated; the
separations used here are R3 design rules (estimates for a 7.4 V robot):
parallel runs longer than :data:`PARALLEL_MIN` keep :data:`SEPARATION` between
bundle surfaces (``emc_segregation_violation``).

**Edge protection.** Sharp convex edges of static CAD meshes (dihedral angle
>= :data:`SHARP_EDGE_DEG`) within :data:`EDGE_GAP` of a bundle surface need
protection (``edge_protection_required``: spiral wrap or grommet edge strip,
added to the BOM).

**Derating.** The catalog ampacity is a free-air, single-conductor rating
(30 K rise at 25 C). Installed rating = ampacity x bundle factor x ambient
factor:

* bundle factor for ``n`` current-carrying conductors in the occupancy group:
  n = 1: 1.0; n = 2-3: the ratio of the NEC Table 310.16 (not more than three
  conductors in a raceway or cable) to the Table 310.17 (single conductor in
  free air) ampacity at 90 C for the conductor size; n > 3: that ratio times
  the NEC 310.15(C)(1) adjustment factor (4-6: 0.80, 7-9: 0.70, 10-20: 0.50,
  21-30: 0.45, 31-40: 0.40, > 40: 0.35). (The SAE AS50881 / MIL-W-5088
  bundle-derating curves are the aerospace equivalent; they are not tabulated
  here.)
* ambient factor ``sqrt((T_c - T_a) / (T_c - 25 C))`` (NEC 310.15(B) Equation
  310.15(B); conductor temperature rises with I^2), with ``T_c = 55 C`` (the
  catalog's 25 C + 30 K basis, which also protects PETG parts and adhesive
  mounts) and the local enclosure ambient ``T_a`` = environment ambient plus
  an estimated rise near heat sources (:data:`HEAT_RISE`).

Current-carrying conductors are those of AWG <= :data:`CURRENT_CARRYING_AWG`
(signal-gauge conductors carry milliamps and do not heat the bundle).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from r3_model.diagnostics import Diagnostic

from .geometry import MeshShape, resample_polyline
from .obstacles import ObstacleSet

SAMPLE = 0.001
SHARED_GAP = 0.003  # m between bundle surfaces that counts as one installed bundle
PARALLEL_MIN = 0.03  # m of run that makes a shared corridor / segregation run
PARALLEL_ANGLE = math.radians(30.0)
CROSSING_MIN_ANGLE = math.radians(45.0)
SEPARATION = {frozenset({"motor", "signal"}): 0.010, frozenset({"power", "signal"}): 0.005}
SHARP_EDGE_DEG = 60.0
EDGE_GAP = 0.0015  # m between bundle surface and a sharp edge
TERMINATION_ZONE = 0.012  # m around connector exits: the connector body handles the wire there
CURRENT_CARRYING_AWG = 20
CONDUCTOR_LIMIT_C = 55.0
REFERENCE_AMBIENT_C = 25.0
HEAT_RISE = {"motor": (0.025, 15.0), "esc": (0.025, 15.0), "battery": (0.015, 8.0), "computer": (0.015, 8.0),
             "pcb": (0.015, 8.0)}  # kind -> (reach m, local rise K): estimates
ENCLOSURE_RISE = 5.0  # K above the environment inside the body (estimate)
# NEC Table 310.16 / 310.17 ampacities at 90 C (copper): raceway (<= 3 conductors) vs single in free air.
NEC_RACEWAY_TO_FREE_AIR = {18: 14 / 18, 16: 18 / 24, 14: 25 / 35, 12: 30 / 40, 10: 40 / 55, 8: 55 / 80}
NEC_ADJUSTMENT = [(3, 1.0), (6, 0.80), (9, 0.70), (20, 0.50), (30, 0.45), (40, 0.40), (math.inf, 0.35)]
MOTOR_SIGNALS = ("MOTOR",)
POWER_SIGNALS = ("VBAT", "BEC", "5V", "3V3", "VCC")


def category(signal: str, awg: int) -> str:
    s = signal.upper()
    if any(m in s for m in MOTOR_SIGNALS):
        return "motor"
    if awg <= 18 or any(p == s or s.startswith(p) for p in POWER_SIGNALS):
        return "power"
    return "signal"


def bundle_factor(conductors: int, awg: int) -> float:
    if conductors <= 1:
        return 1.0
    base = NEC_RACEWAY_TO_FREE_AIR.get(awg, 0.73)
    for limit, factor in NEC_ADJUSTMENT:
        if conductors <= limit:
            return base * factor
    return base * 0.35  # pragma: no cover


def ambient_factor(local_c: float) -> float:
    if local_c >= CONDUCTOR_LIMIT_C:
        return 0.0
    return math.sqrt((CONDUCTOR_LIMIT_C - local_c) / (CONDUCTOR_LIMIT_C - REFERENCE_AMBIENT_C))


@dataclass
class BundleReport:
    diagnostics: list[Diagnostic]
    groups: list[dict[str, Any]]
    derating: dict[str, dict[str, Any]]
    edges: list[dict[str, Any]]
    contacts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _Sample:
    points: np.ndarray
    tangents: np.ndarray
    harness: np.ndarray
    segment: np.ndarray  # global segment index
    radius: np.ndarray


def _samples(routing: Any) -> tuple[_Sample, list[dict[str, Any]]]:
    pts, tan, har, seg, rad = [], [], [], [], []
    segments = []
    for harness_id, route in routing.harnesses.items():
        for seg_data in route.segments:
            p = np.asarray(seg_data["points"], dtype=float)
            if len(p) < 2:
                continue
            dense, _ = resample_polyline(p, SAMPLE)
            t = np.gradient(dense, axis=0)
            t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-12)
            index = len(segments)
            segments.append({"harness": harness_id, "wires": list(seg_data["wires"]), "od": float(seg_data["od"])})
            pts.append(dense)
            tan.append(t)
            har.extend([harness_id] * len(dense))
            seg.extend([index] * len(dense))
            rad.extend([float(seg_data["od"]) / 2] * len(dense))
    if not pts:
        z = np.zeros((0, 3))
        return _Sample(z, z, np.zeros(0, dtype=object), np.zeros(0, dtype=int), np.zeros(0)), segments
    return _Sample(np.vstack(pts), np.vstack(tan), np.asarray(har, dtype=object), np.asarray(seg),
                   np.asarray(rad)), segments


def analyze_bundles(project: Any, obstacles: ObstacleSet, routing: Any) -> BundleReport:
    wires = {w.id: w for h in project.spec.electrical.harnesses for w in h.wires}
    samples, segments = _samples(routing)
    for seg in segments:
        seg["categories"] = sorted({category(wires[w].signal, wires[w].awg) for w in seg["wires"] if w in wires})
        seg["current_carrying"] = [w for w in seg["wires"] if w in wires and wires[w].awg <= CURRENT_CARRYING_AWG]
    diagnostics: list[Diagnostic] = []
    contacts: list[dict[str, Any]] = []
    parent = list(range(len(segments)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    if len(samples.points):
        tree = cKDTree(samples.points)
        reach = 2 * float(samples.radius.max()) + max(SHARED_GAP, max(SEPARATION.values()))
        pairs = tree.query_pairs(reach, output_type="ndarray")
        if len(pairs):
            a, b = pairs[:, 0], pairs[:, 1]
            other = samples.harness[a] != samples.harness[b]
            a, b = a[other], b[other]
            d = np.linalg.norm(samples.points[a] - samples.points[b], axis=1)
            gap = d - samples.radius[a] - samples.radius[b]
            by_pair: dict[tuple[int, int], list[int]] = {}
            for k in range(len(a)):
                key = tuple(sorted((int(samples.segment[a[k]]), int(samples.segment[b[k]]))))
                by_pair.setdefault(key, []).append(k)
            for (sa, sb), ks in by_pair.items():
                ks = np.asarray(ks)
                worst = ks[int(np.argmin(gap[ks]))]
                pa, pb = samples.points[a[worst]], samples.points[b[worst]]
                cos = abs(float(samples.tangents[a[worst]] @ samples.tangents[b[worst]]))
                angle = math.acos(min(1.0, cos))
                close = ks[gap[ks] <= SHARED_GAP]
                run = SAMPLE * len(np.unique(a[close])) if len(close) else 0.0
                ha, hb = segments[sa]["harness"], segments[sb]["harness"]
                entry = {"harnesses": [ha, hb], "segments": [sa, sb], "min_gap": float(gap[worst]),
                         "witness": [pa.tolist(), pb.tolist()], "angle_deg": math.degrees(angle), "shared_run": run}
                contacts.append(entry)
                if gap[worst] < -0.0002:
                    diagnostics.append(Diagnostic.error(
                        "harness_interference", f"{ha} and {hb} bundles overlap by {-gap[worst] * 1e3:.1f} mm",
                        subjects=[ha, hb, *segments[sa]["wires"], *segments[sb]["wires"]],
                        data={**entry, "overlap": float(-gap[worst]), "units": "m, deg"},
                        hint="Route one harness around the other or tie them into one bundle on a common path."))
                if run >= PARALLEL_MIN and angle < PARALLEL_ANGLE:
                    parent[find(sa)] = find(sb)
                elif gap[worst] < 0.0005 and angle < CROSSING_MIN_ANGLE:
                    diagnostics.append(Diagnostic.warning(
                        "harness_crossing_contact",
                        f"{ha} crosses {hb} at {math.degrees(angle):.0f} deg with the bundles touching",
                        subjects=[ha, hb], data={**entry, "required_angle_deg": math.degrees(CROSSING_MIN_ANGLE),
                                                 "units": "m, deg"},
                        hint="Cross closer to 90 deg or add 1 mm separation (abrasion)."))
                cats = {c for c in segments[sa]["categories"]} | {c for c in segments[sb]["categories"]}
                for pair, required in SEPARATION.items():
                    left, right = sorted(pair)
                    has = (left in segments[sa]["categories"] and right in segments[sb]["categories"]) or \
                          (right in segments[sa]["categories"] and left in segments[sb]["categories"])
                    if not has:
                        continue
                    near = ks[gap[ks] < required]
                    parallel = SAMPLE * len(np.unique(a[near])) if len(near) else 0.0
                    if parallel >= PARALLEL_MIN and angle < PARALLEL_ANGLE:
                        diagnostics.append(Diagnostic.warning(
                            "emc_segregation_violation",
                            f"{ha} and {hb} ({' / '.join(sorted(pair))}) run {parallel * 1e3:.0f} mm in parallel "
                            f"{max(gap[worst], 0) * 1e3:.1f} mm apart (keep {required * 1e3:.0f} mm)",
                            subjects=[ha, hb], data={**entry, "parallel_run": parallel, "required_separation": required,
                                                     "categories": sorted(cats), "units": "m, deg"},
                            hint="Separate the runs or cross them at right angles; twist the motor pair."))
    groups: dict[int, list[int]] = {}
    for i in range(len(segments)):
        groups.setdefault(find(i), []).append(i)
    group_list = []
    group_of: dict[int, int] = {}
    for gid, members in enumerate(groups.values()):
        conductors = sorted({w for m in members for w in segments[m]["wires"]})
        carrying = sorted({w for m in members for w in segments[m]["current_carrying"]})
        harnesses = sorted({segments[m]["harness"] for m in members})
        for m in members:
            group_of[m] = gid
        group_list.append({"id": gid, "harnesses": harnesses, "segments": members, "conductors": len(conductors),
                           "current_carrying": len(carrying), "wires": conductors})
    derating = _derate(project, obstacles, routing, segments, group_list, group_of, wires)
    from .supports import support_hosts

    hosts = support_hosts(project, obstacles)
    exits = np.array([n.exit for nodes in routing.nodes.values() for n in nodes.values()
                      if n.kind in ("connector", "floating") and n.exit is not None]).reshape(-1, 3)
    edges = _edge_protection(hosts, samples, segments, diagnostics, exits)
    return BundleReport(diagnostics, group_list, derating, edges, contacts)


def _derate(project: Any, obstacles: ObstacleSet, routing: Any, segments: list[dict[str, Any]],
            groups: list[dict[str, Any]], group_of: dict[int, int], wires: dict[str, Any]) -> dict[str, dict[str, Any]]:
    catalog = project.catalog
    ambient = float(project.spec.simulation.environment.ambient_temperature_c)
    sources = []
    for o in obstacles.obstacles:
        try:
            kind = catalog.component(project.spec.components[o.component].type).kind
        except KeyError:
            continue
        if kind in HEAT_RISE:
            sources.append((o, *HEAT_RISE[kind]))
    out: dict[str, dict[str, Any]] = {}
    for wire_id, route in routing.wires.items():
        wire = wires[wire_id]
        gauge = catalog.wire(wire.awg)
        mine = [i for i, s in enumerate(segments) if wire_id in s["wires"]]
        worst_group = max((groups[group_of[i]] for i in mine), key=lambda g: g["current_carrying"], default=None)
        n = worst_group["current_carrying"] if worst_group else 1
        n = max(n, 1)
        kb = bundle_factor(n, wire.awg) if wire.awg <= CURRENT_CARRYING_AWG else 1.0
        pts, _ = resample_polyline(route.path, 0.003)
        rise, hot = ENCLOSURE_RISE, ""
        for o, reach, extra in sources:
            shape = o.swept if o.moving else o.shape
            if float(shape.sdf_bounded(pts, 0.05).min()) <= reach and extra + ENCLOSURE_RISE > rise:
                rise, hot = extra + ENCLOSURE_RISE, o.component
        local = ambient + rise
        ka = ambient_factor(local)
        free_air = float(gauge.ampacity)
        out[wire_id] = {"free_air_ampacity": free_air, "bundle_factor": kb, "ambient_factor": ka,
                        "installed_ampacity": free_air * kb * ka, "current_carrying_in_bundle": n,
                        "bundle_group": worst_group["id"] if worst_group else None,
                        "bundle_harnesses": worst_group["harnesses"] if worst_group else [route.harness],
                        "local_ambient_c": local, "hottest_neighbour": hot, "conductor_limit_c": CONDUCTOR_LIMIT_C,
                        "source": "NEC 310.16/310.17 ratio and 310.15(C)(1) adjustment; NEC Eq. 310.15(B) ambient; "
                                  "enclosure rise estimated"}
    return out


def sharp_edges(shape: MeshShape, min_angle_deg: float = SHARP_EDGE_DEG) -> np.ndarray:
    """Convex mesh edges whose face normals differ by at least ``min_angle_deg``: (n, 2, 3)."""
    faces = np.asarray(shape.faces)
    v = shape.vertices
    normals = np.cross(v[faces[:, 1]] - v[faces[:, 0]], v[faces[:, 2]] - v[faces[:, 0]])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-15)
    edges: dict[tuple[int, int], list[int]] = {}
    for f, tri in enumerate(faces):
        for i in range(3):
            a, b = int(tri[i]), int(tri[(i + 1) % 3])
            edges.setdefault((min(a, b), max(a, b)), []).append(f)
    out = []
    cos_limit = math.cos(math.radians(min_angle_deg))
    for (a, b), fs in edges.items():
        if len(fs) != 2:
            continue
        n1, n2 = normals[fs[0]], normals[fs[1]]
        if float(n1 @ n2) > cos_limit:
            continue
        other = [int(x) for x in faces[fs[1]] if int(x) not in (a, b)]
        if not other or float((v[other[0]] - v[a]) @ n1) > 1e-9:
            continue  # concave (re-entrant) edge: nothing to cut on
        out.append((v[a], v[b]))
    return np.asarray(out) if out else np.zeros((0, 2, 3))


def _edge_protection(hosts: list[Any], samples: _Sample, segments: list[dict[str, Any]],
                     diagnostics: list[Diagnostic], exits: np.ndarray) -> list[dict[str, Any]]:
    """Sharp edges of fabricated structure (purchased parts are proxy boxes whose edges
    are not real) next to a bundle, outside the connector termination zones."""
    found: dict[tuple[str, str], dict[str, Any]] = {}
    if not len(samples.points):
        return []
    free = np.ones(len(samples.points), dtype=bool)
    if len(exits):
        d_exit, _ = cKDTree(exits).query(samples.points)
        free = d_exit > TERMINATION_ZONE
    if not free.any():
        return []
    index = np.nonzero(free)[0]
    tree = cKDTree(samples.points[index])
    for o in hosts:
        if not isinstance(o.shape, MeshShape):
            continue
        lo, hi = o.shape.aabb()
        near = np.all((samples.points >= lo - 0.02) & (samples.points <= hi + 0.02), axis=1)
        if not near.any():
            continue
        edges = sharp_edges(o.shape)
        if not len(edges):
            continue
        pts = []
        for a, b in edges:
            n = max(2, int(math.ceil(np.linalg.norm(b - a) / 0.0005)) + 1)
            pts.append(a + (b - a) * np.linspace(0, 1, n)[:, None])
        edge_pts = np.vstack(pts)
        d, local = tree.query(edge_pts, distance_upper_bound=0.02)
        ok = np.isfinite(d)
        idx = np.where(ok, index[np.minimum(local, len(index) - 1)], 0)
        if not ok.any():
            continue
        gap = d[ok] - samples.radius[idx[ok]]
        hit = gap <= EDGE_GAP
        if not hit.any():
            continue
        for k in np.nonzero(hit)[0]:
            sample = idx[ok][k]
            harness = samples.harness[sample]
            key = (harness, o.component)
            item = found.setdefault(key, {"harness": harness, "component": o.component, "gap": math.inf,
                                          "length": 0.0, "position": None, "segments": set()})
            item["length"] += 0.0005
            item["segments"].add(int(samples.segment[sample]))
            if gap[k] < item["gap"]:
                item.update(gap=float(gap[k]), position=edge_pts[ok][k].tolist())
    out = []
    for item in found.values():
        od = max(segments[s]["od"] for s in item["segments"])
        protection = {"description": f"PE spiral wrap, {max(3.0, od * 1e3 + 1):.0f} mm, "
                                     f"{max(30.0, item['length'] * 1e3 + 20):.0f} mm long, over the bundle at the edge",
                      "length": max(0.03, item["length"] + 0.02)}
        entry = {**{k: v for k, v in item.items() if k != "segments"}, "protection": protection,
                 "bundle_od": od}
        out.append(entry)
        diagnostics.append(Diagnostic.warning(
            "edge_protection_required",
            f"{item['harness']} passes {max(item['gap'], 0) * 1e3:.1f} mm from a sharp edge of {item['component']}",
            subjects=[item["harness"], item["component"]], data={**entry, "units": "m"},
            hint="Spiral wrap / grommet edge strip added to the BOM; or chamfer the edge in CAD."))
    return out


def installed_ratings(report: BundleReport) -> dict[str, dict[str, Any]]:
    """Per-wire fields for ``routes.json`` (read by the electrical stage)."""
    return {w: {"installed_ampacity": round(d["installed_ampacity"], 4),
                "derating": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()
                             if k != "installed_ampacity"}}
            for w, d in report.derating.items()}


__all__ = ["BundleReport", "ambient_factor", "analyze_bundles", "bundle_factor", "category", "installed_ratings",
           "sharp_edges"]

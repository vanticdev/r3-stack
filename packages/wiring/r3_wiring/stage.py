"""Pipeline stage 4, ``wiring``: route, plan, support, check and document harnesses.

Order (each step works on the geometry of the previous one):

1. obstacles from ``build/cad`` and a CAD mesh audit (closure, winding, units,
   placement, staleness: :mod:`.witness`);
2. voxel routing (:mod:`.router`, topology and corridors);
3. curvature-constrained planning with shared occupancy (:mod:`.planner`);
4. supports placed as hardware on structure faces (:mod:`.supports`), then a
   second planning pass through them, and the unsupported-span check;
5. cut lengths from spans, bends and terminations (:mod:`.cutlength`);
6. physical checks (:mod:`.checks`) with every clearance finding re-measured by
   exact geometry and MuJoCo, witnesses attached, disagreements reported as
   ``geometry_uncertain`` (:mod:`.witness`);
7. bundle occupancy, segregation, edge protection and derating (:mod:`.bundles`);
8. MuJoCo cable slack drive with support constraints and terminal loads;
9. validity per wire and harness, release gate, manufacturing package
   (WireViz, BOM, cut list, tooling, labels, branch dimensions, assembly
   sequence, continuity test: :mod:`.manufacturing`).

Artifacts under ``build/wiring/``: ``routes.json`` (contract plus ``valid``,
``violations``, supports, cut-length breakdown, installed ampacity),
``routing_detail.json``, ``harness_bom.csv``, ``slack_sim.json``,
``geometry_audit.json``, ``supports.json``, ``cut_lengths.json``,
``bundles.json``, ``manufacturing/``, renders and ``result.json``.
"""

from __future__ import annotations

import copy
import json
import time
from collections import Counter
from typing import Any

import numpy as np

from r3_model.diagnostics import Diagnostic, Severity, StageResult
from r3_model.project import Project

from .bom import write_bom
from .bundles import analyze_bundles, installed_ratings
from .checks import run_checks
from .cutlength import apply_cut_lengths, compute_cut_lengths, cut_report, terminal_diagnostics
from .driver import DriveProfile, run_drive
from .export import detail_json, routes_json
from .manufacturing import release_status, write_package
from .obstacles import build_obstacles, describe
from .planner import plan_routing
from .render import render_routes, render_slack
from .router import HarnessRouter, RouterOptions
from .supports import check_spans, place_supports, support_constraints
from .witness import audit_cad, verify_clearances

STAGE = "wiring"
#: Router surface weight used by the stage: routes that hug structure can be supported
#: (the router's own default, 0.6, lets signal leads float mid-air between parts).
SUPPORTABLE_SURFACE_WEIGHT = 3.0
SUPPORT_RETRIES = 2


def route_and_check(project: Project, options: RouterOptions | None = None, *, plan: bool = True,
                    verify: bool = True) -> dict[str, Any]:
    options = options or RouterOptions(surface_weight=SUPPORTABLE_SURFACE_WEIGHT)
    obstacles, diagnostics = build_obstacles(project)
    audit_diagnostics, audit = audit_cad(project, obstacles)
    router = HarnessRouter(project, obstacles, options)
    routing = router.route()
    before = {w: r.path.copy() for w, r in routing.wires.items()}
    planning = supports = None
    planning_diagnostics: list[Diagnostic] = []
    if plan:
        first = plan_routing(project, router, routing, obstacles=obstacles)
        snapshot = copy.deepcopy(routing)
        supports = place_supports(project, obstacles, routing)
        planning = plan_routing(project, router, routing, supports=support_constraints(supports), obstacles=obstacles)
        # A support whose fixed point / face-parallel axis makes a route infeasible is dropped
        # and its group re-planned from the unsupported plan (the span check then reports the gap).
        for _ in range(SUPPORT_RETRIES):
            blamed = _blamed_supports(supports, planning, first)
            if not blamed:
                break
            supports.supports = [s for s in supports.supports if s.id not in blamed]
            supports.dropped.extend(sorted(blamed))
            routing = copy.deepcopy(snapshot)
            planning = plan_routing(project, router, routing, supports=support_constraints(supports),
                                    obstacles=obstacles)
        planning.stats["first_pass"] = first.stats
        planning_diagnostics = planning.diagnostics
        for harness_id, harness in routing.harnesses.items():
            hints = [np.asarray(t, float) for h in project.spec.electrical.harnesses if h.id == harness_id
                     for t in h.route.tie_points]
            harness.tie_points = [s.position.copy() for s in supports.supports if s.harness == harness_id] + hints
        check_spans(project, routing, supports)
        cuts = compute_cut_lengths(project, routing, supports.spans, obstacles.kinematics)
    else:
        cuts = compute_cut_lengths(project, routing, [], obstacles.kinematics)
    apply_cut_lengths(routing, cuts)
    report = run_checks(project, obstacles, routing)
    check_diagnostics, witnesses = (verify_clearances(report.diagnostics, obstacles, routing, project.catalog)
                                    if verify else (report.diagnostics, []))
    bundles = analyze_bundles(project, obstacles, routing)
    routes = routes_json(routing, report.clearances)
    _extend_routes(routes, routing, cuts, supports, bundles)
    all_diagnostics = (diagnostics + audit_diagnostics + routing.diagnostics + planning_diagnostics +
                       (supports.diagnostics if supports else []) + check_diagnostics + bundles.diagnostics)
    return {"obstacles": obstacles, "routing": routing, "report": report, "routes": routes, "router": router,
            "planning": planning, "supports": supports, "cuts": cuts, "bundles": bundles, "audit": audit,
            "witnesses": witnesses, "before": before, "diagnostics": all_diagnostics}


def _blamed_supports(supports: Any, planning: Any, first: Any) -> set[str]:
    """Supports whose own junction is infeasible, in groups that pass 2 made worse than pass 1."""
    before = {"+".join(p.harnesses): len(p.violations) for p in first.plans}
    blamed = set()
    for plan in planning.plans:
        if len(plan.violations) <= before.get("+".join(plan.harnesses), 0):
            continue
        for v in plan.violations:
            for s in supports.supports:
                if v.junction == f"{s.harness}/S:{s.id.split('.')[-1]}":
                    blamed.add(s.id)
    return blamed


def _extend_routes(routes: dict[str, Any], routing: Any, cuts: dict[str, Any], supports: Any, bundles: Any) -> None:
    """Additive routes.json fields (the contract keys stay as they are)."""
    ratings = installed_ratings(bundles)
    for wire_id, entry in routes["wires"].items():
        route = routing.wires[wire_id]
        entry["color"] = route.color
        entry["od"] = round(route.od, 6)
        entry.update(ratings.get(wire_id, {}))
        if wire_id in cuts:
            cut = cuts[wire_id]
            entry["cut"] = {"nominal": round(cut.nominal, 6), "tolerance": [round(cut.tolerance_plus, 6), 0.0],
                            "centreline": round(cut.centreline, 6), "outside_bend_allowance": round(cut.bend_extra, 6),
                            "span_slack": round(cut.span_slack, 6), "service_loops": round(cut.rework, 6),
                            "terminations": round(cut.end_allowance, 6)}
    if supports is not None:
        by_harness = supports.by_harness()
        for harness_id, entry in routes["harnesses"].items():
            entry["supports"] = [{"id": s["id"], "type": s["type"], "mpn": s["mpn"], "host": s["host"],
                                  "position": [round(c, 6) for c in s["position"]],
                                  "permitted_motion": s["permitted_motion"]} for s in by_harness.get(harness_id, [])]


def _apply_release(routes: dict[str, Any], release: dict[str, Any]) -> None:
    for wire_id, entry in routes["wires"].items():
        info = release["wires"].get(wire_id, {"valid": True, "violations": []})
        entry["valid"] = info["valid"]
        entry["violations"] = [{"code": v["code"], "message": v["message"]} for v in info["violations"]]
    for harness_id, entry in routes["harnesses"].items():
        info = release["harnesses"].get(harness_id, {"valid": True, "violations": []})
        entry["valid"] = info["valid"]
        entry["violations"] = [{"code": v["code"], "message": v["message"]} for v in info["violations"]]
    routes["release"] = {"status": release["status"], "blocking_codes": release["blocking_codes"],
                         "rule": release["rule"]}


def _dynamic_diagnostics(drive, routing) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    by_harness: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]] = {}
    for wire, item in drive.min_clearance.items():
        if item["min_distance"] < drive.requirement:
            harness = routing.wires[wire].harness if wire in routing.wires else ""
            by_harness.setdefault((harness, item["component"]), []).append((wire, item))
    for (harness, component), items in by_harness.items():
        wire, worst = min(items, key=lambda kv: kv[1]["min_distance"])
        data = {"min_distance": worst["min_distance"], "required": drive.requirement, "component": component,
                "time": worst["time"], "worst_wire": wire, "load_case": "0.5 g 25 Hz + 3 g bump + lock-to-lock steering",
                "per_wire_min_distance": {w: i["min_distance"] for w, i in items}, "units": "m"}
        subjects = [harness, *sorted(w for w, _ in items), component]
        if worst["min_distance"] < 0:
            out.append(Diagnostic.error(
                "cable_sags_into_moving_part",
                f"{harness} cable enters the {component} envelope by {-worst['min_distance'] * 1e3:.1f} mm "
                f"during the slack simulation (t = {worst['time']:.3f} s)", subjects=subjects, data=data,
                hint="Add a tie point or shorten the slack on this span."))
        else:
            out.append(Diagnostic.error(
                "wire_dynamic_clearance_violation",
                f"{harness} comes within {worst['min_distance'] * 1e3:.1f} mm of moving {component} during the "
                f"slack simulation (required {drive.requirement * 1e3:.0f} mm)", subjects=subjects, data=data))
    return out


def _assumptions(result: StageResult, estimated: bool, drive: Any = None) -> None:
    if estimated:
        result.assume("geometry", "Obstacles are catalog boxes/cylinders posed by component poses (no CAD export); "
                                  "custom shapes such as the tapered chassis plate use their bounding box.")
    result.assume("geometry", "Catalog convention: box frames sit on their bottom face; x/y-axis cylinders are centred on "
                              "the component origin; z-axis cylinders (standoffs) start at their mounting face.")
    result.assume("geometry", "Swept volumes sample joint travel at 1 deg (steering about the kingpin); spinning "
                              "cylinders about their own axis are invariant. Moving parts without joints (motor can, "
                              "gearbox) use their own envelope.")
    result.assume("geometry", "Clearance findings are re-measured with exact point-to-triangle distance (winding-number "
                              "sign) and MuJoCo mj_geomDistance on the parts' convex pieces; they stand only when the "
                              "representations agree within 1 mm + 5 %, else geometry_uncertain.")
    result.assume("routing", f"Voxel A* (3 mm) with length, surface-following (weight {SUPPORTABLE_SURFACE_WEIGHT}), "
                             "moving-proximity and bend costs gives the topology; the curvature-constrained planner "
                             "(SciPy SLSQP elastic band) then enforces bend radius, junction tangency, clearance and "
                             "shared occupancy with harnesses planned earlier (heaviest first).")
    result.assume("routing", "Bend radius of loose bundles: factor x OD of the largest wire at the innermost conductor "
                             "(SAE AS50881 references bundle bends to the largest wire); jacketed bundles: factor x "
                             "bundle OD. The router's own (factor x bundle OD) value only guides its search.")
    result.assume("routing", "Splices may move up to 25 mm, breakouts 30 mm, floating mated pairs 20 mm and integral "
                             "pigtail exits 30 deg from robot.yaml / the router; moves are reported as recommended edits.")
    result.assume("supports", "Supports are catalog hardware (data/supports.yaml) placed on rigid static structure; "
                              "max unsupported span from a clamped-clamped beam sagging 3 mm at 4 g, capped at 100 mm "
                              "(estimates). Splices and floating mated pairs count as held (taped into the bundle).")
    result.assume("cut_length", "Cut length = planned centreline + outside-of-bend allowance + 2 % span slack (>= 2 mm) "
                                "+ strip + insertion depth + one re-termination per removable end; tolerance +3/-0 mm "
                                "(<= 300 mm), +5/-0 mm (<= 1 m). Strip/insertion values are estimates per terminal.")
    result.assume("derating", "Installed ampacity = free-air catalog ampacity x NEC 310.16/310.17 ratio x NEC "
                              "310.15(C)(1) bundle adjustment x sqrt((55 C - T_local) / 30 K); enclosure rise near "
                              "heat sources estimated (+15 K motor/ESC, +8 K battery/Pi/HAT, +5 K elsewhere).")
    for domain, statement in (drive.assumptions if drive is not None else []):
        result.assume("simulation" if domain == "wiring" else domain, statement)
    result.assume("simulation", "Slack drive on a MuJoCo shaker: chassis fixed, its translational acceleration applied "
                                "as effective gravity R^T (g - a) (equivalence principle), jointed parts driven through "
                                "stiff joint equality constraints; moving parts push cables they touch. Supports hold "
                                "cables by their permitted motion (pinned: ball + connect; clamped: weld); terminal "
                                "loads are the cable interaction forces next to each end (cfrc_int).")


def run(project: Project, options: RouterOptions | None = None, drive_profile: DriveProfile | None = None,
        render: bool = True) -> StageResult:
    started = time.time()
    result = StageResult(stage=STAGE)
    result.inputs = project.fingerprint()
    out = project.build_dir(STAGE)
    bundle = route_and_check(project, options)
    obstacles, routing, report, routes = bundle["obstacles"], bundle["routing"], bundle["report"], bundle["routes"]
    supports, cuts, bundles = bundle["supports"], bundle["cuts"], bundle["bundles"]
    result.extend(bundle["diagnostics"])

    drive = run_drive(project, routes, obstacles, drive_profile)
    result.extend(_dynamic_diagnostics(drive, routing))
    result.extend(terminal_diagnostics(cuts, drive.terminal_loads))
    for wire_id, load in drive.terminal_loads.items():
        if wire_id in routes["wires"]:
            routes["wires"][wire_id]["terminal_load"] = {"peak_tension": [round(v, 4) for v in load["end_tension"]],
                                                         "limit": [round(v, 4) if np.isfinite(v) else None
                                                                   for v in cuts[wire_id].pull_limits]}

    release = release_status(project, result.diagnostics)
    _apply_release(routes, release)
    (out / "routes.json").write_text(json.dumps(routes, indent=1))
    result.artifact("wiring/routes.json", "json", "Routed wire paths, harness bundles, ties and clearances (contract), "
                                                  "per-wire/harness validity, supports, cut lengths, installed ampacity")
    detail = detail_json(routing)
    detail["connector_clearances"] = report.connector_clearances
    detail["wire_min_clearance"] = report.wire_min_clearance
    detail["harness_mass"] = report.harness_mass
    detail["obstacles"] = describe(obstacles)
    detail["pins"] = {w: [{"arc": p.arc, "position": np.asarray(p.position).tolist(), "body": p.body, "kind": p.kind}
                          for p in pins] for w, pins in report.pins.items()}
    detail["planning"] = _planning_detail(bundle["planning"])
    detail["witnesses"] = bundle["witnesses"]
    (out / "routing_detail.json").write_text(json.dumps(detail, indent=1, default=float))
    result.artifact("wiring/routing_detail.json", "json", "Routing internals: nodes, segments, bends, mates, holes, "
                                                          "planner statistics, clearance witnesses")
    _dump(out / "geometry_audit.json", bundle["audit"])
    result.artifact("wiring/geometry_audit.json", "json", "CAD mesh audit: closure, winding, units, placement, staleness")
    if supports is not None:
        _dump(out / "supports.json", {"supports": [s.as_dict() for s in supports.supports], "dropped": supports.dropped,
                                      "spans": supports.spans,
                                      "limits": supports.limits})
        result.artifact("wiring/supports.json", "json", "Cable supports (hardware, pose, host, checks) and spans")
    _dump(out / "cut_lengths.json", cut_report(cuts))
    result.artifact("wiring/cut_lengths.json", "json", "Cut-length breakdown, tolerances and terminal pull limits")
    _dump(out / "bundles.json", {"groups": bundles.groups, "derating": bundles.derating, "edges": bundles.edges,
                                 "contacts": bundles.contacts})
    result.artifact("wiring/bundles.json", "json", "Shared occupancy, crossings, edge protection and derating")
    write_bom(out / "harness_bom.csv", project, routing, release["status"])
    result.artifact("wiring/harness_bom.csv", "csv", f"Harness cut list [{release['status'].upper()}]")
    manifest = write_package(project, routing, cuts, supports, bundles, release, out / "manufacturing", render=render)
    result.artifact("wiring/manufacturing/manifest.json", "json",
                    f"Manufacturing package [{release['status'].upper()}]: WireViz drawings, BOM, cut list, tooling, "
                    "labels, branch dimensions, assembly sequence, continuity test")
    for name in manifest["files"]:
        if name.endswith(".png"):
            result.artifact(f"wiring/manufacturing/{name}", "plot_png", "WireViz harness drawing")
    for error in manifest["errors"]:
        result.add(Diagnostic.warning("wireviz_failed", error, subjects=["wiring"], data={"error": error}))
    for cap, entry in sorted(drive.cap_clearance.items()):
        if entry["min_gap"] < 0:
            result.add(Diagnostic.error("cap_cable_interference",
                                        f"cable {entry['wire']} touches printed cap {cap} ({entry['min_gap'] * 1e3:.2f} mm) at t = {entry['time']:.2f} s",
                                        subjects=[cap, entry["wire"]], data=entry,
                                        hint="Reroute the harness away from the cap (route.via) or move the cap's screw."))
    (out / "slack_sim.json").write_text(json.dumps(drive.summary(), indent=1, default=float))
    result.artifact("wiring/slack_sim.json", "json", "Cable slack simulation: clearance to moving parts, terminal loads")

    if render:
        title = project.spec.robot.name
        colors = {w: r.color for w, r in routing.wires.items()}
        for path in render_routes(out, obstacles, routes, detail, title):
            result.artifact(f"wiring/{path.name}", "plot_png", "Harness routing render")
        for path in render_slack(out, obstacles, routes, detail, drive.snapshot, drive.timeline, drive.requirement, title):
            result.artifact(f"wiring/{path.name}", "plot_png", "Cable slack simulation render")
        from .views import render_derating, render_planning, render_support_details, render_supports, render_witnesses

        if supports is not None:
            placed = [s.as_dict() for s in supports.supports]
            for path in render_supports(out, obstacles, routes, colors, placed, supports.spans, title) + \
                    render_support_details(out, obstacles, routes, colors, placed, title):
                result.artifact(f"wiring/{path.name}", "plot_png", "Cable supports render")
        for path in render_witnesses(out, obstacles, routes, colors, bundle["witnesses"], title):
            result.artifact(f"wiring/{path.name}", "plot_png", "Clearance witness pairs")
        violations = [d.data for d in result.diagnostics if d.code == "route_infeasible"]
        for path in render_planning(out, obstacles, bundle["before"], routes, colors, violations, title):
            result.artifact(f"wiring/{path.name}", "plot_png", "Router vs. curvature-constrained plan")
        for path in render_derating(out, bundles.derating, None, title):
            result.artifact(f"wiring/{path.name}", "plot_png", "Installed ampacity")

    static_min = {w: v["distance"] for w, v in report.wire_min_clearance.items()}
    worst_static = min(report.wire_min_clearance.items(), key=lambda kv: kv[1]["distance"], default=(None, {}))
    result.metrics = {
        "harness_length": {h: r.length for h, r in routing.harnesses.items()},
        "wire_routed_length": {w: r.routed_length for w, r in routing.wires.items()},
        "wire_cut_length": {w: r.cut_length for w, r in routing.wires.items()},
        "wire_min_clearance_moving": static_min,
        "worst_static_clearance": {"wire": worst_static[0], **worst_static[1]},
        "worst_dynamic_clearance": drive.worst,
        "cable_sags_into_moving_part": any(drive.sags_into_moving.values()),
        "cap_cable_min_gap": {cap: round(v["min_gap"], 5) for cap, v in drive.cap_clearance.items()},
        "harness_mass": {h: v["mass"] for h, v in report.harness_mass.items()},
        "tie_points": sum(len(h.tie_points) for h in routing.harnesses.values()),
        "supports": len(supports.supports) if supports else 0,
        "pass_throughs": len(routing.holes),
        "release": release["status"],
        "valid_harnesses": sorted(h for h, v in release["harnesses"].items() if v["valid"]),
        "invalid_harnesses": sorted(h for h, v in release["harnesses"].items() if not v["valid"]),
        "valid_wires": sum(1 for v in release["wires"].values() if v["valid"]),
        "wires": len(release["wires"]),
        "moved_junctions": bundle["planning"].moved if bundle["planning"] else {},
        "diagnostic_counts": dict(Counter(d.code for d in result.diagnostics)),
        "severity_counts": dict(Counter(d.severity.value for d in result.diagnostics)),
        "router": routing.stats,
        "planner": bundle["planning"].stats if bundle["planning"] else {},
        "slack_sim": {"max_segment_speed": drive.max_speed, "max_pin_error_m": drive.max_strain,
                      "duration": drive.profile.duration, "engine": "mujoco.elasticity.cable"},
    }
    _assumptions(result, bool(obstacles.estimated), drive)
    result.duration_s = time.time() - started
    result.write(out / "result.json")
    return result


def _planning_detail(planning: Any) -> dict[str, Any]:
    if planning is None:
        return {}
    return {"stats": planning.stats, "moved": planning.moved,
            "violations": [v.as_dict() for p in planning.plans for v in p.violations],
            "feasible": {"+".join(p.harnesses): p.feasible for p in planning.plans}}


def _dump(path: Any, value: Any) -> None:
    path.write_text(json.dumps(value, indent=1, default=_json_default))


def _json_default(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, set):
        return sorted(value)
    return float(value) if isinstance(value, (int, float)) else str(value)


def format_diagnostics(result: StageResult) -> str:
    lines = []
    for d in result.diagnostics:
        lines.append(f"[{d.severity.value:7s}] {d.code}: {d.message}")
    return "\n".join(lines)


__all__ = ["SUPPORTABLE_SURFACE_WEIGHT", "format_diagnostics", "route_and_check", "run", "STAGE", "Severity"]

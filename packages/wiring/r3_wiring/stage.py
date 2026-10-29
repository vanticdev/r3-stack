"""Pipeline stage 4, ``wiring``: route harnesses, check them, simulate slack (MuJoCo cables).

Reads the derived graph, ``build/cad/cad_model.json`` (optional) and writes:

* ``build/wiring/routes.json``: the contract hand-off (paths, lengths, ties, clearances)
* ``build/wiring/routing_detail.json``: nodes, bundle segments, bends, floating mates, holes
* ``build/wiring/harness_bom.csv``: cut list, connectors, splices, ties, grommets
* ``build/wiring/slack_sim.json``: vibration/bump/steering test results
* PNG renders (isometric, top, side, slack snapshot, clearance timeline)
* ``build/wiring/result.json``: the serialised :class:`StageResult`
"""

from __future__ import annotations

import json
import time
from collections import Counter
from typing import Any

import numpy as np

from r3_model.diagnostics import Diagnostic, StageResult
from r3_model.project import Project

from .bom import write_bom
from .checks import run_checks
from .driver import DriveProfile, run_drive
from .export import detail_json, routes_json
from .obstacles import build_obstacles, describe
from .render import render_routes, render_slack
from .router import RouterOptions, route_project

STAGE = "wiring"


def route_and_check(project: Project, options: RouterOptions | None = None) -> dict[str, Any]:
    obstacles, diagnostics = build_obstacles(project)
    routing = route_project(project, obstacles, options)
    report = run_checks(project, obstacles, routing)
    return {"obstacles": obstacles, "routing": routing, "report": report,
            "routes": routes_json(routing, report.clearances),
            "diagnostics": diagnostics + routing.diagnostics + report.diagnostics}


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
    result.assume("routing", "Voxel A* (3 mm) with length, surface-following, moving-proximity and bend costs, then "
                             "any-angle string pulling and elastic-rod relaxation; wires of one harness bundle on "
                             "shared voxels. Harnesses do not avoid each other.")
    result.assume("routing", "Minimum bend radius = catalog factor x bundle OD, bundle OD from optimal circle packing "
                             "of the conductors (conservative for loose, untaped wires).")
    result.assume("routing", "Floating (harness-to-harness) mated pairs are placed at the copper-mass-weighted geodesic "
                             "optimum with a tie-ability term; mated pair length = sum of bodies - 50% engagement.")
    result.assume("routing", "Cut length = routed length + route.slack + termination allowances (estimates per "
                             "connector family: solder cup 5-6 mm, crimp 3 mm, splice 6 mm).")
    result.assume("checks", "Slack reach uses the ellipsoid |x - p1| + |x - p2| <= L between consecutive pins, ignoring "
                            "gravity and stiffness (conservative); route slack is distributed uniformly over spans.")
    result.assume("checks", "Clearance and intersection ignore a termination zone at each end against the component "
                            "the wire terminates on (and the parts it is mounted on).")
    for domain, statement in (drive.assumptions if drive is not None else []):
        result.assume("simulation" if domain == "wiring" else domain, statement)
    result.assume("simulation", "Slack drive on a MuJoCo shaker: chassis fixed, its translational acceleration applied "
                                "as effective gravity R^T (g - a) (equivalence principle), jointed parts driven through "
                                "stiff joint equality constraints; moving parts push cables they touch.")


def run(project: Project, options: RouterOptions | None = None, drive_profile: DriveProfile | None = None,
        render: bool = True) -> StageResult:
    started = time.time()
    result = StageResult(stage=STAGE)
    result.inputs = project.fingerprint()
    out = project.build_dir(STAGE)
    bundle = route_and_check(project, options)
    obstacles, routing, report, routes = bundle["obstacles"], bundle["routing"], bundle["report"], bundle["routes"]
    result.extend(bundle["diagnostics"])

    (out / "routes.json").write_text(json.dumps(routes, indent=1))
    result.artifact("wiring/routes.json", "json", "Routed wire paths, harness bundles, ties and clearances (contract)")
    detail = detail_json(routing)
    detail["connector_clearances"] = report.connector_clearances
    detail["wire_min_clearance"] = report.wire_min_clearance
    detail["harness_mass"] = report.harness_mass
    detail["obstacles"] = describe(obstacles)
    detail["pins"] = {w: [{"arc": p.arc, "position": np.asarray(p.position).tolist(), "body": p.body, "kind": p.kind}
                          for p in pins] for w, pins in report.pins.items()}
    (out / "routing_detail.json").write_text(json.dumps(detail, indent=1, default=float))
    result.artifact("wiring/routing_detail.json", "json", "Routing internals: nodes, bundle segments, bends, mates, holes")
    write_bom(out / "harness_bom.csv", project, routing)
    result.artifact("wiring/harness_bom.csv", "csv", "Harness BOM: wire cut lengths per AWG/colour, connectors, ties")

    drive = run_drive(project, routes, obstacles, drive_profile)
    result.extend(_dynamic_diagnostics(drive, routing))
    for cap, entry in sorted(drive.cap_clearance.items()):
        if entry["min_gap"] < 0:
            result.add(Diagnostic.error("cap_cable_interference",
                                        f"cable {entry['wire']} touches printed cap {cap} ({entry['min_gap'] * 1e3:.2f} mm) at t = {entry['time']:.2f} s",
                                        subjects=[cap, entry["wire"]], data=entry,
                                        hint="Reroute the harness away from the cap (route.via) or move the cap's screw."))
    (out / "slack_sim.json").write_text(json.dumps(drive.summary(), indent=1, default=float))
    result.artifact("wiring/slack_sim.json", "json", "Cable slack simulation: per-wire minimum clearance to moving parts")

    if render:
        title = project.spec.robot.name
        for path in render_routes(out, obstacles, routes, detail, title):
            result.artifact(f"wiring/{path.name}", "plot_png", "Harness routing render")
        for path in render_slack(out, obstacles, routes, detail, drive.snapshot, drive.timeline, drive.requirement, title):
            result.artifact(f"wiring/{path.name}", "plot_png", "Cable slack simulation render")

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
        "pass_throughs": len(routing.holes),
        "diagnostic_counts": dict(Counter(d.code for d in result.diagnostics)),
        "router": routing.stats,
        "slack_sim": {"max_segment_speed": drive.max_speed, "max_pin_error_m": drive.max_strain,
                      "duration": drive.profile.duration, "engine": "mujoco.elasticity.cable"},
    }
    _assumptions(result, bool(obstacles.estimated), drive)
    result.duration_s = time.time() - started
    result.write(out / "result.json")
    return result


def format_diagnostics(result: StageResult) -> str:
    lines = []
    for d in result.diagnostics:
        lines.append(f"[{d.severity.value:7s}] {d.code}: {d.message}")
    return "\n".join(lines)


__all__ = ["format_diagnostics", "route_and_check", "run", "STAGE"]

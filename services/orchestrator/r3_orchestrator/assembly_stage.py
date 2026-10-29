"""``assembly`` stage: compile objects joined at interfaces ("puzzle pieces").

Runs before ``model``. The compiler itself (:mod:`r3_model.assembly`) runs when
the project loads, so every stage sees the derived poses; this stage reports
its diagnostics, adds the envelope interference pre-check on the compiled
poses, and writes ``build/model/assembly.json`` and ``build/model/assembly_graph.png``.

Codes: compiler codes (``assembly_misfit``, ``hole_pattern_mismatch``,
``hole_size_mismatch``, ``fit_out_of_range``, ``header_mismatch``,
``fastener_length_invalid``, ``clock_ambiguous``, ``interface_kind_mismatch``,
``join_kind_invalid``, ``interface_unknown``, ``interface_occupied``,
``object_not_joined``, ``object_overconstrained_conflict``, ``joint_conflict``,
``pose_conflict``, ``authored_pose_ignored``, ...) plus ``object_interference``
and ``seat_not_in_contact``.
"""

from __future__ import annotations

import itertools
import json
import math
import time
from typing import Any

import numpy as np
from scipy.spatial import HalfspaceIntersection, QhullError

from r3_model.assembly import AssemblyResult, invert
from r3_model.diagnostics import Diagnostic, StageResult
from r3_model.project import Project
from r3_model.units import to_si

from .geometry import TOUCH_TOLERANCE, Solid, _chebyshev_center, make_solid, outline_solid, overlap

STAGE = "assembly"
# Kinds where one part legitimately enters the other's envelope (shaft in a hub bore).
INSERTION_KINDS = {"bore", "ball_socket", "spline"}
INSERTION_TOLERANCE = 1e-4  # m: a 48-gon shaft prism inside a bore of the same nominal size
CONTACT_TOLERANCE = 1e-5  # m: a seat origin this close to its envelope surface is on it


def object_solids(project: Project) -> dict[str, Solid]:
    """Catalog envelopes at the compiled poses (same shapes as the model stage)."""
    solids: dict[str, Solid] = {}
    for cid, comp in project.spec.components.items():
        if comp.type not in project.catalog.components:
            continue
        mech = project.catalog.component(comp.type).mechanical
        outline = comp.params.get("outline") if mech.shape == "custom" else None
        if outline:
            solids[cid] = outline_solid(cid, [[to_si(a), to_si(b)] for a, b in outline], float(mech.size[2]),
                                        comp.pose.xyz, comp.pose.rpy)
        else:
            solids[cid] = make_solid(cid, mech.shape, list(mech.size), mech.axis, comp.pose.xyz, comp.pose.rpy)
    return solids


def _intersection_vertices(a: Solid, b: Solid) -> list[np.ndarray]:
    """Vertices of every convex piece-pair intersection thicker than the touch tolerance."""
    pieces = []
    for pa in a.pieces or (a,):
        for pb in b.pieces or (b,):
            halfspaces = np.vstack([pa.halfspaces, pb.halfspaces])
            found = _chebyshev_center(halfspaces)
            if found is None or found[1] < TOUCH_TOLERANCE:
                continue
            try:
                pieces.append(HalfspaceIntersection(halfspaces, found[0]).intersections)
            except QhullError:
                continue
    return pieces


def _inside_bore(points: np.ndarray, bore_world: np.ndarray, diameter: float, depth: float | None) -> bool:
    """All points lie in the bore cylinder: radius d/2, from the opening (z = 0) to −depth."""
    local = (invert(bore_world) @ np.column_stack([points, np.ones(len(points))]).T).T[:, :3]
    radial = np.hypot(local[:, 0], local[:, 1])
    deepest = -(depth if depth is not None else math.inf) - INSERTION_TOLERANCE
    return bool(np.all(radial <= diameter / 2 + INSERTION_TOLERANCE) and np.all(local[:, 2] <= INSERTION_TOLERANCE)
                and np.all(local[:, 2] >= deepest))


def _insertions(assembly: AssemblyResult) -> dict[frozenset[str], list[tuple[str, str]]]:
    """Joined object pairs -> (join id, bore-side interface ref) for insertion joins."""
    pairs: dict[frozenset[str], list[tuple[str, str]]] = {}
    for join_id, entry in assembly.joins.items():
        if not entry.get("valid"):
            continue
        for ref in (entry["a"], entry["b"]):
            iface = assembly.interfaces.get(ref)
            if iface is not None and iface.kind in INSERTION_KINDS:
                objs = frozenset({entry["a"].split(".")[0], entry["b"].split(".")[0]})
                pairs.setdefault(objs, []).append((join_id, ref))
    return pairs


def check_interference(project: Project, assembly: AssemblyResult, solids: dict[str, Solid], result: StageResult) -> list[dict[str, Any]]:
    """Joined pairs may only touch (or insert a shaft into a bore); nothing may penetrate."""
    joined: dict[frozenset[str], list[str]] = {}
    for join_id, entry in assembly.joins.items():
        joined.setdefault(frozenset({entry["a"].split(".")[0], entry["b"].split(".")[0]}), []).append(join_id)
    insertions = _insertions(assembly)
    found = []
    for a, b in itertools.combinations(sorted(solids), 2):
        hit = overlap(solids[a], solids[b])
        if hit is None:
            continue
        pair = frozenset({a, b})
        allowed_by = None
        for join_id, ref in insertions.get(pair, []):
            iface = assembly.interfaces[ref]
            diameter = iface.quantity("d", 0.0) or 0.0
            world = assembly.interface_world(ref)
            if all(_inside_bore(points, world, diameter, iface.quantity("depth")) for points in _intersection_vertices(solids[a], solids[b])):
                allowed_by = join_id
                break
        entry = {"a": a, "b": b, **hit, "joined": pair in joined, "joins": joined.get(pair, []), "insertion": allowed_by}
        found.append(entry)
        if allowed_by:
            continue
        extent_mm = " x ".join(f"{v * 1e3:.1f}" for v in hit["extent"])
        result.add(Diagnostic.error(
            "object_interference",
            f"{a} and {b} penetrate by {hit['volume'] * 1e9:.0f} mm^3 (region {extent_mm} mm, depth {hit['depth'] * 1e3:.2f} mm)"
            + (f"; they are joined by {', '.join(joined[pair])}" if pair in joined else ""),
            subjects=[a, b, *joined.get(pair, [])],
            data={"overlap_volume": hit["volume"], "depth": hit["depth"], "extent": hit["extent"], "center": hit["center"],
                  "joined": pair in joined, "joins": joined.get(pair, [])},
            hint="A join puts one object inside another: check the seat interface's origin and outward z in the catalog."))
    return found


def _signed_distance(solid: Solid, point: np.ndarray) -> float:
    """Signed distance to a convex envelope (exact on and inside the surface; a lower bound outside)."""
    return min(float(np.max(piece.halfspaces[:, :3] @ point + piece.halfspaces[:, 3])) for piece in (solid.pieces or (solid,)))


def check_seat_contact(project: Project, assembly: AssemblyResult, solids: dict[str, Solid], result: StageResult) -> None:
    """Fasten seats must touch: each planar seat origin lies on its object's envelope."""
    for join_id, entry in assembly.joins.items():
        if not entry.get("valid") or entry["kind"] != "fasten":
            continue
        refs = (entry["a"], entry["b"])
        ifaces = [assembly.interfaces[ref] for ref in refs]
        if not all(i.kind in ("face_mount", "hole_pattern", "adhesive_pad") for i in ifaces):
            continue
        distances = {ref: _signed_distance(solids[i.obj], assembly.interface_world(ref)[:3, 3])
                     for ref, i in zip(refs, ifaces) if i.obj in solids}
        off = {ref: d for ref, d in distances.items() if abs(d) > CONTACT_TOLERANCE}
        if off or abs(entry.get("offset", 0.0)) > CONTACT_TOLERANCE:
            result.add(Diagnostic.warning(
                "seat_not_in_contact",
                f"join {join_id}: seats do not touch (gap {entry.get('offset', 0.0) * 1e3:.3f} mm; "
                + ", ".join(f"{ref} is {d * 1e3:+.3f} mm from its envelope" for ref, d in off.items()) + ")",
                subjects=[ifaces[0].obj, ifaces[1].obj, join_id],
                data={"offset": entry.get("offset", 0.0), "surface_distance": distances},
                hint="Put the seat interface origin on the envelope face (or add the spacer as an object)."))


def _plot(path, assembly: AssemblyResult, solids: dict[str, Solid], interference: list[dict[str, Any]]) -> bool:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.lines import Line2D
        from matplotlib.patches import Rectangle
    except ImportError:
        return False
    colors = {"fasten": "#4c78a8", "revolute": "#f58518", "slider": "#54a24b", "ball": "#b279a2", "drive_flat": "#9d755d"}
    depth = {assembly.root: 0}
    order = [assembly.root]
    for edge in assembly.tree:
        depth[edge.child] = depth[edge.parent] + 1
        order.append(edge.child)
    levels: dict[int, list[str]] = {}
    for obj in order:
        levels.setdefault(depth[obj], []).append(obj)
    position = {}
    for level, objs in levels.items():
        for index, obj in enumerate(objs):
            position[obj] = ((index - (len(objs) - 1) / 2) * 1.6, -level)
    bad_joins = {loop["join"] for loop in assembly.loops if not loop["ok"]}
    bad_joins |= {s for d in assembly.errors for s in d.subjects if s in assembly.joins}

    fig, (ax_graph, ax_side) = plt.subplots(1, 2, figsize=(17, 8), gridspec_kw={"width_ratios": [1.15, 1]})
    for join_id, entry in assembly.joins.items():
        a, b = entry["a"].split(".")[0], entry["b"].split(".")[0]
        if a not in position or b not in position:
            continue
        (xa, ya), (xb, yb) = position[a], position[b]
        loop = entry.get("role") == "loop"
        color = "#d62728" if join_id in bad_joins else colors.get(entry["kind"], "#555555")
        if loop:
            ax_graph.annotate("", (xb, yb), (xa, ya), arrowprops=dict(arrowstyle="-", color=color, lw=1.2, ls="--",
                                                                     connectionstyle="arc3,rad=0.25"))
        else:
            ax_graph.plot([xa, xb], [ya, yb], color=color, lw=2.2, zorder=1)
            ax_graph.text((xa + xb) / 2, (ya + yb) / 2, entry["b"].split(".")[1], fontsize=6, color="#444", ha="center",
                          bbox=dict(fc="white", ec="none", alpha=0.7, pad=0.5))
    for obj, (x, y) in position.items():
        ax_graph.scatter([x], [y], s=520, color="#222" if obj == assembly.root else "#e8eef6", edgecolor="#333", zorder=2)
        ax_graph.text(x, y, obj, fontsize=7, ha="center", va="center", zorder=3, color="white" if obj == assembly.root else "black")
    handles = [Line2D([], [], color=c, lw=2.2, label=k) for k, c in colors.items()]
    handles += [Line2D([], [], color="#777", lw=1.2, ls="--", label="loop closure"), Line2D([], [], color="#d62728", lw=2, label="error")]
    ax_graph.legend(handles=handles, fontsize=7, loc="lower left")
    worst = max((loop["position_error"] for loop in assembly.loops), default=0.0)
    ax_graph.set_title(f"Join graph from root {assembly.root}: {len(assembly.tree)} tree joins, {len(assembly.loops)} loop closures "
                       f"(worst residual {worst * 1e3:.4f} mm)", fontsize=9)
    ax_graph.axis("off")

    hits = {frozenset({i["a"], i["b"]}) for i in interference if not i.get("insertion")}
    bad_objects = {o for pair in hits for o in pair}
    for cid, solid in solids.items():
        lo, hi = solid.aabb
        ax_side.add_patch(Rectangle((lo[0] * 1e3, lo[2] * 1e3), (hi[0] - lo[0]) * 1e3, (hi[2] - lo[2]) * 1e3,
                                    fc="#f4a6a6" if cid in bad_objects else "#cfdcec", ec="#d62728" if cid in bad_objects else "#4c6c94",
                                    lw=0.8, alpha=0.55))
    for ref, iface in assembly.interfaces.items():
        if iface.obj not in assembly.poses:
            continue
        frame = assembly.interface_world(ref)
        origin, z = frame[:3, 3] * 1e3, frame[:3, 2] * 4.0
        ax_side.arrow(origin[0], origin[2], z[0], z[2], width=0.12, head_width=1.0, color="#333", length_includes_head=True)
    ax_side.set_aspect("equal")
    ax_side.autoscale_view()
    ax_side.set_xlabel("x (mm)")
    ax_side.set_ylabel("z (mm)")
    ax_side.set_title(f"Side view of compiled envelopes with interface normals ({len(hits)} interference(s) in red)", fontsize=9)
    ax_side.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return True


def run(project: Project) -> StageResult:
    start = time.time()
    result = StageResult(stage=STAGE, inputs=project.fingerprint())
    assembly = project.assembly
    if assembly is None:
        result.add(Diagnostic.info("assembly_not_declared", "robot.yaml has no assembly section; authored poses are used as-is",
                                   hint="Add `assembly: {root, joins}` to derive poses from object interfaces."))
        result.duration_s = time.time() - start
        return result
    for diagnostic in assembly.diagnostics:
        result.add(diagnostic.model_copy())
    result.assume("mechanical", "Object poses are derived from joins; moving joins are compiled at q = 0.")
    result.assume("mechanical", "Hole patterns pass when every pair's center offset is within the floating/fixed fastener "
                                "allowance sum((H - F) / 2) over clearance holes; clearance diameters follow ISO 273 fine..coarse.")
    result.assume("mechanical", "Shaft/bore fits use ISO 286-2 limit deviations; running fits are capped at H11/c11 and "
                                "press fits at H7/s6. Minimum thread engagement is 1.0 x d unless the interface sets "
                                "min_engagement_factor (rule of thumb, not a strength calculation).")
    result.assume("mechanical", "Interference uses catalog envelopes (boxes exact, cylinders as 48-gon prisms); a shaft inside a "
                                "joined bore is allowed. The exact check is the CAD stage's mesh booleans.")
    solids = object_solids(project)
    interference = check_interference(project, assembly, solids, result)
    check_seat_contact(project, assembly, solids, result)

    out_dir = project.build_dir("model")
    payload = assembly.to_json()
    payload.update({
        "tolerance": {"position": float(project.spec.assembly.tolerance.position), "angle": float(project.spec.assembly.tolerance.angle)},
        "interference": interference,
        "diagnostics": [d.model_dump(mode="json") for d in result.diagnostics],
    })
    (out_dir / "assembly.json").write_text(json.dumps(payload, indent=2, default=float))
    result.artifact("model/assembly.json", "json", "Compiled poses, join graph, loop residuals, derived joints and interference")
    if _plot(out_dir / "assembly_graph.png", assembly, solids, interference):
        result.artifact("model/assembly_graph.png", "plot_png", "Join graph and side view of the compiled assembly")
    result.metrics.update({
        "objects": len(project.spec.components), "placed": len(assembly.poses), "joins": len(assembly.joins),
        "tree_joins": len(assembly.tree), "loop_closures": len(assembly.loops),
        "max_loop_position_error": max((loop["position_error"] for loop in assembly.loops), default=0.0),
        "max_loop_angle_error": max((loop["angle_error"] for loop in assembly.loops), default=0.0),
        "interferences": sum(1 for i in interference if not i.get("insertion")),
        "derived_joints": [joint.id for joint in assembly.joints],
    })
    result.duration_s = time.time() - start
    return result

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
EXACT_VOLUME_TOL = 5e-11  # m^3 (0.05 mm^3): mesh-boolean slivers of coincident faces stay below this


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
            solids[cid] = make_solid(cid, mech.shape, list(mech.size), mech.axis, comp.pose.xyz, comp.pose.rpy, mech.envelope_offset)
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


def allowed_pairs(project: Project) -> dict[frozenset[str], str]:
    """Part pairs whose solids legitimately overlap: threads (screw/stud in its tapped
    part, bolt in its nut), press fits, splines and line-to-line running fits."""
    allowed: dict[frozenset[str], str] = {}
    for fastener in project.spec.mechanical.fasteners:
        if fastener.thread_into:
            allowed[frozenset({fastener.part, fastener.thread_into})] = f"thread {fastener.id}"
        if fastener.nut_part:
            allowed[frozenset({fastener.part, fastener.nut_part})] = f"thread {fastener.id}"
    for interface in project.spec.mechanical.interfaces:
        clearance = interface.params.get("clearance")
        line_to_line = interface.kind == "revolute_fit" and clearance and to_si(clearance[0]) < 0
        if interface.kind in ("press_fit", "spline") or line_to_line:
            allowed[frozenset(interface.parts)] = f"{interface.kind} {interface.id}"
    # A purchased part given only as a catalog envelope (no solids, features or fabrication) has no modeled
    # holes or bores: what is joined or fastened to it necessarily overlaps it. Recorded, not hidden.
    def envelope_only(cid: str) -> bool:
        comp = project.spec.components.get(cid)
        if comp is None:
            return False
        mech = project.catalog.component(comp.type).mechanical
        return not (mech.fabricated or mech.solids or mech.features or comp.params.get("solids"))

    related: set[frozenset[str]] = set()
    for fastener in project.spec.mechanical.fasteners:
        members = [fastener.part, *fastener.clamps, *([fastener.thread_into] if fastener.thread_into else [])]
        related |= {frozenset({fastener.part, m}) for m in members if m != fastener.part}
    joined: dict[str, set[str]] = {}
    for join in (project.spec.assembly.joins if project.spec.assembly else []):
        a, b = join.a.partition(".")[0], join.b.partition(".")[0]
        joined.setdefault(a, set()).add(b)
        joined.setdefault(b, set()).add(a)
        kinds = set()
        for end in (join.a, join.b):
            obj, _, name = end.partition(".")
            iface = project.catalog.component(project.spec.components[obj].type).interfaces.get(name)
            kinds.add(iface.kind if iface else "")
        if kinds & {"shaft", "bore", "spline", "ball_stud", "ball_socket"}:  # insertion joins only, not planar seats
            related.add(frozenset({a, b}))
    for fastener in project.spec.mechanical.fasteners:  # an envelope joined to the stack (e.g. a wheel on the axle a screw enters)
        stack = {*fastener.clamps, *([fastener.thread_into] if fastener.thread_into else [])}
        for member in stack:
            related |= {frozenset({fastener.part, other}) for other in joined.get(member, set())}
    for pair in related:
        if pair not in allowed and any(envelope_only(c) for c in pair):
            allowed[pair] = "envelope-only part (holes and bores not modeled)"
    return allowed


def check_exact_interference(project: Project, result: StageResult) -> list[dict[str, Any]]:
    """STEP-derived solids at pulled poses, with the unchanged volume tolerance."""
    from r3_onshape.step_geometry import load_geometry
    from r3_onshape.fit import FitChecker
    from r3_model.pulled import load_pulled

    pulled = load_pulled(project.root)
    model = load_geometry(project.root)
    checker = FitChecker(project, model, {n: p.material or "" for n, p in pulled.parts.items()}, result)
    report = checker.run()
    project._exact_solids = {name: part.manifold() for name, part in model.parts.items()}
    result.metrics["exact_solids"] = len(model.parts)
    return [{"a": a, "b": b, "volume": volume, "allowed": report.allowed.get((a, b)),
             "center": ((np.maximum(model[a].lo, model[b].lo) + np.minimum(model[a].hi, model[b].hi)) / 2).tolist(),
             "extent": (np.minimum(model[a].hi, model[b].hi) - np.maximum(model[a].lo, model[b].lo)).tolist()}
            for (a, b), volume in report.intersections.items() if volume > EXACT_VOLUME_TOL]


def check_caps(project: Project, result: StageResult) -> list[dict[str, Any]]:
    """Printed screw caps: inner diameter = head + 2 x cap clearance of their print
    process (print_fits.yaml), and, on the PCB, the cap footprint against the
    board's own keep-outs (build/pcb/<board>/board.json ``mounting``)."""
    out = []
    boards = {b.component: b for b in project.spec.pcb.boards}
    for join in (project.spec.assembly.joins if project.spec.assembly else []):
        for end, other in ((join.a, join.b), (join.b, join.a)):
            obj, _, name = end.partition(".")
            ctype = project.catalog.component(project.spec.components[obj].type)
            params = (ctype.interfaces.get(name).params if name in ctype.interfaces else {}) or {}
            if "cap_for" not in params:
                continue
            head = project.catalog.fastener(params["cap_for"])
            fit = project.catalog.print_fits.get(params.get("process", "fdm"))
            inner = to_si(params["inner_d"])
            needed = head.head_diameter + 2 * to_si(fit.cap_clearance) if fit else head.head_diameter
            entry = {"cap": obj, "on": other, "inner_d": inner, "required_inner_d": needed, "outer_d": float(ctype.mechanical.size[0])}
            if abs(inner - needed) > 1e-6:
                result.add(Diagnostic.error("cap_clearance_mismatch",
                                            f"{obj}: inner diameter {inner * 1e3:.2f} mm != head {head.head_diameter * 1e3:.2f} mm + "
                                            f"2 x {to_si(fit.cap_clearance) * 1e3:.2f} mm ({fit.process} cap clearance)",
                                            subjects=[obj], data=entry))
            board_obj = other.partition(".")[0]
            board = boards.get(board_obj)
            if board is not None:
                path = project.root / "build" / "pcb" / board.source.split("/")[-1] / "board.json"
                if path.exists():
                    mounting = json.loads(path.read_text()).get("mounting", {})
                    keepout = (mounting.get("keepouts") or {}).get("top_components_radius")
                    entry["board_keepout_radius"] = keepout
                    if keepout is not None and entry["outer_d"] / 2 > keepout + 1e-6:
                        result.add(Diagnostic.warning(
                            "cap_keepout_exceeded",
                            f"{obj}: cap radius {entry['outer_d'] / 2 * 1e3:.2f} mm exceeds the board's top-component keep-out "
                            f"{keepout * 1e3:.2f} mm around its mounting hole (board.json)",
                            subjects=[obj, board_obj], data={**entry, "excess": entry["outer_d"] / 2 - keepout},
                            hint="Widen the KiCad rule area around the mounting holes to the cap radius (PCB stage), or shrink the cap."))
            out.append(entry)
    return out


def _signed_distance(solid: Solid, point: np.ndarray) -> float:
    """Signed distance to a convex envelope (exact on and inside the surface; a lower bound outside)."""
    return min(float(np.max(piece.halfspaces[:, :3] @ point + piece.halfspaces[:, 3])) for piece in (solid.pieces or (solid,)))


def _surface_distance_exact(solid: Any, point: np.ndarray, probe: float = 5e-5) -> float:
    """0 when ``point`` lies on the exact solid's surface (a probe ball is partly
    inside), else the probe radius signed outside (+) or inside (-)."""
    import manifold3d as m3d

    ball = m3d.Manifold.sphere(probe, 24).translate(tuple(float(v) for v in point))
    fraction = (ball ^ solid).volume() / ball.volume()
    if fraction <= 0.02:
        return probe
    if fraction >= 0.98:
        return -probe
    return 0.0


def _seat_distance_exact(solid: Any, frame: np.ndarray, iface: Any) -> float:
    """Probe the seat plane at its origin and on a ring just outside its holes
    (a seat point on a hole axis lies in the hole): 0 if any probe is on the surface."""
    hole = max((h.d / 2 for h in iface.holes if abs(h.x) < 1e-9 and abs(h.y) < 1e-9), default=0.0)
    radii = sorted({hole + 3e-4, *(1e-3 + 4e-4 * k for k in range(11))})
    probes = [frame[:3, 3]] + [frame[:3, 3] + r * (math.cos(t) * frame[:3, 0] + math.sin(t) * frame[:3, 1])
                               for r in radii for t in np.linspace(0.0, 2 * math.pi, 8, endpoint=False)]
    values = [_surface_distance_exact(solid, point) for point in probes]
    return 0.0 if any(v == 0.0 for v in values) else values[0]


def _clamp_bridges_offset(project: Project, assembly: AssemblyResult, join_id: str,
                          refs: tuple[str, str], ifaces: list[Any], exact: dict[str, Any] | None) -> bool:
    """An explicitly clamped spacer may separate the two joined seat faces.

    Confirm that the same declared intermediate solid reaches both seats. Merely
    naming a clamped part, or placing unrelated material in the gap, is not enough.
    The CAD fastener check subsequently verifies the complete loaded contact stack.
    """
    if exact is None:
        return False
    join = next((j for j in project.spec.assembly.joins if j.join_id == join_id), None)
    if join is None or join.fasteners is None:
        return False
    for name in join.fasteners.clamps or []:
        if name in {i.obj for i in ifaces} or name not in exact:
            continue
        if all(_seat_distance_exact(exact[name], assembly.interface_world(ref), iface) == 0.0
               for ref, iface in zip(refs, ifaces)):
            return True
    return False


def check_seat_contact(project: Project, assembly: AssemblyResult, solids: dict[str, Solid], result: StageResult,
                       exact: dict[str, Any] | None = None) -> None:
    """Fasten seats must touch: each planar seat origin lies on its object's surface
    (the exact part solid when available, else the catalog envelope). Thread
    engagements of studs (``thread`` holes) are not seats and are skipped."""
    for join_id, entry in assembly.joins.items():
        if not entry.get("valid") or entry["kind"] != "fasten":
            continue
        refs = (entry["a"], entry["b"])
        ifaces = [assembly.interfaces[ref] for ref in refs]
        if not all(i.kind in ("face_mount", "hole_pattern", "adhesive_pad") for i in ifaces):
            continue
        if any(h.kind == "thread" for i in ifaces for h in i.holes):
            continue
        if exact is not None:
            distances = {ref: _seat_distance_exact(exact[i.obj], assembly.interface_world(ref), i)
                         for ref, i in zip(refs, ifaces) if i.obj in exact}
        else:
            distances = {ref: _signed_distance(solids[i.obj], assembly.interface_world(ref)[:3, 3])
                         for ref, i in zip(refs, ifaces) if i.obj in solids}
        off = {ref: d for ref, d in distances.items() if abs(d) > CONTACT_TOLERANCE}
        gap = abs(entry.get("offset", 0.0)) > CONTACT_TOLERANCE
        if gap and _clamp_bridges_offset(project, assembly, join_id, refs, ifaces, exact):
            gap = False
        if off or gap:
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
    from r3_model.pulled import PullError, load_pulled
    from r3_model.pulled_view import apply_pulled

    start = time.time()
    result = StageResult(stage=STAGE, inputs=project.fingerprint())
    try:
        assembly = apply_pulled(project, load_pulled(project.root))
        result.extend(assembly.diagnostics)
        interference = check_exact_interference(project, result)
        # Catalog-only cap dimensions cannot establish a physical cap fit.
        for join in (project.spec.assembly.joins if project.spec.assembly else []):
            for end in (join.a, join.b):
                obj, _, name = end.partition(".")
                comp = project.spec.components.get(obj)
                ctype = project.catalog.components.get(comp.type) if comp else None
                iface = ctype.interfaces.get(name) if ctype else None
                if iface and iface.params.get("cap_for"):
                    result.add(Diagnostic.error("cap_geometry_unverified", f"{obj}: cap clearance/PCB keep-out check needs a cap-to-pulled-face association; catalog dimensions cannot certify it", subjects=[obj]))
    except PullError as error:
        result.add(Diagnostic.error(error.code, str(error)))
        result.duration_s = time.time() - start
        return result
    payload = assembly.to_json()
    payload.update(interference=interference, interference_method="pulled_step",
                   inputs=project.fingerprint(), diagnostics=[d.model_dump(mode="json") for d in result.diagnostics])
    (project.build_dir("model") / "assembly.json").write_text(json.dumps(payload, indent=2, default=float))
    result.artifact("model/assembly.json", "json", "Pulled poses, mate closure residuals and STEP fit/interference")
    result.metrics.update(objects=len(assembly.poses), joins=len(assembly.joins), loop_closures=len(assembly.loops))
    result.assume("mechanical", "Onshape solved poses are authoritative; fastened mate frames are checked for closure. Fit checks use pulled STEP faces and solids.")
    result.duration_s = time.time() - start
    return result

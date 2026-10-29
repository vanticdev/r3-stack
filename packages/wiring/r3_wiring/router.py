"""3D harness router.

Each harness is a tree of conductors between connector terminations and
splices. Branches (wires sharing the same two end nodes) are searched with A*
on a voxel grid over the chassis volume, cheapest first. The step cost is::

    cost = step_length * (1 + w_s * s_surface + m_moving + k_keepaway) * discount
           + w_bend * turn_angle + pass_through_cost

where ``s_surface`` grows with distance from static structure (wires run along
the plate top and deck underside), ``m_moving`` penalises proximity to moving
swept envelopes (steep inside ``requirements.min_wire_clearance_moving``) and
``discount`` < 1 on voxels already used by the same harness so branches merge
into a bundle. Drillable plates may only be crossed along their normal, at a
cost that is waived for holes already drilled.

The voxel paths are merged into a bundle graph, string-pulled (any-angle,
Theta*-style line-of-sight shortcuts that never increase field cost) and every
corner is replaced by a circular fillet of at least the minimum bend radius
``factor * OD_bundle`` where geometry allows. Ties are placed every ~60 mm where
the bundle runs within reach of static structure.
"""

from __future__ import annotations

import heapq
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from r3_model.diagnostics import Diagnostic
from r3_model.project import Project
from r3_model.spec import Harness

from r3_connectors.frames import Frame
from r3_connectors.pins import (PinEnd, component_connector_frame, floating_pair_frame, geometry_of, pigtail_ends,
                                pin_end, wire_entry_depth)

from .bodies import ROOT
from .geometry import bundle_diameter, polyline_length, project_to_polyline, resample_polyline
from .grid import Fields, VoxelGrid, compute_fields
from .obstacles import ObstacleSet
from .smoothing import curvature_radius, make_segment, relax
from .terminations import (body_width, exit_offset, mated_pair_length, termination_allowance)


@dataclass
class RouterOptions:
    voxel: float = 0.003
    surface_weight: float = 0.6
    surface_band: float = 0.012
    moving_soft_band: float = 0.015
    moving_soft_weight: float = 2.0
    moving_hard_weight: float = 30.0
    keep_away_weight: float = 2.0
    keep_away_band: float = 0.012
    bend_weight: float = 0.006  # metres of equivalent length per radian of turning
    bundle_discount: float = 0.55
    pass_through_cost: float = 0.06
    existing_hole_cost: float = 0.003
    bend_margin: float = 1.25  # target radius / minimum radius
    smoothing_inflation: float = 0.4  # extra clearance (x R_min) kept by string pulling
    tie_spacing: float = 0.06
    tie_reach: float = 0.008  # max gap between bundle surface and structure for a tie
    hole_clearance: float = 0.003  # grommet allowance added to the bundle OD
    max_expansions: int = 3_000_000
    relax_spacing: float = 0.002
    relax_iterations: int = 60
    fillet_min_angle: float = math.radians(8.0)  # smaller turns are measured, not filleted


@dataclass
class NodePoint:
    harness: str
    name: str
    kind: str  # connector | floating | splice | free | unplaced
    exit: np.ndarray | None = None
    direction: np.ndarray | None = None
    connector_type: str | None = None
    mates: str | None = None
    component: str | None = None
    body: str = ROOT
    exempt: set[str] = field(default_factory=set)
    face: np.ndarray | None = None
    body_length: float = 0.0
    od: float = 0.0
    lead_end: np.ndarray | None = None
    lead_length: float = 0.0
    frame: Frame | None = None  # this half's connector frame (robot frame); z points at the mating half
    fan_length: float = 0.0  # arc length over which conductors converge from their pins into the bundle

    @property
    def ref(self) -> str:
        return f"{self.harness}.{self.name}"

    @property
    def termination_length(self) -> float:
        return self.lead_length + max(0.01, 3 * self.od) if self.kind == "connector" else max(0.006, 2 * self.od)


@dataclass
class FloatingMate:
    a: str
    b: str
    type_a: str
    type_b: str
    center: np.ndarray
    axis: np.ndarray
    length: float
    width: float
    cost: float


@dataclass
class PassThrough:
    id: int
    plate: str
    axis: int
    center: np.ndarray
    harnesses: set[str] = field(default_factory=set)
    wires: set[str] = field(default_factory=set)
    diameter: float = 0.0


@dataclass
class Bend:
    position: np.ndarray
    radius: float
    required: float
    conductors: int
    od: float


@dataclass
class WireRoute:
    wire_id: str
    harness: str
    awg: int
    color: str
    signal: str
    from_node: str
    to_node: str
    path: np.ndarray
    bundle_radius: np.ndarray  # per path vertex
    conductors: np.ndarray  # per path vertex
    bends: list[Bend]
    od: float
    slack: float
    termination_allowance: float
    spec_length: float | None
    failed: bool = False
    ends: tuple[PinEnd | None, PinEnd | None] = (None, None)
    fanout: tuple[int, int] = (0, 0)  # path vertices before / after the shared bundle core
    core: np.ndarray | None = None  # the bundle-centreline path the conductor follows between its fan-outs

    @property
    def routed_length(self) -> float:
        return polyline_length(self.path)

    @property
    def cut_length(self) -> float:
        computed = self.routed_length + self.slack + self.termination_allowance
        return self.spec_length if self.spec_length is not None else computed

    @property
    def free_length(self) -> float:
        """Conductor length available between the terminations."""
        return self.cut_length - self.termination_allowance


@dataclass
class HarnessRoute:
    harness: str
    bundle_path: np.ndarray
    tie_points: list[np.ndarray]
    segments: list[dict[str, Any]]
    wires: list[str]
    holes: list[int]
    jacket: str

    @property
    def length(self) -> float:
        return polyline_length(self.bundle_path)


@dataclass
class RoutingResult:
    wires: dict[str, WireRoute]
    harnesses: dict[str, HarnessRoute]
    nodes: dict[str, dict[str, NodePoint]]
    floating: list[FloatingMate]
    holes: list[PassThrough]
    diagnostics: list[Diagnostic]
    grid: VoxelGrid
    stats: dict[str, Any]


@dataclass
class _Context:
    r: float
    blocked: bytearray
    blocked_np: np.ndarray
    zone: bytearray
    zone_np: np.ndarray
    zone_cost: list[float]
    cost: list[float]
    cost_np: np.ndarray
    clear_np: np.ndarray


_MOVES = [(di, dj, dk) for di in (-1, 0, 1) for dj in (-1, 0, 1) for dk in (-1, 0, 1) if (di, dj, dk) != (0, 0, 0)]


class HarnessRouter:
    def __init__(self, project: Project, obstacles: ObstacleSet, options: RouterOptions | None = None) -> None:
        self.project = project
        self.spec = project.spec
        self.catalog = project.catalog
        self.graph = project.graph
        self.obstacles = obstacles
        self.kin = obstacles.kinematics
        self.opt = options or RouterOptions()
        self.grid = VoxelGrid.around(obstacles, self.opt.voxel)
        self.fields: Fields = compute_fields(self.grid, obstacles)
        self.c_req = float(self.spec.requirements.min_wire_clearance_moving)
        self.border = self.grid.border()
        self.diagnostics: list[Diagnostic] = []
        self.holes: list[PassThrough] = []
        self.hole_mask = bytearray(self.grid.size)
        self._contexts: dict[tuple, _Context] = {}
        self._graphs: dict[float, Any] = {}
        self._keep_away_cache: dict[tuple, np.ndarray] = {}
        self.expansions = 0
        self.relax_reverted = 0
        self._build_moves()
        shape = self.grid.shape
        flat = np.arange(self.grid.size)
        self._ci = (flat // (shape[1] * shape[2])).tolist()
        self._cj = ((flat // shape[2]) % shape[1]).tolist()
        self._ck = (flat % shape[2]).tolist()

    # ----------------------------------------------------------------- setup
    def _build_moves(self) -> None:
        sx, sy, _ = self.grid.strides
        v = self.opt.voxel
        self.offsets = [di * sx + dj * sy + dk for di, dj, dk in _MOVES]
        self.steps = [v * math.sqrt(di * di + dj * dj + dk * dk) for di, dj, dk in _MOVES]
        self.dirs = [np.array(m, dtype=float) / np.linalg.norm(m) for m in _MOVES]
        self.pure = [([abs(c) for c in m].index(1) if sum(abs(c) for c in m) == 1 else -1) for m in _MOVES]
        w = self.opt.bend_weight
        self.bend = [[w * math.acos(float(np.clip(a @ b, -1.0, 1.0))) for b in self.dirs] for a in self.dirs]

    def _nearest_move(self, direction: np.ndarray | None) -> int:
        if direction is None:
            return -1
        return int(np.argmax([d @ direction for d in self.dirs]))

    def context(self, r: float, keep_away: tuple[str, ...] = ()) -> _Context:
        key = (round(r, 5), keep_away)
        if key in self._contexts:
            return self._contexts[key]
        f, opt = self.fields, self.opt
        blocked = self.border | (f.static_clear < r) | (f.moving_keep < r)
        zone = np.zeros(self.grid.size, dtype=np.uint8)
        zone_cost = np.zeros(self.grid.size)
        for component, axis, plate_field, thickness in f.plates:
            inside = (plate_field < r) & ~blocked
            keepout = self.obstacles.by_component(component).keepout
            layers = max(1, math.ceil((thickness + 2 * (keepout + r)) / opt.voxel))
            zone[inside] = axis + 1
            zone_cost[inside] = opt.pass_through_cost / layers
        cost = 1.0 + opt.surface_weight * np.clip((f.struct_dist - r - 0.0015) / opt.surface_band, 0.0, 1.0)
        clearance = f.moving_raw - r
        hard = clearance < self.c_req
        soft = np.clip(1.0 - (clearance - self.c_req) / opt.moving_soft_band, 0.0, 1.0) ** 2
        cost += np.where(hard, opt.moving_soft_weight + opt.moving_hard_weight * (self.c_req - clearance) / self.c_req,
                         opt.moving_soft_weight * soft)
        if keep_away:
            cost += opt.keep_away_weight * np.clip(1.0 - (self._keep_away_field(keep_away) - r) / opt.keep_away_band, 0.0, 1.0)
        clear = np.minimum(f.static_clear, f.moving_keep)
        ctx = _Context(r, bytearray(blocked.astype(np.uint8).tobytes()), blocked, bytearray(zone.tobytes()), zone,
                       zone_cost.tolist(), cost.tolist(), cost, clear)
        self._contexts[key] = ctx
        return ctx

    def _keep_away_field(self, components: tuple[str, ...]) -> np.ndarray:
        if components not in self._keep_away_cache:
            points = self.grid.centers()
            out = np.full(len(points), 0.05)
            for cid in components:
                obstacle = self.obstacles.by_component(cid)
                if obstacle is not None:
                    np.minimum(out, obstacle.swept.sdf_bounded(points, 0.05), out=out)
            self._keep_away_cache[components] = out
        return self._keep_away_cache[components]

    # ------------------------------------------------------------- top level
    def route(self) -> RoutingResult:
        nodes = self._resolve_nodes()
        floating = self._place_floating(nodes)
        wires: dict[str, WireRoute] = {}
        harnesses: dict[str, HarnessRoute] = {}
        for harness in self.spec.electrical.harnesses:
            routed = self._route_harness(harness, nodes[harness.id])
            if routed is None:
                continue
            harness_route, wire_routes = routed
            harnesses[harness.id] = harness_route
            wires.update(wire_routes)
        self._size_holes(wires)
        stats = {"voxel": self.opt.voxel, "grid_shape": list(self.grid.shape), "voxels": self.grid.size,
                 "astar_expansions": self.expansions, "relaxed_segments_reverted": self.relax_reverted}
        return RoutingResult(wires, harnesses, nodes, floating, self.holes, self.diagnostics, self.grid, stats)

    # ----------------------------------------------------------------- nodes
    def _wire_od(self, awg: int) -> float:
        return float(self.catalog.wire(awg).outer_diameter)

    def _bend_factor(self, awgs: list[int]) -> float:
        return max(self.catalog.wire(a).min_bend_radius_factor for a in awgs)

    def _floating_pairs(self) -> list[tuple[str, str]]:
        harness_ids = {h.id for h in self.spec.electrical.harnesses}
        pairs: list[tuple[str, str]] = []
        for harness in self.spec.electrical.harnesses:
            for name, conn in harness.connectors.items():
                if conn.mates and conn.mates.split(".")[0] in harness_ids:
                    pair = tuple(sorted((f"{harness.id}.{name}", conn.mates)))
                    if pair not in pairs:
                        pairs.append(pair)  # type: ignore[arg-type]
        return pairs

    def _resolve_nodes(self) -> dict[str, dict[str, NodePoint]]:
        floating = {ref for pair in self._floating_pairs() for ref in pair}
        out: dict[str, dict[str, NodePoint]] = {}
        for harness in self.spec.electrical.harnesses:
            nodes: dict[str, NodePoint] = {}
            awgs: dict[str, list[int]] = {}
            for wire in harness.wires:
                for ref in (wire.from_, wire.to):
                    awgs.setdefault(ref.split(".")[0] if ref not in harness.splices else ref, []).append(wire.awg)
            for name, conn in harness.connectors.items():
                ref = f"{harness.id}.{name}"
                od = bundle_diameter(self._wire_od(a) for a in awgs.get(name, [])) if awgs.get(name) else 0.0
                endpoint = self.graph.endpoints.get(conn.mates) if conn.mates else None
                if endpoint is not None and endpoint.owner_kind == "component":
                    component = conn.mates.split(".")[0]
                    direction = endpoint.direction / np.linalg.norm(endpoint.direction)
                    offset = exit_offset(self.catalog, conn.type)
                    node = NodePoint(harness.id, name, "connector", endpoint.position + direction * offset, direction,
                                     conn.type, conn.mates, component, self.kin.body_of(component),
                                     set(self.kin.mount_chain(component)), endpoint.position.copy(), offset, od)
                    board_half = component_connector_frame(self.project, conn.mates)
                    # A pigtail leaves the component's own connector frame; a mated half is turned about x.
                    node.frame = board_half if conn.type == "integral" else board_half.mated()
                elif ref in floating:
                    node = NodePoint(harness.id, name, "floating", connector_type=conn.type, mates=conn.mates, od=od)
                elif conn.position is not None:
                    node = NodePoint(harness.id, name, "free", np.asarray(conn.position, dtype=float), None, conn.type, od=od)
                else:
                    node = NodePoint(harness.id, name, "unplaced", connector_type=conn.type, mates=conn.mates, od=od)
                    self.diagnostics.append(Diagnostic.error(
                        "wire_endpoint_unplaced", f"{ref} is not mated to a component or harness and has no position",
                        subjects=[harness.id, ref], data={"connector_type": conn.type}))
                nodes[name] = node
            for name, splice in harness.splices.items():
                od = bundle_diameter(self._wire_od(a) for a in awgs.get(name, [])) if awgs.get(name) else 0.0
                nodes[name] = NodePoint(harness.id, name, "splice", np.asarray(splice.position, dtype=float), None, od=od)
            for node in nodes.values():
                if node.kind == "connector":
                    self._lead(node, awgs.get(node.name, []))
                elif node.exit is not None:
                    node.lead_end = node.exit.copy()
            out[harness.id] = nodes
        return out

    def _lead(self, node: NodePoint, awgs: list[int]) -> None:
        """Straight strain-relief run out of a connector, long enough for a
        target-radius 90 deg turn, shortened if it would hit something."""
        if node.direction is None or node.exit is None:
            node.lead_end = None if node.exit is None else node.exit.copy()
            return
        r = node.od / 2
        r_pref = self.opt.bend_margin * self._bend_factor(awgs or [22]) * node.od
        target = r_pref + node.od
        ts = np.arange(0.0005, target + 1e-9, 0.0005)
        points = node.exit + node.direction[None] * ts[:, None]
        static, _ = self.obstacles.min_distance(points, self.obstacles.static, keepout=True, exclude=node.exempt)
        moving, _ = self.obstacles.min_distance(points, self.obstacles.moving, swept=True, keepout=True, exclude=node.exempt)
        bad = np.nonzero(np.minimum(static, moving) < r)[0]
        length = target if not len(bad) else (ts[bad[0] - 1] if bad[0] > 0 else 0.0)
        length = max(length, self.opt.voxel)
        node.lead_length = float(length)
        node.lead_end = node.exit + node.direction * length

    # -------------------------------------------------------------- floating
    def _csgraph(self, ctx: _Context):
        key = (round(ctx.r, 5), id(ctx))
        if key in self._graphs:
            return self._graphs[key]
        from scipy.sparse import csr_matrix

        free = ~ctx.blocked_np
        index = np.nonzero(free)[0]
        zone, zone_cost, cost = ctx.zone_np, np.asarray(ctx.zone_cost), ctx.cost_np
        rows, cols, data = [], [], []
        for m, offset in enumerate(self.offsets):
            if offset <= 0:
                continue
            v = index + offset
            ok = free[v]
            u, v = index[ok], v[ok]
            zu, zv = zone[u], zone[v]
            in_zone = (zu > 0) | (zv > 0)
            if self.pure[m] < 0:
                keep = ~in_zone
            else:
                axis = self.pure[m] + 1
                keep = ~in_zone | (((zu == 0) | (zu == axis)) & ((zv == 0) | (zv == axis)))
            u, v = u[keep], v[keep]
            rows.append(u)
            cols.append(v)
            data.append(self.steps[m] * 0.5 * (cost[u] + cost[v]) + 0.5 * (zone_cost[u] + zone_cost[v]))
        n = self.grid.size
        matrix = csr_matrix((np.concatenate(data), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n))
        self._graphs[key] = matrix
        return matrix

    def _free_voxel(self, point: np.ndarray, ctx: _Context) -> tuple[int, list[int]]:
        """Voxel of ``point`` and, if it is blocked, the shortest 6-connected
        escape path to free space (to be unblocked for this harness)."""
        start = self.grid.flat(point)
        if not ctx.blocked[start]:
            return start, []
        sx, sy, _ = self.grid.strides
        neighbors = (sx, -sx, sy, -sy, 1, -1)
        parent = {start: -1}
        queue = deque([start])
        while queue:
            u = queue.popleft()
            if not ctx.blocked[u] and not ctx.zone[u]:
                path = []
                while u != -1:
                    path.append(u)
                    u = parent[u]
                return start, path
            for off in neighbors:
                v = u + off
                if v in parent or self.border[v]:
                    continue
                parent[v] = u
                queue.append(v)
            if len(parent) > 20000:
                break
        return start, [start]

    def _place_floating(self, nodes: dict[str, dict[str, NodePoint]]) -> list[FloatingMate]:
        from scipy.sparse.csgraph import dijkstra

        placed: list[FloatingMate] = []
        harnesses = {h.id: h for h in self.spec.electrical.harnesses}
        for ref_a, ref_b in self._floating_pairs():
            ha, ca = ref_a.split(".", 1)
            hb, cb = ref_b.split(".", 1)
            na, nb = nodes[ha][ca], nodes[hb][cb]
            length = mated_pair_length(self.catalog, na.connector_type, nb.connector_type)
            width = max(body_width(self.catalog, na.connector_type), body_width(self.catalog, nb.connector_type))
            r = max(na.od, nb.od) / 2
            ctx = self.context(r)
            graph = self._csgraph(ctx)
            sides = []
            for harness_id, name in ((ha, ca), (hb, cb)):
                anchors: dict[int, float] = {}
                for wire in harnesses[harness_id].wires:
                    ends = [wire.from_, wire.to]
                    names = [e if e in harnesses[harness_id].splices else e.split(".")[0] for e in ends]
                    if name not in names:
                        continue
                    other = nodes[harness_id][names[1] if names[0] == name else names[0]]
                    point = other.lead_end if other.lead_end is not None else other.exit
                    if point is None:
                        continue
                    voxel, escape = self._free_voxel(point, ctx)
                    voxel = escape[0] if escape else voxel
                    anchors[voxel] = anchors.get(voxel, 0.0) + float(self.catalog.wire(wire.awg).mass_per_m)
                sides.append(anchors)
            if not sides[0] or not sides[1]:
                continue
            total = np.zeros(self.grid.size)
            heaviest = []
            for anchors in sides:
                for voxel, weight in anchors.items():
                    total += weight * dijkstra(graph, directed=False, indices=voxel)
                heaviest.append(max(anchors, key=anchors.get))
            weight_sum = sum(sum(a.values()) for a in sides)
            # Prefer a spot where the mated pair can be tied to structure.
            total += 2.0 * weight_sum * np.maximum(0.0, self.fields.struct_dist - (width / 2 + 0.004))
            invalid = ctx.blocked_np | (ctx.zone_np > 0) | (self.fields.static_clear < width / 2) | \
                (self.fields.moving_raw < self.c_req + width / 2) | ~np.isfinite(total)
            total[invalid] = np.inf
            _, preds_a = dijkstra(graph, directed=False, indices=heaviest[0], return_predecessors=True)
            _, preds_b = dijkstra(graph, directed=False, indices=heaviest[1], return_predecessors=True)
            order = np.argsort(total)
            chosen = None
            for voxel in order[:400]:
                if not np.isfinite(total[voxel]):
                    break
                center = self.grid.center(int(voxel))
                pa, pb = self._walk(preds_a, int(voxel), 7), self._walk(preds_b, int(voxel), 7)
                axis = pb - pa
                if np.linalg.norm(axis) < 1e-9:
                    continue
                axis /= np.linalg.norm(axis)
                body = center + axis[None] * np.linspace(-length / 2, length / 2, 9)[:, None]
                static, _ = self.obstacles.min_distance(body, self.obstacles.static, keepout=True)
                moving, _ = self.obstacles.min_distance(body, self.obstacles.moving, swept=True)
                if static.min() >= width / 2 and moving.min() >= self.c_req + width / 2:
                    chosen = (center, axis, float(total[voxel]))
                    break
            if chosen is None:
                self.diagnostics.append(Diagnostic.error(
                    "floating_mate_unplaced", f"no free position for mated pair {ref_a} / {ref_b}",
                    subjects=[ref_a, ref_b], data={"pair_length": length, "pair_width": width}))
                continue
            center, axis, cost = chosen
            depth_a = wire_entry_depth(self.catalog, na.connector_type)
            depth_b = wire_entry_depth(self.catalog, nb.connector_type)
            # Mating face: na's conductor exit plus its exit depth (the midpoint without geometry).
            face = center + axis * ((depth_a - length / 2) if depth_a is not None and depth_b is not None else 0.0)
            half_a = floating_pair_frame(face, axis)  # na's z points at nb
            for node, sign, frame in ((na, -1.0, half_a), (nb, 1.0, half_a.mated())):
                node.face = face.copy()
                node.direction = sign * axis
                node.exit = center + sign * axis * (length / 2)
                node.body_length = float(np.linalg.norm(node.exit - face))
                node.lead_length = 0.0
                node.lead_end = node.exit.copy()
                node.frame = frame
            placed.append(FloatingMate(ref_a, ref_b, na.connector_type, nb.connector_type, center, axis, length, width, cost))
        return placed

    def _walk(self, preds: np.ndarray, start: int, steps: int) -> np.ndarray:
        node = start
        for _ in range(steps):
            nxt = preds[node]
            if nxt < 0:
                break
            node = int(nxt)
        return self.grid.center(node)

    # --------------------------------------------------------------- search
    def _astar(self, start: int, goal: int, ctx: _Context, blocked: bytearray, used: bytearray,
               start_dir: np.ndarray | None, goal_dir: np.ndarray | None) -> list[int] | None:
        offsets, steps, pure, bend = self.offsets, self.steps, self.pure, self.bend
        cost, zone, zone_cost, hole = ctx.cost, ctx.zone, ctx.zone_cost, self.hole_mask
        ci, cj, ck = self._ci, self._cj, self._ck
        gi, gj, gk = ci[goal], cj[goal], ck[goal]
        voxel = self.opt.voxel
        discount = self.opt.bundle_discount
        hole_cost = self.opt.existing_hole_cost
        goal_pen = None
        if goal_dir is not None:
            w = self.opt.bend_weight
            goal_pen = [w * math.acos(float(np.clip(d @ goal_dir, -1.0, 1.0))) for d in self.dirs]
        h_scale = voxel * discount
        sqrt = math.sqrt

        def h(v: int) -> float:
            di, dj, dk = ci[v] - gi, cj[v] - gj, ck[v] - gk
            return h_scale * sqrt(di * di + dj * dj + dk * dk)

        g = {start: 0.0}
        parent = {start: -1}
        pdir = {start: self._nearest_move(start_dir)}
        heap = [(h(start), 0.0, start)]
        closed = set()
        expansions = 0
        moves = range(26)
        while heap:
            _, gu, u = heapq.heappop(heap)
            if u in closed:
                continue
            if u == goal:
                break
            closed.add(u)
            expansions += 1
            if expansions > self.opt.max_expansions:
                break
            du = pdir[u]
            zu = zone[u]
            cu = cost[u]
            for m in moves:
                v = u + offsets[m]
                if blocked[v] or v in closed:
                    continue
                zv = zone[v]
                if zu or zv:
                    axis = pure[m]
                    if axis < 0 or (zu and zu - 1 != axis) or (zv and zv - 1 != axis):
                        continue
                c = steps[m] * 0.5 * (cu + cost[v])
                if used[v]:
                    c *= discount
                if zv:
                    c += hole_cost if hole[v] else zone_cost[v]
                if du >= 0:
                    c += bend[du][m]
                if v == goal and goal_pen is not None:
                    c += goal_pen[m]
                ng = gu + c
                if ng < g.get(v, math.inf):
                    g[v] = ng
                    parent[v] = u
                    pdir[v] = m
                    heapq.heappush(heap, (ng + h(v), ng, v))
        self.expansions += expansions
        if goal not in parent:
            return None
        path = []
        node = goal
        while node != -1:
            path.append(node)
            node = parent[node]
        return path[::-1]

    # -------------------------------------------------------------- harness
    def _route_harness(self, harness: Harness, nodes: dict[str, NodePoint]) -> tuple[HarnessRoute, dict[str, WireRoute]] | None:
        def node_name(ref: str) -> str:
            return ref if ref in harness.splices else ref.split(".")[0]

        branches: dict[tuple[str, str], list] = {}
        for wire in harness.wires:
            a, b = node_name(wire.from_), node_name(wire.to)
            key = (a, b) if (b, a) not in branches else (b, a)
            branches.setdefault(key, []).append(wire)
        keep_away = tuple(sorted(harness.route.keep_away))
        allowed = np.zeros(self.grid.size, dtype=bool)
        used = bytearray(self.grid.size)
        positions: dict[Any, np.ndarray] = {}
        sequences: dict[tuple[str, str], list] = {}
        failed: set[tuple[str, str]] = set()
        harness_holes: set[int] = set()
        vias = [np.asarray(v, dtype=float) for v in harness.route.via]

        def branch_od(wires: list) -> float:
            return bundle_diameter(self._wire_od(w.awg) for w in wires)

        order = sorted(branches, key=lambda k: (-len(branches[k]), k))
        for index, key in enumerate(order):
            a, b = (nodes[key[0]], nodes[key[1]])
            if a.lead_end is None or b.lead_end is None:
                failed.add(key)
                continue
            r = branch_od(branches[key]) / 2
            ctx = self.context(r, keep_away)
            blocked = bytearray(ctx.blocked)
            for voxel in np.nonzero(allowed)[0]:
                blocked[voxel] = 0
            terminals = []
            for node in (a, b):
                corridor = self._corridor(node)
                for voxel in corridor:
                    blocked[voxel] = 0
                    allowed[voxel] = True
                voxel, escape = self._free_voxel(node.lead_end, ctx)
                for v in escape:
                    blocked[v] = 0
                    allowed[v] = True
                terminals.append(voxel)
            stops = [terminals[0]]
            stop_dirs: list[np.ndarray | None] = [a.direction]
            if index == 0:
                for via in vias:
                    voxel, escape = self._free_voxel(via, ctx)
                    for v in escape:
                        blocked[v] = 0
                        allowed[v] = True
                    stops.append(voxel)
                    stop_dirs.append(None)
            stops.append(terminals[1])
            voxels: list[int] = []
            ok = True
            for leg in range(len(stops) - 1):
                goal_dir = (-b.direction if b.direction is not None else None) if leg == len(stops) - 2 else None
                start_dir = stop_dirs[leg]
                if leg > 0 and voxels and len(voxels) > 1:
                    start_dir = self.grid.center(voxels[-1]) - self.grid.center(voxels[-2])
                    start_dir = start_dir / (np.linalg.norm(start_dir) or 1.0)
                path = self._astar(stops[leg], stops[leg + 1], ctx, blocked, used, start_dir, goal_dir)
                if path is None:
                    ok = False
                    break
                voxels.extend(path if not voxels else path[1:])
            if not ok:
                failed.add(key)
                self.diagnostics.append(Diagnostic.error(
                    "wire_route_failed", f"no collision-free route for {harness.id} {key[0]} -> {key[1]}",
                    subjects=[harness.id] + [w.id for w in branches[key]],
                    data={"from": a.exit.tolist(), "to": b.exit.tolist(), "bundle_radius": r}))
                sequences[key] = [f"N:{key[0]}", f"N:{key[1]}"]
                positions[f"N:{key[0]}"], positions[f"N:{key[1]}"] = a.exit, b.exit
                continue
            for v in voxels:
                used[v] = 1
            sequences[key] = self._sequence(harness.id, key, a, b, voxels, ctx, positions, harness_holes,
                                            via_voxels=stops[1:-1] if index == 0 else [])

        # ---- bundle graph and segments
        adjacency: dict[Any, set] = {}
        for seq in sequences.values():
            for p, q in zip(seq[:-1], seq[1:]):
                adjacency.setdefault(p, set()).add(q)
                adjacency.setdefault(q, set()).add(p)

        def is_junction(key: Any) -> bool:
            return (isinstance(key, str) and key[0] in "NHV") or len(adjacency.get(key, ())) != 2

        segments: dict[tuple, dict[str, Any]] = {}
        branch_segments: dict[tuple[str, str], list[tuple[tuple, bool]]] = {}
        for key, seq in sequences.items():
            parts, start = [], 0
            for i in range(1, len(seq)):
                if is_junction(seq[i]) or i == len(seq) - 1:
                    chain = tuple(seq[start:i + 1])
                    forward = [str(k) for k in chain] <= [str(k) for k in reversed(chain)]
                    canon = chain if forward else tuple(reversed(chain))
                    segments.setdefault(canon, {"keys": canon, "wires": set(), "awgs": {}})
                    for wire in branches[key]:
                        segments[canon]["wires"].add(wire.id)
                        segments[canon]["awgs"][wire.id] = wire.awg
                    parts.append((canon, forward))
                    start = i
            branch_segments[key] = parts

        for canon, segment in segments.items():
            awgs = list(segment["awgs"].values())
            segment["od"] = bundle_diameter(self._wire_od(a) for a in awgs)
            segment["conductors"] = len(awgs)
            segment["r_min"] = self._bend_factor(awgs) * segment["od"]
            r = segment["od"] / 2
            ctx = self.context(min(r, max(self._wire_od(a) for a in awgs) / 2) if awgs else r, keep_away)
            points = np.array([positions[k] for k in canon])
            if len(canon) > 2:
                points = self._string_pull(canon, points, ctx, allowed, harness_holes,
                                           inflation=self.opt.smoothing_inflation * segment["r_min"])
            segment["points"] = points

        # ---- elastic relaxation of every bundle segment (bend-radius smoothing)
        if self.opt.relax_iterations > 0:
            field = self._relax_field(harness_holes)
            relax_list = []
            for canon, segment in segments.items():
                points = segment["points"]
                if len(points) < 2 or polyline_length(points) < 2 * self.opt.relax_spacing:
                    continue
                lead_head = isinstance(canon[0], str) and canon[0].startswith("N:") and len(canon) > 1 and \
                    isinstance(canon[1], str) and canon[1].startswith("L:")
                lead_tail = isinstance(canon[-1], str) and canon[-1].startswith("N:") and len(canon) > 1 and \
                    isinstance(canon[-2], str) and canon[-2].startswith("L:")
                item = make_segment(points, 2 if lead_head else 1, 2 if lead_tail else 1, segment["od"] / 2,
                                    self.opt.relax_spacing, self.opt.bend_margin * segment["r_min"])
                relax_list.append((segment, item))
            relax([item for _, item in relax_list], field, iterations=self.opt.relax_iterations,
                  spacing=self.opt.relax_spacing)
            raw = self._relax_field(harness_holes, keepout=False)
            for segment, item in relax_list:
                relaxed = item.points()
                if self._penetration(relaxed, raw, segment["od"] / 2) <= \
                        self._penetration(segment["points"], raw, segment["od"] / 2) + 0.0005:
                    segment["points"] = relaxed
                else:
                    self.relax_reverted += 1

        # ---- per-wire assembly and fillets
        pins_at: dict[str, dict[str, float]] = {}
        for wire in harness.wires:
            for ref in (wire.from_, wire.to):
                if ref not in harness.splices and "." in ref:
                    pins_at.setdefault(node_name(ref), {})[ref.split(".", 1)[1]] = self._wire_od(wire.awg)
        for name, by_pin in pins_at.items():
            self._fan_length(nodes[name], by_pin)
        wire_routes: dict[str, WireRoute] = {}
        for key, parts in branch_segments.items():
            for wire in branches[key]:
                forward_wire = node_name(wire.from_) == key[0]
                pts, radius, count, rmin = [], [], [], []
                for canon, forward in parts:
                    seg = segments[canon]
                    p = seg["points"] if forward else seg["points"][::-1]
                    vals = (seg["od"] / 2, seg["conductors"], seg["r_min"])
                    for i, point in enumerate(p):
                        if pts and i == 0:
                            radius[-1] = max(radius[-1], vals[0])
                            count[-1] = max(count[-1], vals[1])
                            rmin[-1] = max(rmin[-1], vals[2])
                            continue
                        pts.append(point)
                        radius.append(vals[0])
                        count.append(vals[1])
                        rmin.append(vals[2])
                pts_np = np.asarray(pts)
                start_node, end_node = nodes[key[0]], nodes[key[1]]
                path, prad, pcount, bends = self._fillet(pts_np, np.asarray(radius), np.asarray(count), np.asarray(rmin),
                                                         start_node, end_node, harness_holes)
                if not forward_wire:
                    path, prad, pcount = path[::-1], prad[::-1], pcount[::-1]
                core = path.copy()
                ends = tuple(self._pin_end(nodes[node_name(ref)], ref, pins_at.get(node_name(ref), {}))
                             for ref in (wire.from_, wire.to))
                path, prad, pcount, fanout, fan_bends = self._fan_out(
                    path, prad, pcount, ends, [nodes[node_name(ref)] for ref in (wire.from_, wire.to)],
                    self._wire_od(wire.awg), self.catalog.wire(wire.awg).min_bend_radius_factor)
                bends = bends + fan_bends
                allowance = sum(termination_allowance(self.catalog, nodes[node_name(ref)].connector_type
                                                      if node_name(ref) in harness.connectors else None)
                                for ref in (wire.from_, wire.to))
                wire_routes[wire.id] = WireRoute(
                    wire.id, harness.id, wire.awg, wire.color, wire.signal, node_name(wire.from_), node_name(wire.to),
                    path, prad, pcount, bends, self._wire_od(wire.awg), float(harness.route.slack), allowance,
                    float(wire.length) if wire.length is not None else None, failed=key in failed,
                    ends=ends, fanout=fanout, core=core)

        ties = self._ties(harness, segments, wire_routes, nodes, harness_holes)
        trunk = max(wire_routes.values(), key=lambda w: float(np.sum(w.conductors)), default=None)
        bundle_path = (trunk.core if trunk.core is not None else trunk.path) if trunk is not None else np.zeros((0, 3))
        segment_out = [{"points": s["points"], "wires": sorted(s["wires"]), "od": s["od"], "conductors": s["conductors"],
                        "min_bend_radius": s["r_min"]} for s in segments.values()]
        for hole_id in harness_holes:
            self.holes[hole_id].wires.update(w.id for w in harness.wires)
        return HarnessRoute(harness.id, bundle_path, ties, segment_out, [w.id for w in harness.wires],
                            sorted(harness_holes), harness.jacket), wire_routes

    # ------------------------------------------------------- pin terminations
    def _pin_end(self, node: NodePoint, ref: str, by_pin: dict[str, float]) -> PinEnd | None:
        """Exact conductor exit of ``ref`` (``<connector>.<pin>``) on its connector half."""
        if node.kind not in ("connector", "floating") or node.frame is None or "." not in ref:
            return None
        pin = ref.split(".", 1)[1]
        if node.connector_type == "integral" or geometry_of(self.catalog, node.connector_type) is None:
            return pigtail_ends(node.frame, list(by_pin), by_pin, node.ref).get(pin)
        return pin_end(self.catalog, node.connector_type, node.frame, pin, node.ref)

    def _fan_length(self, node: NodePoint, by_pin: dict[str, float]) -> None:
        """Arc length over which the conductors of ``node`` converge into the bundle:
        three times the largest pin offset from the bundle axis plus two bundle
        diameters, at least 6 mm (shared by every wire at the node, so their
        bundle cores stay identical)."""
        if node.exit is None or node.direction is None:
            return
        lateral = 0.0
        for pin in by_pin:
            end = self._pin_end(node, f"{node.name}.{pin}", by_pin)
            if end is not None:
                d = end.position - node.exit
                lateral = max(lateral, float(np.linalg.norm(d - (d @ node.direction) * node.direction)))
        node.fan_length = max(0.006, 3.0 * lateral + 2.0 * node.od)

    def _fan_out(self, path: np.ndarray, radius: np.ndarray, count: np.ndarray,
                 ends: tuple[PinEnd | None, PinEnd | None], end_nodes: list[NodePoint], od: float,
                 bend_factor: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, tuple[int, int], list[Bend]]:
        """Start and end the conductor exactly at its pins: a C1 cubic Hermite
        from the pin exit (tangent = wire-entry axis) into the bundle path at the
        node's fan length. Returns the new path, per-vertex radius/count, the
        number of fan-out vertices at each end and the fan-outs' tightest bends."""
        out, rad, cnt = path, radius, count
        fan = [0, 0]
        bends: list[Bend | None] = []
        for side in (0, 1):
            end, node = ends[side], end_nodes[side]
            if end is None or len(out) < 2:
                continue
            pts = out if side == 0 else out[::-1]
            r = rad if side == 0 else rad[::-1]
            c = cnt if side == 0 else cnt[::-1]
            seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
            arc = np.concatenate([[0.0], np.cumsum(seg)])
            target = min(node.fan_length, 0.45 * float(arc[-1]))
            k = int(np.searchsorted(arc, target))
            k = min(max(k, 1), len(pts) - 1)
            q = pts[k]
            tangent = pts[min(k + 1, len(pts) - 1)] - pts[k - 1]
            tangent = tangent / (np.linalg.norm(tangent) or 1.0)
            p0, d0 = end.position, end.direction
            span = float(np.linalg.norm(q - p0))
            n = max(6, int(math.ceil(span / 0.001)))
            s_ = np.linspace(0.0, 1.0, n + 1)[:-1, None]
            h00, h10, h01, h11 = 2 * s_**3 - 3 * s_**2 + 1, s_**3 - 2 * s_**2 + s_, -2 * s_**3 + 3 * s_**2, s_**3 - s_**2
            curve = h00 * p0 + h10 * (d0 * span) + h01 * q + h11 * (tangent * span)
            if span < 1e-6:
                curve = p0[None]
            new = np.vstack([curve, pts[k:]])
            new_r = np.concatenate([np.full(len(curve), od / 2), r[k:]])
            new_c = np.concatenate([np.ones(len(curve)), c[k:]])
            fan[side] = len(curve)
            radii = curvature_radius(np.vstack([curve, q[None]])) if len(curve) >= 2 else np.array([np.inf])
            tight = float(np.min(radii)) if len(radii) else math.inf
            bends.append(Bend(p0.copy(), tight, bend_factor * od, 1, od) if np.isfinite(tight) else None)
            out, rad, cnt = (new, new_r, new_c) if side == 0 else (new[::-1], new_r[::-1], new_c[::-1])
        return out, rad, cnt, (fan[0], fan[1]), [b for b in bends if b is not None and b.radius < 4 * b.required]

    def _corridor(self, node: NodePoint) -> list[int]:
        if node.exit is None or node.lead_end is None or node.direction is None or node.lead_length <= 0:
            return []
        length = float(np.linalg.norm(node.lead_end - node.exit))
        n = max(2, int(math.ceil(length / (self.opt.voxel / 3))) + 1)
        points = node.exit + (node.lead_end - node.exit) * np.linspace(0, 1, n)[:, None]
        return sorted({self.grid.flat(p) for p in points})

    def _sequence(self, harness_id: str, key: tuple[str, str], a: NodePoint, b: NodePoint, voxels: list[int],
                  ctx: _Context, positions: dict[Any, np.ndarray], harness_holes: set[int],
                  via_voxels: list[int]) -> list:
        """Voxel path -> bundle-graph keys. Terminal voxels are replaced by the
        exact exit/lead points; plate crossings collapse into hole nodes."""
        seq: list = [f"N:{a.name}"]
        positions[f"N:{a.name}"] = a.exit
        if a.direction is not None and a.lead_length > 0:
            seq.append(f"L:{a.name}")
            positions[f"L:{a.name}"] = a.lead_end
        run: list[int] = []
        via_set = set(via_voxels)
        for v in voxels[1:-1]:
            if ctx.zone[v]:
                run.append(v)
                continue
            if run:
                seq.append(self._hole(harness_id, run, ctx, positions, harness_holes))
                run = []
            if v in via_set:
                name = f"V:{harness_id}:{v}"
                positions[name] = self.grid.center(v)
                seq.append(name)
                continue
            positions[v] = self.grid.center(v)
            seq.append(v)
        if run:
            seq.append(self._hole(harness_id, run, ctx, positions, harness_holes))
        if b.direction is not None and b.lead_length > 0:
            seq.append(f"L:{b.name}")
            positions[f"L:{b.name}"] = b.lead_end
        seq.append(f"N:{b.name}")
        positions[f"N:{b.name}"] = b.exit
        return seq

    def _hole(self, harness_id: str, run: list[int], ctx: _Context, positions: dict[Any, np.ndarray],
              harness_holes: set[int]) -> str:
        axis = ctx.zone[run[0]] - 1
        centers = np.array([self.grid.center(v) for v in run])
        mid = centers.mean(axis=0)
        plate = None
        for component, plate_axis, plate_field, _ in self.fields.plates:
            if plate_axis == axis and plate_field[run[0]] < ctx.r:
                plate = component
                break
        obstacle = self.obstacles.by_component(plate) if plate else None
        if obstacle is not None:
            lo, hi = obstacle.shape.aabb()
            mid[axis] = 0.5 * (lo[axis] + hi[axis])
        in_plane = [i for i in range(3) if i != axis]
        hole = None
        for existing in self.holes:
            if existing.plate == plate and np.linalg.norm((existing.center - mid)[in_plane]) <= 1.5 * self.opt.voxel:
                hole = existing
                break
        if hole is None:
            hole = PassThrough(len(self.holes), plate or "", axis, mid)
            self.holes.append(hole)
            # Later searches may reuse this hole column for (almost) free.
            centers_all = self.grid.centers()
            near = np.linalg.norm((centers_all - mid)[:, in_plane], axis=1) <= 1.01 * self.opt.voxel
            for v in np.nonzero(near & (ctx.zone_np > 0))[0]:
                self.hole_mask[v] = 1
        hole.harnesses.add(harness_id)
        harness_holes.add(hole.id)
        name = f"H:{hole.id}"
        positions[name] = hole.center
        return name

    # ------------------------------------------------------------- smoothing
    def _los(self, p: np.ndarray, q: np.ndarray, ctx: _Context, allowed: np.ndarray, chain: np.ndarray,
             hole_ok: np.ndarray, inflation: float) -> tuple[bool, float]:
        length = float(np.linalg.norm(q - p))
        n = max(2, int(math.ceil(length / (self.opt.voxel / 3))) + 1)
        points = p + (q - p) * np.linspace(0, 1, n)[:, None]
        idx = np.round((points - self.grid.origin) / self.opt.voxel).astype(int)
        idx = np.clip(idx, 0, np.asarray(self.grid.shape) - 1)
        vox = (idx[:, 0] * self.grid.shape[1] + idx[:, 1]) * self.grid.shape[2] + idx[:, 2]
        exempt = allowed[vox] | np.isin(vox, chain)
        if np.any(ctx.blocked_np[vox] & ~exempt):
            return False, math.inf
        if np.any((ctx.zone_np[vox] > 0) & ~hole_ok[vox] & ~exempt):
            return False, math.inf
        if inflation > 0 and np.any((ctx.clear_np[vox] < ctx.r + inflation) & ~exempt):
            return False, math.inf
        return True, float(ctx.cost_np[vox].mean() * length)

    def _hole_ok(self, harness_holes: set[int]) -> np.ndarray:
        mask = np.zeros(self.grid.size, dtype=bool)
        if not harness_holes:
            return mask
        centers = self.grid.centers()
        for hole_id in harness_holes:
            hole = self.holes[hole_id]
            in_plane = [i for i in range(3) if i != hole.axis]
            mask |= np.linalg.norm((centers - hole.center)[:, in_plane], axis=1) <= 1.6 * self.opt.voxel
        return mask

    def _string_pull(self, keys: tuple, points: np.ndarray, ctx: _Context, allowed: np.ndarray,
                     harness_holes: set[int], inflation: float) -> np.ndarray:
        chain = np.array([k for k in keys if isinstance(k, (int, np.integer))], dtype=np.int64)
        hole_ok = self._hole_ok(harness_holes)
        anchors = [i for i, k in enumerate(keys) if i in (0, len(keys) - 1) or isinstance(k, str)]
        vox = [k if isinstance(k, (int, np.integer)) else self.grid.flat(points[i]) for i, k in enumerate(keys)]
        step = np.linalg.norm(np.diff(points, axis=0), axis=1)
        cumulative = np.concatenate([[0.0], np.cumsum(step * 0.5 * (ctx.cost_np[vox[:-1]] + ctx.cost_np[vox[1:]]))])
        out = [points[0]]
        for start, end in zip(anchors[:-1], anchors[1:]):
            i = start
            while i < end:
                for j in range(end, i, -1):
                    if j == i + 1:
                        break
                    ok, cost = self._los(points[i], points[j], ctx, allowed, chain, hole_ok, inflation)
                    if not ok and inflation > 0:
                        ok, cost = self._los(points[i], points[j], ctx, allowed, chain, hole_ok, 0.0)
                        ok = ok and j - i <= 3
                    if ok and cost <= (cumulative[j] - cumulative[i]) * 1.0 + 1e-9:
                        break
                out.append(points[j])
                i = j
        return np.asarray(out)

    def _penetration(self, points: np.ndarray, field, radius: float) -> float:
        """Deepest raw-geometry penetration of a polyline (sampled at 0.5 mm),
        ignoring the first/last 10 mm (terminations)."""
        dense, arc = resample_polyline(points, 0.0005)
        keep = (arc > 0.01) & (arc < arc[-1] - 0.01)
        if not keep.any():
            return 0.0
        return float(max(0.0, np.max(radius - field(dense[keep]))))

    def _relax_field(self, harness_holes: set[int], keepout: bool = True):
        """Clearance field for relaxation: static and moving obstacles (with
        keepouts, or raw geometry for the final pass) and drillable plates
        except at this harness's holes. The moving-part clearance requirement
        stays a routing cost, not a hard constraint."""
        f = self.fields
        if keepout:
            combined = np.minimum(f.static_clear, f.moving_keep)
            plates = [p[2] for p in f.plates]
        else:
            combined = np.minimum(f.static_raw, f.moving_raw)
            plates = f.plates_raw
        hole_ok = self._hole_ok(harness_holes)
        for plate_field in plates:
            combined = np.minimum(combined, np.where(hole_ok, 0.05, plate_field))
        grid = self.grid
        return lambda points: grid.sample(combined, points)

    def _hard_clear(self, points: np.ndarray, r: float, exclude: set[str], harness_holes: set[int]) -> bool:
        static = [o for o in self.obstacles.static if o.component not in exclude and o.plate_axis is None]
        d, _ = self.obstacles.min_distance(points, static)
        if np.any(d < r - 2e-4):
            return False
        d, _ = self.obstacles.min_distance(points, self.obstacles.moving, swept=True, exclude=exclude)
        if np.any(d < r - 2e-4):
            return False
        for obstacle in self.obstacles.static:
            if obstacle.plate_axis is None or obstacle.component in exclude:
                continue
            d = obstacle.shape.sdf(points)
            bad = d < r - 2e-4
            for hole_id in harness_holes:
                hole = self.holes[hole_id]
                if hole.plate == obstacle.component:
                    in_plane = [i for i in range(3) if i != hole.axis]
                    bad &= np.linalg.norm((points - hole.center)[:, in_plane], axis=1) > 0.006
            if np.any(bad):
                return False
        return True

    def _fillet(self, pts: np.ndarray, radius: np.ndarray, count: np.ndarray, rmin: np.ndarray,
                start: NodePoint, end: NodePoint, harness_holes: set[int]):
        """Replace interior corners with circular arcs of radius >= the
        bundle minimum where the neighbouring edges and obstacles allow."""
        n = len(pts)
        if n < 3:
            return pts.copy(), radius.copy(), count.copy(), []
        edges = np.diff(pts, axis=0)
        lengths = np.linalg.norm(edges, axis=1)
        units = edges / np.where(lengths > 0, lengths, 1.0)[:, None]
        phi = np.zeros(n)
        for k in range(1, n - 1):
            phi[k] = math.acos(float(np.clip(units[k - 1] @ units[k], -1.0, 1.0)))
        target = self.opt.bend_margin * rmin
        need = np.where(phi > 1e-4, target * np.tan(np.minimum(phi, math.pi - 1e-3) / 2), 0.0)
        reserve = np.zeros(len(lengths))
        if start.kind in ("connector", "floating"):
            reserve[0] = min(start.od, 0.3 * lengths[0])
        if end.kind in ("connector", "floating"):
            reserve[-1] = min(end.od, 0.3 * lengths[-1])
        avail_in, avail_out = np.zeros(n), np.zeros(n)
        for e in range(len(lengths)):
            usable = max(0.0, lengths[e] - reserve[e])
            demand = need[e] + need[e + 1]
            scale = 1.0 if demand <= usable or demand == 0 else usable / demand
            avail_out[e] = need[e] * scale
            avail_in[e + 1] = need[e + 1] * scale
        out, out_r, out_c = [pts[0]], [radius[0]], [count[0]]
        bends: list[Bend] = []
        for k in range(1, n - 1):
            if phi[k] <= self.opt.fillet_min_angle:
                # Already smooth: keep the vertex, record its discrete radius.
                out.append(pts[k]); out_r.append(radius[k]); out_c.append(count[k])
                if phi[k] > 1e-6:
                    local = 0.5 * (lengths[k - 1] + lengths[k]) / (2 * math.sin(phi[k] / 2))
                    if local < 3 * rmin[k]:
                        bends.append(Bend(pts[k].copy(), float(local), float(rmin[k]), int(count[k]), float(2 * radius[k])))
                continue
            tan_half = math.tan(min(phi[k], math.pi - 1e-3) / 2)
            r_fit = min(target[k], min(avail_in[k], avail_out[k]) / tan_half)
            near_start = np.linalg.norm(pts[k] - pts[0]) < start.termination_length + r_fit
            near_end = np.linalg.norm(pts[k] - pts[-1]) < end.termination_length + r_fit
            exclude = (start.exempt if near_start else set()) | (end.exempt if near_end else set())
            achieved, arc = 0.0, None
            if not self._hard_clear(pts[k][None], radius[k], exclude, harness_holes):
                # The corner itself already touches an obstacle (the routed path
                # was forced through it); rounding it is not what collides.
                arc = self._arc(pts[k], units[k - 1], units[k], phi[k], r_fit)
                out.extend(arc); out_r.extend([radius[k]] * len(arc)); out_c.extend([count[k]] * len(arc))
                bends.append(Bend(pts[k].copy(), r_fit, float(rmin[k]), int(count[k]), float(2 * radius[k])))
                continue
            for factor in (1.0, 0.75, 0.5, 0.3, 0.15, 0.05):
                candidate = r_fit * factor
                arc = self._arc(pts[k], units[k - 1], units[k], phi[k], candidate)
                if self._hard_clear(arc, radius[k], exclude, harness_holes):
                    achieved = candidate
                    break
            if achieved <= 0.0:
                arc = pts[k][None]
            out.extend(arc); out_r.extend([radius[k]] * len(arc)); out_c.extend([count[k]] * len(arc))
            bends.append(Bend(pts[k].copy(), achieved, float(rmin[k]), int(count[k]), float(2 * radius[k])))
        out.append(pts[-1]); out_r.append(radius[-1]); out_c.append(count[-1])
        return np.asarray(out), np.asarray(out_r), np.asarray(out_c), bends

    @staticmethod
    def _arc(corner: np.ndarray, u_in: np.ndarray, u_out: np.ndarray, phi: float, radius: float) -> np.ndarray:
        if radius <= 0:
            return corner[None]
        t = radius * math.tan(phi / 2)
        start, finish = corner - u_in * t, corner + u_out * t
        bisector = u_out - u_in
        bisector /= np.linalg.norm(bisector)
        center = corner + bisector * (radius / math.cos(phi / 2))
        a, b = start - center, finish - center
        steps = max(2, int(math.ceil(phi / math.radians(7.5))))
        angles = np.linspace(0.0, 1.0, steps + 1)
        # Spherical interpolation between the two radius vectors around the centre.
        omega = math.acos(float(np.clip((a @ b) / (radius * radius), -1.0, 1.0)))
        if omega < 1e-9:
            return np.vstack([start, finish])
        pts = [(math.sin((1 - s) * omega) * a + math.sin(s * omega) * b) / math.sin(omega) + center for s in angles]
        return np.asarray(pts)

    # ------------------------------------------------------------------ ties
    def _ties(self, harness: Harness, segments: dict, wires: dict[str, WireRoute], nodes: dict[str, NodePoint],
              harness_holes: set[int]) -> list[np.ndarray]:
        structures = self.obstacles.structures
        ties: list[np.ndarray] = []

        def eligible(points: np.ndarray, r: float) -> np.ndarray:
            d, _ = self.obstacles.min_distance(points, structures)
            ok = d - r <= self.opt.tie_reach
            for hole_id in harness_holes:
                ok &= np.linalg.norm(points - self.holes[hole_id].center, axis=1) > 0.008
            return ok

        exits = [n.exit for n in nodes.values() if n.kind == "connector" and n.exit is not None]
        for segment in segments.values():
            wire = wires.get(sorted(segment["wires"])[0])
            if wire is None or wire.failed:
                continue
            _, s0 = project_to_polyline(wire.path, segment["points"][0][None])
            _, s1 = project_to_polyline(wire.path, segment["points"][-1][None])
            lo, hi = sorted((s0, s1))
            dense, arc = resample_polyline(wire.path, 0.002)
            mask = (arc >= lo) & (arc <= hi)
            if mask.sum() < 2:
                continue
            seg_pts, seg_arc = dense[mask], arc[mask]
            ok = eligible(seg_pts, segment["od"] / 2)
            for exit_point in exits:
                ok &= np.linalg.norm(seg_pts - exit_point, axis=1) > 0.012
            length = hi - lo
            count = int(length // self.opt.tie_spacing)
            targets = [lo + length * (k + 1) / (count + 1) for k in range(count)]
            if count == 0 and length > 0.035:
                targets = [lo + length / 2]
            for target in targets:
                window = ok & (np.abs(seg_arc - target) <= 0.02)
                if window.any():
                    k = int(np.argmin(np.where(window, np.abs(seg_arc - target), np.inf)))
                    ties.append(seg_pts[k])
        for node in nodes.values():
            if node.kind == "floating" and node.exit is not None:
                probe = node.exit[None]
                d, _ = self.obstacles.min_distance(probe, structures)
                if d[0] - node.od / 2 <= self.opt.tie_reach + body_width(self.catalog, node.connector_type) / 2:
                    ties.append(node.exit.copy())
        for hint in harness.route.tie_points:
            ties.append(np.asarray(hint, dtype=float))
        unique: list[np.ndarray] = []
        for tie in ties:
            if all(np.linalg.norm(tie - other) > 0.02 for other in unique):
                unique.append(tie)
        return unique

    def _size_holes(self, wires: dict[str, WireRoute]) -> None:
        for hole in self.holes:
            ods = [wires[w].od for w in hole.wires if w in wires]
            hole.diameter = bundle_diameter(ods) + self.opt.hole_clearance if ods else 0.0


def route_project(project: Project, obstacles: ObstacleSet, options: RouterOptions | None = None) -> RoutingResult:
    return HarnessRouter(project, obstacles, options).route()

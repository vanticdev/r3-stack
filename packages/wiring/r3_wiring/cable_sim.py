"""Physical cable slack simulation (extended position-based dynamics).

Every wire is a chain of particles (5-10 mm segments) simulated in the
*chassis frame*, so the chassis motion enters as inertial pseudo-forces:

    a = R^T g - R^T a_o - alpha x r - 2 omega x v - omega x (omega x r)

(``R`` chassis attitude, ``a_o`` world acceleration of the robot-frame origin,
``omega``/``alpha`` body-frame angular velocity/acceleration, ``r``/``v``
particle position/velocity in the chassis frame).

Constraints are solved with XPBD (Macklin et al. 2016) using graph colouring so
each colour is an exact vectorised Gauss-Seidel sweep:

* stretch: ``C = |x_i - x_j| - l``, compliance ``l / (E A)`` (stranded copper);
* bending: ``C = |x_{k-1} - 2 x_k + x_{k+1}|``; with ``C ~ l theta`` the energy
  ``C^2 / (2 alpha)`` equals the discrete rod energy ``EI theta^2 / (2 l)``
  when ``alpha = l^3 / EI`` (catalog ``bending_stiffness``);
* pins: connector ends and tie points are kinematic, moving with their body;
* contact: particles are projected out of chassis-fixed component geometry
  (inflated by the wire radius) with Coulomb friction on the tangential
  displacement (static if ``|dx_t| < mu_s d``, else reduced by ``mu_k d``).

Clearance to moving parts (posed from the ``moving`` input) is measured
every step; moving parts do not push cables (a contact is the failure being
detected).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from r3_model.project import Project

from .bodies import IDENTITY, ROOT, Transform, invert
from .geometry import project_to_polyline, quat_to_matrix
from .obstacles import ObstacleSet, build_obstacles
from .terminations import termination_allowance

COPPER_E = 117e9  # Pa, annealed copper
STRANDING_FACTOR = 0.6  # effective axial modulus of a stranded, insulated conductor (estimate)
GRAVITY = np.array([0.0, 0.0, -9.80665])


@dataclass
class CableReport:
    time: float
    min_clearance: dict[str, tuple[float, str]]
    penetrating: list[str]
    max_speed: float
    max_strain: float


class RodSolver:
    """XPBD solver for a set of particle chains."""

    def __init__(self, iterations: int = 2, damping: float = 19.0, friction: float = 0.5) -> None:
        self.iterations = iterations
        self.damping = damping
        self.friction = friction
        self._chains: list[dict[str, Any]] = []
        self.x = np.zeros((0, 3))

    def add_chain(self, points: np.ndarray, rest: np.ndarray, mass: np.ndarray, bending_stiffness: float,
                  axial_stiffness: float, radius: float, pinned: np.ndarray) -> slice:
        start = sum(len(c["points"]) for c in self._chains)
        self._chains.append({"points": np.asarray(points, float), "rest": np.asarray(rest, float),
                             "mass": np.asarray(mass, float), "ei": bending_stiffness, "ea": axial_stiffness,
                             "radius": radius, "pinned": np.asarray(pinned, bool)})
        return slice(start, start + len(points))

    def finalize(self) -> None:
        xs, ms, rs, pins = [], [], [], []
        si, sj, sl, sc, scol = [], [], [], [], []
        ba, bb, bc, bcomp, bcol = [], [], [], [], []
        offset = 0
        for chain in self._chains:
            n = len(chain["points"])
            xs.append(chain["points"]); ms.append(chain["mass"]); rs.append(np.full(n, chain["radius"]))
            pins.append(chain["pinned"])
            for k in range(n - 1):
                si.append(offset + k); sj.append(offset + k + 1); sl.append(chain["rest"][k])
                sc.append(chain["rest"][k] / chain["ea"]); scol.append(k % 2)
            for k in range(1, n - 1):
                lbar = 0.5 * (chain["rest"][k - 1] + chain["rest"][k])
                ba.append(offset + k - 1); bb.append(offset + k); bc.append(offset + k + 1)
                bcomp.append(lbar ** 3 / chain["ei"]); bcol.append(k % 3)
            offset += n
        self.x = np.vstack(xs)
        self.v = np.zeros_like(self.x)
        self.mass = np.concatenate(ms)
        self.radius = np.concatenate(rs)
        self.pinned = np.concatenate(pins)
        self.inv_mass = np.where(self.pinned, 0.0, 1.0 / self.mass)
        self.targets = self.x.copy()
        si, sj, sl, sc, scol = map(np.asarray, (si, sj, sl, sc, scol))
        ba, bb, bc, bcomp, bcol = map(np.asarray, (ba, bb, bc, bcomp, bcol))
        self.stretch = [(si[scol == c], sj[scol == c], sl[scol == c], sc[scol == c]) for c in (0, 1)]
        self.bend = [(ba[bcol == c], bb[bcol == c], bc[bcol == c], bcomp[bcol == c]) for c in (0, 1, 2)]
        self.stretch_all = (si, sj, sl)
        self.colliders: list[tuple[Any, np.ndarray]] = []  # (shape, exempt mask)

    def substep(self, h: float, accel, damping: float | None = None) -> None:
        free = ~self.pinned
        self.v[free] += h * accel(self.x[free], self.v[free])
        previous = self.x.copy()
        p = self.x + h * self.v
        p[self.pinned] = self.targets[self.pinned]
        w = self.inv_mass
        h2 = h * h
        lam_s = [np.zeros(len(g[0])) for g in self.stretch]
        lam_b = [np.zeros(len(g[0])) for g in self.bend]
        for _ in range(self.iterations):
            for g, (i, j, rest, comp) in enumerate(self.stretch):
                if not len(i):
                    continue
                d = p[i] - p[j]
                length = np.linalg.norm(d, axis=1)
                n = d / np.maximum(length, 1e-12)[:, None]
                at = comp / h2
                dl = (-(length - rest) - at * lam_s[g]) / (w[i] + w[j] + at)
                lam_s[g] += dl
                p[i] += (w[i] * dl)[:, None] * n
                p[j] -= (w[j] * dl)[:, None] * n
            for g, (a, b, c, comp) in enumerate(self.bend):
                if not len(a):
                    continue
                e = p[a] + p[c] - 2 * p[b]
                value = np.linalg.norm(e, axis=1)
                ok = value > 1e-12
                u = e / np.where(ok, value, 1.0)[:, None]
                at = comp / h2
                dl = np.where(ok, (-value - at * lam_b[g]) / (w[a] + w[c] + 4 * w[b] + at), 0.0)
                lam_b[g] += dl
                p[a] += (w[a] * dl)[:, None] * u
                p[c] += (w[c] * dl)[:, None] * u
                p[b] -= (2 * w[b] * dl)[:, None] * u
        self._collide(p, previous)
        p[self.pinned] = self.targets[self.pinned]
        self.v = (p - previous) / h
        beta = self.damping if damping is None else damping
        self.v[free] *= math.exp(-beta * h)
        self.v[self.pinned] = 0.0
        self.x = p

    def _collide(self, p: np.ndarray, previous: np.ndarray) -> None:
        mu_s = mu_k = self.friction
        for shape, exempt in self.colliders:
            lo, hi = shape.aabb()
            near = np.all((p >= lo - self.radius[:, None]) & (p <= hi + self.radius[:, None]), axis=1)
            near &= ~exempt & ~self.pinned
            if not near.any():
                continue
            idx = np.nonzero(near)[0]
            d = shape.sdf(p[idx]) - self.radius[idx]
            hit = d < 0
            if not hit.any():
                continue
            idx, d = idx[hit], d[hit]
            normal = shape.gradient(p[idx])
            p[idx] -= normal * d[:, None]
            # Coulomb friction on the tangential displacement of this substep.
            delta = p[idx] - previous[idx]
            tangential = delta - np.einsum("ij,ij->i", delta, normal)[:, None] * normal
            tlen = np.linalg.norm(tangential, axis=1)
            depth = -d
            static = tlen < mu_s * depth
            scale = np.where(static, 1.0, np.minimum(1.0, mu_k * depth / np.maximum(tlen, 1e-15)))
            p[idx] -= tangential * scale[:, None]

    def strain(self) -> float:
        """Maximum tensile strain of any segment (compression is slack, not strain)."""
        i, j, rest = self.stretch_all
        if not len(i):
            return 0.0
        return float(max(0.0, np.max(np.linalg.norm(self.x[i] - self.x[j], axis=1) / rest - 1.0)))


@dataclass
class _WireSim:
    wire_id: str
    harness: str
    sl: slice
    pins: list[tuple[int, str, np.ndarray]]  # (particle, body, nominal position)
    exempt_moving: dict[str, np.ndarray] = field(default_factory=dict)  # component -> particle mask (local)


class CableSimulator:
    """``CableSimulator(project, routes).step(dt, chassis, moving) -> CableReport``.

    ``chassis``: ``pos``, ``quat`` ([w, x, y, z]) of the robot frame in the
    world, and world-frame ``lin_acc`` (of the robot-frame origin),
    ``ang_vel``, ``ang_acc``. ``moving``: world poses (``pos``, ``quat``) of
    component frames (the frame of ``RobotGraph.component_pose``). Components
    not listed stay at their nominal pose relative to the chassis.
    """

    def __init__(self, project: Project, routes: dict[str, Any], obstacles: ObstacleSet | None = None, *,
                 segment_length: float = 0.0075, substep: float = 0.0005, iterations: int = 2,
                 damping: float = 19.0, friction: float = 0.5, settle: float = 0.4, seed: int = 1) -> None:
        self.project = project
        self.routes = routes
        self.obstacles = obstacles or build_obstacles(project)[0]
        self.kin = self.obstacles.kinematics
        self.substep_dt = substep
        self.time = 0.0
        self.solver = RodSolver(iterations, damping, friction)
        self.wires: list[_WireSim] = []
        self.wire_info: dict[str, dict[str, Any]] = {}
        self._rng = np.random.default_rng(seed)
        self._build(segment_length)
        self._transforms: dict[str, Transform] = {}
        self._previous_targets = self.solver.targets.copy()
        self.min_history: dict[str, tuple[float, str, float]] = {}
        if settle > 0:
            self.settle(settle)

    # ----------------------------------------------------------------- build
    def _ends(self, wire_id: str) -> tuple[list[tuple[str, set[str], float]], Any]:
        spec, catalog, graph = self.project.spec, self.project.catalog, self.project.graph
        for harness in spec.electrical.harnesses:
            for wire in harness.wires:
                if wire.id != wire_id:
                    continue
                ends = []
                od = float(catalog.wire(wire.awg).outer_diameter)
                for ref in (wire.from_, wire.to):
                    if ref in harness.splices:
                        ends.append((ROOT, set(), max(0.006, 2 * od)))
                        continue
                    conn = harness.connectors[ref.split(".")[0]]
                    endpoint = graph.endpoints.get(conn.mates) if conn.mates else None
                    if endpoint is not None and endpoint.owner_kind == "component":
                        component = conn.mates.split(".")[0]
                        ends.append((self.kin.body_of(component), set(self.kin.mount_chain(component)), 0.012 + 3 * od))
                    else:
                        ends.append((ROOT, set(), 0.006))
                allowance = sum(termination_allowance(catalog, None if ref in harness.splices
                                                      else harness.connectors[ref.split(".")[0]].type)
                                for ref in (wire.from_, wire.to))
                return ends, (harness, wire, allowance)
        raise KeyError(wire_id)

    def _build(self, segment_length: float) -> None:
        catalog = self.project.catalog
        for wire_id, data in self.routes["wires"].items():
            path = np.asarray(data["path"], dtype=float)
            if len(path) < 2:
                continue
            ends, (harness, wire, allowance) = self._ends(wire_id)
            gauge = catalog.wire(int(data["awg"]))
            od = float(gauge.outer_diameter)
            routed = float(data["routed_length"])
            free_length = float(data["cut_length"]) - allowance
            stretch = max(free_length, routed) / max(routed, 1e-9)
            # pins: ends + ties within reach of this wire's path
            seg = np.linalg.norm(np.diff(path, axis=0), axis=1)
            total = float(seg.sum())
            pin_arcs = [(0.0, ends[0][0], path[0]), (total, ends[1][0], path[-1])]
            for tie in self.routes["harnesses"].get(harness.id, {}).get("tie_points", []):
                distance, arc = project_to_polyline(path, np.asarray(tie)[None])
                if distance <= 1.6 * od + 0.0015 and 0.003 < arc < total - 0.003:
                    pin_arcs.append((arc, ROOT, None))
            pin_arcs.sort(key=lambda item: item[0])
            cum = np.concatenate([[0.0], np.cumsum(seg)])

            def at(s: float) -> np.ndarray:
                return np.column_stack([np.interp(s, cum, path[:, a]) for a in range(3)])

            points, rest, pinned, pin_bodies = [], [], [], []
            for (s0, body0, p0), (s1, body1, p1) in zip(pin_arcs[:-1], pin_arcs[1:]):
                span = s1 - s0
                if span < 1e-6:
                    continue
                n = max(1, int(math.ceil(span / segment_length)))
                arcs = np.linspace(s0, s1, n + 1)
                pts = at(arcs)
                if not points:
                    points.append(pts[0]); pinned.append(True); pin_bodies.append(body0)
                points.extend(pts[1:])
                pinned.extend([False] * (n - 1) + [True])
                pin_bodies.extend([None] * (n - 1) + [body1])
                rest.extend([span * stretch / n] * n)
            points = np.asarray(points)
            interior = ~np.asarray(pinned)
            points[interior] += self._rng.normal(0.0, 5e-5, size=(int(interior.sum()), 3))
            rest = np.asarray(rest)
            mass = np.zeros(len(points))
            mass[:-1] += 0.5 * rest * float(gauge.mass_per_m)
            mass[1:] += 0.5 * rest * float(gauge.mass_per_m)
            area = math.pi * (float(gauge.conductor_diameter) / 2) ** 2
            sl = self.solver.add_chain(points, rest, mass, float(gauge.bending_stiffness),
                                       COPPER_E * STRANDING_FACTOR * area, od / 2, np.asarray(pinned))
            pins = [(sl.start + k, pin_bodies[k], points[k].copy()) for k in range(len(points)) if pinned[k]]
            arc_rest = np.concatenate([[0.0], np.cumsum(rest)])
            sim = _WireSim(wire_id, harness.id, sl, pins)
            for end_index, (body, exempt, zone) in enumerate(ends):
                local = arc_rest <= zone if end_index == 0 else arc_rest[-1] - arc_rest <= zone
                for component in exempt:
                    sim.exempt_moving[component] = sim.exempt_moving.get(component, np.zeros(len(points), bool)) | local
            self.wires.append(sim)
            self.wire_info[wire_id] = {"harness": harness.id, "awg": int(data["awg"]), "color": wire.color,
                                       "particles": len(points), "pins": len(pins), "free_length": free_length,
                                       "routed_length": routed, "od": od, "ends": [e[0] for e in ends]}
        self.solver.finalize()
        self._setup_colliders()

    def _setup_colliders(self) -> None:
        x = self.solver.x
        # Everything rigidly attached to the chassis is a collider, including
        # parts flagged moving without a joint (motor can, gear housing).
        fixed = [o for o in self.obstacles.obstacles if not self.kin.is_moving(o.body)]
        for obstacle in fixed:
            exempt = np.zeros(len(x), dtype=bool)
            for sim in self.wires:
                mask = np.zeros(sim.sl.stop - sim.sl.start, dtype=bool)
                for component, local in sim.exempt_moving.items():
                    if component == obstacle.component:
                        mask |= local
                exempt[sim.sl] |= mask
            # Particles that start inside (forced routes, drilled pass-throughs)
            # are exempt from that collider, plus their neighbours within 4 mm.
            inside = obstacle.shape.sdf(x) < self.solver.radius
            if inside.any():
                close = np.zeros(len(x), dtype=bool)
                for k in np.nonzero(inside)[0]:
                    close |= np.linalg.norm(x - x[k], axis=1) < 0.004
                exempt |= close
            self.solver.colliders.append((obstacle.shape, exempt))
        self._moving_exempt = {}
        for obstacle in self.obstacles.moving:
            mask = np.zeros(len(x), dtype=bool)
            for sim in self.wires:
                if obstacle.component in sim.exempt_moving:
                    mask[sim.sl] |= sim.exempt_moving[obstacle.component]
            self._moving_exempt[obstacle.component] = mask

    # ------------------------------------------------------------- dynamics
    def _relative_transforms(self, chassis: dict[str, Any], moving: dict[str, dict[str, Any]]) -> dict[str, Transform]:
        """Transform of each moved component relative to its nominal pose, in the chassis frame."""
        rc = quat_to_matrix(chassis.get("quat", [1, 0, 0, 0]))
        pc = np.asarray(chassis.get("pos", [0, 0, 0]), dtype=float)
        out = {}
        for component, pose in (moving or {}).items():
            if component not in self.project.spec.components:
                continue
            rm = rc.T @ quat_to_matrix(pose.get("quat", [1, 0, 0, 0]))
            pm = rc.T @ (np.asarray(pose.get("pos"), dtype=float) - pc)
            p0, r0 = self.project.graph.component_pose(component)
            rotation = rm @ r0.T
            out[component] = (rotation, pm - rotation @ p0)
        return out

    def _body_transform(self, body: str, transforms: dict[str, Transform]) -> Transform:
        return transforms.get(body, IDENTITY) if body != ROOT else IDENTITY

    def step(self, dt: float, chassis: dict[str, Any] | None = None,
             moving: dict[str, dict[str, Any]] | None = None) -> CableReport:
        chassis = chassis or {}
        transforms = self._relative_transforms(chassis, moving or {})
        targets = self.solver.targets.copy()
        for sim in self.wires:
            for index, body, nominal in sim.pins:
                rotation, translation = self._body_transform(body, transforms)
                targets[index] = rotation @ nominal + translation
        rc = quat_to_matrix(chassis.get("quat", [1, 0, 0, 0]))
        gravity = rc.T @ GRAVITY
        acc = rc.T @ np.asarray(chassis.get("lin_acc", [0, 0, 0]), dtype=float)
        omega = rc.T @ np.asarray(chassis.get("ang_vel", [0, 0, 0]), dtype=float)
        alpha = rc.T @ np.asarray(chassis.get("ang_acc", [0, 0, 0]), dtype=float)

        def accel(x: np.ndarray, v: np.ndarray) -> np.ndarray:
            a = gravity - acc - np.cross(alpha, x) - 2 * np.cross(omega, v) - np.cross(omega, np.cross(omega, x))
            return a

        n = max(1, int(math.ceil(dt / self.substep_dt - 1e-9)))
        h = dt / n
        start = self._previous_targets
        for k in range(n):
            s = (k + 1) / n
            self.solver.targets = start + (targets - start) * s
            self.solver.substep(h, accel)
        self._previous_targets = targets
        self.time += dt
        self._transforms = transforms
        return self._report()

    def settle(self, duration: float, damping: float = 80.0) -> None:
        """Quasi-static pre-roll under gravity with heavy damping."""
        steps = int(math.ceil(duration / self.substep_dt))
        accel = lambda x, v: np.broadcast_to(GRAVITY, x.shape)  # noqa: E731
        for _ in range(steps):
            self.solver.substep(self.substep_dt, accel, damping=damping)
        self.solver.v[:] = 0.0

    def clearances(self) -> dict[str, tuple[float, str]]:
        x, radius = self.solver.x, self.solver.radius
        best = {sim.wire_id: (math.inf, "") for sim in self.wires}
        for obstacle in self.obstacles.moving:
            rotation, translation = self._transforms.get(obstacle.component,
                                                         self._transforms.get(obstacle.body, IDENTITY))
            inv_r, inv_t = invert((rotation, translation))
            local = x @ inv_r.T + inv_t
            d = obstacle.shape.sdf_bounded(local, 0.2) - radius
            d[self._moving_exempt[obstacle.component]] = math.inf
            for sim in self.wires:
                value = float(d[sim.sl].min())
                if value < best[sim.wire_id][0]:
                    best[sim.wire_id] = (value, obstacle.component)
        return best

    def _report(self) -> CableReport:
        clear = self.clearances()
        for wire, (distance, component) in clear.items():
            if wire not in self.min_history or distance < self.min_history[wire][0]:
                self.min_history[wire] = (distance, component, self.time)
        return CableReport(self.time, clear, [w for w, (d, _) in clear.items() if d < 0],
                           float(np.max(np.linalg.norm(self.solver.v, axis=1))) if len(self.solver.v) else 0.0,
                           self.solver.strain())

    def positions(self) -> dict[str, np.ndarray]:
        return {sim.wire_id: self.solver.x[sim.sl].copy() for sim in self.wires}

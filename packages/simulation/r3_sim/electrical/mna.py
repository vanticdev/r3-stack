"""Nonlinear modified nodal analysis (MNA) with Newton iterations.

Unknowns ``x`` are node voltages (relative to the reference node, handle
``GROUND = -1``) and branch currents of voltage-defining elements. Each row of
the residual ``F(x)`` is either Kirchhoff's current law at a node (sum of
currents *leaving* the node through elements) or a branch constitutive
equation. Newton solves ``J dx = -F`` until both the update and the residual
are below tolerance; the previous step's solution is used as the starting
point (warm start). A ``gmin`` conductance from every node to the reference
keeps floating signal islands well-posed, exactly like SPICE's ``gmin``.

Linear resistors are stamped once per step in a vectorized bank. Every other
element derives from :class:`NumericElement`, which needs only a residual
function; its Jacobian is obtained by central differences.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Iterable

import numpy as np

GROUND = -1
DEFAULT_GMIN = 1e-9  # S, node-to-reference leakage (same role as SPICE gmin)


def softplus(x: float, width: float) -> float:
    """Smooth max(x, 0) with transition width ``width``: w*ln(1+exp(x/w))."""
    z = x / width
    if z > 40.0:
        return x
    if z < -40.0:
        return width * math.exp(z)
    return width * math.log1p(math.exp(z))


def smooth_min(a: float, b: float, width: float) -> float:
    """Smooth min(a, b) = a - softplus(a - b)."""
    return a - softplus(a - b, width)


def spice_softplus(expr: str, width: float) -> str:
    """Numerically stable SPICE expression of :func:`softplus`."""
    return f"(max(({expr}),0)+{width:.9g}*ln(1+exp(-abs({expr})/{width:.9g})))"


def spice_smooth_min(a: str, b: str, width: float) -> str:
    return f"(({a})-{spice_softplus(f'({a})-({b})', width)})"


class SpiceWriter:
    """Collects netlist lines; maps unknown handles to SPICE node names."""

    def __init__(self, circuit: "Circuit") -> None:
        self.circuit = circuit
        self.lines: list[str] = []
        self._count = 0
        self._internal = 0
        self.sense: dict[int, str] = {}  # branch unknown -> name of the 0 V source measuring it

    def node(self, handle: int) -> str:
        return "0" if handle == GROUND else f"n{handle}"

    def v(self, a: int, b: int = GROUND) -> str:
        if b == GROUND:
            return "0" if a == GROUND else f"V({self.node(a)})"
        if a == GROUND:
            return f"(-V({self.node(b)}))"
        return f"V({self.node(a)},{self.node(b)})"

    def name(self, prefix: str) -> str:
        self._count += 1
        return f"{prefix}{self._count}"

    def internal_node(self) -> str:
        self._internal += 1
        return f"x{self._internal}"

    def add(self, line: str) -> None:
        self.lines.append(line)

    def comment(self, text: str) -> None:
        self.lines.append(f"* {text}")


class Element:
    """Base class. ``load`` adds the element's residual and Jacobian."""

    name: str = ""

    def load(self, x: np.ndarray, F: np.ndarray, J: np.ndarray) -> None:
        raise NotImplementedError

    def spice(self, writer: SpiceWriter, x: np.ndarray) -> None:
        raise NotImplementedError(f"{type(self).__name__} has no SPICE representation")


class NumericElement(Element):
    """Element defined by ``residual(values) -> contributions``.

    ``rows`` are the residual rows it contributes to (node KCL rows or its own
    branch rows; ``GROUND`` entries are dropped), ``deps`` the unknowns its
    residual depends on. The Jacobian is computed by central differences."""

    rows: list[int]
    deps: list[int]

    def residual(self, v: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def _values(self, x: np.ndarray) -> np.ndarray:
        return np.array([x[d] if d != GROUND else 0.0 for d in self.deps])

    def evaluate(self, x: np.ndarray) -> np.ndarray:
        return self.residual(self._values(x))

    def load(self, x: np.ndarray, F: np.ndarray, J: np.ndarray) -> None:
        v = self._values(x)
        r = self.residual(v)
        rows = self.rows
        for k, row in enumerate(rows):
            if row != GROUND:
                F[row] += r[k]
        for j, dep in enumerate(self.deps):
            if dep == GROUND:
                continue
            h = 1e-7 * max(1.0, abs(v[j]))
            vp = v.copy()
            vm = v.copy()
            vp[j] += h
            vm[j] -= h
            column = (self.residual(vp) - self.residual(vm)) / (2.0 * h)
            for k, row in enumerate(rows):
                if row != GROUND:
                    J[row, dep] += column[k]


class VoltageSource(NumericElement):
    """Thevenin source between ``pos`` and ``neg`` with branch current ``i``
    delivered *out of* ``pos``: V(pos) - V(neg) = E - R*i. ``enabled=False``
    turns it into an open circuit (i = 0)."""

    def __init__(self, name: str, pos: int, neg: int, branch: int, emf: float = 0.0, resistance: float = 0.0) -> None:
        self.name = name
        self.pos, self.neg, self.branch = pos, neg, branch
        self.emf = emf
        self.resistance = resistance
        self.enabled = True
        self.rows = [pos, neg, branch]
        self.deps = [pos, neg, branch]

    def residual(self, v: np.ndarray) -> np.ndarray:
        vp, vn, i = v
        if not self.enabled:
            return np.array([-i, i, i])
        return np.array([-i, i, (vp - vn) - (self.emf - self.resistance * i)])

    def spice(self, writer: SpiceWriter, x: np.ndarray) -> None:
        writer.comment(f"{self.name}: Thevenin source E={self.emf:.6g} V, R={self.resistance:.6g} ohm")
        if not self.enabled:
            return
        mid, sense = writer.internal_node(), writer.internal_node()
        writer.add(f"{writer.name('V')} {mid} {writer.node(self.neg)} DC {self.emf:.12g}")
        writer.add(f"{writer.name('R')} {mid} {sense} {max(self.resistance, 1e-9):.12g}")
        writer.add(f"{writer.name('V')} {sense} {writer.node(self.pos)} DC 0")


@dataclass
class ResistorBank:
    """Vectorized linear resistors. ``resistance`` may be updated every step."""

    a: list[int] = field(default_factory=list)
    b: list[int] = field(default_factory=list)
    resistance: list[float] = field(default_factory=list)
    names: list[str] = field(default_factory=list)

    def add(self, a: int, b: int, resistance: float, name: str) -> int:
        self.a.append(a)
        self.b.append(b)
        self.resistance.append(float(resistance))
        self.names.append(name)
        return len(self.a) - 1

    def arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        return np.asarray(self.a), np.asarray(self.b), np.asarray(self.resistance, dtype=float)

    def currents(self, x: np.ndarray) -> np.ndarray:
        """Current from ``a`` to ``b`` through every resistor."""
        a, b, r = self.arrays()
        va = np.where(a >= 0, x[np.maximum(a, 0)], 0.0)
        vb = np.where(b >= 0, x[np.maximum(b, 0)], 0.0)
        return (va - vb) / r


class SolverError(RuntimeError):
    pass


@dataclass
class SolveInfo:
    iterations: int
    residual: float
    converged: bool
    gmin_stepping: bool = False


class Circuit:
    """Unknown allocation, linear stamping and the Newton solver."""

    def __init__(self, gmin: float = DEFAULT_GMIN) -> None:
        self.gmin = gmin
        self.labels: list[str] = []
        self.is_node: list[bool] = []
        self._index: dict[str, int] = {}
        self.resistors = ResistorBank()
        self.elements: list[Element] = []
        self.reference = ""

    # ----------------------------------------------------------- allocation
    def set_reference(self, label: str) -> None:
        self.reference = label
        self._index[label] = GROUND

    def node(self, label: str) -> int:
        if label in self._index:
            return self._index[label]
        return self._allocate(label, True)

    def branch(self, label: str) -> int:
        if label in self._index:
            raise ValueError(f"duplicate branch {label!r}")
        return self._allocate(label, False)

    def _allocate(self, label: str, is_node: bool) -> int:
        index = len(self.labels)
        self.labels.append(label)
        self.is_node.append(is_node)
        self._index[label] = index
        return index

    def has(self, label: str) -> bool:
        return label in self._index

    def index(self, label: str) -> int:
        return self._index[label]

    @property
    def size(self) -> int:
        return len(self.labels)

    def add(self, element: Element) -> Element:
        self.elements.append(element)
        return element

    def resistor(self, a: int, b: int, resistance: float, name: str) -> int:
        return self.resistors.add(a, b, resistance, name)

    # -------------------------------------------------------------- solving
    def linear_matrix(self, gmin: float | None = None) -> np.ndarray:
        n = self.size
        A = np.zeros((n, n))
        a, b, r = self.resistors.arrays()
        if len(a):
            g = 1.0 / r
            for ia, ib, gk in zip(a, b, g):
                if ia >= 0:
                    A[ia, ia] += gk
                if ib >= 0:
                    A[ib, ib] += gk
                if ia >= 0 and ib >= 0:
                    A[ia, ib] -= gk
                    A[ib, ia] -= gk
        nodes = np.flatnonzero(np.asarray(self.is_node))
        A[nodes, nodes] += self.gmin if gmin is None else gmin
        return A

    def residual(self, x: np.ndarray, A: np.ndarray | None = None) -> np.ndarray:
        A = self.linear_matrix() if A is None else A
        F = A @ x
        J = np.zeros_like(A)
        for element in self.elements:
            element.load(x, F, J)
        return F

    def solve(self, x0: np.ndarray | None = None, max_iterations: int = 80, tolerance: float = 1e-9,
              voltage_step_limit: float = 2.0) -> tuple[np.ndarray, SolveInfo]:
        x = np.zeros(self.size) if x0 is None or len(x0) != self.size else np.array(x0, dtype=float)
        A = self.linear_matrix()
        result = self._newton(x, A, max_iterations, tolerance, voltage_step_limit)
        if result is not None:
            return result
        # A warm start can sit on the wrong branch of a constant-power load whose
        # source just switched off (I = P/V has negative incremental conductance
        # and a spurious root at V -> inf). Retry cold, then gmin stepping.
        x = np.zeros(self.size)
        result = self._newton(x, A, max_iterations, tolerance, voltage_step_limit)
        if result is not None:
            return result
        for gmin in (1e-2, 1e-3, 1e-4, 1e-5, 1e-6, 1e-7, 1e-8, self.gmin):
            stepped = self._newton(x, self.linear_matrix(gmin), max_iterations * 2, tolerance, voltage_step_limit)
            if stepped is None:
                raise SolverError(f"Newton failed during gmin stepping at gmin={gmin:g}")
            x = stepped[0]
        x, info = stepped
        info.gmin_stepping = True
        return x, info

    def _newton(self, x: np.ndarray, A: np.ndarray, max_iterations: int, tolerance: float,
                voltage_step_limit: float) -> tuple[np.ndarray, SolveInfo] | None:
        node_mask = np.asarray(self.is_node)
        x = x.copy()
        for iteration in range(1, max_iterations + 1):
            F = A @ x
            J = A.copy()
            for element in self.elements:
                element.load(x, F, J)
            residual = float(np.max(np.abs(F))) if F.size else 0.0
            try:
                dx = np.linalg.solve(J, -F)
            except np.linalg.LinAlgError:
                return None
            if not np.all(np.isfinite(dx)):
                return None
            step = float(np.max(np.abs(dx[node_mask]))) if node_mask.any() else 0.0
            if step > voltage_step_limit:
                dx *= voltage_step_limit / step
            x += dx
            scale = 1.0 + float(np.max(np.abs(x)))
            if float(np.max(np.abs(dx))) < tolerance * scale and residual < 1e-6:
                return x, SolveInfo(iteration, residual, True)
        return None

    # ---------------------------------------------------------------- SPICE
    def to_spice(self, x: np.ndarray, title: str = "r3 electrical operating point", nodeset: bool = True,
                 nodeset_scale: float = 1.0) -> tuple[str, dict[str, str]]:
        """Netlist of the network at state ``x`` (elements frozen at their
        per-step parameters). Returns (netlist, spice node -> label).

        ``nodeset`` adds ``.nodeset`` initial guesses from ``x``; they only seed
        ngspice's Newton iteration, the answer is ngspice's own converged
        solution (needed because the stiff smooth-min regulator sources do not
        converge reliably from a zero start)."""
        writer = SpiceWriter(self)
        writer.add(title)
        writer.comment("Generated by r3_sim.electrical; node map in trailing comments")
        a, b, r = self.resistors.arrays()
        for ia, ib, rk, name in zip(a, b, r, self.resistors.names):
            writer.add(f"{writer.name('R')} {writer.node(int(ia))} {writer.node(int(ib))} {rk:.12g} ; {name}")
        for index, is_node in enumerate(self.is_node):
            if is_node:
                writer.add(f"{writer.name('RG')} {writer.node(index)} 0 {1.0 / self.gmin:.12g}")
        for element in self.elements:
            element.spice(writer, x)
        mapping = {writer.node(i): label for i, label in enumerate(self.labels) if self.is_node[i]}
        for node, label in mapping.items():
            writer.comment(f"{node} = {label}")
        if nodeset:
            guesses = [f"V({writer.node(i)})={nodeset_scale * x[i]:.6g}" for i, is_node in enumerate(self.is_node) if is_node]
            for k in range(0, len(guesses), 8):
                writer.add(".nodeset " + " ".join(guesses[k:k + 8]))
        writer.add(".options reltol=1e-7 abstol=1e-12 vntol=1e-9 itl1=500 gmin=1e-15")
        writer.add(".op")
        writer.add(".end")
        return "\n".join(writer.lines) + "\n", mapping


def iter_nodes(circuit: Circuit) -> Iterable[tuple[int, str]]:
    for index, label in enumerate(circuit.labels):
        if circuit.is_node[index]:
            yield index, label


CurrentFunction = Callable[[float], float]

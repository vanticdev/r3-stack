"""Derived component and electrical connectivity graph.

The graph is computed from a :class:`RobotSpec` plus catalog; it is never
edited directly. Nodes are pin terminals:

* ``C:<component>.<pin>`` — a component's internal pin/net (shared by all
  connector pins mapped to it).
* ``H:<harness>.<connector>.<pin>`` — a cable-side connector contact.
* ``S:<harness>.<splice>`` — a splice joint.

Edges are typed: ``wire`` (harness conductor), ``mate`` (mated contacts),
``internal`` (component internal link: resistor, fuse, ...). A *net* is a
connected set of nodes over ``wire`` and ``mate`` edges; ``signal_reach``
additionally crosses ``internal`` links.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable

import numpy as np

from .diagnostics import Diagnostic
from .spec import RobotSpec

if TYPE_CHECKING:  # catalog depends on r3_model, not the other way around at import time
    from r3_catalog.schema import Catalog


@dataclass
class Edge:
    a: str
    b: str
    kind: str  # wire | mate | internal
    ref: str  # wire id, connector pair, or component id
    data: dict[str, Any] = field(default_factory=dict)


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, node: str) -> None:
        self.parent.setdefault(node, node)

    def find(self, node: str) -> str:
        self.add(node)
        root = node
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[node] != root:
            self.parent[node], node = root, self.parent[node]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def rpy_matrix(rpy: Iterable[float]) -> np.ndarray:
    roll, pitch, yaw = rpy
    cr, sr, cp, sp, cy, sy = np.cos(roll), np.sin(roll), np.cos(pitch), np.sin(pitch), np.cos(yaw), np.sin(yaw)
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    return rz @ ry @ rx


@dataclass
class ConnectorEndpoint:
    """A connector in the robot frame (component-mounted or harness free end)."""

    ref: str  # <component|harness>.<connector>
    type: str
    position: np.ndarray
    direction: np.ndarray
    owner_kind: str  # component | harness


class RobotGraph:
    def __init__(self, spec: RobotSpec, catalog: "Catalog") -> None:
        self.spec = spec
        self.catalog = catalog
        self.edges: list[Edge] = []
        self.diagnostics: list[Diagnostic] = []
        self.nodes: set[str] = set()
        self._nets = _UnionFind()
        self._reach = _UnionFind()
        self.endpoints: dict[str, ConnectorEndpoint] = {}
        self._build()

    # ------------------------------------------------------------------ poses
    def component_pose(self, component_id: str) -> tuple[np.ndarray, np.ndarray]:
        """World (robot-frame) position and rotation of a component frame.
        Poses are authored in the robot frame regardless of ``mount``; mount
        expresses the physical attachment, not a relative transform."""
        pose = self.spec.components[component_id].pose
        return np.asarray(pose.xyz, dtype=float), rpy_matrix(pose.rpy)

    def to_robot(self, component_id: str, local: Iterable[float]) -> np.ndarray:
        position, rotation = self.component_pose(component_id)
        return position + rotation @ np.asarray(list(local), dtype=float)

    # ------------------------------------------------------------------ build
    def _node(self, node: str) -> str:
        self.nodes.add(node)
        self._nets.add(node)
        self._reach.add(node)
        return node

    def _edge(self, a: str, b: str, kind: str, ref: str, **data: Any) -> None:
        self._node(a)
        self._node(b)
        self.edges.append(Edge(a, b, kind, ref, data))
        self._reach.union(a, b)
        if kind != "internal":
            self._nets.union(a, b)

    def _connector_pins(self, ref: str) -> tuple[str, dict[str, str]] | None:
        """Return (connector type, pin number -> node) for a component or harness connector."""
        owner, _, connector = ref.partition(".")
        if owner in self.spec.components:
            type_id = self.spec.components[owner].type
            if type_id not in self.catalog.components:
                return None  # reported once as component_unsourced / unknown_component_type
            ctype = self.catalog.component(type_id)
            inst = ctype.connectors.get(connector)
            if inst is None:
                return None
            return inst.type, {num: f"C:{owner}.{pin}" for num, pin in inst.pins.items()}
        for harness in self.spec.electrical.harnesses:
            if harness.id == owner and connector in harness.connectors:
                hconn = harness.connectors[connector]
                count = self.catalog.connector(hconn.type).pin_count
                return hconn.type, {str(n): f"H:{owner}.{connector}.{n}" for n in range(1, count + 1)}
        return None

    def _mate(self, a: str, b: str, context: str) -> None:
        left, right = self._connector_pins(a), self._connector_pins(b)
        for side, ref in ((left, a), (right, b)):
            owner = ref.partition(".")[0]
            if side is None and owner in self.spec.components and self.spec.components[owner].type not in self.catalog.components:
                continue
            if side is None:
                self.diagnostics.append(Diagnostic.error(
                    "unknown_connector", f"{context}: connector {ref!r} does not exist", subjects=[ref]))
        if left is None or right is None:
            return
        (ltype, lpins), (rtype, rpins) = left, right
        lt, rt = self.catalog.connector(ltype), self.catalog.connector(rtype)
        if rtype not in lt.mates_with:
            self.diagnostics.append(Diagnostic.error(
                "connector_mate_mismatch", f"{a} ({ltype}) cannot mate with {b} ({rtype})",
                subjects=[a, b], data={"left_type": ltype, "right_type": rtype, "left_mates_with": lt.mates_with}))
            return
        for number, node in lpins.items():
            if number in rpins:
                self._edge(node, rpins[number], "mate", f"{a}|{b}", contact_resistance=lt.contact_resistance,
                           current_rating=min(lt.current_rating_per_pin, rt.current_rating_per_pin), pin=number)

    def _build(self) -> None:
        spec, catalog = self.spec, self.catalog
        for cid, comp in spec.components.items():
            if not comp.type:
                self.diagnostics.append(Diagnostic.error(
                    "component_unsourced", f"component {cid!r} is declared by requirements (source) but no part is pinned yet",
                    subjects=[cid], data={"slot": comp.source.group or cid if comp.source else cid},
                    hint=f"Run `r3 source {cid}` and choose a candidate (`--choose N`), or give it a catalog `type`."))
                continue
            try:
                ctype = catalog.component(comp.type)
            except KeyError as error:
                self.diagnostics.append(Diagnostic.error("unknown_component_type", str(error), subjects=[cid]))
                continue
            for pin in ctype.pins:
                self._node(f"C:{cid}.{pin}")
            for link in ctype.internal_links:
                self._edge(f"C:{cid}.{link.a}", f"C:{cid}.{link.b}", "internal", cid, link_kind=link.kind, value=link.value)
            for name, inst in ctype.connectors.items():
                for pin in inst.pins.values():
                    if pin not in ctype.pins:
                        self.diagnostics.append(Diagnostic.error(
                            "catalog_pin_missing", f"{comp.type}.{name} maps to undefined pin {pin!r}", subjects=[cid]))
                position, rotation = self.component_pose(cid)
                self.endpoints[f"{cid}.{name}"] = ConnectorEndpoint(
                    f"{cid}.{name}", inst.type, position + rotation @ np.asarray(inst.position),
                    rotation @ np.asarray(inst.direction), "component")

        for harness in spec.electrical.harnesses:
            for name, hconn in harness.connectors.items():
                try:
                    count = catalog.connector(hconn.type).pin_count
                except KeyError as error:
                    self.diagnostics.append(Diagnostic.error("unknown_connector_type", str(error), subjects=[harness.id]))
                    continue
                for n in range(1, count + 1):
                    self._node(f"H:{harness.id}.{name}.{n}")
            for splice in harness.splices:
                self._node(f"S:{harness.id}.{splice}")
            for wire in harness.wires:
                ends = []
                for ref in (wire.from_, wire.to):
                    if ref in harness.splices:
                        ends.append(f"S:{harness.id}.{ref}")
                    else:
                        connector, _, pin = ref.partition(".")
                        if connector not in harness.connectors:
                            self.diagnostics.append(Diagnostic.error(
                                "unknown_wire_endpoint", f"wire {wire.id} endpoint {ref!r} is not a connector pin or splice of {harness.id}",
                                subjects=[harness.id, wire.id]))
                            ends.append(None)
                        else:
                            ends.append(f"H:{harness.id}.{ref}")
                if None not in ends:
                    self._edge(ends[0], ends[1], "wire", wire.id, harness=harness.id, awg=wire.awg)
            for name, hconn in harness.connectors.items():
                if hconn.mates:
                    self._mate(f"{harness.id}.{name}", hconn.mates, f"harness {harness.id}")

        for mate in spec.electrical.direct_mates:
            self._mate(mate.a, mate.b, "direct mate")

    # ------------------------------------------------------------------ queries
    def net_of(self, node: str) -> str:
        return self._nets.find(node)

    def nets(self) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for node in sorted(self.nodes):
            groups.setdefault(self._nets.find(node), []).append(node)
        return groups

    def connected(self, a: str, b: str, through_internal: bool = False) -> bool:
        finder = self._reach if through_internal else self._nets
        return finder.find(self._pin_node(a)) == finder.find(self._pin_node(b))

    def _pin_node(self, ref: str) -> str:
        """Accept ``C:...`` nodes or ``<component>.<connector>.<pin>`` / ``<component>.<pin>`` refs."""
        if ref[:2] in ("C:", "H:", "S:"):
            return ref
        parts = ref.split(".")
        if len(parts) == 3 and parts[0] in self.spec.components:
            resolved = self._connector_pins(f"{parts[0]}.{parts[1]}")
            if resolved and parts[2] in resolved[1]:
                return resolved[1][parts[2]]
        if len(parts) == 2 and parts[0] in self.spec.components:
            return f"C:{parts[0]}.{parts[1]}"
        raise KeyError(f"cannot resolve pin reference {ref!r}")

    def component_pins_on_net(self, node_or_ref: str) -> list[str]:
        root = self._nets.find(self._pin_node(node_or_ref))
        return [n for n in sorted(self.nodes) if n.startswith("C:") and self._nets.find(n) == root]

    def to_json(self) -> dict[str, Any]:
        return {
            "nets": {root: members for root, members in self.nets().items()},
            "edges": [{"a": e.a, "b": e.b, "kind": e.kind, "ref": e.ref, **e.data} for e in self.edges],
            "endpoints": {k: {"type": v.type, "position": v.position.tolist(), "direction": v.direction.tolist(),
                              "owner_kind": v.owner_kind} for k, v in self.endpoints.items()},
            "diagnostics": [d.model_dump(mode="json") for d in self.diagnostics],
        }

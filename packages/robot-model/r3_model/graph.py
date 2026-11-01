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
    from r3_catalog.schema import Catalog, ConnectionSite, ConnectorInstance
    from .pulled import PulledModel


#: Internal-link kinds that carry a logic signal from ``a`` to ``b`` (signal continuity, like a wire).
SIGNAL_LINK_KINDS = frozenset({"series_resistor", "switch", "wire", "level_shifter", "failsafe_arbiter"})
#: Signal links that are active elements: they re-drive ``b`` and conduct no DC between the pins. A
#: ``failsafe_arbiter`` passes ``a`` to ``b`` only while its heartbeat toggles (neutral pulses otherwise).
ACTIVE_SIGNAL_LINKS = frozenset({"level_shifter", "failsafe_arbiter"})


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
    def __init__(self, spec: RobotSpec, catalog: "Catalog", *,
                 native: "PulledModel | None" = None) -> None:
        self.spec = spec
        self.catalog = catalog
        self.native_parts = native.parts if native is not None else None
        self._native_poses = native.poses if native is not None else {}
        self.edges: list[Edge] = []
        self.diagnostics: list[Diagnostic] = []
        self.nodes: set[str] = set()
        self._nets = _UnionFind()
        self._reach = _UnionFind()
        self.endpoints: dict[str, ConnectorEndpoint] = {}
        self.mating_sites: dict[str, ConnectionSite] = {}  # harness ref -> selected site
        self.occupied_sites: dict[str, dict[str, str]] = {}  # component connector -> site id -> occupant
        self._site_options: dict[str, list[ConnectionSite]] = {}
        self._build()

    # ------------------------------------------------------------------ poses
    def component_pose(self, component_id: str) -> tuple[np.ndarray, np.ndarray]:
        """World (robot-frame) position and rotation of a component frame.
        Poses are authored in the robot frame regardless of ``mount``; mount
        expresses the physical attachment, not a relative transform."""
        if component_id in self._native_poses:
            pose = self._native_poses[component_id]
            return pose[:3, 3].copy(), pose[:3, :3].copy()
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

    def connector_instance(self, ref: str) -> "ConnectorInstance":
        """Project connector metadata onto its selected, unchanged native frame.

        Without CAD this remains unqualified catalog research data. A validated
        native model never falls back to that placement when a key is absent.
        """
        owner, _, name = ref.partition(".")
        component = self.spec.components[owner]
        inst = self.catalog.component(component.type).connectors[name]
        if self.native_parts is None:
            return inst
        from .pulled import PullError
        part = self.native_parts.get(owner)
        selector = component.onshape.mate_connectors.get(name, name)
        frames = part.mate_connectors if part is not None else {}
        if selector not in frames:
            raise PullError("cad_native_connector_missing",
                f"{ref}: select an existing native mate connector with onshape.mate_connectors",
                {"component": owner, "connector": name, "mate_connector": selector,
                 "available_connectors": sorted(frames)})
        frame = frames[selector]
        return inst.model_copy(update={"position": frame[:3, 3].tolist(),
                                      "direction": frame[:3, 2].tolist(), "x": frame[:3, 0].tolist()})

    def connection_sites(self, ref: str) -> list["ConnectionSite"]:
        """Lazily derive component-local options for this immutable graph build."""
        if ref not in self._site_options:
            inst = self.connector_instance(ref)
            self._site_options[ref] = self.catalog.connection_sites(inst)
        return self._site_options[ref]

    def _occupy(self, ref: str, site: "ConnectionSite", occupant: str, *, apply: bool = True) -> bool:
        occupied = self.occupied_sites.get(ref, {})
        if occupied.get(site.id) == occupant:
            return True  # reciprocal declaration of the same mate
        conflicts = {sid: who for sid, who in occupied.items() if sid == site.id or sid in site.blocks}
        if conflicts:
            code = "connection_site_occupied" if site.id in conflicts else "connection_site_blocked"
            self.diagnostics.append(Diagnostic.error(
                code, f"{occupant}: {ref} site {site.id} conflicts with {conflicts}",
                subjects=[occupant, ref, *conflicts.values()], data={"site": site.id, "blocked_by": conflicts}))
            return False
        if apply:
            self.occupied_sites.setdefault(ref, {})[site.id] = occupant
        return True

    def _mate(self, a: str, b: str, context: str, at: str | None = None) -> None:
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
        def geometry_known(ref: str) -> bool:
            return self.native_parts is None or ref in self.endpoints

        def options(ref: str) -> list["ConnectionSite"]:
            if not geometry_known(ref):
                # Contact numbering/type/occupancy is still useful offline.
                # Its unqualified placement must never enter mating_sites.
                owner, _, name = ref.partition(".")
                inst = self.catalog.component(self.spec.components[owner].type).connectors[name]
                return self.catalog.connection_sites(inst)
            return self.connection_sites(ref)
        (ltype, lpins), (rtype, rpins) = left, right
        lt, rt = self.catalog.connector(ltype), self.catalog.connector(rtype)
        if at is not None:
            from r3_catalog.sites import SiteError, select_site

            owner, _, name = b.partition(".")
            if owner not in self.spec.components:
                self.diagnostics.append(Diagnostic.error(
                    "connection_site_incompatible_type", "at requires a component header", subjects=[a, b]))
                return
            inst = (self.connector_instance(b) if geometry_known(b) else
                    self.catalog.component(self.spec.components[owner].type).connectors[name])
            try:
                site = select_site(self.catalog, inst, ltype, at, options(b))
            except SiteError as error:
                self.diagnostics.append(Diagnostic.error(error.code, str(error), subjects=[a, b], data={"at": at}))
                return
            if not self._occupy(b, site, a):
                return
            if geometry_known(b):
                self.mating_sites[a] = site
            rpins = {n: rpins[target] for n, target in site.pin_map.items() if target in rpins}
        elif rtype not in lt.mates_with:
            compatible = b.partition(".")[0] in self.spec.components and ltype in options(b)[0].compatible_types
            if not compatible:
                self.diagnostics.append(Diagnostic.error(
                    "connector_mate_mismatch", f"{a} ({ltype}) cannot mate with {b} ({rtype})",
                    subjects=[a, b], data={"left_type": ltype, "right_type": rtype, "left_mates_with": lt.mates_with}))
                return
        if at is None:
            reservations = [(ref, options(ref)[0], occupant) for ref, occupant in ((a, b), (b, a))
                            if ref.partition(".")[0] in self.spec.components]
            if not all(self._occupy(ref, site, occupant, apply=False) for ref, site, occupant in reservations):
                return
            for ref, site, occupant in reservations:
                self._occupy(ref, site, occupant)
                if geometry_known(ref):
                    self.mating_sites[occupant] = site
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
                from .pulled import PullError
                try:
                    inst = self.connector_instance(f"{cid}.{name}")
                except PullError as error:
                    self.diagnostics.append(Diagnostic.error(error.code, str(error), subjects=[cid, f"{cid}.{name}"], data=error.details))
                    continue
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
                    self._mate(f"{harness.id}.{name}", hconn.mates, f"harness {harness.id}", hconn.at)

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

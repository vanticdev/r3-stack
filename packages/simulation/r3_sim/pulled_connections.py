"""CAD mates establish topology; separately sourced physics establishes strength."""
from __future__ import annotations

from r3_model.connections import Connection, ConnectionModel, compile_connections
from r3_model.diagnostics import Diagnostic
from r3_model.spec_files import validate_pulled_spec as load_pulled


def pulled_connections(project) -> ConnectionModel:
    pulled = load_pulled(project.root)
    # Capacity equations remain in the existing shared model. Their dimensions
    # and contact evidence are taken from the pulled geometry projection.
    authored = compile_connections(project)
    model = ConnectionModel(diagnostics=list(authored.diagnostics), estimated=authored.estimated)
    # Resting is deliberately not a CAD mate: contact alone supplies no
    # retention. Keep its monitor so an unfastened body can detach/fall.
    model.connections.extend(c for c in authored.connections
                             if c.kind == "resting" and c.a in pulled.parts and c.b in pulled.parts)
    seen = {c.id for c in model.connections}
    native_groups = {f.id for f in project.spec.mechanical.fasteners if f.mate_connector is not None}
    # A CAD mate tree is not the physical bolt load path. Retain every sourced
    # fastener-to-stack connection instead of dropping edges absent from that tree.
    native_edges = {name: set() for name in pulled.parts}
    for mate in pulled.mates:
        native_edges[mate.a[0]].add(mate.b[0])
        native_edges[mate.b[0]].add(mate.a[0])
    components = {}
    for start in native_edges:
        if start in components:
            continue
        queue = [start]; components[start] = start
        for parent in queue:
            for child in native_edges[parent]:
                if child not in components:
                    components[child] = start; queue.append(child)
    retained = {name: [] for name in pulled.parts}
    for c in authored.connections:
        if c.group not in native_groups:
            continue
        if c.a not in components or c.b not in components or components[c.a] != components[c.b]:
            model.diagnostics.append(Diagnostic.error("cad_fastener_attachment_missing",
                f"{c.id}: fastener stack members must be assembled with native mates before qualification",
                subjects=[c.id, c.a, c.b]))
            continue
        if c.id not in seen:
            model.connections.append(c); seen.add(c.id)
        # Unknown or zero strength cannot justify any other mate's retention.
        if c.capacity_known and c.axial > 0 and c.shear > 0:
            retained[c.a].append((c.b, c.id)); retained[c.b].append((c.a, c.id))

    def retention_path(a: str, b: str) -> list[str] | None:
        paths = {a: []}; queue = [a]
        for parent in queue:
            for child, connection in retained[parent]:
                if child in paths:
                    continue
                paths[child] = [*paths[parent], connection]
                if child == b:
                    return paths[child]
                queue.append(child)
        return None

    for mate in pulled.mates:
        a, b = mate.a[0], mate.b[0]
        frame = pulled.poses[a] @ pulled.parts[a].mate_connectors[mate.a[1]]
        candidates = [c for c in authored.connections if {c.a, c.b} == {a, b}]
        if candidates:
            for c in candidates:
                if c.id in seen:
                    continue
                seen.add(c.id)
                c.point = frame[:3, 3].tolist()
                c.axis = frame[:3, 2].tolist()
                model.connections.append(c)
        else:
            path = retention_path(a, b) if mate.kind == "fastened" else None
            if path:
                model.diagnostics.append(Diagnostic.info("cad_mate_retention_path",
                    f"{mate.id}: retention is monitored through the native fastener stack",
                    subjects=[mate.id, a, b], data={"connections": path}))
                continue
            kind = {"fastened": "fastener", "revolute": "revolute_fit", "ball": "ball_joint"}.get(mate.kind, mate.kind)
            model.connections.append(Connection(id=mate.id, a=a, b=b, kind=kind,
                point=frame[:3, 3].tolist(), axis=frame[:3, 2].tolist(),
                axial=0, shear=0, torque=0, bending=0, capacity_known=False,
                missing_data=["sourced mate retention and load capacities"]))
            model.diagnostics.append(Diagnostic.error("joint_capacity_unknown",
                f"{mate.id}: CAD mate specifies motion, not retention strength; provide sourced connection capacities",
                subjects=[mate.id, a, b]))
    return model

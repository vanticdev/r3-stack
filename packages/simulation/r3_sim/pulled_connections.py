"""CAD mates establish topology; separately sourced physics establishes strength."""
from __future__ import annotations

from r3_model.connections import Connection, ConnectionModel, compile_connections
from r3_model.diagnostics import Diagnostic
from r3_model.pulled import load_pulled


def pulled_connections(project) -> ConnectionModel:
    pulled = load_pulled(project.root)
    # Capacity equations remain in the existing shared model. Their dimensions
    # and contact evidence are taken from the pulled geometry projection.
    authored = compile_connections(project)
    model = ConnectionModel(diagnostics=list(authored.diagnostics), estimated=authored.estimated)
    for mate in pulled.mates:
        a, b = mate.a[0], mate.b[0]
        frame = pulled.poses[a] @ pulled.parts[a].mate_connectors[mate.a[1]]
        candidates = [c for c in authored.connections if {c.a, c.b} == {a, b}]
        if candidates:
            for c in candidates:
                c.point = frame[:3, 3].tolist()
                c.axis = frame[:3, 2].tolist()
                model.connections.append(c)
        else:
            kind = {"fastened": "fastener", "revolute": "revolute_fit", "ball": "ball_joint"}.get(mate.kind, mate.kind)
            model.connections.append(Connection(id=mate.id, a=a, b=b, kind=kind,
                point=frame[:3, 3].tolist(), axis=frame[:3, 2].tolist(),
                axial=0, shear=0, torque=0, bending=0, capacity_known=False,
                missing_data=["sourced mate retention and load capacities"]))
            model.diagnostics.append(Diagnostic.error("joint_capacity_unknown",
                f"{mate.id}: CAD mate specifies motion, not retention strength; provide sourced connection capacities",
                subjects=[mate.id, a, b]))
    return model

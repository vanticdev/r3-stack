"""An offset joint requires its declared intermediate solid to reach both seats."""
from types import SimpleNamespace

import manifold3d as m3d
import numpy as np
import pytest

from r3_model.diagnostics import StageResult
from r3_orchestrator.assembly_stage import check_seat_contact


@pytest.mark.parametrize("declared,height,x,expected", [
    (True, .005, 0., False),
    (True, .004, 0., True),
    (True, .005, .03, True),
    (False, .005, 0., True),
])
def test_only_a_contacting_declared_spacer_explains_seat_offset(declared, height, x, expected):
    def box(z, depth, shift=0.):
        return m3d.Manifold.cube([.02, .02, depth]).translate([shift-.01, -.01, z])
    exact = {"base": box(0., .002), "cap": box(.007, .002), "spacer": box(.002, height, x)}
    frames = {}
    for name, z in [("base.top", .002), ("cap.bottom", .007)]:
        frame = np.eye(4)
        frame[2, 3] = z
        frames[name] = frame
    interfaces = {name: SimpleNamespace(obj=name.split(".")[0], kind="face_mount", holes=[])
                  for name in frames}
    assembly = SimpleNamespace(
        joins={"clamp": {"a": "base.top", "b": "cap.bottom", "valid": True,
                          "kind": "fasten", "offset": .005}},
        interfaces=interfaces, interface_world=lambda ref: frames[ref],
    )
    join = SimpleNamespace(join_id="clamp", fasteners=SimpleNamespace(clamps=["spacer"] if declared else []))
    project = SimpleNamespace(spec=SimpleNamespace(assembly=SimpleNamespace(joins=[join])))
    result = StageResult(stage="assembly")
    check_seat_contact(project, assembly, {}, result, exact)
    assert any(d.code == "seat_not_in_contact" for d in result.diagnostics) is expected

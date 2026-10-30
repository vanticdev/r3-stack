"""Offline blind cuts preserve the native extrude depth and seating surface."""
import numpy as np
import pytest

from r3_onshape.fit import plane_contacts
from r3_onshape.geometry import Loop, PartPlan, Plane, Prism, operation_manifold
from r3_onshape.recompose import part_from_plan


@pytest.mark.parametrize("base", ["TOP", "FRONT", "RIGHT"])
@pytest.mark.parametrize("direction", [-1., 1.])
def test_blind_cut_ends_at_declared_depth(base, direction):
    plane = Plane(base, .02)
    cut = Prism(plane, [Loop.rectangle([-.005, -.005], [.005, .005])], .004, direction, "remove")
    vertices = np.asarray(operation_manifold(cut).to_mesh().vert_properties)[:, :3]
    depth = (vertices - plane.origin) @ (plane.normal * direction)
    assert depth.max() == pytest.approx(.004, abs=2e-9)
    assert depth.min() < 0  # the entrance remains open despite coplanar booleans


def test_insert_contacts_the_bottom_of_a_blind_pocket():
    body = PartPlan("socket", "fabricated", "Aluminum", 2700., ops=[
        Prism(Plane("TOP"), [Loop.rectangle([-.01, -.01], [.01, .01])], .02),
        Prism(Plane("TOP", .02), [Loop.rectangle([-.005, -.005], [.005, .005])], .005, -1., "remove"),
    ])
    insert = PartPlan("insert", "fabricated", "Aluminum", 2700., ops=[
        Prism(Plane("TOP", .015), [Loop.rectangle([-.004, -.004], [.004, .004])], .005),
    ])
    socket, plug = part_from_plan("socket", body), part_from_plan("insert", insert)
    contacts = plane_contacts(socket, plug)
    assert sum(c.area for c in contacts) == pytest.approx(.008 ** 2, rel=1e-5)
    assert (socket.manifold() ^ plug.manifold()).volume() < 1e-12

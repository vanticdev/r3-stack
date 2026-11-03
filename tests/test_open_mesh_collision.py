"""Open display meshes keep conservative collision geometry instead of none."""
import numpy as np
import trimesh

from r3_wiring.mujoco_cables import PartGeom, convex_pieces, environment_only, is_closed_solid


def test_closed_mesh_still_splits_exactly():
    box = trimesh.creation.box((1.0, 2.0, 3.0))
    pieces, hull_only = convex_pieces(np.asarray(box.vertices), np.asarray(box.faces))
    assert len(pieces) == 1 and hull_only == 0


def test_open_mesh_keeps_its_hull_and_reports_it():
    box = trimesh.creation.box((1.0, 2.0, 3.0))
    open_faces = np.asarray(box.faces)[:-2]  # an open seam, as display meshes can have
    pieces, hull_only = convex_pieces(np.asarray(box.vertices), open_faces)
    assert hull_only == 1 and len(pieces) == 1
    assert np.allclose(pieces[0].min(axis=0), box.vertices.min(axis=0))
    assert np.allclose(pieces[0].max(axis=0), box.vertices.max(axis=0))


def test_mesh_without_volume_has_no_hull_to_invent():
    vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float)
    pieces, hull_only = convex_pieces(vertices, np.array([[0, 1, 2]]))
    assert pieces == [] and hull_only == 0


def test_closed_solid_is_told_apart_from_an_open_mesh():
    box = trimesh.creation.box((1.0, 2.0, 3.0))
    assert is_closed_solid(np.asarray(box.vertices), np.asarray(box.faces))
    assert not is_closed_solid(np.asarray(box.vertices), np.asarray(box.faces)[:-2])


def test_environment_only_contact_reaches_the_ground_but_not_other_parts():
    import mujoco

    xml = """<mujoco><worldbody>
      <geom name="ground" type="plane" size="0 0 0.05" contype="0" conaffinity="3"/>
      <body pos="0 0 0.09"><freejoint/><geom name="a" type="box" size=".1 .1 .1" contype="2" conaffinity="0"/></body>
      <body pos="0.05 0 0.09"><freejoint/><geom name="b" type="box" size=".1 .1 .1" contype="2" conaffinity="0"/></body>
    </worldbody></mujoco>"""
    model = mujoco.MjModel.from_xml_string(xml)
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    pairs = {tuple(sorted((int(contact.geom1), int(contact.geom2)))) for contact in data.contact[:data.ncon]}
    ground, a, b = (mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, name) for name in ("ground", "a", "b"))
    assert pairs == {tuple(sorted((ground, a))), tuple(sorted((ground, b)))}


def test_fallback_hull_touches_only_the_environment():
    part = PartGeom('body', 'body', False, 'mesh', [np.zeros((4, 3))], hull_only=1, open_mesh=True)
    assert environment_only(part)


def test_real_geometry_keeps_part_contact_even_on_an_open_mesh_part():
    pieces = [np.zeros((4, 3)), np.ones((4, 3))]
    assert not environment_only(PartGeom('body', 'body', False, 'mesh', pieces, hull_only=0, open_mesh=True))
    assert not environment_only(PartGeom('body', 'body', False, 'mesh', pieces, hull_only=1, open_mesh=True))
    assert not environment_only(PartGeom('body', 'body', False, 'mesh', pieces[:1], hull_only=0, open_mesh=True))


def test_closed_part_is_never_environment_only():
    assert not environment_only(PartGeom('body', 'body', False, 'mesh', [np.zeros((4, 3))], hull_only=1, open_mesh=False))

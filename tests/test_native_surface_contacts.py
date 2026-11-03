"""Unchanged native surfaces preserve empty cavities and retention filtering."""
import xml.etree.ElementTree as ET

import mujoco
import numpy as np
import trimesh
import pytest

from r3_model.connections import Connection, ConnectionModel
from r3_sim.joints import JointMonitor
from r3_sim.surface_collisions import emit_surface_file, surface_plane_distances, surface_supports


def test_rigid_native_channel_keeps_its_cavity_and_supports_a_falling_probe(tmp_path):
    tree = ET.Element('mujoco')
    ET.SubElement(tree, 'option', timestep='.00025', gravity='0 0 -9.81')
    world = ET.SubElement(tree, 'worldbody')
    origin = np.array([.03, -.02, .015])
    channel = ET.SubElement(world, 'body', name='channel', pos=' '.join(map(str, origin)))
    ET.SubElement(channel, 'inertial', pos='0 0 0', mass='1', diaginertia='.01 .01 .01')
    probe = ET.SubElement(world, 'body', name='probe', pos='0 0 .06')
    ET.SubElement(probe, 'freejoint')
    ET.SubElement(probe, 'geom', type='sphere', size='.01', mass='.01')
    surfaces = []
    for size, position in [([.12, .12, .01], [0, 0, 0]),
                           ([.01, .12, .08], [-.055, 0, .04]),
                           ([.01, .12, .08], [.055, 0, .04])]:
        mesh = trimesh.creation.box(extents=size)
        mesh.apply_translation(position)
        surfaces.append(mesh)
    native = trimesh.util.concatenate(surfaces)
    original = native.vertices.copy(), native.faces.copy()
    path = tmp_path / 'native.stl'
    native.export(path)
    before = path.read_bytes()
    emit_surface_file(channel, 'channel', path, origin, '.7 .005 .0001')
    expected_vertices = native.vertices.astype(np.float32).astype(float)
    ET.SubElement(world, 'geom', name='reference', type='plane', pos='0 0 -.2',
                  size='1 1 .1', contype='0', conaffinity='0')
    model = mujoco.MjModel.from_xml_string(ET.tostring(tree, encoding='unicode'))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    assert model.nbody == 3 and model.nv == 6  # no deformable DOFs or fabricated bodies
    assert model.nflex == 1 and model.flex_rigid[0]
    assert model.body_mass.sum() == 1.01
    np.testing.assert_allclose(np.unique(data.flexvert_xpos, axis=0),
                               np.unique(expected_vertices, axis=0), rtol=0, atol=1e-15)
    np.testing.assert_array_equal(native.faces, original[1])
    assert surface_supports(model, data, 2) == pytest.approx([expected_vertices[:, 2].min()], abs=1e-12)
    assert surface_plane_distances(model, data, model.geom('reference').id) == pytest.approx(
        [expected_vertices[:, 2].min() + .2], abs=1e-12)
    assert data.ncon == 0, 'A convex hull must not fill the empty channel'
    contacted = False
    for _ in range(4000):
        mujoco.mj_step(model, data)
        contacted |= data.ncon > 0
        assert not any(w.number for w in data.warning)
    assert contacted
    assert .014 <= data.xpos[model.body('probe').id, 2] <= .016
    assert np.linalg.norm(data.qvel) < .01
    assert path.read_bytes() == before


@pytest.mark.parametrize('other_surface', [False, True])
def test_retained_surface_contact_returns_after_the_connection_releases(tmp_path, other_surface):
    tree = ET.Element('mujoco')
    world = ET.SubElement(tree, 'worldbody')
    a = ET.SubElement(world, 'body', name='a')
    ET.SubElement(a, 'freejoint')
    ET.SubElement(a, 'inertial', pos='0 0 0', mass='1', diaginertia='1 1 1')
    b = ET.SubElement(world, 'body', name='b', pos='0 0 .005')
    ET.SubElement(b, 'freejoint')
    ET.SubElement(b, 'geom', type='sphere', size='.01', mass='.01')
    if other_surface:
        b.remove(b.find('geom'))
        ET.SubElement(b, 'inertial', pos='0 0 0', mass='.01', diaginertia='.00001 .00001 .00001')
        sphere = trimesh.creation.icosphere(subdivisions=2, radius=.01)
        sphere.apply_translation([0, 0, .005])
        path = tmp_path / 'probe.stl'
        sphere.export(path)
        emit_surface_file(b, 'b', path, np.array([0, 0, .005]), '.7 .005 .0001')
    plane = trimesh.Trimesh(vertices=[[-.1, -.1, 0], [.1, -.1, 0],
                                    [.1, .1, 0], [-.1, .1, 0]],
                            faces=[[0, 1, 2], [0, 2, 3]], process=False)
    path = tmp_path / 'surface.stl'
    plane.export(path)
    emit_surface_file(a, 'a', path, np.zeros(3), '.7 .005 .0001')
    ET.SubElement(ET.SubElement(tree, 'equality'), 'weld', name='retention', body1='a', body2='b')
    contact = ET.SubElement(tree, 'contact')
    names = [f'joint_exclude:{i}' for i in range(4)]
    for name in names:
        ET.SubElement(contact, 'exclude', name=name, body1='a', body2='b')
    model = mujoco.MjModel.from_xml_string(ET.tostring(tree, encoding='unicode'))
    data = mujoco.MjData(model)
    connection = Connection('retention', 'a', 'b', 'adhesive', [0, 0, 1], [0, 0, 0],
                            100., 100., 100., 100.)
    bindings = [dict(index=0, body1='a', body2='b', equality='retention', rigid_edges=[],
                     contact_groups=[dict(name=names[0], slots=names, body_pair=['a', 'b'],
                                          paths=[['retention']], rigid_pair=None)])]
    monitor = JointMonitor(model, data, ConnectionModel([connection]), bindings, 'a')
    mujoco.mj_forward(model, data)
    assert data.ncon == 0
    monitor.fail(connection, model.equality('retention').id, 101, 'axial', 100, 'synthetic overload')
    mujoco.mj_forward(model, data)
    assert data.ncon > 0, 'Surface contacts must not remain suppressed after retention fails'


@pytest.mark.parametrize('cone', ['pyramidal', 'elliptic'])
def test_native_surface_contact_reports_its_real_support_force(tmp_path, cone):
    from r3_sim.dynamics import RobotDynamics

    tree = ET.Element('mujoco')
    ET.SubElement(tree, 'option', timestep='.00025', cone=cone)
    world = ET.SubElement(tree, 'worldbody')
    ET.SubElement(world, 'geom', name='ground', type='plane', size='1 1 .1')
    body = ET.SubElement(world, 'body', name='roller', pos='0 0 .04')
    ET.SubElement(body, 'freejoint')
    ET.SubElement(body, 'inertial', pos='0 0 0', mass='1', diaginertia='.01 .01 .01')
    path = tmp_path / 'native.stl'
    native = trimesh.creation.box(extents=[.04, .04, .04])
    native.export(path)
    emit_surface_file(body, 'roller', path, np.zeros(3), '.7 .005 .0001')
    model = mujoco.MjModel.from_xml_string(ET.tostring(tree, encoding='unicode'))
    data = mujoco.MjData(model)
    for _ in range(4000):
        mujoco.mj_step(model, data)
        assert not any(w.number for w in data.warning)
    assert data.ncon > 0
    assert any(-1 in c.geom and 0 in c.flex for c in data.contact)
    # Exercise the runtime force accounting against the real native contacts,
    # independently of motor or firmware configuration.
    runtime = RobotDynamics.__new__(RobotDynamics)
    runtime.model, runtime.data = model, data
    runtime.wheel_normal_force = {'roller': 0.0, 'uncontacted': 0.0}
    runtime._wheel_order = ['roller', 'uncontacted']
    runtime._tire_index = np.full(model.ngeom, -1, dtype=np.int64)
    runtime._flex_tire_index = np.array([0], dtype=np.int64)
    runtime._contact_force = np.zeros(6)
    runtime._update_contacts()
    assert runtime.wheel_normal_force['roller'] == pytest.approx(9.81, abs=.01)
    assert runtime.wheel_normal_force['uncontacted'] == 0

"""Native display meshes remain physical bodies even when their seams are open."""
import hashlib
import xml.etree.ElementTree as ET

import mujoco
import numpy as np
import pytest
import trimesh

from r3_model.project import Project
from r3_model.pulled import load_pulled
from r3_onshape.pull import pull
from r3_sim.assembly import build_assembly_model
from test_cad_pulled_consumers import project
from test_cad_pull import native_snapshot
from test_native_mesh_prototype import NativeViewer
from test_native_viewer_capture import URL


def install_open_display_mesh(project, native_snapshot):
    native = NativeViewer(native_snapshot)
    mesh = trimesh.creation.box([.01, .02, .03])
    from native_mass_fixture import uniform_mesh
    native.definition["definition"]["parts"][0]["native_mass_properties"] = uniform_mesh(mesh, 1000.)
    mesh.update_faces(np.arange(len(mesh.faces) - 1))
    native.raw = mesh.export(file_type='obj').encode()
    native.handle = hashlib.sha256(native.raw).hexdigest() + '.obj'
    native.provenance['mesh_artifact'] = native.handle
    result = pull(project, url=URL, version_id='version', transport=native,
                  mesh_source='viewer', tab_id=12)
    assert result['ok'], result
    assert native.exports == 0
    return Project.load(project.root)


def test_open_native_mesh_has_a_visible_estimated_collision_shape(project, native_snapshot):
    current = install_open_display_mesh(project, native_snapshot)
    part = load_pulled(current.root).parts['coupon']
    original = part.mesh.read_bytes()
    assembly = build_assembly_model(current)
    tree = ET.fromstring(assembly.xml)
    body = tree.find("worldbody/body[@name='coupon']")
    colliders = [g for g in body.findall('geom') if g.get('contype') != '0']
    assert colliders, 'A native body must never silently become visual-only'
    assert any(d.code == 'joint_collision_estimated' for d in assembly.diagnostics)
    assert part.mesh.read_bytes() == original
    assert part.geometry_qualification == 'display_mesh'


def test_real_mujoco_ground_supports_the_native_body(project, native_snapshot):
    current = install_open_display_mesh(project, native_snapshot)
    current.spec.robot.mobility = "mobile"  # A free body, not the stationary fixture.
    assembly = build_assembly_model(current)
    model = mujoco.MjModel.from_xml_string(assembly.xml)
    data = mujoco.MjData(model)
    body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, 'coupon')
    mujoco.mj_forward(model, data)
    assert data.xpos[body, 2] >= .015, 'Initial placement must clear the native body below its datum'
    ground = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, 'ground')
    saw_ground = False
    for _ in range(2000):
        mujoco.mj_step(model, data)
        saw_ground |= any(ground in row.geom for row in data.contact[:data.ncon])
    # Sleeping islands need not retain an active contact list after settling.
    assert saw_ground
    assert .014 <= data.xpos[body, 2] <= .016
    assert np.linalg.norm(data.qvel) < .01


@pytest.mark.parametrize('mount', ['auto', 'fixed'])
def test_fixed_fixture_preserves_authored_native_datum(project, native_snapshot, mount):
    current = install_open_display_mesh(project, native_snapshot)
    current.spec.robot.mobility = 'stationary' if mount == 'auto' else 'mobile'
    current.spec.simulation.base_mount = mount
    assembly = build_assembly_model(current)
    model = mujoco.MjModel.from_xml_string(assembly.xml)
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    body = model.body('coupon').id
    # The ordinary 0.5 mm scene offset remains; native below-datum geometry
    # must not silently move a declared fixed mount or its instrumentation.
    assert data.xpos[body, 2] == pytest.approx(.0005, abs=1e-12)
    assert 'joint_fixture_weld' in assembly.xml


def test_invalid_collision_mesh_cannot_silently_supply_no_geometry():
    from r3_wiring.mujoco_cables import convex_pieces
    with pytest.raises(ValueError, match='[Cc]ollision'):
        convex_pieces(np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]]),
                      np.array([[0, 1, 2]]))


def test_rigid_contact_exclusion_survives_alternate_path_then_releases():
    from r3_model.connections import Connection, ConnectionModel
    from r3_sim.joints import JointMonitor
    world = ''.join(f'<body name="{name}"><freejoint/><geom type="box" size=".01 .01 .01" mass=".1"/></body>'
                    for name in 'abcd')
    edges = [('a', 'b', 'ab'), ('b', 'c', 'bc'), ('a', 'd', 'ad'), ('d', 'c', 'dc')]
    equalities = ''.join(f'<weld name="{name}" body1="{a}" body2="{b}"/>' for a, b, name in edges)
    model = mujoco.MjModel.from_xml_string(f'<mujoco><worldbody>{world}</worldbody><equality>{equalities}</equality>'
                                         '<contact><exclude name="joint_exclude:0" body1="a" body2="c"/></contact></mujoco>')
    data = mujoco.MjData(model)
    connections = ConnectionModel([Connection(name, a, b, 'adhesive', [0,0,1], [0,0,0],
                                               100., 100., 100., 100.) for a, b, name in edges])
    bindings = [dict(index=i, body1=a, body2=b, equality=name) for i, (a,b,name) in enumerate(edges)]
    bindings[0].update(rigid_edges=edges, contact_groups=[dict(name='joint_exclude:0', paths=[], rigid_pair=['a','c'])])
    signature = int(model.exclude_signature[0])
    monitor = JointMonitor(model, data, connections, bindings, 'a')
    assert signature in model.exclude_signature
    # One broken attachment leaves another independent retained path.
    monitor.fail(connections.connections[0], model.equality('ab').id, 101, 'axial', 100, 'synthetic overload')
    assert signature in model.exclude_signature
    # Releasing the remaining attachment must restore contact; no permanent
    # rigid-body collision bypass may survive a failed retention path.
    monitor.fail(connections.connections[2], model.equality('ad').id, 101, 'axial', 100, 'synthetic overload')
    assert signature not in model.exclude_signature
    mujoco.mj_forward(model, data)
    pair = {model.body('a').id, model.body('c').id}
    assert any({int(model.geom_bodyid[row.geom[0]]), int(model.geom_bodyid[row.geom[1]])} == pair
               for row in data.contact[:data.ncon])


def test_collision_geometry_does_not_read_the_routing_decimation(project, native_snapshot):
    from r3_wiring.obstacles import build_obstacles
    from r3_wiring.mujoco_cables import part_geoms
    current = install_open_display_mesh(project, native_snapshot)
    obstacles, _ = build_obstacles(current)
    obstacles.obstacles[0].shape = object()  # Routing approximation is not a physics input.
    parts = part_geoms(current, obstacles)
    assert len(parts) == 1
    vertices = np.concatenate(parts[0].pieces)
    np.testing.assert_allclose(vertices.min(axis=0), [-.005, -.01, -.015], atol=1e-12)
    np.testing.assert_allclose(vertices.max(axis=0), [.005, .01, .015], atol=1e-12)


def test_exact_native_mesh_failure_cannot_use_the_display_hull_workaround(project, monkeypatch):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from pulled_fixture import write_pull
    from r3_model.pulled import PullError
    from r3_wiring.obstacles import build_obstacles
    from r3_wiring import mujoco_cables
    write_pull(project.root, {'coupon': BRepPrimAPI_MakeBox(10,20,30).Shape()})
    current = Project.load(project.root)
    obstacles, _ = build_obstacles(current)
    def rejected(*args):
        raise ValueError('synthetic failed exact decomposition')
    monkeypatch.setattr(mujoco_cables, 'convex_pieces', rejected)
    with pytest.raises(PullError) as error:
        mujoco_cables.part_geoms(current, obstacles)
    assert error.value.code == 'cad_collision_geometry_invalid'

"""Cable rest geometry and the persisted conductor playback contract."""
import json

import mujoco
import numpy as np
import pytest

from r3_model.project import Project
from r3_sim.cable_playback import cable_metadata
from r3_sim.vehicle import build_vehicle_model
from r3_wiring.geometry import project_to_polyline
from r3_wiring.mujoco_cables import CableMonitor
from r3_orchestrator.api.projects import decode_recording


@pytest.fixture(scope="module")
def cable_world(tmp_path_factory):
    from r3_wiring.stage import route_and_check
    from test_wiring_support import write_project

    project = Project.load(write_project(tmp_path_factory.mktemp("cable-playback"), moving_end=True))
    routes = route_and_check(project)["routes"]
    # A distinct per-conductor display offset must survive, even though the
    # physical bundle core is shared. This is the connectors-stage contract.
    display = np.asarray(routes["wires"]["W1"]["path"]) + [0, 0.0004, 0]
    file = project.build_dir("connectors") / "connectors.json"
    file.write_text(json.dumps({"wires": {"W1": {"display_path": display.tolist()}}}))
    from r3_wiring.mujoco_cables import core_path

    core = core_path(routes["wires"]["W1"])
    tie = core[len(core) // 2].tolist()
    routes["harnesses"]["lead"]["tie_points"] = [tie]
    routes["harnesses"]["lead"]["supports"] = [
        {"position": tie, "host": "arm", "permitted_motion": "pinned"}]
    vm = build_vehicle_model(project, routes=routes)
    model = mujoco.MjModel.from_xml_string(vm.xml)
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    monitor = CableMonitor(model, data, vm.cables, vm.root)
    return project, routes, vm, model, data, monitor


def test_rest_cables_follow_routes_before_settling(cable_world):
    _, _, vm, _, _, monitor = cable_world
    for bundle in vm.cables.bundles:
        path = np.asarray(vm.cables.conductors[bundle.wires[0]]["core_path"])
        vertices = monitor.positions()[bundle.id]
        # Physical nodes stay on-route; the dense display retains bends between nodes.
        assert max(project_to_polyline(path, q)[0] for q in vertices) < 0.0005
        for span in bundle.spans:
            assert np.max(np.linalg.norm(np.diff(span.vertices, axis=0), axis=1)) <= 0.010001
    assert monitor.pin_error() < 1e-6
    assert any(span.end_body == "arm" and span.end_motion == "pinned"
               for bundle in vm.cables.bundles for span in bundle.spans)
    assert any(span.start_body == "arm" and span.start_motion == "pinned"
               for bundle in vm.cables.bundles for span in bundle.spans)


def test_recorded_wire_mapping_diameter_fanouts_and_motion(cable_world, tmp_path):
    project, routes, vm, model, data, monitor = cable_world
    metadata = cable_metadata(monitor)
    assert set(metadata["wires"]) == set(routes["wires"])
    display = project.read_build_json("connectors/connectors.json")["wires"]["W1"]["display_path"]
    assert metadata["wires"]["W1"]["display_path"] == display
    wire_specs = {w.id: w for h in project.spec.electrical.harnesses for w in h.wires}
    for wire, info in metadata["wires"].items():
        assert info["wire_id"] == wire
        assert info["od"] == pytest.approx(project.catalog.wire(wire_specs[wire].awg).outer_diameter)
        assert info["color"] == wire_specs[wire].color
        assert info["bundle"] == monitor.wire_bundle[wire]
    xml = tmp_path / "vehicle.xml"
    xml.write_text(vm.xml)
    q0 = model.qpos0.copy()
    q1 = q0.copy()
    # A translation and yaw exercise world/robot-frame conversion. Geometry must
    # move exactly with the car, including both pin-to-core fan-outs.
    rotation=np.array([[np.cos(.4),-np.sin(.4),0],[np.sin(.4),np.cos(.4),0],[0,0,1]])
    yaw=np.array([np.cos(.2),0,0,np.sin(.2)])
    for j in range(model.njnt):
        if model.jnt_type[j] != mujoco.mjtJoint.mjJNT_FREE:
            continue
        address=model.jnt_qposadr[j]
        q1[address:address+3]=rotation@q0[address:address+3]+[1,-.5,.2]
        mujoco.mju_mulQuat(q1[address+3:address+7],yaw,q0[address+3:address+7])
    npz = tmp_path / "recording.npz"
    np.savez_compressed(npz, t=[0., 1.], snapshot_t=[0., 1.], snapshot_qpos=[q0, q1],
                        cable_metadata=np.asarray(json.dumps(metadata)))
    recorded = np.load(npz, allow_pickle=False)
    assert json.loads(str(recorded["cable_metadata"])) == metadata
    frames = decode_recording(str(npz), npz.stat().st_mtime_ns, 30, True)["frames"]
    assert set(frames["cables"]) == set(routes["wires"])
    rot = np.array([[np.cos(.4), -np.sin(.4), 0], [np.sin(.4), np.cos(.4), 0], [0, 0, 1]])
    for wire, cable in frames["cables"].items():
        path = np.asarray(metadata["wires"][wire]["display_path"])
        assert cable["od"] == metadata["wires"][wire]["od"]
        assert cable["color"] == metadata["wires"][wire]["color"]
        # No route shape, endpoints or conductor offsets vanish at qpos0.
        np.testing.assert_allclose(cable["points"][0], path + data.xpos[monitor.root_body], atol=2e-6)
        np.testing.assert_allclose(cable["points"][1], path @ rot.T + q1[:3], atol=2e-6)
    assert frames["rest"][vm.root] == [0, 0, 0, 1, 0, 0, 0]


def test_pin_fanout_follows_its_joint_body(cable_world, tmp_path):
    project, _, vm, model, _, monitor = cable_world
    data = mujoco.MjData(model)
    metadata = cable_metadata(monitor)
    qpos = np.tile(model.qpos0, (2, 1))
    joint = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "steer")
    qpos[1, model.jnt_qposadr[joint]] = 0.3
    # The connector is its own free body now. Pose its real fixed attachment
    # along with the arm for this kinematics-only recording fixture.
    mujoco.mj_forward(model,data)
    arm=model.body("arm").id;old_position=data.xpos[arm].copy();old_rotation=data.xmat[arm].reshape(3,3).copy()
    data.qpos[:]=qpos[1];mujoco.mj_kinematics(model,data)
    rotation=data.xmat[arm].reshape(3,3)@old_rotation.T
    translation=data.xpos[arm]-rotation@old_position
    quat=np.zeros(4);mujoco.mju_mat2Quat(quat,rotation.ravel())
    for cid,component in project.spec.components.items():
        if component.mount != "arm":
            continue
        body=model.body(cid).id;j=model.body_jntadr[body]
        assert model.jnt_type[j]==mujoco.mjtJoint.mjJNT_FREE
        address=model.jnt_qposadr[j]
        qpos[1,address:address+3]=rotation@qpos[0,address:address+3]+translation
        mujoco.mju_mulQuat(qpos[1,address+3:address+7],quat,qpos[0,address+3:address+7])
    (tmp_path / "vehicle.xml").write_text(vm.xml)
    file = tmp_path / "recording.npz"
    np.savez_compressed(file, t=[0., 1.], snapshot_t=[0., 1.], snapshot_qpos=qpos,
                        cable_metadata=np.asarray(json.dumps(metadata)))
    frames = decode_recording(str(file), file.stat().st_mtime_ns, 30, True)["frames"]
    data.qpos[:] = qpos[1]
    mujoco.mj_kinematics(model, data)
    arm = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "arm")
    for wire, info in metadata["wires"].items():
        pin = np.asarray(info["display_path"][-1])
        expected = data.xpos[arm] + data.xmat[arm].reshape(3, 3) @ (pin - vm.body_origin["arm"])
        np.testing.assert_allclose(frames["cables"][wire]["points"][1][-1], expected, atol=2e-6)

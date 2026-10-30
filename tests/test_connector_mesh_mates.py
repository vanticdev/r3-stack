"""Geometry-derived mating diagnostics, independent of catalog placement claims."""

import numpy as np
import pytest
import trimesh

from r3_catalog import load_catalog
from r3_connectors.frames import Frame
from r3_connectors.mesh_mates import check_pair, detect, check_solder


def box(size, centre):
    mesh = trimesh.creation.box(extents=np.array(size) * 0.001)
    mesh.apply_translation(np.array(centre) * 0.001)
    return mesh


@pytest.fixture
def setup():
    catalog = load_catalog()
    geometry = catalog.connector("header-1x3-2.54").geometry
    socket = geometry.model_copy(deep=True)
    pins = [box([0.64, 0.64, 6], [x, 0, 3]) for x in (-2.54, 0, 2.54)]
    housing = box([10, 3, 7], [0, 0, -3.5])
    for x in (-2.54, 0, 2.54):
        housing = trimesh.boolean.difference(
            [housing, box([0.9, 0.9, 8], [x, 0, -3])], engine="manifold"
        )
    # Search envelope must cover both the physical pins and socket depth.
    geometry.depth = socket.depth = 0.007
    geometry.reach = socket.reach = 0.006
    geometry.envelope = socket.envelope = [0.010, 0.003, 0.007]
    return geometry, socket, pins, [housing]


def run_pair(
    setup, offset=(0, 0, 0), pins=None, extra=None, declared=None, required=0.0055
):
    g, s, original, housing = setup
    fa = Frame.identity()
    fb = Frame.from_axes(offset, [1, 0, 0], [0, 0, -1])
    part = original if pins is None else pins
    partner = [m.copy() for m in housing]
    for mesh in partner:
        mesh.apply_transform(fb.matrix())
    if extra is not None:
        part = [*part, extra]
    da, db = detect(part, fa, g), detect(partner, fb, s)
    geometries = {"header": g, "socket": s}
    if declared is not None:
        geometries["wrong"] = declared
    return check_pair(
        "part.J",
        "lead.A",
        fa,
        fb,
        da,
        db,
        g,
        s,
        ("wrong" if declared else "header", "socket"),
        geometries,
        required,
    )


def codes(result):
    return {d.code for d in result[1]}


def test_physical_posts_enter_socket(setup):
    record, diagnostics = run_pair(setup)
    assert not diagnostics
    assert record["sides"]["part.J"]["count"] == 3
    assert record["min_insertion"] == pytest.approx(0.006, abs=1e-7)
    assert record["max_lateral_error"] < 1e-7


def test_empty_component_at_declared_header(setup):
    assert "connector_empty" in codes(run_pair(setup, pins=[]))


def test_wrong_pitch_type(setup):
    wrong = load_catalog().connector("jst-gh-4-header").geometry
    assert "connector_type_mismatch" in codes(run_pair(setup, declared=wrong))


def test_pin_offset_by_one_pitch(setup):
    assert "connector_pin_unmated" in codes(run_pair(setup, offset=(0.00254, 0, 0)))


def test_insertion_short(setup):
    assert "connector_insertion_short" in codes(run_pair(setup, offset=(0, 0, 0.003)))


def test_plug_pushed_through_board(setup):
    board = box([12, 8, 1.6], [0, 0, -0.8])
    assert "connector_body_interference" in codes(
        run_pair(setup, offset=(0, 0, -0.003), extra=board)
    )


def test_solder_checks_only_occupied_pads(setup):
    g, _, _, _ = setup
    g.depth = 0.001
    board = box([10, 3, 1], [0, 0, -0.5])
    for x in (-2.54, 0, 2.54):
        drill = trimesh.creation.cylinder(radius=0.0005, height=0.003, sections=64)
        drill.apply_translation([x * 0.001, 0, -0.0005])
        board = trimesh.boolean.difference([board, drill], engine="manifold")
    pads = detect([board], Frame.identity(), g)
    routes = {
        "wires": {
            "one": {
                "path": [[0, 0, 0.01], [0, 0, 0.0005]],
                "ends": [None, {"connector": "lead.B", "pin": "2"}],
            }
        }
    }
    record, errors = check_solder("part.J", "lead.B", Frame.identity(), pads, routes)
    assert not errors
    assert record["status"] == "passed"
    routes["wires"]["one"]["path"][-1][0] = 0.00127
    assert "connector_pin_unmated" in codes(
        check_solder("part.J", "lead.B", Frame.identity(), pads, routes)
    )


@pytest.mark.parametrize(
    "code",
    [
        "connector_empty",
        "connector_type_mismatch",
        "connector_pin_unmated",
        "connector_insertion_short",
        "connector_body_interference",
    ],
)
def test_each_mesh_error_blocks_release(tmp_path, code):
    from api_helpers import copy_rc_car
    from r3_model.diagnostics import Diagnostic, StageResult
    from r3_model.project import Project
    from r3_orchestrator.release import evaluate

    root = copy_rc_car(tmp_path)
    project = Project.load(root)
    result = StageResult(stage="connectors", inputs=project.fingerprint())
    result.add(Diagnostic.error(code, "Measured physical mating failure"))
    result.write(project.build_dir("connectors") / "result.json")
    release = evaluate(project)
    item = next(i for i in release["items"] if i["id"] == "stage.connectors")
    assert item["status"] == "failed"
    assert "stage.connectors" in release["blocking"]
    assert not release["fabrication_ready"]


def test_round_pin_diameter_is_part_of_classification(setup):
    geometry, socket, _, _ = setup
    geometry.pin_shape = "bullet"
    geometry.pin_size = 0.002
    pins = []
    for x in (-0.00254, 0, 0.00254):
        pin = trimesh.creation.cylinder(radius=0.00032, height=0.006, sections=64)
        pin.apply_translation([x, 0, 0.003])
        pins.append(pin)
    assert "connector_type_mismatch" in codes(run_pair(setup, pins=pins))


def test_post_must_fit_physical_socket_opening(setup):
    g, s, pins, _ = setup
    housing = box([10, 3, 7], [0, 0, -3.5])
    for x in (-2.54, 0, 2.54):
        housing = trimesh.boolean.difference(
            [housing, box([0.4, 0.4, 8], [x, 0, -3])], engine="manifold"
        )
    assert "connector_type_mismatch" in codes(run_pair((g, s, pins, [housing])))

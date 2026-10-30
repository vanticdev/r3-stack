"""Derived mating options, partial contact maps, occupancy, frames and read APIs."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from r3_catalog import load_catalog
from r3_catalog.schema import ConnectorInstance, ConnectorPin
from r3_catalog.sites import SiteError, select_site
from r3_connectors.connection_points import connection_points
from r3_connectors.frames import Frame
from r3_connectors.pins import component_connector_frame, mating_site_frame, pin_end
from r3_connectors.stage import ConnectorStage
from r3_model.diagnostics import StageResult
from r3_model.graph import RobotGraph, rpy_matrix
from r3_model.project import Project
from r3_model.spec import RobotSpec


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def header(catalog, type_id="header-2x20-2.54"):
    geometry = catalog.connector(type_id).geometry
    return ConnectorInstance(type=type_id, pins={n: f"PIN{n}" for n in geometry.pins},
                             position=[.02, .03, .04], x=[0, 1, 0], direction=[1, 0, 0])


def make_project(tmp_path, catalog, connectors=None, *, direct_mates=None):
    inst = header(catalog)
    ctype = catalog.component("raspberry-pi-5-8gb").model_copy(deep=True)
    ctype.id = "test-header-board"
    ctype.connectors = {"J": inst}
    ctype.pins = {signal: next(iter(ctype.pins.values())).model_copy() for signal in inst.pins.values()}
    ctype.model = None
    catalog = catalog.model_copy(deep=True)
    catalog.components[ctype.id] = ctype
    raw = {"robot": {"name": "Sites", "version": "0.1.0"}, "components": {"board": {"type": ctype.id}},
           "electrical": {"harnesses": [{"id": "lead", "connectors": connectors or {
               "B": {"type": "dupont-1x3", "mates": "board.J", "at": "8"}}, "wires": []}],
               "direct_mates": direct_mates or []}}
    (tmp_path / "robot.yaml").write_text(yaml.safe_dump(raw))
    project = Project(tmp_path, RobotSpec.model_validate(raw))
    project._catalog = catalog
    return project


@pytest.mark.parametrize("length", range(1, 21))
def test_two_by_twenty_placement_counts(catalog, length):
    sites = catalog.connection_sites(header(catalog))
    for rows, expected in [(1, 2 * (21 - length)), (2, 21 - length)]:
        options = [s for s in sites if f"dupont-{rows}x{length}" in s.compatible_types]
        assert len(options) == expected
        assert all(len(s.pins) == rows * length for s in options)
    assert sites[0].id == "full" and "socket-2x20-2.54" in sites[0].compatible_types


def test_synthetic_one_by_eight_header(catalog):
    catalog = catalog.model_copy(deep=True)
    base = catalog.connector("header-1x3-2.54").model_copy(deep=True)
    base.id, base.pin_count, base.family = "synthetic-1x8", 8, "Synthetic unshrouded header"
    # Deliberately no naming/family convention; pin geometry defines the grid.
    base.geometry.pins = {str(i + 1): ConnectorPin(position=[(i - 3.5) * .00254, 0, 0]) for i in range(8)}
    base.mates_with = []
    catalog.connectors[base.id] = base
    sites = catalog.connection_sites(header(catalog, base.id))
    assert len(sites) == sum(range(1, 9)) + 2 * 6
    for length in range(1, 9):
        assert sum(f"dupont-1x{length}" in s.compatible_types for s in sites) == 9 - length
    assert "dupont-1x8" in sites[0].compatible_types
    assert not any("dupont-2x" in s.id for s in sites)
    base.geometry.pins.pop("8")
    for pin in base.geometry.pins.values():
        pin.position[0] += .00127  # retain the geometry's centred mating-face frame
    assert len(catalog.connection_sites(header(catalog, base.id))) == sum(range(1, 8)) + 2 * 5


def test_blocking_uses_bodies_as_well_as_pins(catalog):
    sites = {s.id: s for s in catalog.connection_sites(header(catalog))}
    first = sites["dupont-1x1@1"]
    assert "dupont-1x3@1" in first.blocks  # shared pin
    assert "dupont-1x1@3" in first.blocks  # end walls overlap despite disjoint pins
    assert "dupont-1x1@2" not in first.blocks  # neighbouring row fits (2.50 < 2.54 mm)
    assert "dupont-1x1@5" not in first.blocks
    assert all(s.id in sites["full"].blocks for s in sites.values() if s.id != "full")
    for site in sites.values():
        assert site.id not in site.blocks
        assert all(site.id in sites[other].blocks for other in site.blocks)


@pytest.mark.parametrize("type_id,at,code", [
    ("dupont-1x3", "38", "connection_site_does_not_fit"),
    ("dupont-2x2", "2", "connection_site_does_not_fit"),
    ("dupont-1x1", "41", "connection_site_pin_outside_header"),
    ("xt60-female", "8", "connection_site_incompatible_type"),
])
def test_partial_mate_errors(tmp_path, catalog, type_id, at, code):
    project = make_project(tmp_path, catalog, {"B": {"type": type_id, "mates": "board.J", "at": at}})
    assert [d.code for d in project.graph.diagnostics] == [code]
    assert project.graph.occupied_sites == {}


@pytest.mark.parametrize("second,code", [("1", "connection_site_occupied"), ("3", "connection_site_blocked")])
def test_occupied_or_blocked_site_is_rejected(tmp_path, catalog, second, code):
    project = make_project(tmp_path, catalog, {
        "A": {"type": "dupont-1x1", "mates": "board.J", "at": "1"},
        "B": {"type": "dupont-1x1", "mates": "board.J", "at": second}})
    assert [d.code for d in project.graph.diagnostics] == [code]
    assert project.graph.occupied_sites == {"board.J": {"dupont-1x1@1": "lead.A"}}


@pytest.mark.parametrize("type_id,at,covered", [("dupont-1x3", "8", ["8", "10", "12"]),
                                              ("dupont-2x2", "7", ["7", "8", "9", "10"])])
def test_contacts_wire_exits_graph_and_stage_use_the_same_site(tmp_path, catalog, type_id, at, covered):
    project = make_project(tmp_path, catalog, {"B": {"type": type_id, "mates": "board.J", "at": at}})
    assert project.graph.diagnostics == []
    conn = project.spec.electrical.harnesses[0].connectors["B"]
    board = component_connector_frame(project, "board.J")
    frame = mating_site_frame(project, "lead.B", conn).mated()
    header_geometry = project.catalog.connector("header-2x20-2.54").geometry
    for number, target in enumerate(covered, 1):
        end = pin_end(project.catalog, type_id, frame, str(number), "lead.B")
        contact = board.point(header_geometry.pins[target].position)
        assert np.linalg.norm(end.contact - contact) < 1e-6
        assert np.linalg.norm(end.position - (contact + .014 * board.z)) < 1e-6
        assert project.graph.connected(f"H:lead.B.{number}", f"C:board.PIN{target}")
    stage = ConnectorStage(project, StageResult(stage="connectors"))
    stage.component_connectors()
    stage.harness_connectors()
    stage.mated_pairs()
    # This coordinate-only fixture has no sourced contact models. The mesh
    # verifier must say so; a valid partial site is not physical fit evidence.
    assert len(stage.result.diagnostics) == 2
    assert all(d.code == "connector_unverifiable" and d.severity.value == "warning"
               for d in stage.result.diagnostics)
    assert stage.data["mated_pairs"][0]["max_pin_lateral_error"] < 1e-6
    np.testing.assert_allclose(stage._frame("lead.B").matrix(), frame.matrix(), atol=1e-12)


def test_moving_component_moves_sites_and_wire_ends(tmp_path, catalog):
    project = make_project(tmp_path, catalog)
    conn = project.spec.electrical.harnesses[0].connectors["B"]
    frame = mating_site_frame(project, "lead.B", conn).mated()
    end = pin_end(project.catalog, conn.type, frame, "2", "lead.B")
    before = connection_points(project)["connection_points"]
    pose = project.spec.components["board"].pose
    pose.xyz, pose.rpy = [.1, -.2, .3], [.3, -.4, .7]
    project._graph = RobotGraph(project.spec, project.catalog)
    rotation, offset = rpy_matrix(pose.rpy), np.asarray(pose.xyz)
    after = connection_points(project)["connection_points"]
    for old, new in zip(before, after):
        np.testing.assert_allclose(new["origin"], offset + rotation @ old["origin"], atol=1e-12)
        np.testing.assert_allclose(new["exit_direction"], rotation @ old["exit_direction"], atol=1e-12)
    moved = pin_end(project.catalog, conn.type, mating_site_frame(project, "lead.B", conn).mated(), "2", "lead.B")
    np.testing.assert_allclose(moved.position, offset + rotation @ end.position, atol=1e-6)
    np.testing.assert_allclose(moved.contact, offset + rotation @ end.contact, atol=1e-6)


def test_inventory_occupancy_and_access(tmp_path, catalog):
    project = make_project(tmp_path, catalog)
    points = {p["site"]: p for p in connection_points(project)["connection_points"]}
    used = points["dupont-1x3@8"]
    assert used["status"] == "occupied" and used["occupied_by"] == "lead.B"
    assert list(used["pins"]) == ["8", "10", "12"]
    assert used["pins"]["8"]["signal"] == "PIN8" and used["pins"]["8"]["net"]
    assert points["dupont-1x1@8"]["status"] == "blocked"
    assert points["dupont-1x1@8"]["blocked_by"][0]["by"] == "lead.B"
    assert points["dupont-1x1@1"]["status"] == "free"
    assert all(p["free_access_reach"] is None for p in points.values())  # only owning part exists
    # Add a known collision envelope whose near face is 50 mm along this port's +x exit.
    directory = project.build_dir("cad")
    p = np.asarray(used["origin"])
    obstacle = project.spec.components["board"].model_copy(deep=True)
    project.spec.components["obstacle"] = obstacle
    (directory / "cad_model.json").write_text(json.dumps({"parts": {
        "obstacle": {"bbox_min": (p + [.05, -.01, -.01]).tolist(), "bbox_max": (p + [.06, .01, .01]).tolist()}}}))
    project._graph = RobotGraph(project.spec, project.catalog)
    used = next(p for p in connection_points(project)["connection_points"] if p["id"] == used["id"])
    assert used["free_access_reach"] == pytest.approx(.05, abs=1e-9)
    assert used["access_blocker"] == "obstacle" and used["access_geometry"] == "cad_aabb"


def test_rc_car_stage_writes_every_site(tmp_path):
    import shutil
    from r3_orchestrator.stages import run_stage

    source = Path(__file__).resolve().parents[1] / "examples/robots/rc-car"
    shutil.copy(source / "robot.yaml", tmp_path / "robot.yaml")
    shutil.copytree(source / "pcb", tmp_path / "pcb")
    project = Project.load(tmp_path)
    result = run_stage(project, "connectors", render=False)
    assert not [d for d in result.diagnostics if d.code == "stage_crashed"]
    points = json.loads((tmp_path / "build/connectors/connection_points.json").read_text())["connection_points"]
    expected = {(cid, name, site.id) for cid, comp in project.spec.components.items()
                for name, inst in project.catalog.component(comp.type).connectors.items()
                for site in project.catalog.connection_sites(inst)}
    assert {(p["component"], p["connector"], p["site"]) for p in points if p["component"]} == expected
    # Direct HAT socket occupies the whole Pi header, including all smaller options.
    pi = [p for p in points if p["component"] == "pi" and p["connector"] == "J8"]
    assert next(p for p in pi if p["site"] == "full")["occupied_by"] == "hat.J_GPIO"
    assert all(p["status"] != "free" for p in pi)
    assert any(a.path == "connectors/connection_points.json" for a in result.artifacts)


def test_mcp_filters_and_read_only_registration(tmp_path, catalog, monkeypatch):
    from r3_orchestrator import api, mcp_server as m

    project = make_project(tmp_path, catalog)
    monkeypatch.setattr(api, "load_project", lambda _: project)
    before = {str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")}
    response = m.r3_connection_points(str(tmp_path), "board", True, "dupont-1x3")
    assert response["ok"] and response["connection_points"]
    assert all(p["component"] == "board" and p["status"] == "free" and "dupont-1x3" in p["compatible_types"]
               for p in response["connection_points"])
    assert m.r3_connection_points(str(tmp_path), "missing")["connection_points"] == []
    assert m.r3_connection_points(str(tmp_path), fits_type="missing")["connection_points"] == []
    assert {str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")} == before
    tools = {t.name: t for t in asyncio.run(m.mcp.list_tools())}
    assert tools["r3_connection_points"].annotations.read_only_hint is True
    assert m.TOOL_TIERS["r3_connection_points"] == "read"


def test_http_filters(tmp_path, catalog, monkeypatch):
    from fastapi.testclient import TestClient
    from r3_orchestrator import api
    from r3_orchestrator.api.server import create_app

    root = tmp_path / "sites"
    root.mkdir()
    project = make_project(root, catalog)
    monkeypatch.setattr(api, "load_project", lambda _: project)
    with TestClient(create_app([tmp_path])) as client:
        projects = client.get("/api/projects").json()["projects"]
        pid = projects[0]["id"]
        response = client.get(f"/api/projects/{pid}/connection-points",
                              params={"component": "board", "free_only": True, "fits_type": "dupont-1x1"})
        assert response.status_code == 200, response.text
        points = response.json()["connection_points"]
        assert points and all(p["status"] == "free" and p["component"] == "board" for p in points)


def test_router_resolves_partial_site_and_each_conductor(tmp_path, catalog):
    from r3_wiring.obstacles import build_obstacles
    from r3_wiring.router import HarnessRouter, RouterOptions

    project = make_project(tmp_path, catalog)
    obstacles, _ = build_obstacles(project)
    router = HarnessRouter(project, obstacles, RouterOptions(voxel=.01, plan=False))
    node = router._resolve_nodes()["lead"]["B"]
    board = component_connector_frame(project, "board.J")
    geometry = project.catalog.connector("header-2x20-2.54").geometry
    for number, target in enumerate(["8", "10", "12"], 1):
        end = router._pin_end(node, f"B.{number}", {str(number): .001})
        expected = board.point(geometry.pins[target].position) + .014 * board.z
        np.testing.assert_allclose(end.position, expected, atol=1e-6)


def test_plain_mates_preserve_full_frame_and_pin_numbers(tmp_path, catalog):
    project = make_project(tmp_path, catalog, {"B": {"type": "socket-2x20-2.54", "mates": "board.J"}})
    conn = project.spec.electrical.harnesses[0].connectors["B"]
    assert project.graph.diagnostics == []
    assert project.graph.mating_sites["lead.B"].id == "full"
    np.testing.assert_array_equal(mating_site_frame(project, "lead.B", conn).matrix(),
                                  component_connector_frame(project, "board.J").matrix())
    assert all(project.graph.connected(f"H:lead.B.{i}", f"C:board.PIN{i}") for i in range(1, 41))


def test_failed_direct_mate_reserves_neither_side(tmp_path, catalog):
    project = make_project(tmp_path, catalog)
    project.spec.components["socket"] = project.spec.components["board"].model_copy(deep=True)
    ctype = project.catalog.component("test-header-board").model_copy(deep=True)
    ctype.id = "test-socket-board"
    ctype.connectors["J"].type = "socket-2x20-2.54"
    project.catalog.components[ctype.id] = ctype
    project.spec.components["socket"].type = ctype.id
    from r3_model.spec import DirectMate
    project.spec.electrical.direct_mates.append(DirectMate(a="socket.J", b="board.J"))
    assert [d.code for d in project.graph.diagnostics] == ["connection_site_blocked"]
    assert "socket.J" not in project.graph.occupied_sites
    assert project.graph.occupied_sites["board.J"] == {"dupont-1x3@8": "lead.B"}


def test_floating_pair_inventory_uses_authored_and_solved_centres(tmp_path, catalog):
    project = make_project(tmp_path, catalog, {"A": {"type": "xt60-male", "mates": "lead.B", "position": [.1, .2, .3]},
                                               "B": {"type": "xt60-female", "mates": "lead.A"}})
    points = [p for p in connection_points(project)["connection_points"] if p["component"] is None]
    assert len(points) == 1 and points[0]["pair"] == ["lead.A", "lead.B"]
    assert points[0]["origin"] == [.1, .2, .3]
    directory = project.build_dir("wiring")
    (directory / "routing_detail.json").write_text(json.dumps({"nodes": {"lead.A": {
        "frame": {"origin": [.4, .5, .6], "x": [0, 1, 0], "z": [1, 0, 0]}}}}))
    point = next(p for p in connection_points(project)["connection_points"] if p["component"] is None)
    assert point["origin"] == [.4, .5, .6] and point["exit_direction"] == [1, 0, 0]


@pytest.mark.parametrize("direction", [[0, 0, 1], [1, 0, 0], [.95, .31, 0]])
def test_default_clocking_matches_connector_frame(catalog, direction):
    from r3_connectors.pins import instance_frame

    inst = header(catalog)
    inst.x, inst.direction = None, direction
    expected = instance_frame(inst)
    actual = Frame.from_placement(catalog.connection_sites(inst)[0].frame)
    np.testing.assert_allclose(actual.matrix(), expected.matrix(), atol=1e-12)


def test_partial_housing_cannot_select_fixed_port_pins(catalog):
    inst = ConnectorInstance(type="xt60-male", pins={"1": "P", "2": "N"})
    with pytest.raises(SiteError) as error:
        select_site(catalog, inst, "dupont-1x1", "1")
    assert error.value.code == "connection_site_incompatible_type"


def test_new_header_type_accepts_full_housing_without_catalog_id_rules(tmp_path, catalog):
    project = make_project(tmp_path, catalog, {"B": {"type": "dupont-1x3", "mates": "board.J"}})
    ctype = project.catalog.connector("header-1x3-2.54").model_copy(deep=True)
    ctype.id, ctype.mates_with = "unfamiliar-header", []
    project.catalog.connectors[ctype.id] = ctype
    board = project.catalog.component("test-header-board")
    board.connectors["J"].type = ctype.id
    board.connectors["J"].pins = {str(n): f"PIN{n}" for n in range(1, 4)}
    assert ctype.id not in project.catalog.connector("dupont-1x3").mates_with
    assert project.graph.diagnostics == []
    assert project.graph.mating_sites["lead.B"].id == "full"
    assert all(project.graph.connected(f"H:lead.B.{i}", f"C:board.PIN{i}") for i in range(1, 4))

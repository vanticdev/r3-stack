"""An unresolved conductor must produce a failed route report, not a crash."""
from r3_model.project import Project
from r3_wiring.manufacturing import release_status
from r3_wiring.obstacles import build_obstacles
from r3_wiring.planner import plan_routing
from r3_wiring.router import HarnessRouter, RouterOptions
from test_wiring_support import write_project


def test_partial_harness_reports_the_missing_conductor_and_keeps_other_routes(tmp_path):
    project = Project.load(write_project(tmp_path, block=False))
    obstacles, _ = build_obstacles(project)
    router = HarnessRouter(project, obstacles, RouterOptions(plan=False))
    routing = router.route()
    missing = next(iter(routing.wires))
    retained = set(routing.wires) - {missing}
    assert retained
    del routing.wires[missing]
    report = plan_routing(project, router, routing, obstacles=obstacles, max_iterations=2)
    finding = next(d for d in report.diagnostics if d.code == 'wire_route_failed' and missing in d.subjects)
    assert finding.severity.value == 'error'
    assert retained <= routing.wires.keys()
    released = release_status(project, report.diagnostics)
    assert released['status'] == 'unreleased'
    assert not released['wires'][missing]['valid']


def test_unavailable_terminal_geometry_remains_a_failed_harness(tmp_path, monkeypatch):
    project = Project.load(write_project(tmp_path, block=False))
    obstacles, _ = build_obstacles(project)
    router = HarnessRouter(project, obstacles, RouterOptions(plan=True))
    nodes = router._resolve_nodes()
    nodes['lead']['A'].lead_end = None
    monkeypatch.setattr(router, '_resolve_nodes', lambda: nodes)
    routing = router.route()
    expected = {wire.id for wire in project.spec.electrical.harnesses[0].wires}
    assert not routing.wires
    errors = [d for d in routing.diagnostics + routing.planning.diagnostics if d.severity.value == 'error']
    assert any(d.code == 'wire_route_failed' and 'terminal lead geometry' in d.message for d in errors)
    assert expected <= {subject for d in errors for subject in d.subjects}
    assert release_status(project, errors)['status'] == 'unreleased'


def test_failed_search_placeholder_never_becomes_a_physical_cable(tmp_path):
    from r3_wiring.export import routes_json
    from r3_wiring.mujoco_cables import plan_cables
    project = Project.load(write_project(tmp_path, block=False))
    obstacles, _ = build_obstacles(project)
    routing = HarnessRouter(project, obstacles, RouterOptions(plan=False)).route()
    missing = next(iter(routing.wires))
    routing.wires[missing].failed = True
    data = routes_json(routing, [])
    assert data['wires'][missing]['failed'] is True
    plan = plan_cables(project, data, obstacles)
    assert missing not in plan.wire_bundle
    assert set(routing.wires) - {missing} <= plan.wire_bundle.keys()
    finding = next(d for d in plan.diagnostics if d.code == 'wire_route_failed' and missing in d.subjects)
    assert finding.severity.value == 'error'
    assert release_status(project, plan.diagnostics)['status'] == 'unreleased'
    from r3_wiring.cable_sim import CableSimulator
    simulator = CableSimulator(project, data, obstacles, plan=plan)
    simulator.step(.01, {'pos': [0, 0, 0]}, {})
    assert missing not in simulator.positions()
    assert set(routing.wires) - {missing} <= simulator.positions().keys()

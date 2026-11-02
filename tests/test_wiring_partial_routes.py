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

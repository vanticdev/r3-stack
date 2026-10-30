"""The topology router and constrained planner must agree on physical bundle bends."""
import pytest
import yaml
from r3_model.project import Project
from r3_wiring.obstacles import build_obstacles
from r3_wiring.router import HarnessRouter, RouterOptions
from test_wiring_support import write_project


@pytest.mark.parametrize('jacket, radius, lead', [('none', .018, .0305), ('heat-shrink', .032, .048)])
def test_loose_and_jacketed_pairs_use_the_same_rule_in_both_routing_phases(tmp_path, jacket, radius, lead):
    root = write_project(tmp_path, block=False)
    file = root / 'robot.yaml'
    spec = yaml.safe_load(file.read_text())
    harness = spec['electrical']['harnesses'][0]
    harness['wires'] = harness['wires'][:2]
    for wire in harness['wires']:
        wire['awg'] = 12
    harness['jacket'] = jacket
    file.write_text(yaml.safe_dump(spec, sort_keys=False))
    project = Project.load(root)
    obstacles, _ = build_obstacles(project)
    routing = HarnessRouter(project, obstacles, RouterOptions(plan=False)).route()
    assert routing.nodes['lead']['A'].lead_length == pytest.approx(lead)
    assert routing.harnesses['lead'].segments
    for segment in routing.harnesses['lead'].segments:
        assert segment['min_bend_radius'] == pytest.approx(radius)

"""Authored in-line connector exits preserve mating and real strain-relief runs."""
import numpy as np
import pytest
from pydantic import ValidationError

from r3_model.project import Project
from r3_model.spec import HarnessConnector
from r3_wiring.obstacles import build_obstacles
from r3_wiring.router import HarnessRouter, RouterOptions
from test_wiring_support import write_branch_project


def test_floating_pair_obeys_exit_axis_and_has_straight_leads(tmp_path):
    project = Project.load(write_branch_project(tmp_path))
    half = project.spec.electrical.harnesses[0].connectors['B']
    half.position = [.08, 0, .035]
    half.direction = [-1, 0, 0]
    obstacles, _ = build_obstacles(project)
    router = HarnessRouter(project, obstacles, RouterOptions(plan=False))
    nodes = router._resolve_nodes()
    pairs = router._place_floating(nodes)
    assert len(pairs) == 1
    assert nodes['pig']['B'].direction == pytest.approx([-1, 0, 0])
    assert nodes['ysplit']['P1'].direction == pytest.approx([1, 0, 0])
    for node in (nodes['pig']['B'], nodes['ysplit']['P1']):
        assert node.lead_length >= router.opt.voxel
        assert node.lead_end == pytest.approx(node.exit + node.direction * node.lead_length)
    assert np.linalg.norm(nodes['pig']['B'].face - nodes['ysplit']['P1'].face) < 1e-12


def test_zero_connector_axis_is_rejected():
    with pytest.raises(ValidationError, match='non-zero'):
        HarnessConnector(type='xt60-male', direction=[0, 0, 0])

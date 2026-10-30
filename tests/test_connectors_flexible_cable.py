"""A flexible FFC's seated ends do not imply that its intervening route is verified."""
from pathlib import Path

import numpy as np

from r3_connectors.pins import component_connector_frame
from r3_model.diagnostics import StageResult
from r3_model.project import Project
from r3_orchestrator.model_stage import ModelChecker


def test_free_cable_end_follows_mate_and_keeps_route_estimated():
    project = Project.load(Path(__file__).parent / 'fixtures' / 'printbot')
    cable = project.catalog.component(project.spec.components['csi_cable'].type)
    retained = component_connector_frame(project, 'csi_cable.A')
    cable.connectors['B'].flexible_end = True
    installed = component_connector_frame(project, 'csi_cable.B')
    socket = component_connector_frame(project, 'pi.CAM0')
    np.testing.assert_allclose(installed.matrix(), socket.mated().matrix(), atol=1e-12)
    np.testing.assert_array_equal(component_connector_frame(project, 'csi_cable.A').matrix(), retained.matrix())
    result = StageResult(stage='model')
    ModelChecker(project, result).check_cable_reach()
    assert not result.errors
    assert any(d.code == 'flat_cable_route_estimated' for d in result.diagnostics)
    cable.behavior.params['length'] = .001
    short = StageResult(stage='model')
    ModelChecker(project, short).check_cable_reach()
    assert any(d.code == 'cable_too_short' for d in short.errors)

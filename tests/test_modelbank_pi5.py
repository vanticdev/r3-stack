"""Pi outline, asymmetric clearance envelope and mounting coordinates stay distinct."""
import numpy as np
import pytest

from r3_catalog import load_catalog
from r3_catalog.modelbank import ModelBank
from r3_catalog.modelbank.fetch import validate
from r3_orchestrator.geometry import make_solid
from r3_model.graph import rpy_matrix


def test_pi_outline_envelope_and_mount_pattern():
    catalog = load_catalog()
    bank = ModelBank().get('raspberry-pi-5-4gb')
    for identifier in [bank.catalog_component, *bank.catalog_aliases]:
        mech = catalog.component(identifier).mechanical
        assert mech.board_outline == pytest.approx([.085, .056])
        assert mech.size == pytest.approx([.0885, .057630352, .018976])
        holes = np.asarray(mech.mount_holes)
        assert np.ptp(holes[:, :2], axis=0) == pytest.approx([.058, .049])
        assert holes[:, 0].tolist() == pytest.approx([-.039, .019, -.039, .019])
        assert mech.envelope_offset == pytest.approx([.0013, -.000815176, -.00145])
    assert bank.validation.tolerance_m == .0015
    assert validate(bank, bank.validation.bbox_m).status == 'passed'
    # A real discrepancy still fails; correcting dimensions did not weaken validation.
    assert validate(bank, [.0885, .060, .018976]).status == 'mismatch'


def test_clearance_envelope_keeps_board_frame_under_rotation():
    from r3_onshape.plan import _envelope_solids
    from r3_wiring.obstacles import _catalog_shape
    from r3_model.project import Project
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / 'examples/robots/rc-car'
    project = Project.load(root)
    mech = project.catalog.component(project.spec.components['pi'].type).mechanical
    xyz, rpy = [.1, .2, .3], [.2, -.3, 1.2]
    solid = make_solid('pi', 'custom', mech.size, 'z', xyz, rpy, mech.envelope_offset)
    local = (solid.vertices - xyz) @ rpy_matrix(rpy)
    assert local.min(axis=0) == pytest.approx([-.04295, -.029630352, -.00145])
    assert local.max(axis=0) == pytest.approx([.04555, .028, .017526])
    fallback = _envelope_solids(mech, {})[0]
    assert fallback.position == pytest.approx([.0013, -.000815176, .008038])
    obstacle = _catalog_shape(project, 'pi')
    pos, rot = project.graph.component_pose('pi')
    assert obstacle.center == pytest.approx(pos + rot @ np.asarray(fallback.position))

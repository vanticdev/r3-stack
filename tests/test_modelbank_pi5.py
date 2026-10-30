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

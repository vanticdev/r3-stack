"""AMASS XT90H V1.2 drawing dimensions used by the power-lead fan-out."""
import numpy as np
import pytest
from r3_catalog import load_catalog


def test_xt90h_contact_pitch_and_capped_lengths_match_manufacturer_drawing():
    # https://www.electrokit.com/upload/product/41020/41020964/XT90H.pdf
    # 9.90 is the narrow cross-section dimension, not the contact pitch.
    catalog = load_catalog()
    for name, length in [('xt90h-male', .0307), ('xt90h-female', .0297)]:
        connector = catalog.connector(name)
        geometry = connector.geometry
        pins = [np.asarray(geometry.pins[n].position) for n in ['1', '2']]
        assert np.linalg.norm(pins[1] - pins[0]) == pytest.approx(.011)
        assert connector.body_size[2] == pytest.approx(length)
        assert geometry.depth == pytest.approx(length)
        # The unmapped mating recess remains an estimated conservative proxy.
        assert geometry.model.provenance.status == 'estimated'

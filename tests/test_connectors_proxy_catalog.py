"""Proxy generation accepts catalog data unrelated to a particular robot."""

import numpy as np
import yaml

from r3_connectors.derive import derive_proxy, load_proxy_specs


def test_catalog_defined_proxy_contacts_and_extent(tmp_path):
    source = tmp_path / "proxies.yaml"
    source.write_text(yaml.safe_dump({"proxies": {"fixture-contact-block": {
        "pins": [["P", -3.0, 2.0], ["Q", 7.0, -1.0]],
        "builder": "housing", "params": {"width": 18, "thickness": 9, "length": 21, "cavity": 2},
        "depth": 21, "reach": 0, "envelope": [18, 9, 21],
        "tip": 1, "pin_shape": "blade", "pin_size": .8,
        "source": "Synthetic robot-independent connector fixture",
    }}}))
    specs = load_proxy_specs(source)
    geometry = derive_proxy("fixture-contact-block", specs["fixture-contact-block"], None)
    np.testing.assert_allclose(geometry["pins"]["P"]["wire_entry"], [-3, 2, -21])
    np.testing.assert_allclose(geometry["pins"]["Q"]["position"], [7, -1, 0])
    np.testing.assert_allclose(geometry["envelope"], [18, 9, 21])
    assert geometry["model"]["provenance"]["source"] == specs["fixture-contact-block"].source

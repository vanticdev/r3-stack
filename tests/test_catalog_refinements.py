"""Physical vendor refinements apply before RAM variants inherit the base."""

import pytest
import yaml

from r3_catalog import _component_entries, load_catalog


def test_all_pi_variants_inherit_measured_connectors():
    catalog = load_catalog()
    baseline = catalog.component("raspberry-pi-5-8gb")
    for identifier in ("raspberry-pi-5-4gb", "raspberry-pi-5-16gb"):
        part = catalog.component(identifier)
        assert part.connectors == baseline.connectors
        assert part.interfaces["J8"] == baseline.interfaces["J8"]
    assert len(baseline.connectors) == 16
    assert len(baseline.connectors["J8"].pins) == 40
    assert baseline.pins["GPIO2"].net == "GPIO2/SDA1"


def test_refinement_precedes_extends(tmp_path):
    path = tmp_path / "parts.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "components": [
                    {"id": "base", "pins": {"A": 1}},
                    {"id": "variant", "extends": "base"},
                ],
                "refinements": {"base": {"pins": {"B": 2}}},
            }
        )
    )
    entries = dict((entry["id"], entry) for _, entry in _component_entries([path], {}))
    assert entries["variant"]["pins"] == {"A": 1, "B": 2}


@pytest.mark.parametrize(
    "refinement",
    [{"missing": {}}, {"base": {"id": "renamed"}}, {"base": {"extends": "other"}}],
)
def test_refinement_rejects_unknown_or_identity_changes(tmp_path, refinement):
    path = tmp_path / "parts.yaml"
    path.write_text(
        yaml.safe_dump({"components": [{"id": "base"}], "refinements": refinement})
    )
    with pytest.raises(ValueError, match="invalid component refinement"):
        _component_entries([path], {})

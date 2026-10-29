"""Sourcing: requirement values, matching, money and the additive `source:` spec schema."""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from r3_catalog import default_catalog
from r3_catalog.sourcing import Requirement, SourcedValue, catalog_params, parse_constraint, parse_money, parse_value
from r3_catalog.sourcing.quantities import Approx, Bound, Equals, Range, Tagged
from r3_model.spec import RobotSpec
from sourcing_helpers import RC_CAR


@pytest.mark.parametrize(("text", "expected"), [
    ("4.5V ~ 5.5V", [4.5, 5.5]), ("4.5 V to 5.5 V", [4.5, 5.5]), ("4.5-5.5V", [4.5, 5.5]), ("110 mm", 0.11),
    ("12 mm hex", {"size": 0.012, "shape": "hex"}), ("2.4GHz", 2.4e9), ("8GB", 8e9), ("3.3V, 5V", [3.3, 5.0]),
    ("yes", True), ("CRSF", "CRSF"), ("17 dBm", 17.0),
])
def test_parse_value(text, expected):
    value = parse_value(text)
    if isinstance(expected, list):
        assert value == pytest.approx(expected)
    elif isinstance(expected, dict):
        assert value["shape"] == expected["shape"] and value["size"] == pytest.approx(expected["size"])
    else:
        assert value == pytest.approx(expected) if isinstance(expected, float) else value == expected


def test_constraint_kinds_and_checks():
    assert isinstance(parse_constraint(["4.5 V", "5.5 V"]), Range)
    assert isinstance(parse_constraint(">= 4 GB"), Bound)
    assert isinstance(parse_constraint("2.4 GHz"), Approx)
    assert isinstance(parse_constraint("12 mm hex"), Tagged)
    assert isinstance(parse_constraint("crsf"), Equals)
    supply = parse_constraint(["4.5 V", "5.5 V"])
    assert supply.check([4.0, 6.0])[0] == "pass"  # operating range covers the required range
    assert supply.check([4.75, 5.25])[0] == "fail"  # does not cover 4.5 V
    assert supply.check(5.0)[0] == "pass" and supply.check(6.0)[0] == "fail"
    assert parse_constraint(">= 4 GB").check(2e9)[0] == "fail"
    assert parse_constraint("2.4 GHz").check([2.4e9, 2.4835e9])[0] == "pass"
    assert parse_constraint("2.4 GHz").check(9.15e8)[0] == "fail"
    hub = parse_constraint("12 mm hex")
    assert hub.check({"size": 0.012, "shape": "hex"})[0] == "pass"
    assert hub.check({"size": 0.006, "shape": "bore"})[0] == "fail"
    assert hub.check("12 mm hex")[0] == "pass"
    assert parse_constraint("Raspberry Pi 5").check("raspberry-pi-5")[0] == "pass"
    assert parse_constraint("crsf").check(["sbus", "crsf"])[0] == "pass"
    assert parse_constraint("crsf").check(None)[0] == "unknown"


def test_money():
    assert parse_money("$77.50").amount == 77.5 and parse_money("$77.50").currency == "USD"
    assert parse_money("120 USD").amount == 120 and parse_money("EUR 3").currency == "EUR"
    assert parse_money({"amount": 2, "currency": "gbp"}).currency == "GBP"
    with pytest.raises(ValueError):
        parse_money("cheap")


def test_requirement_evaluate_and_digest():
    requirement = Requirement.from_source("pi", {"requires": {"kind": "computer", "family": "raspberry-pi-5", "ram": ">= 4 GB"},
                                                 "budget": "150 USD"}, ["pi"])
    params = {"family": SourcedValue(value="raspberry-pi-5", status="datasheet", source="https://example.com/brief"),
              "memory": SourcedValue(value=8e9, status="vendor", source="https://example.com/listing")}  # alias of ram
    matches = {m.param: m for m in requirement.evaluate(params, "computer")}
    assert matches["kind"].status == "pass" and matches["family"].status == "pass" and matches["ram"].status == "pass"
    assert matches["ram"].provenance == "vendor" and matches["ram"].source == "https://example.com/listing"
    assert requirement.budget.amount == 150 and requirement.quantity == 1
    assert "raspberry pi 5" in requirement.keywords()
    other = Requirement.from_source("pi", {"requires": {"kind": "computer", "family": "raspberry-pi-5", "ram": ">= 8 GB"}}, ["pi"])
    assert other.digest() != requirement.digest()


def test_catalog_params_of_rc_car_parts():
    catalog = default_catalog()
    pi = catalog_params(catalog.component("raspberry-pi-5-8gb"))
    assert pi["family"].value == "raspberry-pi-5" and pi["ram"].value == 8e9
    wheel = catalog_params(catalog.component("wheel-110x45"))
    assert wheel["diameter"].value == pytest.approx(0.11) and wheel["hub"].value["shape"] == "bore"
    receiver = catalog_params(catalog.component("elrs-receiver-2g4"))
    assert receiver["protocol"].value == "crsf" and receiver["supply"].value == [4.5, 5.5]


def _spec(mutate):
    data = yaml.safe_load((RC_CAR / "robot.yaml").read_text())
    mutate(data["components"])
    return data


def test_existing_specs_unchanged_and_source_is_additive():
    spec = RobotSpec.model_validate(_spec(lambda c: None))
    assert all(c.source is None for c in spec.components.values()) and spec.source_slots() == {}
    sourced = RobotSpec.model_validate(_spec(lambda c: c["pi"].update(source={"requires": {"kind": "computer", "ram": ">= 4 GB"}})))
    assert sourced.components["pi"].type == "raspberry-pi-5-8gb" and sourced.source_slots() == {"pi": ["pi"]}
    only = RobotSpec.model_validate(_spec(lambda c: c.update(pi={**{k: v for k, v in c["pi"].items() if k != "type"},
                                                                  "source": {"requires": {"kind": "computer"}}})))
    assert only.components["pi"].type == ""


def test_source_validation():
    with pytest.raises(ValidationError, match="needs a catalog `type` or a `source`"):
        RobotSpec.model_validate(_spec(lambda c: c["pi"].pop("type")))
    with pytest.raises(ValidationError, match="must name the catalog `kind`"):
        RobotSpec.model_validate(_spec(lambda c: c["pi"].update(source={"requires": {"ram": ">= 4 GB"}})))

    def mismatched(components):
        components["wheel_fl"]["source"] = {"requires": {"kind": "wheel", "diameter": "110 mm"}, "group": "wheels"}
        components["wheel_fr"]["source"] = {"requires": {"kind": "wheel", "diameter": "100 mm"}, "group": "wheels"}

    with pytest.raises(ValidationError, match="share sourcing group"):
        RobotSpec.model_validate(_spec(mismatched))

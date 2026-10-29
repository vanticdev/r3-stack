"""FDM materials and printers in the bundled catalog, and r3_fab material resolution."""

from __future__ import annotations

import pytest

from r3_catalog import default_catalog
from r3_fab.stage import resolve_material


@pytest.fixture(scope="module")
def catalog():
    return default_catalog()


def test_materials_load_with_datasheet_values(catalog):
    assert {"petg", "pla", "pla-plus", "asa", "pa6-cf", "paht-cf"} <= set(catalog.materials)
    petg = catalog.materials["petg"]
    # Bambu Lab PETG HF TDS V1.0: 1.28 g/cm3, 34 MPa X-Y, 23 MPa Z, 1810 MPa, HDT 69 C at 0.45 MPa.
    assert petg.density == pytest.approx(1280.0)
    assert petg.tensile_strength == pytest.approx(34e6)
    assert petg.z_strength == pytest.approx(23e6)
    assert petg.youngs_modulus == pytest.approx(1.81e9)
    assert petg.hdt == pytest.approx(69.0)
    for material in catalog.materials.values():
        assert material.provenance.source.startswith(("Bambu", "eSUN")) and "http" in material.provenance.source
        assert material.z_strength < material.tensile_strength
        assert all(word == word.lower() for word in material.keywords)
    assert catalog.materials["asa"].enclosure and catalog.materials["pa6-cf"].enclosure
    assert not catalog.materials["pla"].enclosure


def test_printers_load(catalog):
    x1c = catalog.printers["bambu-x1c"]
    assert x1c.build_volume == pytest.approx([0.256, 0.256, 0.256]) and x1c.enclosed
    assert catalog.printers["bambu-a1-mini"].build_volume == pytest.approx([0.18, 0.18, 0.18])
    assert catalog.printers["prusa-mk4s"].build_volume == pytest.approx([0.25, 0.21, 0.22])
    assert {"bambu-p1s", "bambu-a1", "prusa-core-one"} <= set(catalog.printers)
    assert all(p.provenance.source for p in catalog.printers.values())


@pytest.mark.parametrize(("text", "expected"), [
    ("PETG", "petg"), ("petg", "petg"), ("PLA+", "pla-plus"), ("eSUN PLA plus", "pla-plus"), ("PLA", "pla"),
    ("ASA", "asa"), ("PA6-CF", "pa6-cf"), ("PAHT-CF", "paht-cf"), ("PA12-CF", "paht-cf"),
    ("plastic", None), ("Aluminum plate", None), ("Nylon PA12", None), ("Steel 4140", None), ("", None),
])
def test_material_resolution_uses_whole_words(catalog, text, expected):
    material = resolve_material(catalog, text)
    assert (material.id if material else None) == expected


def test_fdm_print_fit_covers_new_keywords(catalog):
    for text in ("PLA+", "PA12-CF", "PA6-CF", "PAHT-CF", "PETG-CF"):
        assert catalog.print_fit(text).process == "fdm"
    assert catalog.print_fit("Nylon PA12").process == "sls"

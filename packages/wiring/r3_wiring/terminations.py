"""Connector termination geometry and allowances (engineering estimates).

``body_size[2]`` of a catalog connector is its length along the mating axis.
"""

from __future__ import annotations

from r3_catalog.schema import Catalog

# Extra conductor length consumed by each termination style (strip length,
# solder cup or crimp barrel, strain-relief loop). Estimates from common
# hobby/robotics harness practice; not datasheet values.
_ALLOWANCE_BY_FAMILY: list[tuple[str, float]] = [
    ("Soldered", 0.005),  # integral pigtail root soldered to a pad/terminal
    ("XT60", 0.006),
    ("XT30", 0.005),
    ("bullet", 0.006),
    ("JST", 0.003),
    ("servo", 0.003),
    ("U.FL", 0.002),
]
SPLICE_ALLOWANCE = 0.006  # solder splice overlap under heat-shrink, per conductor end
DEFAULT_ALLOWANCE = 0.005
# Fraction of the shorter half that slides inside the mating half (estimate).
ENGAGEMENT_FRACTION = 0.5
# Effective density of a connector body (nylon housing ~1.15 g/cm3 with brass
# contacts, about half the bounding box solid): estimate.
CONNECTOR_EFFECTIVE_DENSITY = 0.5 * 1600.0


def termination_allowance(catalog: Catalog, connector_type: str | None) -> float:
    if connector_type is None:
        return SPLICE_ALLOWANCE
    family = catalog.connector(connector_type).family
    for key, value in _ALLOWANCE_BY_FAMILY:
        if key.lower() in family.lower():
            return value
    return DEFAULT_ALLOWANCE


def body_length(catalog: Catalog, connector_type: str) -> float:
    if connector_type == "integral":
        return 0.0
    return float(catalog.connector(connector_type).body_size[2])


def body_width(catalog: Catalog, connector_type: str) -> float:
    if connector_type == "integral":
        return 0.0
    size = catalog.connector(connector_type).body_size
    return float(max(size[0], size[1]))


def exit_offset(catalog: Catalog, harness_type: str) -> float:
    """Distance from the component connector's mating position to the point
    where the conductors leave the cable-side housing (along the exit
    direction). Integral pigtails leave the component surface directly."""
    return body_length(catalog, harness_type)


def mated_pair_length(catalog: Catalog, type_a: str, type_b: str) -> float:
    la, lb = body_length(catalog, type_a), body_length(catalog, type_b)
    return la + lb - ENGAGEMENT_FRACTION * min(la, lb)


def connector_mass(catalog: Catalog, connector_type: str) -> float:
    if connector_type == "integral":
        return 0.0
    sx, sy, sz = catalog.connector(connector_type).body_size
    return float(sx * sy * sz * CONNECTOR_EFFECTIVE_DENSITY)

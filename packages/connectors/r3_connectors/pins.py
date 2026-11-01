"""Connector frames and pin positions in the robot frame.

* Component connectors: the catalog ``ConnectorInstance`` pose (origin on the
  mating face, ``direction`` = z, ``x`` = pin row) composed with the component pose.
* Harness connectors mated to a component connector: the mated frame (same
  origin and x, z reversed), so pin n sits on pin n of the component half.
* Harness-to-harness ("floating") pairs: a pair frame placed by the router
  (mating-face point and axis); x is chosen horizontal.
* Integral pigtails (a component's own leads, connector type ``integral``):
  conductors leave the component surface side by side along the instance x
  axis at one wire diameter pitch.

A wire endpoint is the conductor's exit from its half: the catalog
``wire_entry`` of a cable-side half (crimp barrel or solder cup), or the pigtail
root. ``wire_axis`` is the direction the conductor leaves in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

import numpy as np

from r3_catalog.schema import Catalog, ConnectorGeometry, ConnectorInstance

from .frames import Frame, any_perpendicular

if TYPE_CHECKING:  # pragma: no cover
    from r3_model.project import Project
    from r3_model.pulled import PulledPart
    from r3_model.spec import HarnessConnector


def native_mating_frame(part: "PulledPart", selector: str | None = None) -> np.ndarray:
    """Select an existing pulled frame; never invent a pose or guess among IDs.

    Names are optional in Onshape REST responses. Explicit selection therefore
    uses the exact pulled key, commonly a generated native feature ID. Older
    named mating/origin frames remain defaults only when no selector was given.
    """
    from r3_model.pulled import PullError

    keys = (selector,) if selector is not None else ("mating", "origin")
    for key in keys:
        if key in part.mate_connectors:
            return part.mate_connectors[key]
    raise PullError("cad_pull_invalid",
                    f"{part.name}: no selected native mating frame; set mate_connector to an existing pulled key",
                    {"mate_connector": selector, "available_connectors": sorted(part.mate_connectors)})


@dataclass(frozen=True)
class PinEnd:
    """Where one conductor terminates, in the robot frame (m)."""

    connector: str  # <harness>.<connector>
    pin: str
    position: np.ndarray  # wire entry (conductor exit from the housing / pigtail root)
    direction: np.ndarray  # unit vector the conductor leaves in
    contact: np.ndarray  # contact point on the mating face
    kind: str  # crimp | solder_cup | pigtail | splice


def instance_frame(inst: ConnectorInstance) -> Frame:
    """Connector frame in the component frame."""
    z = np.asarray(inst.direction, dtype=float)
    x = np.asarray(inst.x, dtype=float) if inst.x is not None else any_perpendicular(z, (1.0, 0.0, 0.0))
    return Frame.from_axes(inst.position, x, z)


def component_connector_frame(project: "Project", ref: str) -> Frame:
    """Robot-frame connector frame of ``<component>.<connector>``."""
    component, _, name = ref.partition(".")
    ctype = project.catalog.component(project.spec.components[component].type)
    inst = ctype.connectors[name]
    if ctype.kind == "cable" and inst.flexible_end:
        partners = [m.b if m.a == ref else m.a for m in project.spec.electrical.direct_mates
                    if ref in (m.a, m.b)]
        if len(partners) == 1:
            owner = partners[0].partition(".")[0]
            if (owner in project.spec.components and
                    project.catalog.component(project.spec.components[owner].type).kind != "cable"):
                return component_connector_frame(project, partners[0]).mated()
    position, rotation = project.graph.component_pose(component)
    return Frame(rotation, position) @ instance_frame(inst)


def mating_site_frame(project: "Project", ref: str, conn: "HarnessConnector") -> Frame:
    """Robot-frame cable-exit frame selected by a harness's mates/at.

    Full mates keep the original frame exactly. Partial site poses compose
    with the current component pose, so both contacts and conductors follow it.
    """
    assert conn.mates is not None
    if conn.at is None:
        return component_connector_frame(project, conn.mates)
    site = project.graph.mating_sites.get(ref)
    if site is None:
        raise ValueError(f"{ref}: invalid partial mate (see graph diagnostics)")
    position, rotation = project.graph.component_pose(conn.mates.partition(".")[0])
    return Frame(rotation, position) @ Frame.from_placement(site.frame)


def floating_pair_frame(face: np.ndarray, axis: np.ndarray) -> Frame:
    """Frame of the half whose z is ``axis`` (pointing at its partner), x horizontal."""
    axis = np.asarray(axis, dtype=float) / np.linalg.norm(axis)
    x = np.cross([0.0, 0.0, 1.0], axis)
    if np.linalg.norm(x) < 1e-6:
        x = any_perpendicular(axis, (1.0, 0.0, 0.0))
    return Frame.from_axes(face, x, axis)


def geometry_of(catalog: Catalog, connector_type: str | None) -> ConnectorGeometry | None:
    if connector_type is None or connector_type not in catalog.connectors:
        return None
    return catalog.connector(connector_type).geometry


def wire_entry_depth(catalog: Catalog, connector_type: str) -> float | None:
    """Distance from the mating face to the conductor exits of a cable-side half (m)."""
    geometry = geometry_of(catalog, connector_type)
    if geometry is None:
        return None
    depths = [-p.wire_entry[2] for p in geometry.pins.values() if p.wire_entry is not None]
    return float(max(depths)) if depths else None


def pin_end(catalog: Catalog, connector_type: str, frame: Frame, pin: str, ref: str) -> PinEnd | None:
    """Conductor exit of ``pin`` of a cable-side half posed at ``frame``."""
    geometry = geometry_of(catalog, connector_type)
    if geometry is None or pin not in geometry.pins:
        return None
    p = geometry.pins[pin]
    if p.wire_entry is None:
        return None
    axis = frame.vector(p.wire_axis if p.wire_axis is not None else [0.0, 0.0, -1.0])
    family = catalog.connector(connector_type).family
    kind = "solder_cup" if any(key in family for key in ("XT30", "XT60", "bullet")) else "crimp"
    return PinEnd(ref, pin, frame.point(p.wire_entry), axis / np.linalg.norm(axis), frame.point(p.position), kind)


def pigtail_ends(frame: Frame, pins: Sequence[str], diameters: dict[str, float], ref: str) -> dict[str, PinEnd]:
    """Integral leads leave the component surface in a row along x, one OD apart,
    centred on the connector origin, in pin-number order."""
    order = sorted(pins, key=lambda n: (int(n) if n.isdigit() else 10 ** 6, n))
    widths = [diameters.get(n, 0.0) for n in order]
    total = sum(widths)
    out: dict[str, PinEnd] = {}
    cursor = -total / 2
    for number, width in zip(order, widths):
        offset = cursor + width / 2
        cursor += width
        point = frame.point([offset, 0.0, 0.0])
        out[number] = PinEnd(ref, number, point, frame.z.copy(), point, "pigtail")
    return out


__all__ = ["PinEnd", "component_connector_frame", "floating_pair_frame", "geometry_of", "instance_frame",
           "mating_site_frame", "pigtail_ends", "pin_end", "wire_entry_depth"]

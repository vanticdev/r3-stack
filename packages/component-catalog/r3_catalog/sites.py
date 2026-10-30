"""Mating options derived from contact grids and purchased housing envelopes.

Partial mates keep the header's row axis and cable exit. Fit is a rigid
translation of the cable contact array, after the normal mating half-turn;
it therefore works for odd/even numbering and right-angle headers alike.
"""
from __future__ import annotations

import numpy as np

from .schema import Catalog, ConnectionSite, ConnectorInstance, Placement


class SiteError(ValueError):
    """A stable diagnostic code and a human-readable selection error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _axes(inst: ConnectorInstance) -> np.ndarray:
    z = np.asarray(inst.direction, float)
    norm = np.linalg.norm(z)
    if norm < 1e-12:
        raise ValueError("zero-length connector direction")
    z /= norm
    if inst.x is None:
        # Match the connector frame's existing default clocking convention.
        hint = np.array([1., 0., 0.])
        if np.linalg.norm(hint - (hint @ z) * z) <= 1e-6:
            hint = np.array([0., 1., 0.])
    else:
        hint = np.asarray(inst.x, float)
    x = hint - (hint @ z) * z
    norm = np.linalg.norm(x)
    if norm < 1e-12:
        raise ValueError("connector x axis is parallel to direction")
    x /= norm
    return np.column_stack([x, np.cross(z, x), z])


def connection_sites(catalog: Catalog, inst: ConnectorInstance) -> list[ConnectionSite]:
    """One full mate plus every translated, fitting crimp housing on a header.

    Geometry itself defines the grid, including pitch, rows and numbering.
    ``mates_with`` retains its whole-connector meaning; partial compatibility
    is declared separately by the housing's ``header_pitch``.
    """
    ctype = catalog.connector(inst.type)
    geometry = ctype.geometry
    numbers = list(geometry.pins) if geometry is not None else list(inst.pins)
    rot = _axes(inst)
    origin = np.asarray(inst.position)
    sites: list[ConnectionSite] = []
    bounds: list[tuple[np.ndarray, np.ndarray]] = []

    def add(identifier: str, mapping: dict[str, str], compatible: list[str], offset: np.ndarray,
            envelope: np.ndarray, depth: float, reach: float, start: str | None = None) -> None:
        lo = np.array([-envelope[0] / 2, -envelope[1] / 2, -reach])
        hi = np.array([envelope[0] / 2, envelope[1] / 2, depth])
        sites.append(ConnectionSite(id=identifier, start_pin=start, pins=list(mapping.values()), pin_map=mapping,
                                    frame=Placement(origin=(origin + rot @ offset).tolist(),
                                                    x=rot[:, 0].tolist(), z=rot[:, 2].tolist()),
                                    envelope_min=lo.tolist(), envelope_max=hi.tolist(), compatible_types=compatible))
        bounds.append((offset + lo, offset + hi))

    # Full mates are deliberately retained even if no cable geometry is known
    # (integral pigtails, or project-local connectors without CAD).
    compatible = list(ctype.mates_with)
    compatible.extend(t.id for t in catalog.connectors.values() if inst.type in t.mates_with and t.id not in compatible)
    partners = [catalog.connector(t) for t in compatible if t in catalog.connectors]
    sizes = [np.asarray(p.geometry.envelope if p.geometry and p.geometry.envelope else p.body_size) for p in partners]
    envelope = np.max(sizes, axis=0) if sizes else np.asarray(ctype.body_size)
    depth = max((p.geometry.depth if p.geometry else p.body_size[2] for p in partners), default=float(envelope[2]))
    reach = max((p.geometry.reach if p.geometry else 0. for p in partners), default=0.)
    add("full", {n: n for n in numbers}, compatible, np.zeros(3), envelope, depth, reach)

    # Unshrouded square-post pin headers only; keyed JST/FFC sockets are fixed.
    if geometry is not None and geometry.pins and geometry.pin_shape == "square_post" and ctype.gender == "header":
        target = {n: np.asarray(p.position) for n, p in geometry.pins.items()}
        target_numbers = list(target)
        points = np.asarray(list(target.values()))
        steps = [step for axis in (0, 1) for step in np.diff(np.unique(np.round(points[:, axis], 10))) if step > 1e-9]
        for housing in catalog.connectors.values():
            g = housing.geometry
            # Existing full-header cable types (e.g. servo crimps) participate
            # without modifying their legacy mates_with declarations.
            header_partners = [catalog.connectors[t] for t in housing.mates_with if t in catalog.connectors
                               and catalog.connectors[t].gender == "header"]
            inferred_pitch = housing.header_pitch
            if inferred_pitch is None and header_partners and g is not None and any(p.wire_entry for p in g.pins.values()):
                grid = sorted({round(p.position[0], 10) for p in g.pins.values()})
                inferred_pitch = min(np.diff(grid), default=0.)
            if not inferred_pitch or g is None:
                continue
            if steps and any(abs(step / inferred_pitch - round(step / inferred_pitch)) > 1e-7
                             for step in steps):
                continue
            local = {n: np.asarray(p.position) * [1., -1., -1.] for n, p in g.pins.items()}
            if not local:
                continue
            first = "1" if "1" in local else min(local)
            for start, position in target.items():
                offset = position - local[first]
                mapping: dict[str, str] = {}
                for n, p in local.items():
                    distances = np.linalg.norm(points - (offset + p), axis=1)
                    index = int(np.argmin(distances))
                    if distances[index] > 1e-9:
                        break
                    mapping[n] = target_numbers[index]
                if len(mapping) != len(local) or len(set(mapping.values())) != len(local):
                    continue
                add(f"{housing.id}@{start}", mapping, [housing.id], offset,
                    np.asarray(g.envelope or housing.body_size), g.depth, g.reach, start)

    # A newly cataloged header also accepts a fitting complete crimp housing
    # without adding its id to every cable type's legacy mates_with list.
    full = sites[0]
    keep = [0]
    for i, site in enumerate(sites[1:], 1):
        if site.pin_map != full.pin_map or not np.allclose(site.frame.origin, full.frame.origin, atol=1e-12, rtol=0):
            keep.append(i)
            continue
        full.compatible_types.extend(t for t in site.compatible_types if t not in full.compatible_types)
        lo, hi = bounds[0]
        bounds[0] = np.minimum(lo, bounds[i][0]), np.maximum(hi, bounds[i][1])
    full.envelope_min, full.envelope_max = bounds[0][0].tolist(), bounds[0][1].tolist()
    # A complete housing is selectable both by plain mates and by at=pin 1,
    # but is one physical placement in the inventory, not two alternatives.
    sites, bounds = [sites[i] for i in keep], [bounds[i] for i in keep]

    # All options share axes here. Positive-volume intersection blocks; merely
    # touching envelope faces does not. Contact sharing always blocks.
    lo = np.asarray([b[0] for b in bounds])
    hi = np.asarray([b[1] for b in bounds])
    for i, site in enumerate(sites):
        overlaps = np.all(np.minimum(hi[i], hi) - np.maximum(lo[i], lo) > 1e-10, axis=1)
        own = set(site.pins)
        site.blocks = [other.id for j, other in enumerate(sites)
                       if i != j and (overlaps[j] or own.intersection(other.pins))]
    return sites


def select_site(catalog: Catalog, inst: ConnectorInstance, housing_type: str, at: str | None = None,
                sites: list[ConnectionSite] | None = None) -> ConnectionSite:
    """Resolve ``mates``/``at`` without silently falling back to a full mate."""
    sites = connection_sites(catalog, inst) if sites is None else sites
    if at is None:
        site = sites[0]
        if housing_type not in site.compatible_types:
            raise SiteError("connection_site_incompatible_type", f"{housing_type} cannot fully mate with {inst.type}")
        return site
    target = catalog.connector(inst.type)
    if target.gender != "header" or target.geometry is None or target.geometry.pin_shape != "square_post":
        raise SiteError("connection_site_incompatible_type", f"{inst.type} is not a partial-mate pin header")
    if at not in sites[0].pins:
        raise SiteError("connection_site_pin_outside_header", f"pin {at!r} is outside {inst.type}")
    full = sites[0]
    if housing_type in full.compatible_types:
        if full.pin_map.get("1") == at:
            return full
        # Smaller placements of the same type may still exist on other rows.
        if not any(housing_type in site.compatible_types for site in sites[1:]):
            raise SiteError("connection_site_does_not_fit", f"{housing_type} does not fit {inst.type} starting at pin {at}")
    housing = catalog.connectors.get(housing_type)
    if housing is None or not any(housing_type in site.compatible_types for site in sites[1:]):
        # A recognised crimp that runs off the edge still reports does_not_fit.
        if housing is not None and housing.header_pitch is not None:
            raise SiteError("connection_site_does_not_fit", f"{housing_type} does not fit {inst.type} starting at pin {at}")
        raise SiteError("connection_site_incompatible_type", f"{housing_type} is not a compatible partial header housing")
    for site in sites:
        if site.start_pin == at and housing_type in site.compatible_types:
            return site
    raise SiteError("connection_site_does_not_fit", f"{housing_type} does not fit {inst.type} starting at pin {at}")

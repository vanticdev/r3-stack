"""Parametric proxy bodies from datasheet dimensions (connector frame, metres).

Used only where no KiCad or freely downloadable vendor model exists. Each
builder returns named meshes: ``body`` (housing) and ``contact_<n>`` (one per
pin), so viewers can colour and pick contacts individually.
"""

from __future__ import annotations

from typing import Any, Callable

import numpy as np
import trimesh

from r3_catalog.schema import ConnectorGeometry

MM = 1e-3


def _box(size: list[float], center: list[float]) -> trimesh.Trimesh:
    return trimesh.creation.box(extents=np.asarray(size) * MM, transform=trimesh.transformations.translation_matrix(
        np.asarray(center) * MM))


def _cylinder(d: float, length: float, z0: float, xy: tuple[float, float] = (0.0, 0.0), sections: int = 32) -> trimesh.Trimesh:
    mesh = trimesh.creation.cylinder(radius=d * MM / 2, height=length * MM, sections=sections)
    mesh.apply_translation([xy[0] * MM, xy[1] * MM, (z0 + length / 2) * MM])
    return mesh


def _tube(od: float, idia: float, length: float, z0: float, sections: int = 32) -> trimesh.Trimesh:
    mesh = trimesh.creation.annulus(r_min=idia * MM / 2, r_max=od * MM / 2, height=length * MM, sections=sections)
    mesh.apply_translation([0.0, 0.0, (z0 + length / 2) * MM])
    return mesh


def _difference(a: trimesh.Trimesh, b: trimesh.Trimesh) -> trimesh.Trimesh:
    try:
        return a.difference(b, engine="manifold")
    except Exception:  # pragma: no cover - manifold3d missing: keep the solid envelope
        return a


def _pins_mm(geometry: ConnectorGeometry) -> list[tuple[str, np.ndarray]]:
    return [(n, np.asarray(p.position) / MM) for n, p in sorted(geometry.pins.items(), key=lambda kv: int(kv[0]))]


def housing(geometry: ConnectorGeometry, p: dict[str, Any]) -> dict[str, trimesh.Trimesh]:
    """Crimp housing (JST XHP/GHR, servo J): box behind the face with a contact cavity per pin."""
    w, t, length, cav = p["width"], p["thickness"], p["length"], p["cavity"]
    body = _box([w, t, length], [0.0, 0.0, -length / 2])
    out = {}
    for number, pos in _pins_mm(geometry):
        body = _difference(body, _box([cav, cav, length * 0.6], [pos[0], pos[1], -length * 0.3 + 0.01]))
        out[f"contact_{number}"] = _box([cav * 0.7, cav * 0.7, length * 0.85], [pos[0], pos[1], -length * 0.85 / 2 - 0.2])
    return {"body": body, **out}


def xt_female(geometry: ConnectorGeometry, p: dict[str, Any]) -> dict[str, trimesh.Trimesh]:
    """XT30 female: narrower insert in front of the shoulder (mating face), body and cups behind."""
    ix, iy, iz = p["insert"]
    bx, by, bz = p["body"]
    body = _box([ix, iy, iz], [0.0, 0.0, iz / 2]).union(_box([bx, by, bz], [0.0, 0.0, -bz / 2]), engine="manifold")
    out = {}
    for number, pos in _pins_mm(geometry):
        body = _difference(body, _cylinder(p["contact_d"] + 0.4, iz + 0.2, 0.0, (pos[0], pos[1])))
        out[f"contact_{number}"] = _cylinder(p["contact_d"], iz - 0.6 + bz + p["cup"], -bz - p["cup"], (pos[0], pos[1]))
    return {"body": body, **out}


def xt_male(geometry: ConnectorGeometry, p: dict[str, Any]) -> dict[str, trimesh.Trimesh]:
    """XT30 male cable plug: shell open at the mating face, bullet contacts recessed 1 mm."""
    sx, sy, sz = p["shell"]
    cx, cy, cz = p["cavity"]
    body = _difference(_box([sx, sy, sz], [0.0, 0.0, -sz / 2]), _box([cx, cy, cz + 0.1], [0.0, 0.0, -cz / 2 + 0.05]))
    out = {}
    for number, pos in _pins_mm(geometry):
        out[f"contact_{number}"] = _cylinder(p["contact_d"], sz + p.get("cup", 0.0) - 1.0, -sz - p.get("cup", 0.0),
                                             (pos[0], pos[1]))
    return {"body": body, **out}


def xt_male_pcb_ra(geometry: ConnectorGeometry, p: dict[str, Any]) -> dict[str, trimesh.Trimesh]:
    """XT30PW-M right angle: XT30 male shell on its side, contacts bent down into the board.
    Connector frame: z out of the opening, y = board normal (board surface at y = -height)."""
    parts = xt_male(geometry, p)
    h, row = p["height"], p["pad_row"]
    sz = p["shell"][2]
    for number, pos in _pins_mm(geometry):
        horizontal = _cylinder(p["contact_d"], row - sz + 0.5, -row, (pos[0], pos[1]))
        down = trimesh.creation.cylinder(radius=p["contact_d"] * MM / 2, height=(h + 3.0) * MM, sections=24)
        down.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
        down.apply_translation([pos[0] * MM, -(h + 3.0) / 2 * MM, -row * MM])
        parts[f"contact_{number}"] = trimesh.util.concatenate([parts[f"contact_{number}"], horizontal, down])
    # Two board pegs under the shell (footprint holes 10 mm in front of the pad row).
    pegs = []
    for x, _ in p.get("pegs", []):
        peg = trimesh.creation.cylinder(radius=0.5 * MM, height=4.0 * MM, sections=16)
        peg.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
        peg.apply_translation([x * MM, (-h - 1.5) * MM, (-row + 10.0) * MM])
        pegs.append(peg)
    parts["body"] = trimesh.util.concatenate([parts["body"], *pegs])
    return parts


def bullet_male(geometry: ConnectorGeometry, p: dict[str, Any]) -> dict[str, trimesh.Trimesh]:
    pin = _cylinder(p["pin_d"], p["pin_length"], 0.0)
    cup = _tube(p["cup_d"], p["pin_d"] - 0.6, p["cup_length"], -p["cup_length"])
    return {"body": cup, "contact_1": pin}


def bullet_female(geometry: ConnectorGeometry, p: dict[str, Any]) -> dict[str, trimesh.Trimesh]:
    socket = _tube(p["od"], 3.55, p["socket_depth"], -p["socket_depth"])
    rest = _cylinder(p["od"], p["length"] - p["socket_depth"], -p["length"])
    return {"body": trimesh.util.concatenate([socket, rest]), "contact_1": _tube(3.9, 3.55, p["socket_depth"] - 0.5,
                                                                                 -p["socket_depth"] + 0.5)}


BUILDERS: dict[str, Callable[[ConnectorGeometry, dict[str, Any]], dict[str, trimesh.Trimesh]]] = {
    "housing": housing, "xt_female": xt_female, "xt_male": xt_male, "xt_male_pcb_ra": xt_male_pcb_ra,
    "bullet_male": bullet_male, "bullet_female": bullet_female,
}


def build_proxy(geometry: ConnectorGeometry) -> dict[str, trimesh.Trimesh]:
    params = dict(geometry.model.params)
    builder = BUILDERS[params.pop("builder")]
    return builder(geometry, params)


__all__ = ["BUILDERS", "build_proxy"]

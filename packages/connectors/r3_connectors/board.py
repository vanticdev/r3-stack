"""Connector poses of a KiCad board in its catalog component frame.

The default component frame of a board is centred on the board outline with x right,
y toward the top edge of the KiCad drawing (KiCad's y points down), z out of
the component side and z = 0 on the bottom copper. A placed footprint's 3D
frame (the KiCad model frame) is mapped through the footprint transform fitted
to its pads (``kicad.footprint_to_board``); the connector frame follows from the
catalog ``footprint_placement``. A ``kicad_board`` model placement overrides
the default origin, so extending one edge does not move its mounting datum.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from r3_catalog.schema import Catalog, ComponentType

from .frames import Frame
from .kicad import MM, board_footprints, footprint_to_board, library_footprints


@dataclass
class BoardConnector:
    name: str  # catalog connector name (J_RX)
    reference: str  # KiCad reference (J5)
    footprint: str
    frame: Frame  # connector frame in the component frame (m)
    model_frame: Frame  # KiCad 3D model frame in the component frame (m)
    side: str  # top | bottom
    pad_residual: float  # rms pad fit residual (m)
    pad_error: float  # max |catalog pad - board pad| through the derived frame (m)
    pads: dict[str, np.ndarray]  # board pads in the component frame (m, on the copper surface)
    nets: dict[str, str]


def footprint_frame(placed: dict[str, Any], library: dict[str, Any], size: tuple[float, float],
                    thickness: float) -> tuple[Frame, float, str]:
    """KiCad 3D frame of a placed footprint in the board's component frame (m)."""
    a, t, rms = footprint_to_board(library, placed)
    flip = np.diag([1.0, -1.0])  # KiCad 2D (y down) <-> 3D (y up)
    linear = np.zeros((3, 3))
    linear[:2, :2] = flip @ a @ flip
    bottom = np.linalg.det(a) < 0
    linear[2, 2] = -1.0 if bottom else 1.0
    offset = np.array([t[0] - size[0] / 2, size[1] / 2 - t[1], 0.0 if bottom else thickness]) * MM
    return Frame(linear, offset), rms * MM, "bottom" if bottom else "top"


def board_connectors(board: Path, component: ComponentType, catalog: Catalog,
                     references: dict[str, str]) -> dict[str, BoardConnector]:
    """``references``: catalog connector name -> KiCad reference."""
    dump = board_footprints(board, list(references.values()))
    size = (float(dump["size"][0]), float(dump["size"][1]))
    thickness = float(dump["thickness"])
    # KiCad STEP uses page x, -page y. Honour the same model placement used for
    # the board solid instead of silently recentering an asymmetric outline.
    board_in_component = Frame(np.eye(3), np.zeros(3))
    if component.model is not None and component.model.source == "kicad_board":
        page = dump["origin_page"]
        centre = np.array([page[0] + size[0] / 2, -page[1] - size[1] / 2, 0.0]) * MM
        board_in_component = Frame.from_placement(component.model.placement) @ Frame(np.eye(3), centre)
    placed = dump["footprints"]
    library = library_footprints([placed[ref]["lib_id"] for ref in references.values() if ref in placed])
    out: dict[str, BoardConnector] = {}
    for name, ref in references.items():
        if ref not in placed:
            continue
        inst = component.connectors[name]
        geometry = catalog.connector(inst.type).geometry
        lib = library[placed[ref]["lib_id"]]
        fp_frame, rms, side = footprint_frame(placed[ref], lib, size, thickness)
        fp_frame = board_in_component @ fp_frame
        fp_in_connector = Frame.from_placement(geometry.footprint_placement)
        # Placements are stored in metres by the catalog loader.
        connector = fp_frame @ fp_in_connector.inverse()
        model_frame = connector @ Frame.from_placement(geometry.model.placement)
        pads = {}
        nets = {}
        for pad in placed[ref]["pads"]:
            if pad["number"].isdigit():
                p = np.array([pad["position"][0] - size[0] / 2, size[1] / 2 - pad["position"][1],
                              0.0 if side == "bottom" else thickness]) * MM
                pads[pad["number"]] = board_in_component.point(p)
                nets[pad["number"]] = pad["net"].lstrip("/")
        errors = [np.linalg.norm(connector.point(geometry.pins[n].pad) - pads[n])
                  for n in pads if n in geometry.pins and geometry.pins[n].pad is not None]
        out[name] = BoardConnector(name, ref, placed[ref]["lib_id"], connector, model_frame, side, rms,
                                   float(max(errors, default=0.0)), pads, nets)
    return out


__all__ = ["BoardConnector", "board_connectors", "footprint_frame"]

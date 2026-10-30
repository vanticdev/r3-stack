"""Derive the catalog connector geometry (``connector_geometry.yaml``).

    python -m r3_connectors.derive [--write]

Board-side parts and the XT60 halves come from the official KiCad library:
pad positions are read from the footprint through ``pcbnew`` and the body
extents from the KiCad STEP model (converted with cascadio). The mating face of
each part was measured on that model (shroud floor, socket entry, shoulder);
the numbers are recorded in ``KICAD`` below with how they were measured.
Cable-side housings with no freely downloadable vendor CAD (JST distributes
CAD by e-mail request, Amass publishes none) are parametric proxies built from
datasheet dimensions, labelled ``proxy``.

Connector frame (see ``r3_catalog.schema.ConnectorGeometry``): origin at the
centre of the contact array on the mating face, z toward the mating half, x
from pin 1 toward the last pin.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from r3_catalog import DATA_DIR

from .frames import Frame
from .kicad import library_footprints, resolve_model_path

OUT = DATA_DIR / "connector_geometry.yaml"
KICAD_LICENSE = ("KiCad libraries, CC-BY-SA 4.0 with the KiCad library exception (designs and generated files "
                 "using the libraries are not adapted material); https://www.kicad.org/libraries/license/")


@dataclass
class KicadSpec:
    footprint: str
    model: str  # relative to ${KICAD10_3DMODEL_DIR}
    origin: tuple[float, float, float]  # connector-frame origin in model coordinates (mm)
    x: tuple[float, float, float]
    z: tuple[float, float, float]
    single_row: bool
    tip: float  # contact reach beyond the mating face (mm), measured on the model
    pin_shape: str
    pin_size: float  # mm
    measured: str
    color: str = "#202020"
    contact_color: str = "#d4a640"
    wire_entry_z: float | None = None  # model z of the solder-cup exit (cable-side halves), mm
    pins: tuple[str, ...] | None = None  # footprint pads that are catalog pins (default: all numbered pads)
    contact_at_origin: bool = False  # coaxial: the contact is on the mating axis, not over its solder pad


@dataclass
class ProxySpec:
    pins: list[tuple[str, float, float]]  # number, x, y in the connector frame (mm)
    builder: str
    params: dict[str, Any]
    depth: float  # body behind the mating face (mm); wire entry plane
    reach: float  # body in front of the mating face (mm)
    envelope: tuple[float, float, float]  # x, y, z extents (mm)
    tip: float
    pin_shape: str
    pin_size: float
    source: str
    color: str = "#202020"
    contact_color: str = "#d4a640"
    pads: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    footprint: str = ""
    wire_entry: bool = True


DIR = "${KICAD10_3DMODEL_DIR}/"

KICAD: dict[str, KicadSpec] = {
    "header-2x20-2.54": KicadSpec(
        "Connector_PinHeader_2.54mm:PinHeader_2x20_P2.54mm_Vertical",
        "Connector_PinHeader_2.54mm.3dshapes/PinHeader_2x20_P2.54mm_Vertical.step",
        (1.27, -24.13, 2.54), (0, -1, 0), (0, 0, 1), False, 6.0, "square_post", 0.64,
        "mating face = top of the 2.54 mm insulator; 0.64 mm square posts reach 8.54 mm above the board", "#151515"),
    "socket-2x20-2.54": KicadSpec(
        "Connector_PinSocket_2.54mm:PinSocket_2x20_P2.54mm_Vertical",
        "Connector_PinSocket_2.54mm.3dshapes/PinSocket_2x20_P2.54mm_Vertical.step",
        (-1.27, -24.13, 8.5), (0, -1, 0), (0, 0, 1), False, -1.1, "socket", 1.0,
        "mating face = socket entry face 8.5 mm from the board; contacts start 1.1 mm inside", "#151515"),
    "header-1x3-2.54": KicadSpec(
        "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
        "Connector_PinHeader_2.54mm.3dshapes/PinHeader_1x03_P2.54mm_Vertical.step",
        (0.0, -2.54, 2.54), (0, -1, 0), (0, 0, 1), True, 6.0, "square_post", 0.64,
        "mating face = top of the 2.54 mm insulator; posts reach 8.54 mm", "#151515"),
    "header-1x3-2.54-ra": KicadSpec(
        "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Horizontal",
        "Connector_PinHeader_2.54mm.3dshapes/PinHeader_1x03_P2.54mm_Horizontal.step",
        (4.04, -2.54, 1.27), (0, -1, 0), (1, 0, 0), True, 6.0, "square_post", 0.64,
        "mating face = outer insulator face at x 4.04 mm; horizontal posts at z 1.27 mm", "#151515"),
    "jst-xh-3-header": KicadSpec(
        "Connector_JST:JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical",
        "Connector_JST.3dshapes/JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical.step",
        (2.5, 0.0, 1.85), (1, 0, 0), (0, 0, 1), True, 4.65, "square_post", 0.64,
        "mating face = shroud floor 1.85 mm above the board (ray cast on the model); posts reach 6.5 mm",
        "#e8e4c9"),
    "jst-xh-3-header-ra": KicadSpec(
        "Connector_JST:JST_XH_S3B-XH-A_1x03_P2.50mm_Horizontal",
        "Connector_JST.3dshapes/JST_XH_S3B-XH-A_1x03_P2.50mm_Horizontal.step",
        (2.5, -4.05, 3.75), (1, 0, 0), (0, -1, 0), True, 4.65, "square_post", 0.64,
        "side entry: mating face = cavity back wall 4.05 mm in front of the pad row, contact axis 3.75 mm above "
        "the board (post centre); posts reach 8.7 mm in front of the pads", "#e8e4c9"),
    "jst-gh-4-header": KicadSpec(
        "Connector_JST:JST_GH_SM04B-GHS-TB_1x04-1MP_P1.25mm_Horizontal",
        "Connector_JST.3dshapes/JST_GH_SM04B-GHS-TB_1x04-1MP_P1.25mm_Horizontal.step",
        (0.0, 0.13, 1.37), (1, 0, 0), (0, -1, 0), True, 2.1, "blade", 0.3,
        "side entry: mating face = cavity back wall 0.13 mm behind the footprint origin, contact axis 1.37 mm "
        "above the board (contact z 0.67-2.07 mm); contacts reach 2.1 mm toward the opening", "#e8e4c9"),
    "xt60-male": KicadSpec(
        "Connector_AMASS:AMASS_XT60-M_1x02_P7.20mm_Vertical",
        "Connector_AMASS.3dshapes/AMASS_XT60-M_1x02_P7.2mm_Vertical.step",
        (3.6, 0.0, 16.0), (1, 0, 0), (0, 0, 1), True, -1.4, "bullet", 3.5,
        "mating face = front of the shell (16.0 mm); male contacts end 1.4 mm inside; solder cups exit at the "
        "model base (-4.5 mm). Chamfered corners at the pin-1 end on both halves (section at 12 mm), so a half "
        "turn about x mates pin 1 to pin 1", "#d9a21b", wire_entry_z=-4.5),
    "xt60-female": KicadSpec(
        "Connector_AMASS:AMASS_XT60-F_1x02_P7.20mm_Vertical",
        "Connector_AMASS.3dshapes/AMASS_XT60-F_1x02_P7.2mm_Vertical.step",
        (3.6, 0.0, 8.0), (1, 0, 0), (0, 0, 1), True, 7.7, "socket", 3.5,
        "mating face = shoulder at 8.0 mm where the insert (14.0 x 6.6 mm) steps out to the 15.9 x 8.1 mm body "
        "(bisected sections); the male shell front stops on it. Socket tops at 15.7 mm; solder cups exit at "
        "-4.5 mm", "#d9a21b", wire_entry_z=-4.5),
    "ufl": KicadSpec(
        "Connector_Coaxial:U.FL_Hirose_U.FL-R-SMT-1_Vertical",
        "Connector_Coaxial.3dshapes/U.FL_Hirose_U.FL-R-SMT-1_Vertical.step",
        (0.0, 0.0, 1.25), (1, 0, 0), (0, 0, 1), True, 0.0, "coax", 0.5,
        "mating face = top of the receptacle (1.25 mm); centre contact on the axis; pad 1 is its solder tab, "
        "pads 2 the shield", "#c8c8c8", pins=("1",), contact_at_origin=True),
    # MIPI CSI camera FPC sockets (horizontal ZIF; the flat cable enters the front face, opposite the solder tails).
    "fpc-22p-0.5mm-socket": KicadSpec(
        "Connector_FFC-FPC:Hirose_FH12-22S-0.5SH_1x22-1MP_P0.50mm_Horizontal",
        "Connector_FFC-FPC.3dshapes/Hirose_FH12-22S-0.5SH_1x22-1MP_P0.50mm_Horizontal.step",
        (0.0, -4.4, 1.0), (1, 0, 0), (0, -1, 0), True, -1.5, "blade", 0.3,
        "side entry: mating face = front (cable entry) face of the 15.1 x 6.4 x 2.0 mm body, 4.4 mm in front of the "
        "footprint origin, contact axis at mid-height 1.0 mm; contacts start about 1.5 mm inside (FH12 drawing: "
        "FPC insertion depth about 3 mm)", "#e8e4c9"),
    "fpc-15p-1.0mm-socket": KicadSpec(
        "Connector_FFC-FPC:TE_1-84952-5_1x15-1MP_P1.0mm_Horizontal",
        "Connector_FFC-FPC.3dshapes/TE_1-84952-5_1x15-1MP_P1.0mm_Horizontal.step",
        (0.0, -4.6, 1.28), (1, 0, 0), (0, -1, 0), True, -1.5, "blade", 0.5,
        "side entry: mating face = front (cable entry) face of the 22.9 x 6.5 x 2.6 mm body, 4.6 mm in front of the "
        "footprint origin, contact axis at mid-height 1.28 mm; contacts start about 1.5 mm inside", "#e8e4c9"),
}

XT30_FEMALE = dict(insert=[8.2, 4.2, 5.6], body=[10.2, 5.2, 5.0], contact_d=2.0)
PROXIES: dict[str, ProxySpec] = {
    "xt30pw-male": ProxySpec(
        [("1", -2.5, 0.0), ("2", 2.5, 0.0)], "xt_male_pcb_ra",
        {"shell": [10.2, 5.2, 10.6], "cavity": [8.4, 4.4, 5.8], "contact_d": 1.0, "height": 3.1, "pad_row": 13.4,
         "pegs": [[-5.5, -10.0], [5.5, -10.0]]},
        13.4, 0.0, (10.2, 5.2, 13.4), -1.0, "bullet", 1.0,
        "Amass XT30PW-M datasheet outline (10.2 x 5.2 mm shell, 5.0 mm contact pitch) on the KiCad "
        "AMASS_XT30PW-M footprint (pads, pegs, courtyard); no KiCad or vendor 3D model exists (proxy)",
        "#d9a21b", footprint="Connector_AMASS:AMASS_XT30PW-M_1x02_P2.50mm_Horizontal", wire_entry=False),
    "xt30-female": ProxySpec(
        [("1", -2.5, 0.0), ("2", 2.5, 0.0)], "xt_female", {**XT30_FEMALE, "cup": 3.0},
        8.0, 5.6, (10.2, 5.2, 13.6), 5.0, "socket", 2.0,
        "Amass XT30U-F datasheet: 10.2 x 5.2 mm body, 5.0 mm pitch, insert 5.6 mm (proxy)", "#d9a21b"),
    "xt30-male": ProxySpec(
        [("1", -2.5, 0.0), ("2", 2.5, 0.0)], "xt_male", {"shell": [10.2, 5.2, 10.6], "cavity": [8.4, 4.4, 5.8],
                                                          "contact_d": 1.0, "cup": 3.0},
        13.6, 0.0, (10.2, 5.2, 13.6), -1.0, "bullet", 1.0,
        "Amass XT30U-M datasheet: 10.2 x 5.2 x 10.6 mm shell, 5.0 mm pitch (proxy)", "#d9a21b"),
    "bullet-3.5-male": ProxySpec(
        [("1", 0.0, 0.0)], "bullet_male", {"pin_d": 3.5, "pin_length": 9.0, "cup_d": 4.4, "cup_length": 5.0},
        5.0, 9.0, (4.4, 4.4, 14.0), 9.0, "bullet", 3.5,
        "3.5 mm gold bullet: 9 mm split pin, 5 mm solder cup (hobby vendor drawings; proxy)", "#d9b54a"),
    "bullet-3.5-female": ProxySpec(
        [("1", 0.0, 0.0)], "bullet_female", {"od": 5.0, "socket_depth": 9.5, "length": 16.0},
        16.0, 0.0, (5.0, 5.0, 16.0), -0.5, "socket", 3.5,
        "3.5 mm gold bullet socket: 5 mm OD, 16 mm long incl. solder cup (hobby vendor drawings; proxy)",
        "#d9b54a"),
    "jst-xh-3-housing": ProxySpec(
        [("1", -2.5, 0.0), ("2", 0.0, 0.0), ("3", 2.5, 0.0)], "housing",
        {"width": 9.9, "thickness": 5.75, "length": 7.0, "cavity": 1.6},
        7.0, 0.0, (9.9, 5.75, 7.0), 0.0, "crimp", 1.0,
        "JST XH datasheet (eXH.pdf) XHP-3: 2.50 mm pitch, 9.9 mm wide (2.5(n-1)+4.9); SXH-001T-P0.6 crimps "
        "(proxy)", "#f2efdc"),
    "jst-gh-4-housing": ProxySpec(
        [("1", -1.875, 0.0), ("2", -0.625, 0.0), ("3", 0.625, 0.0), ("4", 1.875, 0.0)], "housing",
        {"width": 6.25, "thickness": 4.25, "length": 4.5, "cavity": 0.8},
        4.5, 0.0, (6.25, 4.25, 4.5), 0.0, "crimp", 0.6,
        "JST GH datasheet (eGH.pdf) GHR-04V-S: 1.25 mm pitch, 6.25 mm wide; SSHL-002T-P0.2 crimps (proxy)",
        "#f2efdc"),
    "xt90h-male": ProxySpec(
        [("1", -5.5, 0.0), ("2", 5.5, 0.0)], "housing",
        {"width": 20.8, "thickness": 11.7, "length": 30.7, "cavity": 4.5},
        30.7, 0.0, (20.8, 11.7, 30.7), 0.0, "solder_cup", 4.5,
        "AMASS XT90H V1.2 drawing: 11.00 mm pitch, 20.8 x 11.7 x 30.7 mm capped envelope; "
        "conservative envelope proxy, mating recess and wire exit estimated; https://www.electrokit.com/upload/product/41020/41020964/XT90H.pdf",
        "#d9a21b"),
    "xt90h-female": ProxySpec(
        [("1", -5.5, 0.0), ("2", 5.5, 0.0)], "housing",
        {"width": 20.8, "thickness": 11.7, "length": 29.7, "cavity": 4.5},
        29.7, 0.0, (20.8, 11.7, 29.7), 0.0, "solder_cup", 4.5,
        "AMASS XT90H V1.2 drawing: 11.00 mm pitch, 20.8 x 11.7 x 29.7 mm capped envelope; "
        "conservative envelope proxy, mating recess and wire exit estimated; https://www.electrokit.com/upload/product/41020/41020964/XT90H.pdf",
        "#d9a21b"),
    "servo-3-female": ProxySpec(
        [("1", -2.54, 0.0), ("2", 0.0, 0.0), ("3", 2.54, 0.0)], "housing",
        {"width": 7.6, "thickness": 2.6, "length": 14.0, "cavity": 1.0},
        14.0, 0.0, (7.6, 2.6, 14.0), 0.0, "socket", 1.0,
        "Futaba J / 2.54 mm 1x3 crimp housing: 7.6 x 2.6 x 14 mm (vendor listings; proxy)", "#1d1d1d"),
    # Flat-cable ends: the stiffened FFC end with exposed contacts on one face; no wires (the cable is the conductor).
    "fpc-22p-0.5mm-cable": ProxySpec(
        [(str(n), -5.25 + 0.5 * (n - 1), 0.0) for n in range(1, 23)], "ffc_end",
        {"width": 11.5, "thickness": 0.3, "stiffener": 4.0, "contact_width": 0.3},
        4.0, 0.0, (11.5, 0.3, 4.0), 0.0, "blade", 0.3,
        "Raspberry Pi 22-way 0.5 mm camera FFC: 11.5 mm wide, 0.3 mm thick, 4 mm stiffener (Raspberry Pi cable "
        "listings; proxy)", "#d9a441", wire_entry=False),
    "fpc-15p-1.0mm-cable": ProxySpec(
        [(str(n), -7.0 + 1.0 * (n - 1), 0.0) for n in range(1, 16)], "ffc_end",
        {"width": 16.0, "thickness": 0.3, "stiffener": 4.0, "contact_width": 0.6},
        4.0, 0.0, (16.0, 0.3, 4.0), 0.0, "blade", 0.6,
        "Raspberry Pi 15-way 1.0 mm camera FFC: 16 mm wide, 0.3 mm thick, 4 mm stiffener (Raspberry Pi cable "
        "listings; proxy)", "#d9a441", wire_entry=False),
}


# Same housing geometry with heavier crimp terminals; retain it when regenerating.
PROXIES["servo-3-female-hd"] = PROXIES["servo-3-female"]

def _round(v: float, nd: int = 4) -> float:
    r = round(float(v), nd)
    return 0.0 if r == 0 else r


def _mm(v: np.ndarray | list[float]) -> str:
    return "[" + ", ".join(f"{_round(c)} mm" if abs(c) > 1e-9 else "0" for c in v) + "]"


def model_bounds(step: Path) -> np.ndarray:
    """Model-frame bounds (mm) of a KiCad STEP model, from its cascadio tessellation."""
    import tempfile

    import cascadio
    import trimesh

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "model.glb"
        cascadio.step_to_glb(str(step), str(out), tol_linear=0.01, tol_angular=0.5)
        return np.asarray(trimesh.load(out).bounds) * 1e3


def _box_bounds(frame: Frame, bounds: np.ndarray) -> np.ndarray:
    corners = np.array([[bounds[i][0], bounds[j][1], bounds[k][2]] for i in (0, 1) for j in (0, 1) for k in (0, 1)])
    local = frame.inverse().points(corners)
    return np.array([local.min(axis=0), local.max(axis=0)])


def derive_kicad(type_id: str, spec: KicadSpec, footprint: dict[str, Any]) -> dict[str, Any]:
    frame = Frame.from_axes(spec.origin, spec.x, spec.z)  # connector frame in model coordinates (mm)
    inv = frame.inverse()
    pins: dict[str, Any] = {}
    seen: set[str] = set()
    for pad in footprint["pads"]:
        number = pad["number"]
        if not number or number in seen or not number.isdigit() or (spec.pins is not None and number not in spec.pins):
            continue
        seen.add(number)
        pad_model = np.array([pad["position"][0], -pad["position"][1], 0.0])  # KiCad 3D axes: y up
        pad_local = inv.point(pad_model)
        contact = pad_local.copy()
        contact[2] = 0.0
        if spec.single_row:
            contact[1] = 0.0
        if spec.contact_at_origin:
            contact[:] = 0.0
        entry: dict[str, Any] = {"position": contact, "tip": spec.tip, "pad": pad_local}
        if spec.wire_entry_z is not None:
            entry["wire_entry"] = inv.point([pad_model[0], pad_model[1], spec.wire_entry_z])
            entry["wire_axis"] = inv.vector([0.0, 0.0, -1.0])
        pins[number] = entry
    bounds = _box_bounds(frame, model_bounds(resolve_model_path(DIR + spec.model)))
    return {
        "model": {"source": "kicad", "path": DIR + spec.model, "license": KICAD_LICENSE, "units": "mm",
                  "placement": inv, "provenance": {"status": "datasheet", "source": f"KiCad 3D library {spec.model}",
                                                   "notes": spec.measured}},
        "footprint": spec.footprint, "footprint_placement": inv,
        "pins": pins, "pin_shape": spec.pin_shape, "pin_size": spec.pin_size,
        "envelope": bounds[1] - bounds[0], "depth": -bounds[0][2], "reach": bounds[1][2],
        "color": spec.color, "contact_color": spec.contact_color,
        "provenance": {"status": "derived", "source": f"pads: KiCad footprint {spec.footprint} via pcbnew; "
                                                      f"body and mating face: KiCad model {spec.model}",
                       "notes": spec.measured},
    }


def derive_proxy(type_id: str, spec: ProxySpec, footprint: dict[str, Any] | None) -> dict[str, Any]:
    pins: dict[str, Any] = {}
    placement = Frame.identity()
    fp_placement = None
    pads: dict[str, np.ndarray] = {}
    if footprint is not None:
        # XT30PW-M (right angle): contact axis ``height`` above the board, mating face 13.4 mm in front of
        # the pad row (front of the courtyard less its 0.25 mm margin). Footprint 3D frame in the connector
        # frame: pins 1 -> 2 run along footprint -x, the opening faces footprint +y, the board normal is +y_c.
        h = spec.params["height"]
        fp_placement = Frame(np.array([[-1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, 1.0, 0.0]]), np.array([-2.5, -h, -13.4]))
        for pad in footprint["pads"]:
            if pad["number"].isdigit():
                pads[pad["number"]] = fp_placement.point([pad["position"][0], -pad["position"][1], 0.0])
    for number, x, y in spec.pins:
        entry: dict[str, Any] = {"position": np.array([x, y, 0.0]), "tip": spec.tip}
        if number in pads:
            entry["pad"] = pads[number]
        if spec.wire_entry:
            entry["wire_entry"] = np.array([x, y, -spec.depth])
            entry["wire_axis"] = np.array([0.0, 0.0, -1.0])
        pins[number] = entry
    return {
        "model": {"source": "proxy", "units": "mm", "placement": placement,
                  "params": {"builder": spec.builder, **spec.params},
                  "provenance": {"status": "estimated", "source": spec.source}},
        "footprint": spec.footprint, "footprint_placement": fp_placement,
        "pins": pins, "pin_shape": spec.pin_shape, "pin_size": spec.pin_size,
        "envelope": np.array(spec.envelope), "depth": spec.depth, "reach": spec.reach,
        "color": spec.color, "contact_color": spec.contact_color,
        "provenance": {"status": "estimated", "source": spec.source},
    }


def _frame_yaml(frame: Frame, indent: str) -> str:
    return (f"{{origin: {_mm(frame.origin)}, x: {[_round(v, 6) for v in frame.x]}, "
            f"z: {[_round(v, 6) for v in frame.z]}}}")


def _scalar(v: Any) -> str:
    return json.dumps(v)


def to_yaml(geometry: dict[str, dict[str, Any]]) -> str:
    lines = [
        "# GENERATED by `python -m r3_connectors.derive --write`; do not edit by hand.",
        "# Exact connector geometry per connector type (see r3_catalog.schema.ConnectorGeometry).",
        "# Connector frame: origin at the centre of the contact array on the mating face, z toward the mating",
        "# half, x from pin 1 toward the last pin; mated halves share origin and x with opposite z.",
        "# Pads come from the KiCad footprints through pcbnew; bodies from the KiCad STEP models or, labelled",
        "# proxy, from datasheet dimensions.",
        "geometry:",
    ]
    for type_id, g in geometry.items():
        m = g["model"]
        lines.append(f"  {type_id}:")
        model = [f"source: {m['source']}"]
        if m.get("path"):
            model.append(f"path: {_scalar(m['path'])}")
        if m.get("license"):
            model.append(f"license: {_scalar(m['license'])}")
        model.append(f"units: {m['units']}")
        model.append(f"placement: {_frame_yaml(m['placement'], '')}")
        if m.get("params"):
            model.append(f"params: {json.dumps(m['params'])}")
        prov = m["provenance"]
        model.append("provenance: {" + ", ".join(f"{k}: {_scalar(v)}" for k, v in prov.items()) + "}")
        lines.append("    model:")
        lines.extend(f"      {item}" for item in model)
        if g.get("footprint"):
            lines.append(f"    footprint: {_scalar(g['footprint'])}")
        if g.get("footprint_placement") is not None:
            lines.append(f"    footprint_placement: {_frame_yaml(g['footprint_placement'], '')}")
        lines.append(f"    pin_shape: {g['pin_shape']}")
        lines.append(f"    pin_size: {_round(g['pin_size'])} mm")
        lines.append(f"    envelope: {_mm(g['envelope'])}")
        lines.append(f"    depth: {_round(g['depth'])} mm")
        lines.append(f"    reach: {_round(g['reach'])} mm")
        lines.append(f"    color: {_scalar(g['color'])}")
        lines.append(f"    contact_color: {_scalar(g['contact_color'])}")
        prov = g["provenance"]
        lines.append("    provenance: {" + ", ".join(f"{k}: {_scalar(v)}" for k, v in prov.items()) + "}")
        lines.append("    pins:")
        for number in sorted(g["pins"], key=int):
            p = g["pins"][number]
            parts = [f"position: {_mm(p['position'])}", f"tip: {_round(p['tip'])} mm"]
            if p.get("pad") is not None:
                parts.append(f"pad: {_mm(p['pad'])}")
            if p.get("wire_entry") is not None:
                parts.append(f"wire_entry: {_mm(p['wire_entry'])}")
                parts.append(f"wire_axis: {[_round(v, 6) for v in p['wire_axis']]}")
            lines.append(f"      \"{number}\": {{{', '.join(parts)}}}")
    return "\n".join(lines) + "\n"


def derive_all() -> dict[str, dict[str, Any]]:
    names = [s.footprint for s in KICAD.values()] + [s.footprint for s in PROXIES.values() if s.footprint]
    footprints = library_footprints(names)
    out: dict[str, dict[str, Any]] = {}
    for type_id, spec in KICAD.items():
        out[type_id] = derive_kicad(type_id, spec, footprints[spec.footprint])
    for type_id, spec in PROXIES.items():
        out[type_id] = derive_proxy(type_id, spec, footprints.get(spec.footprint) if spec.footprint else None)
    return dict(sorted(out.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = parser.parse_args(argv)
    text = to_yaml(derive_all())
    if args.write:
        OUT.write_text(text)
        print(f"wrote {OUT}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

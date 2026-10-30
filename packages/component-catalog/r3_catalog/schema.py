"""Catalog schemas: component types, connector types and wire gauges.

Every numeric property records provenance. ``status`` distinguishes values
copied from a datasheet from engineering estimates; validation reports which
results depend on estimates.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from r3_model.units import Quantity, Vector


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ``vendor``: a value published by the seller (product page, listing) rather than a manufacturer datasheet.
ProvenanceStatus = Literal["datasheet", "vendor", "measured", "estimated", "derived"]


class Provenance(_Model):
    status: ProvenanceStatus
    source: str = ""
    notes: str = ""


PinRole = Literal[
    "power_in", "power_out", "ground", "gpio", "pwm_in", "pwm_out",
    "i2c_sda", "i2c_scl", "spi_mosi", "spi_miso", "spi_sck", "spi_cs",
    "uart_tx", "uart_rx", "analog", "motor", "rf", "passive", "nc",
]


class Pin(_Model):
    role: PinRole
    net: str = Field(default="", description="Internal net name inside the component, e.g. VIN, GND.")
    voltage_nominal: Quantity | None = None
    voltage_min: Quantity | None = None
    voltage_max: Quantity | None = Field(default=None, description="Absolute maximum.")
    current_max: Quantity | None = None
    logic_level: Quantity | None = Field(default=None, description="Logic high level (V) for signal pins.")
    tolerant_5v: bool = False
    functions: list[str] = Field(default_factory=list, description="e.g. pwm0_ch2, uart0_tx, i2c1_sda.")


class ConnectorInstance(_Model):
    """A connector mounted on a component, in the component frame.

    With catalog connector geometry, ``position`` is the origin of the connector
    frame (centre of the contact array on the mating face), ``direction`` its z
    axis (toward the mating half; the cable exit) and ``x`` its pin-row axis
    (pin 1 toward the last pin). See :class:`ConnectorGeometry`."""

    type: str
    position: Vector = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    direction: Vector = Field(default_factory=lambda: [0.0, 0.0, 1.0], description="Unit vector of cable exit.")
    x: Vector | None = Field(default=None, description="Pin-row axis of the connector frame (perpendicular to direction).")
    pins: dict[str, str] = Field(description="connector pin number -> component pin name.")
    embedded: bool = Field(default=False, description="The connector body is part of the component's own 3D model.")
    source: str = Field(default="", description="Where the pose comes from, e.g. 'kicad:J5' or 'step:J8 pin 1'.")


FeatureKind = Literal["bore", "d_bore", "shaft", "d_shaft", "tapped_hole", "clearance_hole", "spline", "face", "slot",
                      "sphere", "socket", "pocket"]


class Feature(_Model):
    """A datasheet mechanical interface of a part, in the component frame.

    Material-removing kinds (bore, d_bore, tapped_hole, clearance_hole, slot,
    socket, pocket) are cut into the envelope starting at ``position`` and going
    along ``axis`` (into the material) for ``depth``. Material-adding kinds
    (shaft, d_shaft, spline, sphere) grow from ``position`` along ``axis`` for
    ``depth``. D-shapes keep a flat at ``flat_offset`` from the axis whose
    outward normal is ``flat_normal``. ``thread`` is the catalog fastener whose
    thread a tapped hole accepts (the hole is cut at its tap-drill diameter; depth 0 = threaded through).
    Slots are obround, ``length`` center-to-center along ``slot_dir``. Pockets
    are boxes of ``size`` [along slot_dir, across, depth]. ``face`` only names a
    seating face for verification (``position`` on it, ``axis`` its outward normal).
    """

    id: str
    kind: FeatureKind
    position: Vector = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    axis: Vector = Field(default_factory=lambda: [0.0, 0.0, -1.0])
    diameter: Quantity = 0.0
    depth: Quantity = 0.0
    flat_offset: Quantity | None = None
    flat_normal: Vector | None = None
    thread: str | None = None
    length: Quantity = 0.0
    slot_dir: Vector | None = None
    size: Vector | None = None
    provenance: Provenance = Field(default_factory=lambda: Provenance(status="datasheet"))


SolidKind = Literal["box", "cylinder", "prism", "revolve", "sphere"]


class Solid(_Model):
    """One primitive of a part's envelope or fabricated geometry, component frame.

    * ``box``: ``position`` is the center, ``size`` the full extents.
    * ``cylinder``: base center ``position``, grows along ``axis`` for ``length``.
    * ``prism``: 2D ``profile`` polygon (plane coordinates u, v) on the plane
      through ``position`` normal to ``axis``, extruded ``depth`` along ``axis``.
      ``u_axis`` gives the in-plane u direction (v = axis x u). A profile string
      ``"@name"`` reads the polygon from the instance ``params[name]``.
    * ``revolve``: profile ``[[r, t], ...]`` (radius, distance along ``axis`` from
      ``position``) revolved 360 degrees about the axis.
    * ``sphere``: center ``position``, ``diameter``.

    ``op: remove`` subtracts the primitive (pockets, cavities).
    """

    kind: SolidKind
    op: Literal["add", "remove"] = "add"
    name: str = ""
    position: Vector = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    axis: Vector = Field(default_factory=lambda: [0.0, 0.0, 1.0])
    u_axis: Vector | None = None
    size: Vector | None = None
    diameter: Quantity = 0.0
    length: Quantity = 0.0
    depth: Quantity = 0.0
    profile: list[Vector] | str | None = None
    inner: list[list[Vector]] = Field(default_factory=list, description="prism: inner loops (holes) in plane coordinates.")


class Mechanical(_Model):
    shape: Literal["box", "cylinder", "custom"] = "box"
    size: Vector = Field(description="Overall envelope including protrusions: box/custom [x, y, z]; cylinder [diameter, length] along local axis.")
    board_outline: Vector | None = Field(default=None, min_length=2, max_length=2, description="PCB outline [x, y], excluding connector protrusions; size remains the overall envelope.")
    envelope_offset: Vector = Field(default_factory=lambda: [0.0, 0.0, 0.0], min_length=3, max_length=3, description="Box/custom envelope bottom-face centre relative to the component frame; preserves board/mount coordinates for asymmetric protrusions. Leave zero for cylinders.")
    axis: Literal["x", "y", "z"] = "z"
    mass: Quantity
    material: str = ""
    density: Quantity | None = None
    color: str = "#888888"
    mount_holes: list[Vector] = Field(default_factory=list)
    keepout_margin: Quantity = 0.002
    moving: bool = Field(default=False, description="Part rotates or sweeps volume (wheels, gears).")
    fabricated: bool = Field(default=False, description="Made for this robot: mass from geometry x density, holes and bores "
                                                        "derived from the fasteners and interfaces that use it.")
    solids: list[Solid] = Field(default_factory=list, description="Custom geometry; empty means the box/cylinder envelope.")
    features: list[Feature] = Field(default_factory=list, description="Datasheet interfaces realized on the envelope.")
    proxy_holes_from: list[str] = Field(default_factory=list, description="Explicit hole-pattern interfaces used by a "
                                      "missing-model proxy. Never modifies an imported purchased model.")
    provenance: Provenance = Field(default_factory=lambda: Provenance(status="estimated"))


class BehaviorModel(_Model):
    """Reference to a simulation behavior model and its parameters.

    ``model`` names an implementation registered by the simulation packages
    (e.g. ``battery.lipo``, ``motor.dc_brushed``, ``esc.brushed``,
    ``regulator.buck``, ``load.computer``, ``servo.pwm``, ``receiver.crsf``,
    ``gps.nmea``, ``imu.mpu6050``, ``power_monitor.ina226``, ``encoder.hall``,
    ``fuse.blade``, ``switch.toggle``, ``board.pcb``, ``passive``).
    """

    model: str
    params: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance = Field(default_factory=lambda: Provenance(status="estimated"))


class SoftwareInterface(_Model):
    bus: Literal["i2c", "spi", "uart", "pwm", "gpio", "usb", "none"] = "none"
    address: int | None = None
    protocol: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class InternalLink(_Model):
    """Signal or power continuity between two pins inside a component, through
    a non-ideal element (series resistor, fuse, switch, shunt). Pins that share
    a name are the same node and need no link.

    ``failsafe_arbiter``: an active signal element. ``b`` follows ``a`` only while
    the board's heartbeat pin toggles; otherwise ``b`` carries neutral pulses
    (``value`` = heartbeat timeout in s; model ``r3_kicad.arbiter.FailsafeArbiter``).
    It carries the logic signal like a series resistor but conducts no DC."""

    a: str
    b: str
    kind: Literal["series_resistor", "fuse", "switch", "shunt", "wire", "level_shifter", "failsafe_arbiter"]
    value: Quantity = Field(default=0.0, description="Resistance (ohm), fuse rating (A) or arbiter heartbeat timeout (s).")


InterfaceKind = Literal[
    "face_mount", "hole_pattern", "shaft", "bore", "spline", "header", "socket",
    "ball_stud", "ball_socket", "strap_seat", "strap_slot", "adhesive_pad", "connector_body",
]


class Interface(_Model):
    """A named mating feature of a component, in the component frame.

    ``z`` is the outward normal of a seat face (or the shaft/bore axis pointing
    out of the part), ``x`` the clocking reference, ``origin`` the seat point.
    ``params`` depend on ``kind`` (see :mod:`r3_model.assembly`), e.g.
    ``holes: [[x, y, d, clearance|tapped|thread, depth]]`` in the interface frame."""

    kind: InterfaceKind
    origin: Vector = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    z: Vector = Field(default_factory=lambda: [0.0, 0.0, 1.0])
    x: Vector = Field(default_factory=lambda: [1.0, 0.0, 0.0])
    params: dict[str, Any] = Field(default_factory=dict)
    description: str = ""

    @model_validator(mode="after")
    def _check_frame(self) -> "Interface":
        for name, vector in (("origin", self.origin), ("z", self.z), ("x", self.x)):
            if len(vector) != 3:
                raise ValueError(f"interface {name} must have 3 components")
        z_norm = sum(v * v for v in self.z) ** 0.5
        x_norm = sum(v * v for v in self.x) ** 0.5
        if z_norm < 1e-12 or x_norm < 1e-12:
            raise ValueError("interface z and x axes must be non-zero")
        cosine = sum(a * b for a, b in zip(self.z, self.x)) / (z_norm * x_norm)
        if abs(cosine) > 1e-6:
            raise ValueError(f"interface x axis must be perpendicular to z (cos = {cosine:.3g})")
        return self


class Placement(_Model):
    """A frame expressed in a parent frame: ``origin`` and the ``x``/``z`` axes
    (``y = z x x``). Lengths in metres."""

    origin: Vector = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    x: Vector = Field(default_factory=lambda: [1.0, 0.0, 0.0])
    z: Vector = Field(default_factory=lambda: [0.0, 0.0, 1.0])

    @model_validator(mode="after")
    def _check(self) -> "Placement":
        x_norm = sum(v * v for v in self.x) ** 0.5
        z_norm = sum(v * v for v in self.z) ** 0.5
        if x_norm < 1e-12 or z_norm < 1e-12 or abs(sum(a * b for a, b in zip(self.x, self.z))) > 1e-6 * x_norm * z_norm:
            raise ValueError("placement x and z must be non-zero and perpendicular")
        return self


class ModelRef(_Model):
    """A 3D model of a part and where its frame sits in the owner's frame.

    ``source``: ``kicad`` (official KiCad 3D library, ``path`` uses
    ``${KICAD10_3DMODEL_DIR}``), ``vendor`` (manufacturer STEP fetched from
    ``url``, verified against ``sha256``, ``path`` names the file inside the
    archive), ``kicad_board`` (STEP exported by the KiCad stage, ``path``
    relative to the project) or ``proxy`` (parametric body from datasheet
    dimensions, built by ``r3_connectors.proxy`` from ``params``).
    ``placement`` is the model frame expressed in the owner frame; ``units`` is
    the model's length unit."""

    source: Literal["kicad", "vendor", "kicad_board", "proxy"]
    path: str = ""
    url: str = ""
    sha256: str = ""
    license: str = ""
    units: Literal["mm", "m"] = "mm"
    placement: Placement = Field(default_factory=Placement)
    params: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance = Field(default_factory=lambda: Provenance(status="datasheet"))


class ComponentType(_Model):
    id: str
    kind: Literal[
        "computer", "battery", "motor", "esc", "servo", "regulator", "sensor",
        "radio", "pcb", "structure", "wheel", "fuse", "switch", "gearbox", "antenna", "camera", "cable",
    ]
    name: str
    manufacturer: str = ""
    part_number: str = ""
    description: str = ""
    mechanical: Mechanical
    pins: dict[str, Pin] = Field(default_factory=dict)
    connectors: dict[str, ConnectorInstance] = Field(default_factory=dict)
    interfaces: dict[str, Interface] = Field(default_factory=dict, description="Mating features for the assembly compiler.")
    cad_name: str = Field(default="", description="Short part / Part Studio name in CAD, e.g. 'Steering knuckle'.")
    cad_family: str = Field(default="", description="Part Studio shared by related types (e.g. left/right axles); default cad_name.")
    internal_links: list[InternalLink] = Field(default_factory=list)
    behavior: BehaviorModel | None = None
    software: list[SoftwareInterface] = Field(default_factory=list)
    thermal: dict[str, Any] = Field(default_factory=dict, description="theta_ja_c_per_w, max_temp_c, ...")
    model: ModelRef | None = Field(default=None, description="Detailed 3D model (vendor STEP or the KiCad board export).")
    provenance: Provenance = Field(default_factory=lambda: Provenance(status="estimated"))


class ConnectorPin(_Model):
    """One contact in the connector frame (metres).

    ``position`` is the contact axis on the mating face, ``axis`` the contact
    direction (toward the mating half, normally +z). ``tip`` is how far the
    contact reaches beyond the mating face along ``axis`` (negative when
    recessed). ``pad`` is the PCB pad centre for board-side parts (checked
    against the KiCad footprint). ``wire_entry``/``wire_axis`` give where the
    conductor leaves a cable-side half (crimp barrel or solder cup exit) and the
    direction it leaves in."""

    position: Vector
    axis: Vector = Field(default_factory=lambda: [0.0, 0.0, 1.0])
    tip: Quantity = 0.0
    pad: Vector | None = None
    wire_entry: Vector | None = None
    wire_axis: Vector | None = None


class ConnectorGeometry(_Model):
    """Exact geometry of a connector type.

    Connector frame: origin at the centre of the contact array on the mating
    face, z normal to the mating face pointing toward the mating half, x along
    the pin row (pin 1 toward the last pin). Two mated halves have coincident
    origins, opposite z and equal x (the partner frame is this one turned 180
    degrees about x), so pin n meets pin n. ``footprint`` names the KiCad
    footprint whose pads the pin map was verified against and
    ``footprint_placement`` the footprint frame (KiCad 3D axes: x right, y up,
    z out of the board's component side) in the connector frame. ``envelope``
    is the body box [x, y, z] behind and around the mating face, ``depth`` the
    body length behind the face (wire side) and ``reach`` its extent in front."""

    model: ModelRef
    footprint: str = ""
    footprint_placement: Placement | None = None
    pins: dict[str, ConnectorPin]
    pin_shape: Literal["square_post", "socket", "blade", "crimp", "bullet", "solder_cup", "coax"] = "square_post"
    pin_size: Quantity = 0.00064
    envelope: Vector | None = None
    depth: Quantity = 0.0
    reach: Quantity = 0.0
    color: str = "#202020"
    contact_color: str = "#d4a640"
    provenance: Provenance = Field(default_factory=lambda: Provenance(status="derived"))


class ConnectorType(_Model):
    id: str
    family: str
    gender: Literal["male", "female", "header", "socket", "none"]
    mates_with: list[str]
    pin_count: int
    current_rating_per_pin: Quantity = Field(description="Continuous rating at 25 C ambient.")
    voltage_rating: Quantity
    contact_resistance: Quantity = Field(description="Per mated contact.")
    awg_min: int = Field(description="Thickest supported wire (smallest AWG number).")
    awg_max: int = Field(description="Thinnest supported wire.")
    mating_cycles: int | None = None
    locking: bool = False
    body_size: Vector = Field(default_factory=lambda: [0.01, 0.01, 0.01])
    geometry: ConnectorGeometry | None = None
    provenance: Provenance = Field(default_factory=lambda: Provenance(status="estimated"))


class WireGauge(_Model):
    awg: int
    conductor_diameter: Quantity
    outer_diameter: Quantity = Field(description="Silicone-insulated outer diameter.")
    resistance_per_m: Quantity = Field(description="At 20 C, stranded copper.")
    ampacity: Quantity = Field(description="Continuous, single conductor in free air, silicone 150 C insulation.")
    mass_per_m: Quantity
    min_bend_radius_factor: float = Field(default=4.0, description="Minimum bend radius / outer diameter (static).")
    bending_stiffness: Quantity = Field(description="EI estimate (N*m^2) for slack simulation.")
    provenance: Provenance = Field(default_factory=lambda: Provenance(status="estimated"))


FastenerKind = Literal["socket_head_screw", "button_head_screw", "ball_stud", "nut", "standoff_mf", "heat_set_insert"]


class FastenerType(_Model):
    """Purchased fastener dimensions (ISO metric coarse threads).

    ``minor_diameter`` is the internal-thread minor diameter D1 = d - 1.0825 P
    (ISO 724); ``tap_drill`` the drill a tapped hole is cut with (the modeled hole
    diameter). The screw shank is modeled at the major diameter ``d``, so the
    screw/tapped-part intersection volume is pi/4 (d^2 - tap_drill^2) x engagement.
    Heads: ``head_diameter`` dk, ``head_height`` k. Nuts: ``across_flats`` s and
    ``height`` m (modeled as a hexagonal prism). Ball studs: ``collar_*`` (hex
    collar modeled as a cylinder), ``neck_*`` and ``ball_diameter``. Male-female
    standoffs: hex ``across_flats`` x ``height`` body, male ``thread_length``,
    female ``female_depth``. Clearance holes follow ISO 273 (close/normal/coarse).
    """

    id: str
    kind: FastenerKind
    name: str
    standard: str = ""
    thread: str = Field(description="e.g. M3")
    d: Quantity
    pitch: Quantity
    minor_diameter: Quantity
    tap_drill: Quantity
    head_diameter: Quantity | None = None
    head_height: Quantity | None = None
    across_flats: Quantity | None = None
    height: Quantity | None = None
    collar_diameter: Quantity | None = None
    collar_height: Quantity | None = None
    neck_diameter: Quantity | None = None
    neck_length: Quantity | None = None
    ball_diameter: Quantity | None = None
    thread_length: Quantity | None = None
    female_depth: Quantity | None = None
    lengths: list[Quantity] = Field(default_factory=list, description="Standard nominal lengths.")
    clearance_close: Quantity | None = None
    clearance_normal: Quantity | None = None
    clearance_coarse: Quantity | None = None
    material: str = "Steel 10.9"
    density: Quantity = 7850.0
    color: str = "#30343a"
    provenance: Provenance = Field(default_factory=lambda: Provenance(status="datasheet"))


class PrintFit(_Model):
    """Fit allowances of one additive process (diametral values, m).

    Printed holes and bores shrink and printed pins grow, so ISO 286 metal fits
    do not apply. ``hole_clearance``: screw clearance hole diameter minus the
    nominal screw diameter; ``sliding_clearance``: bore minus shaft for a running
    or sliding fit; ``press_interference``: shaft minus bore for a press fit (a
    range); ``cap_clearance``: radial gap between a screw head and the inner wall
    of a printed cap (cap inner diameter = head diameter + 2 x cap_clearance)."""

    process: Literal["fdm", "sls"]
    description: str = ""
    materials: list[str] = Field(default_factory=list, description="Lower-case material keywords printed with this process.")
    hole_clearance: Vector = Field(description="[min, max] diametral clearance over the nominal screw diameter.")
    sliding_clearance: Vector
    press_interference: Vector
    cap_clearance: Quantity
    provenance: Provenance = Field(default_factory=lambda: Provenance(status="estimated"))


class Material(_Model):
    """An engineering material of fabricated parts (FDM filaments first).

    Strengths are ultimate tensile strengths of printed test bars (ISO 527 /
    ASTM D638) in the XY (in-layer) direction; ``tensile_strength_z`` is across
    layers, the weak direction of an FDM part. ``hdt`` is the heat deflection
    temperature at 0.45 MPa (ISO 75-2 B)."""

    id: str
    name: str
    process: Literal["fdm", "sls", "machined"] = "fdm"
    keywords: list[str] = Field(default_factory=list, description="Lower-case words that identify the material in a catalog "
                                                                  "`mechanical.material` string (e.g. petg).")
    density: Quantity
    tensile_strength: Quantity = Field(description="XY ultimate tensile strength (Pa).")
    tensile_strength_z: Quantity | None = Field(default=None, description="Z (interlayer) tensile strength (Pa); default "
                                                                          "z_strength_factor x tensile_strength.")
    z_strength_factor: float = Field(default=0.5, description="Interlayer/in-layer strength ratio when Z is not published.")
    youngs_modulus: Quantity
    poisson_ratio: float = 0.35
    elongation_at_break: float | None = Field(default=None, description="Fraction, XY.")
    hdt: Quantity | None = Field(default=None, description="Heat deflection temperature at 0.45 MPa (deg C).")
    max_service_temp_c: float | None = None
    nozzle_temp_c: Vector | None = None
    bed_temp_c: Vector | None = None
    enclosure: bool = False
    drying: str = ""
    color: str = "#888888"
    notes: str = ""
    provenance: Provenance = Field(default_factory=lambda: Provenance(status="datasheet"))

    @property
    def z_strength(self) -> float:
        return float(self.tensile_strength_z if self.tensile_strength_z is not None else self.z_strength_factor * self.tensile_strength)


class Printer(_Model):
    """A 3D printer: printable volume [x, y, z] (m) and default nozzle."""

    id: str
    name: str
    manufacturer: str = ""
    process: Literal["fdm", "sls"] = "fdm"
    build_volume: Vector
    nozzle: Quantity = 0.0004
    layer_height: Quantity = 0.0002
    enclosed: bool = False
    max_nozzle_temp_c: float | None = None
    max_bed_temp_c: float | None = None
    provenance: Provenance = Field(default_factory=lambda: Provenance(status="datasheet"))


class Catalog(_Model):
    components: dict[str, ComponentType] = Field(default_factory=dict)
    connectors: dict[str, ConnectorType] = Field(default_factory=dict)
    wires: dict[int, WireGauge] = Field(default_factory=dict)
    fasteners: dict[str, FastenerType] = Field(default_factory=dict)
    print_fits: dict[str, PrintFit] = Field(default_factory=dict)
    materials: dict[str, Material] = Field(default_factory=dict)
    printers: dict[str, Printer] = Field(default_factory=dict)

    def material(self, text: str) -> Material | None:
        """The material entry for a material id or a catalog ``mechanical.material`` string (None if unknown)."""
        if text in self.materials:
            return self.materials[text]
        lowered = text.lower()
        # Whole words only ("plastic" is not PLA); the longest keyword wins ("pa6-cf" over "pa").
        best: tuple[int, Material] | None = None
        for material in self.materials.values():
            for word in material.keywords:
                if re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9+])", lowered) and (best is None or len(word) > best[0]):
                    best = (len(word), material)
        return best[1] if best else None

    def print_fit(self, material: str) -> PrintFit | None:
        """The print-fit table entry for a printed material (None for machined parts)."""
        text = material.lower()
        for fit in self.print_fits.values():
            if any(re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text) for word in fit.materials):
                return fit
        return None

    def fastener(self, type_id: str) -> FastenerType:
        try:
            return self.fasteners[type_id]
        except KeyError:
            raise KeyError(f"fastener type {type_id!r} not in catalog") from None

    def component(self, type_id: str) -> ComponentType:
        try:
            return self.components[type_id]
        except KeyError:
            raise KeyError(f"component type {type_id!r} not in catalog") from None

    def connector(self, type_id: str) -> ConnectorType:
        try:
            return self.connectors[type_id]
        except KeyError:
            raise KeyError(f"connector type {type_id!r} not in catalog") from None

    def wire(self, awg: int) -> WireGauge:
        try:
            return self.wires[awg]
        except KeyError:
            raise KeyError(f"wire gauge AWG {awg} not in catalog") from None

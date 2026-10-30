"""Declarative robot specification (``robot.yaml``, schema ``r3/robot@1``).

Conventions
-----------
* Units: SI floats, or strings with explicit units (see :mod:`r3_model.units`).
* Robot frame: +x forward, +y left, +z up. The origin is the project-defined design datum. Rotations are roll/pitch/yaw (rad) applied as R = Rz·Ry·Rx.
* Identities: component ids, connector ids, harness ids and wire ids are stable
  strings. Pin references are ``<component>.<connector>.<pin>`` for component
  connectors and ``<harness>.<connector>.<pin>`` for cable-side connectors.
* The spec is the single source of truth; the component/net graph is derived
  (:mod:`r3_model.graph`) and external revisions are pinned in ``r3.lock.yaml``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic_core import PydanticCustomError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .units import Quantity, Vector

SCHEMA_ID = "r3/robot@1"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Pose(_Model):
    xyz: Vector = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    rpy: Vector = Field(default_factory=lambda: [0.0, 0.0, 0.0])


class RobotInfo(_Model):
    mobility: Literal["mobile", "stationary"] | None = Field(default=None, description="Base mobility; inferred from a positive top_speed when omitted.")
    name: str
    description: str = ""
    version: str = "0.1.0"


class VibrationProfile(_Model):
    """Tailored MIL-STD-810H 514.8 Annex C wheeled-vehicle random profile.

    R3's small-vehicle default is an estimate, not a qualification spectrum.
    PSD breakpoints are Hz and (m/s²)²/Hz; three axes run consecutively.
    """

    duration: Quantity = 60.0
    psd: list[list[float]] = Field(default_factory=lambda: [[5.0, 0.05], [20.0, 0.2], [80.0, 0.2], [200.0, 0.02]])
    ramp: Quantity = Field(default=1.0, gt=0, description="Smooth vibration start/stop envelope (s).")
    motor_frequency: Quantity = 120.0
    motor_acceleration: Quantity = 1.0
    source: str = "MIL-STD-810H Method 514.8 Annex C; R3 estimated small-vehicle tailoring"
    estimated: bool = True

    @model_validator(mode="after")
    def _valid_spectrum(self) -> "VibrationProfile":
        if self.duration <= 0 or self.motor_frequency <= 0 or self.motor_acceleration < 0:
            raise ValueError("vibration duration/frequency must be positive and acceleration nonnegative")
        if len(self.psd) < 2 or any(len(p) != 2 or p[0] <= 0 or p[1] < 0 for p in self.psd):
            raise ValueError("vibration PSD needs at least two positive-frequency, nonnegative-density points")
        if any(a[0] >= b[0] for a, b in zip(self.psd, self.psd[1:])):
            raise ValueError("vibration PSD frequencies must increase")
        return self


class Requirements(_Model):
    """Acceptance criteria checked by validation and simulation scenarios."""

    drop_height: Quantity = Field(default=1.0, ge=0, description="Drop clearance above ground, each face and corner (m).")
    impact_speed: Quantity | None = Field(default=None, ge=0, description="Wall impact speed (m/s); default requirements.top_speed.")
    vibration: VibrationProfile = Field(default_factory=VibrationProfile)
    max_mass: Quantity | None = None
    top_speed: Quantity | None = None
    min_runtime: Quantity | None = Field(default=None, description="Mission runtime at the scenario duty cycle.")
    radio_range: Quantity | None = Field(default=None, description="Distance with positive link margin.")
    failsafe_stop_distance: Quantity | None = Field(
        default=None, description="Stop distance after a failsafe (link loss, hung software) at the autonomous maximum cruise speed.")
    failsafe_stop_distance_at_top_speed: Quantity | None = Field(
        default=None, description="Radio-failsafe stop distance from top speed (manual driving).")
    max_wire_voltage_drop_fraction: float = 0.03
    min_wire_clearance_moving: Quantity = 0.008
    max_component_temperature_c: float = 85.0
    extra: dict[str, Any] = Field(default_factory=dict)


class SourceSpec(_Model):
    """A component slot declared by requirements and sourced online (``r3 source``).

    ``requires`` are hard filters keyed by parameter name. Values are a
    quantity (``2.4 GHz``, matched within a small tolerance), a range
    (``[4.5 V, 5.5 V]``), a bound (``">= 4 GB"``), a tagged size (``12 mm hex``)
    or a plain string (``crsf``); ``kind`` is the catalog kind. ``prefer`` holds
    soft preferences (``price: low``, ``mass: low``, ``vendor: <name>``, or a
    parameter target). The chosen part is pinned in ``r3.lock.yaml`` under
    ``sourcing.<group or component id>`` and becomes a project catalog entry;
    until then ``type`` (if given) is used.
    """

    requires: dict[str, Any] = Field(description="Hard requirements; must include `kind`.")
    prefer: dict[str, Any] = Field(default_factory=dict, description="Soft preferences used in ranking.")
    quantity: int | None = Field(default=None, ge=1, description="Units to buy (default: components sharing the slot).")
    budget: str | float | None = Field(default=None, description="Total budget for `quantity` units, e.g. '120 USD'.")
    providers: list[str] = Field(default_factory=list, description="Restrict the search to these provider ids.")
    group: str | None = Field(default=None, description="Slot name shared by several components (one choice, one lock entry).")

    @model_validator(mode="after")
    def _check_kind(self) -> "SourceSpec":
        if not isinstance(self.requires.get("kind"), str) or not self.requires["kind"]:
            raise ValueError("source.requires must name the catalog `kind` (e.g. wheel, radio, computer)")
        return self


class OwnedSpec(_Model):
    """A part already available to the builder."""

    note: str = Field(default="", description="Optional inventory or provenance note.")


class ComponentInstance(_Model):
    type: str = Field(default="", description="Catalog id, e.g. raspberry-pi-5-8gb. Optional when `source` is given; "
                                              "a pinned sourcing choice replaces it.")
    source: SourceSpec | None = Field(default=None, description="Requirements for sourcing this part online.")
    owned: bool | OwnedSpec = Field(default=False, description="Already owned; excluded from purchases and sourcing.")

    @model_validator(mode="after")
    def _check_owned(self) -> "ComponentInstance":
        if self.owned and self.source is not None:
            raise PydanticCustomError("owned_source_conflict", "An owned component cannot declare source")
        return self

    mount: str = Field(default="world", description="Parent component id or 'world'.")
    pose: Pose = Field(default_factory=Pose, description="Pose of the component frame in the robot frame.")
    params: dict[str, Any] = Field(default_factory=dict, description="Overrides of catalog parameters.")
    label: str = ""


class Joint(_Model):
    id: str
    type: Literal["revolute", "continuous", "prismatic", "fixed"]
    parent: str
    child: str
    origin: Vector = Field(description="Joint origin in the robot frame (m).")
    axis: Vector = Field(default_factory=lambda: [0.0, 0.0, 1.0])
    limits: Vector | None = Field(default=None, description="[lower, upper] rad or m.")
    damping: Quantity = 0.0
    friction: Quantity = 0.0
    interface: str | None = Field(default=None, description="Mechanical interface id that physically realizes this joint. "
                                                            "Required for revolute/continuous/prismatic joints once the spec "
                                                            "declares mechanical.interfaces.")


class Transmission(_Model):
    """Couples an actuator component to joints."""

    id: str
    type: Literal["differential", "direct", "ackermann_steering"]
    actuator: str = Field(description="Component id of motor or servo.")
    joints: list[str]
    ratio: float = 1.0
    efficiency: float = 1.0
    params: dict[str, Any] = Field(default_factory=dict)
    path: list[str] = Field(default_factory=list, description="Ordered interface (or fastener) ids from the actuator to the driven "
                                                              "joints. Each link after the first shares a part with an earlier one.")


class Fastener(_Model):
    """One purchased fastener (screw, ball stud, stacking standoff, or a nut on a
    male thread) placed in the robot frame.

    ``position`` is the head seat point (underside of the head, or the bearing
    face of a nut) and ``axis`` the insertion direction (head toward tip).
    ``clamps`` lists the clamped parts in stack order, head side first;
    ``thread_into`` is the part with the tapped hole (or, for a nut type, the
    part carrying the male thread). ``nut`` names a catalog nut that ends a
    through-bolt; it becomes the part ``nut:<id>``. ``preloaded: false`` marks a
    pivot screw (e.g. a kingpin) that is threadlocked without clamping, so its
    head must clear the parts it retains.
    """

    id: str
    type: str = Field(description="Catalog fastener id (fasteners.yaml).")
    length: Quantity | None = Field(default=None, description="Nominal length (screws, studs); a catalog standard length.")
    position: Vector
    axis: Vector = Field(default_factory=lambda: [0.0, 0.0, -1.0])
    clamps: list[str] = Field(default_factory=list, description="Clamped parts in stack order, head side first.")
    thread_into: str | None = Field(default=None, description="Part with the tapped hole (or male thread for nuts).")
    nut: str | None = Field(default=None, description="Catalog nut id when the fastener ends in a nut.")
    grip: Quantity | None = Field(default=None, description="Clamp length from the head seat to the nut seat (through bolts).")
    torque: Quantity | None = Field(default=None, ge=0, description="Assembly torque in N m; catalog estimate when omitted.")
    locking: str | None = Field(default=None, description="nyloc, threadlocker, lock_washer, or locking_cap; explicit locking feature.")
    preloaded: bool = True
    body: str | None = Field(default=None, description="Component that is this fastener (a kingpin screw, ball stud, stacking "
                                                       "standoff or nut modeled as an assembly object); default: its own part.")
    join: str | None = Field(default=None, description="Assembly join this fastener was derived from.")

    @property
    def part(self) -> str:
        return self.body or f"fastener:{self.id}"

    @property
    def nut_part(self) -> str | None:
        return f"nut:{self.id}" if self.nut else None


InterfaceKind = Literal["revolute_fit", "press_fit", "drive_flat", "ball_joint", "spline", "adhesive", "strap",
                        "magnetic_target", "connector_retention", "snap_fit", "resting"]


class Interface(_Model):
    """A physical mechanical interface between two parts, verified on exact CAD geometry.

    ``parts`` is ``[a, b]``; for shaft/bore kinds ``a`` carries the shaft (or
    ball, magnet, male pins) and ``b`` the bore (socket, sensor, female socket).
    ``axis_point``/``axis_dir`` locate the interface axis in the robot frame.
    ``params`` holds the kind-specific limits in SI units, e.g. ``clearance``
    ``[min, max]`` radial, ``min_engagement``, ``retainers`` (part ids),
    ``flat_offset``, ``min_fraction`` or ``gap``.
    """

    id: str
    kind: InterfaceKind
    parts: list[str] = Field(min_length=2, max_length=2)
    axis_point: Vector | None = None
    axis_dir: Vector | None = None
    frame: str | None = Field(default=None, description="Component whose frame axis_point/axis_dir are written in (default: robot "
                                                        "frame). Resolved to the robot frame once the assembly is compiled.")
    params: dict[str, Any] = Field(default_factory=dict)
    join: str | None = Field(default=None, description="Assembly join this interface was derived from.")


class Mechanical(_Model):
    joints: list[Joint] = Field(default_factory=list)
    transmissions: list[Transmission] = Field(default_factory=list)
    fasteners: list[Fastener] = Field(default_factory=list)
    interfaces: list[Interface] = Field(default_factory=list)


class HarnessConnector(_Model):
    """A cable-side connector. ``mates`` names the component connector it plugs
    into (``<component>.<connector>``); omit for splices or loose ends."""

    type: str
    mates: str | None = Field(default=None, description="<component|harness>.<connector> this plugs into.")
    at: str | None = Field(default=None, description="Partial header mate: component pin receiving housing pin 1; row axis unchanged.")
    position: Vector | None = Field(default=None, description="Free-end position when not mated (robot frame).")
    direction: Vector | None = Field(default=None, min_length=3, max_length=3,
                                    description="Wire exit axis for a floating in-line connector half (robot frame).")

    @model_validator(mode="after")
    def _exit_axis(self) -> "HarnessConnector":
        if self.direction is not None and sum(v * v for v in self.direction) < 1e-12:
            raise ValueError("connector direction must be non-zero")
        return self

    @model_validator(mode="after")
    def _check_site(self) -> "HarnessConnector":
        if self.at is not None and self.mates is None:
            raise ValueError("at requires mates")
        return self


class Splice(_Model):
    position: Vector


class Wire(_Model):
    id: str
    from_: str = Field(alias="from", description="Pin ref: <harness_connector>.<pin> or splice id.")
    to: str
    awg: int
    color: str = ""
    signal: str = Field(default="", description="Human-readable signal name, e.g. VBAT, CRSF_TX.")
    length: Quantity | None = Field(default=None, description="Cut length; default = routed length + slack.")


class RouteHint(_Model):
    via: list[Vector] = Field(default_factory=list, description="Ordered waypoints in the robot frame.")
    wire_via: dict[str, list[Vector]] = Field(default_factory=dict, description="Branch waypoints keyed by a wire ID, "
                                             "ordered from that wire's from terminal to its to terminal. Wires joining "
                                             "the same terminal pair share these waypoints.")
    tie_points: list[Vector] = Field(default_factory=list, description="Requested bundle centres at real cable supports; "
                                   "placement validates mounting hardware, access and retention before pinning the bundle.")
    slack: Quantity = Field(default=0.015, description="Extra length added beyond the routed path.")
    keep_away: list[str] = Field(default_factory=list, description="Component ids to route around.")


class Harness(_Model):
    id: str
    description: str = ""
    connectors: dict[str, HarnessConnector] = Field(default_factory=dict)
    splices: dict[str, Splice] = Field(default_factory=dict)
    wires: list[Wire]
    route: RouteHint = Field(default_factory=RouteHint)
    jacket: str = Field(default="none", description="none | braided-sleeve | heat-shrink")

    @model_validator(mode="after")
    def _branch_waypoints(self) -> "Harness":
        wires = {wire.id: wire for wire in self.wires}
        branches: dict[tuple[str, str], list[Vector]] = {}
        for wire_id, points in self.route.wire_via.items():
            if wire_id not in wires:
                raise ValueError(f"wire_via names unknown wire {wire_id!r} in {self.id}")
            wire = wires[wire_id]
            a, b = (ref if ref in self.splices else ref.split(".")[0] for ref in (wire.from_, wire.to))
            key = tuple(sorted((a, b)))
            ordered = points if a <= b else points[::-1]
            if key in branches and branches[key] != ordered:
                raise ValueError(f"wire_via has conflicting waypoints for branch {key} in {self.id}")
            branches[key] = ordered
        return self


class DirectMate(_Model):
    """Two connectors plugged into each other without a cable (e.g. HAT on a Pi).
    Each side is ``<component|harness>.<connector>``."""

    a: str
    b: str


class Electrical(_Model):
    ground_net: str = "GND"
    harnesses: list[Harness] = Field(default_factory=list)
    direct_mates: list[DirectMate] = Field(default_factory=list)


class Board(_Model):
    """A custom PCB designed in an electronics tool (KiCad by default)."""

    component: str = Field(description="Component instance that this board realizes.")
    tool: Literal["kicad"] = "kicad"
    source: str = Field(description="Project-relative directory of the board sources/generator.")
    layers: int = 2
    copper_oz: float = 1.0
    thickness: Quantity = 0.0016


class Pcb(_Model):
    boards: list[Board] = Field(default_factory=list)


class Cad(_Model):
    provider: Literal["onshape", "none"] = "onshape"
    generator: str | None = Field(default=None, description="Project-relative FeatureScript/CAD source.")
    folder: str | None = Field(default=None, description="Provider folder id where documents are created.")
    document_name: str | None = None
    parts: dict[str, str] = Field(default_factory=dict, description="component id -> CAD part name.")
    library_name: str = Field(default="R3 Parts Library", description="Reusable document holding purchased parts and fasteners.")
    subassemblies: dict[str, list[str]] = Field(
        default_factory=dict, description="Subassembly name -> component ids; the rest sit in the top-level assembly.")


class HardwareBinding(_Model):
    """Maps a software-visible handle to a physical interface on a computer."""

    interface: Literal["gpio", "pwm", "i2c", "spi", "uart", "csi"]
    computer: str
    pins: list[str] = Field(description="Computer pin refs <component>.<connector>.<pin>.")
    device: str = Field(default="", description="Linux device or chip, e.g. /dev/ttyAMA0, pwmchip0, gpiochip0, /dev/i2c-1; "
                                                "csi: the camera port (cam0, cam1).")
    channel: int | None = Field(default=None, description="PWM channel, GPIO line, SPI chip-select.")
    baud: int | None = None
    frequency: Quantity | None = None
    expects: list[str] = Field(default_factory=list, description="Device pin refs this binding must reach electrically.")


class SoftwareModule(_Model):
    name: str
    language: Literal["python", "cpp", "rust"] = "python"
    runs_on: str = Field(description="Computer component id.")
    entrypoint: str = ""


class Software(_Model):
    modules: list[SoftwareModule] = Field(default_factory=list)
    bindings: dict[str, HardwareBinding] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict, description="Runtime config passed to modules.")


class RadioEndpoint(_Model):
    position: Vector
    power_dbm: float
    antenna_gain_dbi: float = 2.0
    height: Quantity | None = None


class GnssBase(_Model):
    """RTK reference station in the scenario world: a GNSS receiver in fixed-base mode streaming RTCM3
    corrections through a telemetry radio to the robot's rover (both catalog types)."""

    receiver: str = Field(description="Catalog id of the base GNSS receiver (behavior gps.rtk), e.g. gnss-rtk-zed-f9p.")
    radio: str = Field(description="Catalog id of the base correction radio (behavior telemetry.sik).")
    position: Vector = Field(description="Base antenna phase centre in the local ENU world frame (m).")
    radio_antenna_position: Vector | None = Field(default=None, description="Radio antenna in the world frame; default "
                                                                             "0.3 m below the GNSS antenna on the same mast.")
    survey_error: Vector = Field(default_factory=lambda: [0.0, 0.0, 0.0],
                                 description="Error of the base's fixed (surveyed) coordinates, ENU (m); shifts every RTK solution.")
    rtcm: dict[str, Any] = Field(default_factory=dict, description="RTCM3 output: {messages: {1005: 0.1 Hz, 1074: 1 Hz, ...}}.")
    correction_link: dict[str, Any] = Field(default_factory=dict, description="Correction radio link overrides, e.g. "
                                                                               "power_dbm, interference_dbm, air_rate.")


class Environment(_Model):
    gravity: Quantity = 9.80665
    ambient_temperature_c: float = 25.0
    ground_friction: float = 0.9
    ground_rolling_resistance: float = 0.015
    terrain: str = "flat"
    obstacles: list[dict[str, Any]] = Field(default_factory=list, description="Static boxes: {id, center, size, material}.")
    radio_transmitter: RadioEndpoint | None = None
    radio_environment: dict[str, Any] = Field(default_factory=dict)
    visual: dict[str, Any] = Field(default_factory=dict, description=(
        "What cameras see (r3_sim.vehicle.scene_visual): ground (grass|asphalt|concrete|checker), size, "
        "track {points|from: mission, width, line_width, edge_color, center_line}, cones [{position, color}] "
        "or cone_spacing along the track edges. Visual only unless cones set collide: true."))
    gnss_base: GnssBase | None = Field(default=None, description="RTK base station and its correction link.")


class Scenario(_Model):
    id: str
    description: str = ""
    duration: Quantity
    initial: dict[str, Any] = Field(default_factory=dict, description="e.g. battery_soc, pose.")
    mission: dict[str, Any] = Field(default_factory=dict)
    pilot: dict[str, Any] = Field(default_factory=dict, description="Scripted transmitter stick inputs.")
    faults: list[dict[str, Any]] = Field(default_factory=list)
    expect: dict[str, Any] = Field(default_factory=dict, description="Pass criteria evaluated on the recording.")


class Simulation(_Model):
    base_mount: Literal["auto", "free", "fixed"] = Field(default="auto", description="auto fixes a stationary base to the world; drop cases release the test fixture.")
    joint_failure_replay_duration: Quantity | None = Field(default=2.0, gt=0, description="Seconds of fully coupled replay after the first irreversible joint failure; null runs the full failed scenario. Truncated evidence always fails.")
    timestep: Quantity = 0.001
    physics_max_timestep: Quantity = Field(default=0.00025, description="Maximum native MuJoCo step; master steps are subdivided without changing software/electrical timing.")

    @field_validator("timestep", "physics_max_timestep")
    @classmethod
    def positive_timestep(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("simulation timesteps must be positive")
        return value

    @property
    def physics_substeps(self) -> int:
        import math
        return max(1, math.ceil(float(self.timestep) / float(self.physics_max_timestep)))

    @property
    def physics_timestep(self) -> float:
        return float(self.timestep) / self.physics_substeps

    seed: int = 1
    environment: Environment = Field(default_factory=Environment)
    scenarios: list[Scenario] = Field(default_factory=list)


class Fasteners(_Model):
    """Fasteners that close a ``fasten`` join, e.g. ``{type: "M3x10", count: 2}``.
    ``type`` is ``M<d>x<length>`` (ISO metric, length under the head, mm).

    Each matched hole pair becomes one fastener in ``mechanical.fasteners``
    (id ``<join id>`` for a single pair, else ``<join id>_<n>``): a screw
    (``part``, default ``shcs-m<d>``) whose head seats on the far face of the
    clearance side, threading into the tapped side, or a through bolt ending in
    ``nut`` with the head on side ``head``. A stud on an object (``thread``
    hole) or a nut object produces a fastener whose body is that object.
    ``clamps`` adds parts clamped in the stack beyond the two joined objects."""

    type: str
    count: int | None = Field(default=None, description="Default: every matched hole pair.")
    part: str | None = Field(default=None, description="Catalog fastener id (fasteners.yaml); default from the thread size.")
    nut: str | None = Field(default=None, description="Catalog nut id for a through bolt (both holes clearance).")
    head: Literal["a", "b"] = Field(default="a", description="Head side of a through bolt.")
    clamps: list[str] = Field(default_factory=list, description="Further clamped components, in stack order.")
    torque: Quantity | None = Field(default=None, ge=0, description="Assembly torque in N m; catalog estimate when omitted.")
    locking: str | None = Field(default=None, description="nyloc, threadlocker, lock_washer, or locking_cap; explicit locking feature.")
    preloaded: bool = True


class Join(_Model):
    """Two object interfaces mated like puzzle pieces (see :mod:`r3_model.assembly`).

    ``b``'s interface frame is placed on ``a``'s with opposing z axes, aligned x
    axes, then rotated by ``clock`` about ``a``'s z and moved ``offset`` along
    ``a``'s z (a positive offset leaves a gap)."""

    a: str = Field(description="<object>.<interface> on the parent side.")
    b: str = Field(description="<object>.<interface> on the child side.")
    kind: Literal["fasten", "revolute", "slider", "ball", "drive_flat"] = "fasten"
    id: str | None = Field(default=None, description="Stable join id; default '<a>~<b>'.")
    clock: Quantity | None = Field(default=None, description="Rotation about a's z (rad). Omitted: inferred when unique.")
    offset: Quantity = 0.0
    fasteners: Fasteners | None = None
    joint: str | None = Field(default=None, description="mechanical.joints id realized by a moving join.")
    limits: Vector | None = Field(default=None, description="[lower, upper] rad or m for a derived joint.")
    fit: Literal["clearance", "transition", "interference", "any"] | None = Field(
        default=None, description="Required shaft/bore fit; default clearance for revolute/slider, any otherwise.")
    damping: Quantity = 0.0
    friction: Quantity = 0.0
    verify: dict[str, Any] = Field(default_factory=dict, description="Exact-geometry verification limits copied into the derived "
                                                                     "mechanical interface (clearance, min_engagement, retainers, ...).")

    @property
    def join_id(self) -> str:
        return self.id or f"{self.a}~{self.b}"


class AssemblyTolerance(_Model):
    position: Quantity = 5e-5
    angle: Quantity = 0.1 * 3.141592653589793 / 180.0


class Assembly(_Model):
    """Object/interface assembly: poses are derived from joins, never authored
    (except the root's pose in the robot frame)."""

    root: str
    joins: list[Join] = Field(default_factory=list)
    tolerance: AssemblyTolerance = Field(default_factory=AssemblyTolerance)


class LoadCase(_Model):
    """A quasi-static load case for printed structural parts: an acceleration of
    every mass the part carries (``accel`` in g, robot frame, e.g. [0, 0, -5]
    for a 5 g landing, [-10, 0, 0] for a 10 g frontal crash)."""

    id: str
    accel: Vector = Field(description="Acceleration in units of g along robot x, y, z (gravity included by the author).")
    description: str = ""


class Fabrication(_Model):
    """3D printing as a manufacturing process (see ``r3_fab``)."""

    printer: str = Field(default="bambu-x1c", description="Catalog printer id (printers.yaml): bed size and nozzle.")
    material: str | None = Field(default=None, description="Default FDM material id (materials.yaml) for printed parts "
                                                           "whose catalog material is not a printable material.")
    parts: dict[str, dict[str, Any]] = Field(default_factory=dict, description=(
        "Per component overrides: {material, orientation: [rx, ry, rz] deg or 'auto', infill, perimeters, print: false}."))
    load_cases: list[LoadCase] = Field(default_factory=list, description="Default: 5 g bump (z) and 10 g frontal crash (x).")
    min_safety_factor: float = Field(default=2.0, description="Required strength / von Mises stress on printed load paths.")
    max_overhang_deg: float = Field(default=45.0, description="Unsupported overhang limit from vertical.")
    min_wall: Quantity = Field(default=0.0012, description="Minimum printable wall (3 perimeters x 0.4 mm nozzle).")


class RobotSpec(_Model):
    schema_: str = Field(alias="schema", default=SCHEMA_ID)
    robot: RobotInfo
    requirements: Requirements = Field(default_factory=Requirements)
    components: dict[str, ComponentInstance]
    mechanical: Mechanical = Field(default_factory=Mechanical)
    electrical: Electrical = Field(default_factory=Electrical)
    pcb: Pcb = Field(default_factory=Pcb)
    cad: Cad = Field(default_factory=Cad)
    software: Software = Field(default_factory=Software)
    simulation: Simulation = Field(default_factory=Simulation)
    assembly: Assembly | None = None
    fabrication: Fabrication = Field(default_factory=Fabrication)

    @model_validator(mode="after")
    def _check_references(self) -> "RobotSpec":
        if self.schema_ != SCHEMA_ID:
            raise ValueError(f"unsupported schema {self.schema_!r}; expected {SCHEMA_ID}")
        ids = set(self.components)
        for cid, comp in self.components.items():
            if not comp.type and comp.source is None:
                raise ValueError(f"component {cid!r} needs a catalog `type` or a `source` block")
            if comp.mount != "world" and comp.mount not in ids:
                raise ValueError(f"component {cid!r} mounts on unknown component {comp.mount!r}")
        self._check_mount_graph()
        for slot, members in self.source_slots().items():
            first = self.components[members[0]].source
            for cid in members[1:]:
                other = self.components[cid].source
                if (other.requires, other.prefer) != (first.requires, first.prefer):
                    raise ValueError(f"components {members[0]!r} and {cid!r} share sourcing group {slot!r} "
                                     "but declare different requires/prefer")
        for joint in self.mechanical.joints:
            for end in (joint.parent, joint.child):
                if end != "world" and end not in ids:
                    raise ValueError(f"joint {joint.id!r} references unknown component {end!r}")
        self._check_mechanical(ids)
        harness_ids = [h.id for h in self.electrical.harnesses]
        if len(harness_ids) != len(set(harness_ids)):
            raise ValueError("harness ids must be unique")
        wire_ids = [w.id for h in self.electrical.harnesses for w in h.wires]
        if len(wire_ids) != len(set(wire_ids)):
            raise ValueError("wire ids must be unique across harnesses")
        if self.assembly is not None:
            if self.assembly.root not in ids:
                raise ValueError(f"assembly root {self.assembly.root!r} is not a component")
            join_ids = [join.join_id for join in self.assembly.joins]
            if len(join_ids) != len(set(join_ids)):
                raise ValueError("assembly join ids must be unique")
            for join in self.assembly.joins:
                for end in (join.a, join.b):
                    obj, dot, iface = end.partition(".")
                    if not dot or not iface:
                        raise ValueError(f"join end {end!r} must be <object>.<interface>")
                    if obj not in ids:
                        raise ValueError(f"join {join.join_id!r} references unknown component {obj!r}")
        for board in self.pcb.boards:
            if board.component not in ids:
                raise ValueError(f"board references unknown component {board.component!r}")
        for module in self.software.modules:
            if module.runs_on not in ids:
                raise ValueError(f"software module {module.name!r} runs on unknown component {module.runs_on!r}")
        return self

    def source_slots(self) -> dict[str, list[str]]:
        """Sourcing slot key (``source.group`` or the component id) -> component ids, in spec order."""
        slots: dict[str, list[str]] = {}
        for cid, comp in self.components.items():
            if comp.source is not None:
                slots.setdefault(comp.source.group or cid, []).append(cid)
        return slots

    def _check_mount_graph(self) -> None:
        """Mount parents must form a forest rooted at ``world`` (no cycles)."""
        for start in self.components:
            seen: list[str] = []
            node = start
            while node != "world":
                if node in seen:
                    raise ValueError(f"component mount cycle: {' -> '.join(seen + [node])}")
                seen.append(node)
                node = self.components[node].mount

    def part_ids(self) -> set[str]:
        """Every CAD part name the mechanical section may reference."""
        parts = set(self.components)
        for fastener in self.mechanical.fasteners:
            parts.add(fastener.part)
            if fastener.nut_part:
                parts.add(fastener.nut_part)
        return parts

    def _check_mechanical(self, ids: set[str]) -> None:
        if self.assembly is not None:
            # Fasteners, interfaces and joints are derived from the joins when the
            # assembly compiles; r3_model.mechanics validates the result afterwards.
            return
        self.validate_mechanical(ids)

    def validate_mechanical(self, ids: set[str] | None = None) -> None:
        """Reference checks of mechanical.fasteners/interfaces/joints/transmissions (raises ValueError)."""
        ids = set(self.components) if ids is None else ids
        mech = self.mechanical
        for label, values in (("joint", [j.id for j in mech.joints]), ("transmission", [t.id for t in mech.transmissions]),
                              ("fastener", [f.id for f in mech.fasteners]), ("interface", [i.id for i in mech.interfaces])):
            duplicates = sorted({v for v in values if values.count(v) > 1})
            if duplicates:
                raise ValueError(f"{label} ids must be unique: {duplicates}")
        shared = sorted({f.id for f in mech.fasteners} & {i.id for i in mech.interfaces})
        if shared:
            raise ValueError(f"fastener and interface ids must differ (transmission paths reference both): {shared}")
        parts = self.part_ids()
        collisions = sorted(ids & {p for p in parts if ":" in p})
        if collisions:
            raise ValueError(f"part names collide with component ids: {collisions}")
        for fastener in mech.fasteners:
            refs = [*fastener.clamps, *([fastener.thread_into] if fastener.thread_into else [])]
            for ref in refs:
                if ref not in parts:
                    raise ValueError(f"fastener {fastener.id!r} references unknown part {ref!r}")
            if fastener.part in refs:
                raise ValueError(f"fastener {fastener.id!r} references itself")
            if len(set(refs)) != len(refs):
                raise ValueError(f"fastener {fastener.id!r} lists a part twice")
            if not refs:
                raise ValueError(f"fastener {fastener.id!r} neither clamps nor threads into anything")
            # Missing retainers are diagnosed by joint_capacity_zero and replayed as loose pins.
            if fastener.thread_into is not None and fastener.nut is not None:
                raise ValueError(f"fastener {fastener.id!r} cannot both thread into a part and end in a nut")
            if fastener.nut is not None and fastener.grip is None:
                raise ValueError(f"fastener {fastener.id!r} ends in a nut and needs its grip length")
            if sum(c * c for c in fastener.axis) < 1e-12:
                raise ValueError(f"fastener {fastener.id!r} has a zero axis")
        interfaces = {i.id: i for i in mech.interfaces}
        for interface in mech.interfaces:
            for ref in interface.parts:
                if ref not in parts:
                    raise ValueError(f"interface {interface.id!r} references unknown part {ref!r}")
            if interface.parts[0] == interface.parts[1]:
                raise ValueError(f"interface {interface.id!r} joins a part to itself")
            axial = interface.kind in ("revolute_fit", "press_fit", "drive_flat", "spline", "ball_joint",
                                       "magnetic_target", "connector_retention")
            if axial and (interface.axis_point is None or interface.axis_dir is None):
                raise ValueError(f"interface {interface.id!r} ({interface.kind}) needs axis_point and axis_dir")
            for key in ("retainers", "through"):
                for ref in interface.params.get(key, []) or []:
                    if ref not in parts:
                        raise ValueError(f"interface {interface.id!r} {key} references unknown part {ref!r}")
        joint_ids = {j.id for j in mech.joints}
        for joint in mech.joints:
            moving = joint.type in ("revolute", "continuous", "prismatic")
            if moving and mech.interfaces and joint.interface is None:
                raise ValueError(f"joint {joint.id!r} ({joint.type}) must reference the interface that realizes it")
            if joint.interface is not None:
                interface = interfaces.get(joint.interface)
                if interface is None:
                    raise ValueError(f"joint {joint.id!r} references unknown interface {joint.interface!r}")
                rotating_flat = interface.kind == "drive_flat" and interface.params.get("rotating_bore")
                if joint.type in ("revolute", "continuous") and interface.kind not in ("revolute_fit", "spline") and not rotating_flat:
                    raise ValueError(f"joint {joint.id!r} is {joint.type} but interface {interface.id!r} is {interface.kind}")
        for transmission in mech.transmissions:
            if transmission.actuator not in ids:
                raise ValueError(f"transmission {transmission.id!r} actuator {transmission.actuator!r} is not a component")
            for jid in transmission.joints:
                if jid not in joint_ids:
                    raise ValueError(f"transmission {transmission.id!r} references unknown joint {jid!r}")
            reached: set[str] = set()
            for index, link in enumerate(transmission.path):
                members = self.link_parts(link)
                if members is None:
                    raise ValueError(f"transmission {transmission.id!r} path references unknown interface or fastener {link!r}")
                if index == 0 and transmission.actuator not in members:
                    raise ValueError(f"transmission {transmission.id!r} path must start at actuator {transmission.actuator!r}")
                if index > 0 and not reached.intersection(members):
                    raise ValueError(f"transmission {transmission.id!r} path breaks at {link!r}: shares no part with earlier links")
                reached.update(members)

    def link_parts(self, link: str) -> list[str] | None:
        """Parts joined by an interface or fastener id (None if unknown)."""
        for interface in self.mechanical.interfaces:
            if interface.id == link:
                return list(interface.parts)
        for fastener in self.mechanical.fasteners:
            if fastener.id == link:
                members = [fastener.part, *fastener.clamps]
                if fastener.thread_into:
                    members.append(fastener.thread_into)
                if fastener.nut_part:
                    members.append(fastener.nut_part)
                return members
        return None

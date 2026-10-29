"""Declarative robot specification (``robot.yaml``, schema ``r3/robot@1``).

Conventions
-----------
* Units: SI floats, or strings with explicit units (see :mod:`r3_model.units`).
* Robot frame: +x forward, +y left, +z up. Origin at ground level directly below
  the rear axle center. Rotations are roll/pitch/yaw (rad) applied as R = Rz·Ry·Rx.
* Identities: component ids, connector ids, harness ids and wire ids are stable
  strings. Pin references are ``<component>.<connector>.<pin>`` for component
  connectors and ``<harness>.<connector>.<pin>`` for cable-side connectors.
* The spec is the single source of truth; the component/net graph is derived
  (:mod:`r3_model.graph`) and external revisions are pinned in ``r3.lock.yaml``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .units import Quantity, Vector

SCHEMA_ID = "r3/robot@1"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Pose(_Model):
    xyz: Vector = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    rpy: Vector = Field(default_factory=lambda: [0.0, 0.0, 0.0])


class RobotInfo(_Model):
    name: str
    description: str = ""
    version: str = "0.1.0"


class Requirements(_Model):
    """Acceptance criteria checked by validation and simulation scenarios."""

    max_mass: Quantity | None = None
    top_speed: Quantity | None = None
    min_runtime: Quantity | None = Field(default=None, description="Mission runtime at the scenario duty cycle.")
    radio_range: Quantity | None = Field(default=None, description="Distance with positive link margin.")
    failsafe_stop_distance: Quantity | None = None
    max_wire_voltage_drop_fraction: float = 0.03
    min_wire_clearance_moving: Quantity = 0.008
    max_component_temperature_c: float = 85.0
    extra: dict[str, Any] = Field(default_factory=dict)


class ComponentInstance(_Model):
    type: str = Field(description="Catalog id, e.g. raspberry-pi-5-8gb.")
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


class Transmission(_Model):
    """Couples an actuator component to joints."""

    id: str
    type: Literal["differential", "direct", "ackermann_steering"]
    actuator: str = Field(description="Component id of motor or servo.")
    joints: list[str]
    ratio: float = 1.0
    efficiency: float = 1.0
    params: dict[str, Any] = Field(default_factory=dict)


class Mechanical(_Model):
    joints: list[Joint] = Field(default_factory=list)
    transmissions: list[Transmission] = Field(default_factory=list)


class HarnessConnector(_Model):
    """A cable-side connector. ``mates`` names the component connector it plugs
    into (``<component>.<connector>``); omit for splices or loose ends."""

    type: str
    mates: str | None = Field(default=None, description="<component|harness>.<connector> this plugs into.")
    position: Vector | None = Field(default=None, description="Free-end position when not mated (robot frame).")


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
    tie_points: list[Vector] = Field(default_factory=list, description="Cable tie anchors that pin the bundle.")
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


class HardwareBinding(_Model):
    """Maps a software-visible handle to a physical interface on a computer."""

    interface: Literal["gpio", "pwm", "i2c", "spi", "uart"]
    computer: str
    pins: list[str] = Field(description="Computer pin refs <component>.<connector>.<pin>.")
    device: str = Field(default="", description="Linux device or chip, e.g. /dev/ttyAMA0, pwmchip0, gpiochip0, /dev/i2c-1.")
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


class Environment(_Model):
    gravity: Quantity = 9.80665
    ambient_temperature_c: float = 25.0
    ground_friction: float = 0.9
    ground_rolling_resistance: float = 0.015
    terrain: str = "flat"
    obstacles: list[dict[str, Any]] = Field(default_factory=list, description="Static boxes: {id, center, size, material}.")
    radio_transmitter: RadioEndpoint | None = None
    radio_environment: dict[str, Any] = Field(default_factory=dict)


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
    timestep: Quantity = 0.001
    seed: int = 1
    environment: Environment = Field(default_factory=Environment)
    scenarios: list[Scenario] = Field(default_factory=list)


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

    @model_validator(mode="after")
    def _check_references(self) -> "RobotSpec":
        if self.schema_ != SCHEMA_ID:
            raise ValueError(f"unsupported schema {self.schema_!r}; expected {SCHEMA_ID}")
        ids = set(self.components)
        for cid, comp in self.components.items():
            if comp.mount != "world" and comp.mount not in ids:
                raise ValueError(f"component {cid!r} mounts on unknown component {comp.mount!r}")
        for joint in self.mechanical.joints:
            for end in (joint.parent, joint.child):
                if end != "world" and end not in ids:
                    raise ValueError(f"joint {joint.id!r} references unknown component {end!r}")
        harness_ids = [h.id for h in self.electrical.harnesses]
        if len(harness_ids) != len(set(harness_ids)):
            raise ValueError("harness ids must be unique")
        wire_ids = [w.id for h in self.electrical.harnesses for w in h.wires]
        if len(wire_ids) != len(set(wire_ids)):
            raise ValueError("wire ids must be unique across harnesses")
        for board in self.pcb.boards:
            if board.component not in ids:
                raise ValueError(f"board references unknown component {board.component!r}")
        for module in self.software.modules:
            if module.runs_on not in ids:
                raise ValueError(f"software module {module.name!r} runs on unknown component {module.runs_on!r}")
        return self

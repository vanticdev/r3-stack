"""Fixed-step co-simulation kernel.

Per step (``simulation.timestep``), in the documented order:

1. software outputs held by the twin HAL server (PWM, GPIO, UART TX) are applied;
2. device models turn them into ESC/servo pulses (only where the binding pins
   electrically reach the device);
3. ``ElectricalSystem.step`` solves the DC network -> motor torque, rails, brownout;
4. MuJoCo vehicle steps with motor torque (differential) and servo torque (Ackermann);
5. the routed cables are MuJoCo elastic cables in the same world, stepped by
   step 4; their clearance to moving parts is read from MuJoCo contact data
   every step;
6. radio link + ELRS receiver -> CRSF bytes on the receiver UART;
7. sensors (IMU, GPS, encoder, INA226) sample the physical state;
8. time advances; the HAL server wakes software whose deadline has passed.

All randomness comes from one ``numpy`` SeedSequence spawned per subsystem, so
a run is a deterministic function of (spec, scenario, seed, software).
"""

from __future__ import annotations

import collections
import json
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np

from r3_model.diagnostics import Diagnostic
from r3_model.project import Project
from r3_model.spec import Scenario
from r3_model.units import to_si

from .devices import ArbiterDevice, GpsReceiver, HallEncoder, Ina226, Mpu6050, RtkGnssReceiver, arbiter_params
from .fallbacks import FallbackCableSimulator, FallbackElectricalSystem
from .pilot import Pilot
from .radio import ElrsReceiver, RadioLink
from .radio_scene import scene_for_project
from .signals import BindingMap, map_bindings, uart_paths
from .vehicle import Vehicle, build_vehicle_model
from .vehicle.mjcf import _behavior_params

CABLE_PERIOD = 0.005
RECORD_PERIOD = 0.01
SNAPSHOT_PERIOD = 1.0 / 30.0
UART_BUFFER_BYTES = 4096  # Linux n_tty receive buffer
#: Scenario fault types the kernel and runner inject (``scenario.faults``).
FAULT_TYPES = {"software_freeze", "transmitter_off"}
DEFAULT_GPS_ORIGIN = (37.4275, -122.1697, 30.0)  # fixed geodetic origin of the local tangent plane (estimate)
#: UTC at scenario t = 0 for GNSS time stamps (NMEA UTC, RTCM GPS time of week).
GNSS_EPOCH_UTC = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
RTK_PREROLL_S = 30.0  # base + rover on before the scenario (warm start); >= 3 x the datasheet RTK convergence bound
RTK_PREROLL_STEP = 0.01  # s


def _get(mapping: Any, *path: str, default: Any = None) -> Any:
    for key in path:
        if not isinstance(mapping, Mapping) or key not in mapping:
            return default
        mapping = mapping[key]
    return mapping


def _first(outputs: dict[str, Any], candidates: list[tuple[str, ...]], default: Any = None) -> Any:
    for path in candidates:
        value = _get(outputs, *path)
        if value is not None:
            return value
    return default


def _find_kind(project: Project, prefix: str) -> list[str]:
    out = []
    for cid, comp in project.spec.components.items():
        ctype = project.catalog.component(comp.type)
        if ctype.behavior and ctype.behavior.model.startswith(prefix):
            out.append(cid)
    return out


@dataclass
class VirtualUart:
    device: str
    binding: str
    component: str | None
    rx_ok: bool
    tx_ok: bool
    device_baud: int
    open: bool = False
    baud: int | None = None
    rx: collections.deque = field(default_factory=collections.deque)  # (t, byte) pending arrival
    ready: bytearray = field(default_factory=bytearray)
    overruns: int = 0
    mismatch_reported: bool = False
    written: int = 0

    def push(self, items: list[tuple[float, int]], rng: np.random.Generator) -> None:
        if not self.open or not self.rx_ok:
            return
        garble = self.baud is not None and abs(self.baud - self.device_baud) / self.device_baud > 0.03
        for t, byte in items:
            self.rx.append((t, int(rng.integers(0, 256)) if garble else byte))

    def deliver(self, now: float) -> None:
        while self.rx and self.rx[0][0] <= now + 1e-12:
            _, byte = self.rx.popleft()
            if len(self.ready) >= UART_BUFFER_BYTES:
                self.overruns += 1
            else:
                self.ready.append(byte)

    def next_arrival(self) -> float | None:
        return self.rx[0][0] if self.rx else None


@dataclass
class GpioLineState:
    chip: str
    line: int
    direction: str = "in"
    bias: str = "none"
    edge: str = "none"
    value_out: int = 0
    pending_edges: list[tuple[float, bool]] = field(default_factory=list)
    rising_count: int = 0
    last_rising_t: float = 0.0


@dataclass
class Recorder:
    channels: dict[str, list[float]] = field(default_factory=lambda: collections.defaultdict(list))
    snapshots_t: list[float] = field(default_factory=list)
    snapshots_qpos: list[np.ndarray] = field(default_factory=list)
    logs: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    def add(self, **values: float) -> None:
        for key, value in values.items():
            self.channels[key].append(float(value))

    def arrays(self) -> dict[str, np.ndarray]:
        return {k: np.asarray(v) for k, v in self.channels.items()}


class CoSimKernel:
    def __init__(self, project: Project, scenario: Scenario, seed: int | None = None) -> None:
        self.project = project
        self.scenario = scenario
        spec = project.spec
        self.dt = float(spec.simulation.timestep)
        self.seed = spec.simulation.seed if seed is None else seed
        streams = np.random.SeedSequence(self.seed).spawn(8)
        self.rng_radio, self.rng_imu, self.rng_gps, self.rng_encoder, self.rng_ina, self.rng_uart, self.rng_misc = (
            np.random.default_rng(s) for s in streams[:7])
        self.rng_rtk_base, self.rng_rtk_link = (np.random.default_rng(s) for s in streams[7].spawn(2))
        self.diagnostics: list[Diagnostic] = []
        self.assumptions: list[tuple[str, str]] = []
        self.step_index = 0
        self.duration = float(scenario.duration)
        initial = scenario.initial or {}
        pose = initial.get("pose", [0.0, 0.0, 0.0])
        self.initial_pose = (to_si(pose[0]), to_si(pose[1]), to_si(pose[2]) if len(pose) > 2 else 0.0)

        mission = scenario.mission or {}
        waypoints = mission.get("waypoints") or []
        routes = project.read_build_json("wiring/routes.json")
        self.vm = build_vehicle_model(project, self.initial_pose, spec.simulation.environment.obstacles, waypoints,
                                      routes=routes)
        self.diagnostics.extend(self.vm.diagnostics)
        self.assumptions.extend(self.vm.assumptions)
        self.vehicle = Vehicle(project, self.vm)
        self.assumptions.append(("mechanical", f"Aerodynamic drag CdA = {self.vehicle.drag_area} m^2 (estimate), air density "
                                               f"{self.vehicle.rho:.3f} kg/m^3; rolling resistance C_rr = "
                                               f"{next(iter(self.vm.wheels.values())).rolling_resistance if self.vm.wheels else 0} "
                                               "applied as N*C_rr*r moment per wheel; rigid chassis (no suspension)."))

        # --- identities
        self.computer = spec.software.modules[0].runs_on if spec.software.modules else next(
            iter(_find_kind(project, "load.computer")), "pi")
        drive = self.vm.drives[0] if self.vm.drives else None
        self.motor_id = drive.actuator_component if drive else "motor"
        self.servo_id = self.vm.steering[0].actuator_component if self.vm.steering else "servo"
        self.esc_id = next(iter(_find_kind(project, "esc")), "esc")
        self.battery_id = next(iter(_find_kind(project, "battery")), "battery")
        self.board_id = next(iter(_find_kind(project, "board")), None)
        self.cells = int(_behavior_params(project, self.battery_id).get("cells_series", 2)) if self.battery_id in spec.components else 2
        computer_params = _behavior_params(project, self.computer) if self.computer in spec.components else {}
        self.boot_time = to_si(computer_params.get("boot_time", 20.0))
        self.brownout_voltage = to_si(computer_params.get("brownout_voltage", 4.63))

        # --- electrical / cable models (built in parallel by other packages; fall back if absent)
        soc = float(initial.get("battery_soc", 1.0))
        self.electrical = self._load_electrical(routes, soc)
        self.cables = self._load_cables(routes)
        self.hat_supply_wire = self._supply_wire_of(self.board_id)

        # --- signal mapping and devices
        self.bindings: dict[str, BindingMap] = map_bindings(project)
        self.pwm_devices: dict[tuple[str, int], list[str]] = {}
        for bmap in self.bindings.values():
            if bmap.interface == "pwm":
                self.pwm_devices[(bmap.device, int(bmap.channel or 0))] = sorted({a.component for a in bmap.attachments})
        self.pwm_state: dict[tuple[str, int], dict[str, Any]] = {}
        self.uarts: dict[str, VirtualUart] = {}
        receiver_ids = _find_kind(project, "receiver")
        gps_ids = _find_kind(project, "gps")
        self.receiver_id = receiver_ids[0] if receiver_ids else None
        self.gps_id = gps_ids[0] if gps_ids else None
        for name, bmap in self.bindings.items():
            if bmap.interface != "uart":
                continue
            component, rx_ok, tx_ok = uart_paths(project, bmap)
            binding = spec.software.bindings[name]
            device_baud = int(binding.baud or 115200)
            if component:
                params = _behavior_params(project, component)
                device_baud = int(params.get("uart_baud", params.get("baud", device_baud)))
            self.uarts[bmap.device] = VirtualUart(bmap.device, name, component, rx_ok, tx_ok, device_baud)
            if component is None or not rx_ok:
                self.diagnostics.append(Diagnostic.error(
                    "binding_not_connected", f"UART binding {name} ({bmap.device}) does not electrically reach a device TX pin",
                    subjects=[name, *(a.component for a in bmap.attachments)], data={"rx_path": rx_ok, "tx_path": tx_ok}))
        for name, bmap in self.bindings.items():
            if bmap.interface in ("pwm", "gpio", "i2c") and not bmap.attachments:
                self.diagnostics.append(Diagnostic.error(
                    "binding_not_connected", f"{bmap.interface} binding {name} does not electrically reach any device pin",
                    subjects=[name], data={"pins": len(spec.software.bindings[name].pins)}))

        env = spec.simulation.environment
        receiver_params = _behavior_params(project, self.receiver_id) if self.receiver_id else {}
        radio_scene = scene_for_project(project, self.receiver_id, receiver_params)
        self.radio = RadioLink(env, receiver_params, self.rng_radio, scene=radio_scene,
                               scene_dir=project.build_dir("sim") / "radio" / f"scene_{radio_scene.digest()}")
        self.diagnostics.extend(self.radio.diagnostics)
        self.assumptions.extend(self.radio.assumptions())
        rx_uart = next((u for u in self.uarts.values() if u.component == self.receiver_id), None)
        self.rx_uart = rx_uart
        self.receiver = ElrsReceiver(self.radio, float(rx_uart.device_baud if rx_uart else 420000), self.rng_radio)
        # Car antenna in the robot frame: CAD rf.antenna_position when exposed, else receiver pose + antenna height.
        self.rx_antenna_offset = np.asarray(radio_scene.antenna_offset, dtype=float)

        origin = initial.get("gps_origin") or env.radio_environment.get("gps_origin") or DEFAULT_GPS_ORIGIN
        self.gps_origin = tuple(float(v) for v in (list(origin) + [DEFAULT_GPS_ORIGIN[2]])[:3])
        gps_params = _behavior_params(project, self.gps_id) if self.gps_id else {}
        gps_model = project.catalog.component(spec.components[self.gps_id].type).behavior.model if self.gps_id else ""
        base = env.gnss_base
        self.gps: GpsReceiver | None = None
        if gps_model == "gps.rtk":
            from .correction_link import gps_tow_ms

            self.gps = RtkGnssReceiver(gps_params, self.gps_origin, self.rng_gps, epoch_utc=GNSS_EPOCH_UTC,
                                       survey_error=tuple(base.survey_error) if base else (0.0, 0.0, 0.0),
                                       gps_tow0_ms=gps_tow_ms(GNSS_EPOCH_UTC))
        elif self.gps_id:
            self.gps = GpsReceiver(gps_params, self.gps_origin, self.rng_gps, epoch_utc=GNSS_EPOCH_UTC)
        self.gps_uart = next((u for u in self.uarts.values() if u.component == self.gps_id), None)
        self.gps_antenna_offset = project.graph.component_pose(self.gps_id)[0] if self.gps_id else np.zeros(3)
        self.assumptions.append(("sensors", f"GPS local tangent plane: world +x east, +y north around "
                                            f"{self.gps_origin[0]:.5f}, {self.gps_origin[1]:.5f}; errors = Gauss-Markov "
                                            "(60 s) + white noise sized to the datasheet CEP."))
        self.rtk_base = None
        self.rtk_link = None
        self.rtk_pending: collections.deque[tuple[float, int]] = collections.deque()
        self.rtk_radio_id: str | None = None
        self.rtk_garble = False
        if isinstance(self.gps, RtkGnssReceiver):
            self._setup_rtk(project, radio_scene)

        self.i2c_devices: dict[str, list[Any]] = {}
        for bmap in self.bindings.values():
            if bmap.interface != "i2c":
                continue
            devices: list[Any] = []
            for cid in sorted({a.component for a in bmap.attachments}):
                ctype = project.catalog.component(spec.components[cid].type)
                params = _behavior_params(project, cid)
                for interface in ctype.software:
                    if interface.bus != "i2c" or interface.address is None:
                        continue
                    if interface.protocol == "mpu6050":
                        devices.append(Mpu6050(interface.address, self.rng_imu))
                    elif interface.protocol == "ina226":
                        shunt = to_si((params.get("ina226") or {}).get("shunt", 0.002))
                        devices.append(Ina226(interface.address, shunt, self.rng_ina))
            self.i2c_devices[bmap.device] = devices
        for devices in self.i2c_devices.values():
            for device in devices:
                device.set_powered(True)  # the computer and HAT are powered at scenario start
        self.imu = next((d for ds in self.i2c_devices.values() for d in ds if isinstance(d, Mpu6050)), None)
        self.ina = next((d for ds in self.i2c_devices.values() for d in ds if isinstance(d, Ina226)), None)
        if self.ina is not None:
            self.assumptions.append(("sensors", "INA226 measures the current of the branch its shunt sits in (the HAT supply, "
                                                f"{self.hat_supply_wire or 'board input'}); it does not see ESC/motor current."))

        self.gpio_lines: dict[tuple[str, int], GpioLineState] = {}
        # CSI port -> r3_sim.devices.camera.VirtualCamera, created by the HAL server on camera.configure.
        self.cameras: dict[str, Any] = {}
        self.encoder_line: tuple[str, int] | None = None
        self.encoder: HallEncoder | None = None
        self.encoder_joint: str | None = None
        self.encoder_carrier: tuple[list[str], float] | None = None
        for bmap in self.bindings.values():
            if bmap.interface == "gpio" and bmap.attachments:
                cid = bmap.attachments[0].component
                ctype = project.catalog.component(spec.components[cid].type)
                if ctype.behavior and ctype.behavior.model.startswith("encoder"):
                    params = _behavior_params(project, cid)
                    self.encoder = HallEncoder(int(params.get("pulses_per_wheel_rev", 1)), to_si(params.get("jitter", 0.0)),
                                               self.rng_encoder)
                    self.encoder_line = (bmap.device, int(bmap.channel or 0))
                    if params.get("target") == "differential_carrier" and self.vm.drives:
                        # Magnets on the spur shaft: carrier angle (mean of the driven wheels) x spur/carrier ratio.
                        self.encoder_joint = "carrier"
                        self.encoder_carrier = (list(self.vm.drives[0].joints), float(params.get("target_ratio", 1.0)))
                        self.assumptions.append(("sensors", f"Hall encoder magnets ride on the spur shaft: {cid} sees the differential "
                                                            f"carrier (mean of {', '.join(self.vm.drives[0].joints)}) x "
                                                            f"{self.encoder_carrier[1]:g}."))
                    else:
                        self.encoder_joint = self._nearest_drive_joint(cid)
                        self.assumptions.append(("sensors", f"Hall encoder magnets ride on {self.encoder_joint} (drive joint nearest "
                                                            f"to {cid})."))

        # --- hardware failsafe arbiters between a PWM binding and its consumer (board ``failsafe_arbiter`` links)
        self.arbiters: dict[tuple[str, int], ArbiterDevice] = {}        # PWM (chip, channel) -> arbiter
        self.heartbeat_lines: dict[tuple[str, int], ArbiterDevice] = {}  # GPIO (chip, line) -> arbiter
        self.pi_pulses: dict[tuple[str, int], float] = {}                # what the computer outputs, per PWM channel
        self._setup_arbiters()

        # --- injected faults
        self.faults = [dict(f) for f in scenario.faults]
        self.freeze_at: float | None = None
        self.transmitter_off: tuple[float, float] | None = None
        for fault in self.faults:
            kind = fault.get("type")
            at = to_si(fault.get("at", 0.0))
            if kind == "software_freeze":
                self.freeze_at = at if self.freeze_at is None else min(self.freeze_at, at)
            elif kind == "transmitter_off":
                self.transmitter_off = (at, to_si(fault["until"]) if fault.get("until") is not None else math.inf)
            else:
                self.diagnostics.append(Diagnostic.warning(
                    "fault_unsupported", f"scenario fault type {kind!r} is not supported (known: {', '.join(sorted(FAULT_TYPES))})",
                    subjects=[scenario.id], data={"faults": len(self.faults)}))
        self.software_frozen = False
        self.frozen_at: float | None = None

        mode = initial.get("mode", spec.software.config.get("mode", "autonomous"))
        self.pilot = Pilot(scenario.pilot or {}, spec.software.config)
        self.mode = mode

        # --- runtime state
        self.pi_powered = True
        self.software_running = False
        self.boot_ready_t: float | None = 0.0  # software considered booted at t = 0
        self.brownout_times: list[float] = []
        self.last_outputs: dict[str, Any] = {}
        self.wire_clearance: dict[str, tuple[float, str]] = {}
        self.min_wire_clearance = (math.inf, "", "")
        self.recorder = Recorder()
        self._next_record = 0.0
        self._next_snapshot = 0.0
        self._next_cable = 0.0
        self.esc_pulse = 0.0
        self.servo_pulse = 0.0
        self.temperature_c = env.ambient_temperature_c
        self.on_brownout: Callable[[float], None] | None = None
        self.on_power_restored: Callable[[float], None] | None = None

    # ------------------------------------------------------------------ setup helpers
    def _setup_rtk(self, project: Project, radio_scene: Any) -> None:
        """Base station + SiK correction link into the rover's UART2 (only if the harness actually connects them)."""
        from .correction_link import GnssBaseStation, SikCorrectionLink, correction_scene
        from .radio_scene import car_body

        spec, env = project.spec, project.spec.simulation.environment
        base = env.gnss_base
        gps_type = project.catalog.component(spec.components[self.gps_id].type)
        # Correction input: the rover's second UART receive pin (UART1 RX carries the computer's commands).
        rx2 = next((name for name, pin in gps_type.pins.items() if pin.role == "uart_rx" and name != "RX"), None)
        radios = [cid for cid in _find_kind(project, "telemetry.sik")]
        connected = [cid for cid in radios if rx2 and project.graph.connected(f"C:{cid}.TX", f"C:{self.gps_id}.{rx2}", through_internal=True)]
        self.assumptions.append(("sensors", "RTK rover: single/float/fixed solution model driven by the RTCM3 frames that reach UART2 "
                                            "intact (age of corrections, base position, baseline); RTK fixed CEP 0.01 m + 1 ppm "
                                            "(datasheet); float accuracy, fix hold age and convergence draw are estimates; "
                                            "wrong-fix events not modelled."))
        if base is None:
            self.diagnostics.append(Diagnostic.warning(
                "rtk_base_missing", f"{self.gps_id} is an RTK rover but simulation.environment.gnss_base is not defined; "
                                    "it navigates on standalone GNSS", subjects=[self.gps_id]))
            return
        if not connected:
            self.diagnostics.append(Diagnostic.error(
                "rtk_corrections_not_connected",
                f"no correction radio TX reaches {self.gps_id}.{rx2}: RTCM3 corrections cannot arrive (standalone GNSS only)",
                subjects=[self.gps_id, *radios], data={"radios": len(radios)},
                hint="Wire a telemetry.sik radio's TX to the rover's UART2 RX (harness), and power it."))
            return
        self.rtk_radio_id = connected[0]
        rover_params = _behavior_params(project, self.rtk_radio_id)
        base_radio_params = dict(project.catalog.component(base.radio).behavior.params)
        rover_baud = int(rover_params.get("serial_baud", 57600))
        if abs(rover_baud - self.gps.uart2_baud) / rover_baud > 0.03:
            self.rtk_garble = True
            self.diagnostics.append(Diagnostic.error(
                "rtk_uart_baud_mismatch", f"{self.rtk_radio_id} serial {rover_baud} baud vs {self.gps_id} UART2 {self.gps.uart2_baud} baud: "
                                          "corrections are garbled", subjects=[self.rtk_radio_id, self.gps_id],
                data={"radio_baud": rover_baud, "uart2_baud": self.gps.uart2_baud}))
        position, rotation = project.graph.component_pose(self.rtk_radio_id)
        port = np.asarray([to_si(v) for v in rover_params.get("antenna_port", [0.0, 0.0, 0.0])], dtype=float)
        self.rtk_antenna_offset = (np.asarray(position, dtype=float) + np.asarray(rotation) @ port
                                   + np.array([0.0, 0.0, to_si(rover_params.get("antenna_feed_height", 0.06))]))
        base_position = np.asarray(base.position, dtype=float)
        tx_position = (np.asarray(base.radio_antenna_position, dtype=float) if base.radio_antenna_position is not None
                       else base_position - np.array([0.0, 0.0, 0.3]))
        overrides = dict(base.correction_link or {})
        scene = correction_scene(radio_scene, to_si(base_radio_params.get("frequency", 915e6)), tx_position,
                                 float(overrides.get("power_dbm", base_radio_params.get("tx_power_dbm", 20.0))),
                                 float(base_radio_params.get("antenna_gain_dbi", 2.0)), float(rover_params.get("antenna_gain_dbi", 2.0)),
                                 self.rtk_antenna_offset, car_body(project, exclude=(self.rtk_radio_id,)) if radio_scene.body else [])
        self.rtk_link = SikCorrectionLink(scene, base_radio_params, rover_params, env, overrides, self.rng_rtk_link,
                                          scene_dir=project.build_dir("sim") / "radio" / f"rtk_scene_{scene.digest()}")
        self.diagnostics.extend(self.rtk_link.diagnostics)
        self.assumptions.extend(self.rtk_link.assumptions())
        self.rtk_base = GnssBaseStation(base, self.gps.ltp, self.rng_rtk_base, int(base_radio_params.get("serial_baud", 57600)),
                                        GNSS_EPOCH_UTC)
        self._rtk_preroll(RTK_PREROLL_S)

    def _rtk_preroll(self, seconds: float) -> None:
        """Run base, link and rover for ``seconds`` before t = 0 with the car parked at its start pose: the rover is
        warm at scenario start (as ``GpsReceiver`` assumes), with whatever solution the corrections gave it."""
        position = np.asarray(self.vehicle.position(), dtype=float)
        rotation = np.asarray(self.vehicle.rotation(), dtype=float)
        gps_pos = position + rotation @ self.gps_antenna_offset
        self.rtk_base.next_epoch = -seconds + 1.0
        self.rtk_link.next_window = -seconds
        self.gps.next_epoch = -seconds
        steps = int(round(seconds / RTK_PREROLL_STEP))
        for k in range(steps):
            t = -seconds + (k + 1) * RTK_PREROLL_STEP
            if t >= -1e-9:
                break
            self._rtk_step(t, position, rotation, powered=True)
            self.gps.advance(t, gps_pos, np.zeros(3), True)
        self.gps.tx_free_t = 0.0
        stats = self.gps.stats()
        self.gps.reset_statistics()  # scenario metrics count epochs from t = 0
        self.assumptions.append(("sensors", f"RTK base and rover run {seconds:.0f} s before the scenario with the car parked at the "
                                            f"start (warm receivers): rover in {self.gps.mode} at t = 0 (first fix at "
                                            f"t = {stats['rtk_time_to_first_fix_s']} s)."))

    # ------------------------------------------------------------------ radio path cache
    def _route_poses(self, spacing: float, band: float, corner_radius: float = 3.0) -> list[tuple[np.ndarray, float]]:
        """(robot-origin position, heading) samples the car will occupy: along the mission polyline (from the start
        pose, closed when it has more than two waypoints), +-``band`` across it, every heading between the
        adjacent segments within ``corner_radius`` of a waypoint."""
        mission = self.scenario.mission or {}
        waypoints = [np.asarray([to_si(v) for v in p[:2]], dtype=float) for p in (mission.get("waypoints") or [])]
        if not waypoints:
            return []
        start = np.asarray(self.initial_pose[:2], dtype=float)
        course = waypoints + ([waypoints[0]] if len(waypoints) > 2 else [])
        legs = [start] + course
        out: list[tuple[np.ndarray, float]] = []
        headings = [math.atan2(*(b - a)[::-1]) for a, b in zip(legs[:-1], legs[1:])]
        for i, (a, b) in enumerate(zip(legs[:-1], legs[1:])):
            length = float(np.linalg.norm(b - a))
            if length < 1e-6:
                continue
            u = (b - a) / length
            n = np.array([-u[1], u[0]])
            yaw = headings[i]
            for s_along in np.arange(0.0, length + 1e-9, spacing):
                p = a + u * s_along
                near_end = length - s_along <= corner_radius and i + 1 < len(headings)
                yaws = [yaw]
                if near_end:
                    turn = (headings[i + 1] - yaw + math.pi) % (2 * math.pi) - math.pi
                    yaws = [yaw + turn * f for f in np.linspace(0.0, 1.0, 5)]
                for offset in np.arange(-band, band + 1e-9, spacing):
                    for y in yaws:
                        out.append((p + n * offset, y))
        return out

    def radio_cache_keys(self, model: Any, antenna_offset: np.ndarray, limit: int = 4000) -> list[tuple[int, int, int, int]]:
        key_for = getattr(model, "key_for", None)
        if key_for is None:
            return []
        z0 = float(self.vehicle.position()[2])
        keys: list[tuple[int, int, int, int]] = []
        seen: set[tuple[int, int, int, int]] = set()
        grid = float(getattr(model, "grid", 0.25))
        for xy, yaw in self._route_poses(spacing=grid, band=grid):
            c, s = math.cos(yaw), math.sin(yaw)
            rotation = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
            key = key_for(np.array([xy[0], xy[1], z0]) + rotation @ antenna_offset, rotation)
            if key not in seen:
                seen.add(key)
                keys.append(key)
            if len(keys) >= limit:
                break
        return keys

    def prepare_radio(self) -> dict[str, Any]:
        """Trace the radio cells along the mission ahead of the run, in worker processes (both links)."""
        report: dict[str, Any] = {}
        links = [("elrs", self.radio.model, self.rx_antenna_offset)]
        if self.rtk_link is not None:
            links.append(("rtk", self.rtk_link.model, self.rtk_antenna_offset))
        for name, model, offset in links:
            if not hasattr(model, "precompute"):
                continue
            keys = self.radio_cache_keys(model, np.asarray(offset, dtype=float))
            t0 = time.perf_counter()
            added = model.precompute(keys)
            report[name] = {"keys": len(keys), "traced": added, "seconds": round(time.perf_counter() - t0, 2)}
        return report

    def save_radio_cache(self) -> None:
        for model in (self.radio.model, self.rtk_link.model if self.rtk_link is not None else None):
            save = getattr(model, "save_cache", None)
            if callable(save):
                save()

    def _rtk_step(self, t1: float, position: np.ndarray, rotation: np.ndarray, powered: bool | None = None) -> None:
        """Base RTCM -> SiK air link -> rover radio serial -> rover UART2, delivering bytes that have arrived by ``t1``."""
        if self.rtk_link is None or self.rtk_base is None:
            return
        powered = self.pi_powered if powered is None else powered
        self.rtk_link.offer(self.rtk_base.advance(t1))
        antenna = position + rotation @ self.rtk_antenna_offset
        self.rtk_pending.extend(self.rtk_link.advance(t1, antenna, rotation, powered))
        due: list[tuple[float, int]] = []
        while self.rtk_pending and self.rtk_pending[0][0] <= t1 + 1e-12:
            t, byte = self.rtk_pending.popleft()
            due.append((t, int(self.rng_uart.integers(0, 256)) if self.rtk_garble else byte))
        if due and powered:
            self.gps.feed_rtcm(due)

    def _load_electrical(self, routes: Any, soc: float) -> Any:
        try:
            from r3_sim.electrical import ElectricalSystem  # type: ignore[attr-defined]
        except Exception as error:  # noqa: BLE001 - any import failure selects the fallback
            self._fallback_warning("electrical_model_fallback", "r3_sim.electrical.ElectricalSystem", error)
            return FallbackElectricalSystem(self.project, routes, initial_soc=soc)
        try:
            try:
                system = ElectricalSystem(self.project, routes=routes, initial=dict(self.scenario.initial or {}))
            except TypeError:
                system = ElectricalSystem(self.project, routes=routes)
            for setter in ("set_initial_state", "set_initial", "reset"):
                if hasattr(system, setter):
                    try:
                        getattr(system, setter)(dict(self.scenario.initial or {}))
                        break
                    except TypeError:
                        continue
            return system
        except Exception as error:  # noqa: BLE001
            self._fallback_warning("electrical_model_fallback", "ElectricalSystem construction", error)
            return FallbackElectricalSystem(self.project, routes, initial_soc=soc)

    def _load_cables(self, routes: Any) -> Any:
        """In-world MuJoCo cables when routes.json exists; the labeled static fallback otherwise."""
        if self.vehicle.cables is not None:
            return self.vehicle.cables
        if routes:
            self.diagnostics.append(Diagnostic.warning(
                "cable_model_fallback", "routes.json present but the MuJoCo cables could not be built; using the labeled "
                "fallback in r3_sim.fallbacks", subjects=["wiring/routes.json"], data={"fallback": 1}))
        else:
            self._fallback_warning("cable_model_fallback", "build/wiring/routes.json", FileNotFoundError("not built"))
        return FallbackCableSimulator(self.project, routes, self.vm.geometry)

    def _fallback_warning(self, code: str, what: str, error: Exception) -> None:
        self.diagnostics.append(Diagnostic.warning(
            code, f"{what} unavailable ({type(error).__name__}: {error}); using the labeled fallback in r3_sim.fallbacks",
            subjects=[what], data={"fallback": 1}))

    def _supply_wire_of(self, board: str | None) -> str | None:
        """Harness wire whose net feeds the board's VBAT/power_in pin directly."""
        if board is None:
            return None
        graph = self.project.graph
        ctype = self.project.catalog.component(self.project.spec.components[board].type)
        power_pins = [name for name, pin in ctype.pins.items() if pin.role == "power_in" and name != "3V3"]
        for pin in power_pins:
            node = f"C:{board}.{pin}"
            mates = {e.b if e.a == node else e.a for e in graph.edges if e.kind == "mate" and node in (e.a, e.b)}
            for edge in graph.edges:
                if edge.kind == "wire" and (edge.a in mates or edge.b in mates):
                    return edge.ref
        return None

    def _setup_arbiters(self) -> None:
        """Arbiter devices from boards with a ``failsafe_arbiter`` internal link: the PWM bindings whose pins
        share a net with the link's input pin pass through it; the GPIO bindings on the board's heartbeat pin
        feed it."""
        project, spec, graph = self.project, self.project.spec, self.project.graph

        def on_net(pins: list[str], node: str) -> bool:
            for pin in pins:
                try:
                    if graph.connected(pin, node):
                        return True
                except KeyError:
                    continue
            return False

        for cid, inst in spec.components.items():
            ctype = project.catalog.components.get(inst.type)
            links = [link for link in (ctype.internal_links if ctype else []) if link.kind == "failsafe_arbiter"]
            if not links:
                continue
            failsafe = _behavior_params(project, cid).get("failsafe") or {}
            heartbeat = failsafe.get("heartbeat") or next(
                (s.params.get("pin") for s in ctype.software if s.bus == "gpio" and s.protocol == "heartbeat"), None)
            mine = [b for b in spec.software.bindings.values() if b.computer == self.computer]
            for link in links:
                device = ArbiterDevice(cid, arbiter_params(failsafe, float(link.value) or None))
                pwm = [(b.device, int(b.channel or 0)) for b in mine if b.interface == "pwm" and on_net(b.pins, f"C:{cid}.{link.a}")]
                beats = [(b.device, int(b.channel or 0)) for b in mine
                         if b.interface == "gpio" and heartbeat and on_net(b.pins, f"C:{cid}.{heartbeat}")]
                for key in pwm:
                    self.arbiters[key] = device
                for key in beats:
                    self.heartbeat_lines[key] = device
                if pwm and not beats:
                    self.diagnostics.append(Diagnostic.error(
                        "arbiter_heartbeat_unbound", f"{cid}: failsafe arbiter {link.a}->{link.b} has no software heartbeat binding "
                        f"on {heartbeat}; its output stays at neutral", subjects=[cid, str(heartbeat)], data={"pwm_bindings": len(pwm)},
                        hint="bind a gpio output to the board's heartbeat pin and toggle it every control cycle"))
                if pwm:
                    p = device.params
                    self.assumptions.append(("electrical", f"{cid} failsafe arbiter ({p.part}, r3_kicad.arbiter): {link.a} reaches "
                                                           f"{link.b} only while the heartbeat toggles; {p.timeout * 1e3:.0f} ms without "
                                                           f"an edge -> {p.neutral_us:.0f} us neutral from the next {p.frame * 1e3:.0f} ms "
                                                           f"frame; re-arm after {p.rearm_edges} edges at neutral; boots in failsafe."))

    def gpio_written(self, key: tuple[str, int], value: int) -> None:
        """Software wrote a GPIO output level (heartbeat lines feed their arbiter)."""
        arbiter = self.heartbeat_lines.get(key)
        if arbiter is not None:
            arbiter.heartbeat_write(self.t, value)

    def _nearest_drive_joint(self, component: str) -> str | None:
        if not self.vm.drives:
            return None
        pos, _ = self.project.graph.component_pose(component)
        joints = {j.id: j for j in self.project.spec.mechanical.joints}
        return min(self.vm.drives[0].joints, key=lambda j: float(np.linalg.norm(np.asarray(joints[j].origin) - pos)))

    # ------------------------------------------------------------------ software-facing state
    @property
    def t(self) -> float:
        return self.step_index * self.dt

    def software_config(self) -> dict[str, Any]:
        config = dict(self.project.spec.software.config)
        config["mode"] = self.mode
        config["mission"] = dict(self.scenario.mission or {})
        config["gps_origin"] = {"lat": self.gps_origin[0], "lon": self.gps_origin[1], "alt": self.gps_origin[2]}
        config["crsf_channels"] = dict(self.pilot.channel_map)
        config["scenario"] = self.scenario.id
        return config

    def reset_software_outputs(self) -> None:
        """Power loss: PWM peripherals stop, GPIO returns to inputs, UARTs close."""
        self.pwm_state.clear()
        for line in self.gpio_lines.values():
            line.direction, line.pending_edges = "in", []
        self.gpio_lines.clear()
        for uart in self.uarts.values():
            uart.open, uart.baud = False, None
            uart.rx.clear()
            uart.ready.clear()

    def bytes_available(self, device: str) -> int:
        uart = self.uarts[device]
        uart.deliver(self.t)
        return len(uart.ready)

    # ------------------------------------------------------------------ step
    def _pulse_for(self, component: str) -> float:
        """Pulse width (us) ``component`` sees: the computer's PWM output, through a failsafe arbiter when the
        binding passes one (0: no pulses)."""
        for key, devices in self.pwm_devices.items():
            if component in devices:
                state = self.pwm_state.get(key)
                pulse = 0.0
                if state and state["enabled"] and state["period_ns"] > 0 and self.pi_powered:
                    pulse = state["duty_ns"] / 1000.0
                self.pi_pulses[key] = pulse
                arbiter = self.arbiters.get(key)
                return arbiter.output(self.t, pulse) if arbiter is not None else pulse
        return 0.0

    def _inputs(self) -> tuple[dict[str, Any], float, float, float]:
        """Steps 1-2: software outputs -> device inputs. Returns (electrical inputs, motor
        speed, ESC pulse, servo pulse); reads only state that MuJoCo's mj_step2 leaves alone."""
        vehicle = self.vehicle
        esc_pulse = self._pulse_for(self.esc_id)
        servo_pulse = self._pulse_for(self.servo_id)
        servo = vehicle.servos[0] if vehicle.servos else None
        motor_omega = vehicle.motor_omega(0) if self.vm.drives else 0.0
        activity = 0.0 if not self.pi_powered else (0.55 if self.software_running else 0.8)
        inputs = {
            self.esc_id: {"pulse_us": esc_pulse},
            self.motor_id: {"omega": motor_omega},
            self.servo_id: {"pulse_us": servo_pulse, "horn_torque": servo.last_torque if servo else 0.0,
                            "horn_speed": servo.omega if servo else 0.0},
            self.computer: {"activity": activity},
        }
        return inputs, motor_omega, esc_pulse, servo_pulse

    def _apply(self, out: dict[str, Any], t0: float, motor_omega: float, esc_pulse: float, servo_pulse: float) -> None:
        """Step 3 outputs -> power state; step 4 controls (motor torque, servo) for mj_step2."""
        vehicle = self.vehicle
        self.esc_pulse, self.servo_pulse = esc_pulse, servo_pulse
        self.last_outputs = out
        torque = float(_first(out, [(self.motor_id, "torque")], 0.0))
        powered = _first(out, [(self.computer, "powered")])
        if powered is None:
            powered = float(_first(out, [("_nets", "5V")], 5.0)) >= self.brownout_voltage
        self._update_power(bool(powered), t0)
        servo_v = float(_first(out, [(self.servo_id, "voltage"), (self.servo_id, "v_supply"), (self.esc_id, "bec_voltage"),
                                     ("_nets", "BEC_6V")], 6.0))
        if self.vm.drives:
            vehicle.set_motor_torque(0, torque, motor_omega)
        if self.vm.steering:
            vehicle.step_servo(0, self.dt, servo_pulse, servo_v)

    def step(self) -> None:
        """One master step, serially except that mj_step1 runs while the circuit is solved."""
        t0 = self.t
        vehicle = self.vehicle
        inputs, motor_omega, esc_pulse, servo_pulse = self._inputs()
        vehicle.start_step()  # mj_step1 depends only on the state
        out = self.electrical.step(self.dt, inputs)  # 3. electrical network
        self._apply(out, t0, motor_omega, esc_pulse, servo_pulse)
        vehicle.finish_step()  # 4. mj_step2 with the new controls
        self.step_index += 1
        self._post(t0, out, vehicle)

    def _run_overlapped(self, steps: int) -> None:
        """``steps`` master steps with no software interaction in between, overlapping
        MuJoCo's step halves (background worker) with the Python work they do not touch:

        * mj_step1 of step k  ||  steps 5-8 of step k-1 (from a snapshot of its state) and the
          bookkeeping half of step k's electrical step (model states, temperatures, outputs);
        * mj_step2 of step k  ||  the network solve of step k+1 (its inputs are final: software
          outputs cannot change between these steps; motor speed comes from mj_step1).

        Every quantity is read at the same point of the step sequence as in :meth:`step`,
        so the results are identical. Stops early after a brownout."""
        vehicle = self.vehicle
        # Split the electrical step only if it is the system's own (a wrapped instance ``step``,
        # e.g. fault injection, must run as a whole).
        split = (hasattr(self.electrical, "solve_step") and hasattr(self.electrical, "finish_step")
                 and "step" not in vars(self.electrical))
        frame: tuple[float, dict[str, Any], Any] | None = None
        prepared: tuple[Any, ...] | None = None
        for k in range(steps):
            t0 = self.t
            if prepared is None:
                inputs, motor_omega, esc_pulse, servo_pulse = self._inputs()
                vehicle.start_step()
                solution = (self.electrical.solve_step if split else self.electrical.step)(self.dt, inputs)
            else:
                inputs, motor_omega, esc_pulse, servo_pulse, solution = prepared
                vehicle.start_step()
            if frame is not None:
                self._post(*frame)
                frame = None
            out = self.electrical.finish_step(self.dt, *solution) if split else solution
            prepared = None
            self._apply(out, t0, motor_omega, esc_pulse, servo_pulse)
            vehicle.launch_step2()
            if k < steps - 1 and self.pi_powered:
                upcoming = self._inputs()
                solve = self.electrical.solve_step if split else self.electrical.step
                prepared = (*upcoming, solve(self.dt, upcoming[0]))
            vehicle.complete_step()
            self.step_index += 1
            frame = (t0, out, vehicle.state(moving_parts=vehicle.cables is None))
            if not self.pi_powered:
                break
        if frame is not None:
            self._post(*frame)

    def _post(self, t0: float, out: dict[str, Any], state: Any) -> None:
        """Steps 5-8 after the MuJoCo step: cable clearance, radio, sensors, UARTs, recording.
        ``state`` is the vehicle or a snapshot of it taken right after the step."""
        dt, t1 = self.dt, self.t
        position = state.position()
        rotation = state.rotation()
        # 5. cables: stepped inside MuJoCo (step 4); clearance from this step's contact data (captured by
        # Vehicle.complete_step in the overlapped loop and measured here, while the next mj_step1 runs).
        if self.vehicle.cables is not None:
            if self.vehicle.cables.pending:
                self.vehicle.cables.update()
            if float(self.vehicle.cables.current.min()) < self.min_wire_clearance[0]:
                self._update_min_clearance()
            if t1 + 1e-9 >= self._next_cable:
                self._next_cable += CABLE_PERIOD
                self.wire_clearance = self.vehicle.cables.wire_clearance()
        elif t1 + 1e-9 >= self._next_cable:
            self._next_cable += CABLE_PERIOD
            chassis = {"pos": position, "quat": state.quaternion()}
            report = self.cables.step(CABLE_PERIOD, chassis, state.moving_parts())
            self.wire_clearance = dict(getattr(report, "min_clearance", {}) or {})
            for wire, (distance, part) in self.wire_clearance.items():
                if distance < self.min_wire_clearance[0]:
                    self.min_wire_clearance = (float(distance), wire, part)
        # 6. radio.
        rx_powered = self.pi_powered and float(_first(out, [("_nets", "5V")], 5.0)) >= 4.5
        if self.transmitter_off is not None:
            on = not (self.transmitter_off[0] <= t1 < self.transmitter_off[1])
            if on != self.receiver.transmitter_on:
                self.recorder.events.append({"t": t1, "event": "transmitter_on" if on else "transmitter_off"})
            self.receiver.transmitter_on = on
        rx_pos = position + rotation @ self.rx_antenna_offset
        channels = self.pilot.channels_us(t1)
        frame_bytes = self.receiver.advance(t1, rx_pos, channels, rx_powered, rotation)
        if self.rx_uart is not None:
            self.rx_uart.push(frame_bytes, self.rng_uart)
        # 7. sensors.
        if self.gps is not None:
            self._rtk_step(t1, position, rotation)
            gps_pos = position + rotation @ self.gps_antenna_offset
            gps_bytes = self.gps.advance(t1, gps_pos, state.velocity(), self.pi_powered)
            if self.gps_uart is not None:
                self.gps_uart.push(gps_bytes, self.rng_uart)
        if self.imu is not None:
            self.imu.set_powered(self.pi_powered)
            acc, gyro = state.imu()
            self.imu.update(dt, acc, gyro, self.temperature_c + 8.0)
        if self.ina is not None:
            self.ina.set_powered(self.pi_powered)
            branch = _first(out, [("_branches", self.hat_supply_wire or "", "current"), (self.board_id or "", "current_in"),
                                  (self.board_id or "", "current")], 0.0)
            bus_v = _first(out, [(self.board_id or "", "v_in"), ("_nets", "VBAT"), (self.battery_id, "v_terminal")], 0.0)
            self.ina.update(dt, float(branch), float(bus_v))
        if self.encoder is not None and self.encoder_joint is not None:
            if self.encoder_carrier is not None:
                joints, ratio = self.encoder_carrier
                angle = ratio * sum(state.joint_angle(j) for j in joints) / len(joints)
            else:
                angle = state.joint_angle(self.encoder_joint)
            edges = self.encoder.update(t0, t1, angle)
            line = self.gpio_lines.get(self.encoder_line) if self.pi_powered else None
            for t_edge, rising in edges:
                if line is not None:
                    if rising:
                        line.rising_count += 1
                        line.last_rising_t = t_edge
                    if line.edge == "both" or (line.edge == "rising" and rising) or (line.edge == "falling" and not rising):
                        line.pending_edges.append((t_edge, rising))
        for uart in self.uarts.values():
            uart.deliver(t1)
        for camera in self.cameras.values():
            camera.sample(t1, state)
        # 8. record.
        if t1 + 1e-9 >= self._next_record:
            self._next_record += RECORD_PERIOD
            self._record(out, position, state)
        if t1 + 1e-9 >= self._next_snapshot:
            self._next_snapshot += SNAPSHOT_PERIOD
            self.recorder.snapshots_t.append(t1)
            self.recorder.snapshots_qpos.append(state.qpos())

    def _update_min_clearance(self) -> None:
        """Running minimum over every MuJoCo step (the monitor tracks it per bundle)."""
        for wire, (distance, part, _) in self.vehicle.cables.wire_minimum().items():
            if distance < self.min_wire_clearance[0]:
                self.min_wire_clearance = (distance, wire, part)

    def _update_power(self, powered: bool, t: float) -> None:
        if powered != self.pi_powered:
            for arbiter in set(self.arbiters.values()):  # arbiters run from the computer's 3V3
                arbiter.set_powered(t, powered)
        if self.pi_powered and not powered:
            self.pi_powered = False
            self.brownout_times.append(t)
            self.recorder.events.append({"t": t, "event": "brownout", "rail_5v": float(_first(self.last_outputs, [("_nets", "5V")], 0.0))})
            self.reset_software_outputs()
            self.boot_ready_t = None
            if self.on_brownout:
                self.on_brownout(t)
        elif not self.pi_powered and powered:
            self.pi_powered = True
            self.boot_ready_t = t + self.boot_time
            self.recorder.events.append({"t": t, "event": "power_restored", "boot_ready_t": self.boot_ready_t})
            if self.on_power_restored:
                self.on_power_restored(t)

    def _record(self, out: dict[str, Any], position: np.ndarray, state: Any = None) -> None:
        vehicle = self.vehicle
        state = vehicle if state is None else state
        sample = self.receiver.last_sample
        v = state.velocity()
        battery_v = float(_first(out, [(self.battery_id, "v_terminal")], 0.0))
        self.recorder.add(
            t=self.t, x=position[0], y=position[1], z=position[2], yaw=state.yaw(), speed=state.forward_speed(),
            vx=v[0], vy=v[1],
            steer=state.steer_angle(0) if self.vm.steering else 0.0,
            steer_target=0.5 * sum(vehicle.steer_targets[0]) if self.vm.steering else 0.0,
            horn=vehicle.servos[0].theta if vehicle.servos else 0.0,
            motor_omega=state.motor_omega(0) if self.vm.drives else 0.0,
            motor_torque=vehicle.motor_torque[0] if self.vm.drives else 0.0,
            motor_current=float(_first(out, [(self.motor_id, "current")], 0.0)),
            esc_pulse=self.esc_pulse, servo_pulse=self.servo_pulse,
            battery_v=battery_v, battery_i=float(_first(out, [(self.battery_id, "current")], 0.0)),
            soc=float(_first(out, [(self.battery_id, "soc")], 0.0)),
            cell_v=float(_first(out, [(self.battery_id, "cell_voltage")], battery_v / max(self.cells, 1))),
            rail_5v=float(_first(out, [("_nets", "5V"), (self.computer, "v_in")], 0.0)),
            bec_v=float(_first(out, [("_nets", "SERVO_6V"), ("_nets", "BEC_6V"), (self.esc_id, "bec_voltage")], 0.0)),
            esc_pulse_pi=next((v for k, v in self.pi_pulses.items() if k in self.arbiters), self.esc_pulse),
            arbiter_failsafe=1.0 if any(a.failsafe for a in self.arbiters.values()) else 0.0,
            software_frozen=1.0 if self.software_frozen else 0.0,
            hat_current=float(_first(out, [("_branches", self.hat_supply_wire or "", "current"), (self.board_id or "", "current_in")], 0.0)),
            servo_current=float(_first(out, [(self.servo_id, "current")], 0.0)),
            pi_powered=1.0 if self.pi_powered else 0.0, software=1.0 if self.software_running else 0.0,
            rssi=sample.rx_power_dbm if sample else float("nan"), snr=sample.snr_db if sample else float("nan"),
            per=sample.per if sample else float("nan"), lq=float(self.receiver.lq),
            multipath=sample.fading_db if sample else float("nan"), shadowing=sample.shadowing_db if sample else float("nan"),
            link=1.0 if self.receiver.connected else 0.0,
            tx_distance=float(np.linalg.norm(position[:2] - self.radio.tx_position[:2])),
            wire_clearance=min((d for d, _ in self.wire_clearance.values()), default=float("nan")),
            armed=1.0 if self.pilot.armed(self.t) else 0.0,
            gnss_mode=float({"single": 1, "float": 5, "fixed": 4}.get(getattr(self.gps, "mode", ""), 1 if self.gps else 0)),
            gnss_age=float(min(getattr(self.gps, "age", math.nan), 999.0)),
        )

    @property
    def freeze_due(self) -> bool:
        """A ``software_freeze`` fault is due now (the runner stops the program)."""
        return self.freeze_at is not None and not self.software_frozen and self.t + 1e-9 >= self.freeze_at

    def advance_until(self, done: Callable[[], bool], limit: float | None = None, deadline: float | None = None) -> str:
        """Step until ``done()``; returns 'done' | 'brownout' | 'freeze' | 'end' (scenario duration).
        ``deadline``: ``done()`` is exactly ``t >= deadline``, so the steps up to it can run
        with MuJoCo overlapped across steps (:meth:`_run_overlapped`). 'freeze': a software-freeze
        fault is due; the request in progress is never answered."""
        end = self.duration if limit is None else min(self.duration, limit)
        while True:
            if done():
                return "done"
            if not self.pi_powered:
                return "brownout"
            if self.freeze_due:
                return "freeze"
            if self.t >= end - 1e-9:
                return "end" if self.t >= self.duration - 1e-9 else "done"
            if deadline is not None and self.vehicle.overlapped:
                frozen_by = self.freeze_at if self.freeze_at is not None and not self.software_frozen else math.inf
                steps = self._steps_until(min(deadline, frozen_by), end)
                if steps > 1:
                    self._run_overlapped(steps)
                    continue
            self.step()

    def _steps_until(self, deadline: float, end: float) -> int:
        """Steps the serial loop would take before ``t >= deadline`` or ``t >= end`` (same
        float tests as :meth:`advance_until`, on ``t = step_index * dt``)."""
        steps = max(0, int((min(deadline, end) - self.t) / self.dt) - 1)
        while True:
            t = (self.step_index + steps) * self.dt
            if t + 1e-9 >= deadline or t >= end - 1e-9:
                return steps
            steps += 1

    def log(self, level: str, message: str, fields: dict[str, Any]) -> None:
        self.recorder.logs.append({"t": round(self.t, 6), "level": level, "message": message, "fields": fields})

    def summary_json(self) -> str:
        return json.dumps({"t": self.t})

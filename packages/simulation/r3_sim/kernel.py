"""Fixed-step co-simulation kernel.

Per step (``simulation.timestep``), in the documented order:

1. software outputs held by the twin HAL server (PWM, GPIO, UART TX) are applied;
2. device models turn them into ESC/servo pulses (only where the binding pins
   electrically reach the device);
3. ``ElectricalSystem.step`` solves the DC network -> motor torque, rails, brownout;
4. MuJoCo vehicle steps with motor torque (differential) and servo torque (Ackermann);
5. ``CableSimulator.step`` every 5 ms -> wire clearance to moving parts;
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
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from r3_model.diagnostics import Diagnostic
from r3_model.project import Project
from r3_model.spec import Scenario
from r3_model.units import to_si

from .devices import GpsReceiver, HallEncoder, Ina226, Mpu6050
from .fallbacks import FallbackCableSimulator, FallbackElectricalSystem
from .pilot import Pilot
from .radio import ElrsReceiver, RadioLink
from .signals import BindingMap, map_bindings, uart_paths
from .vehicle import Vehicle, build_vehicle_model
from .vehicle.mjcf import _behavior_params

CABLE_PERIOD = 0.005
RECORD_PERIOD = 0.01
SNAPSHOT_PERIOD = 1.0 / 30.0
UART_BUFFER_BYTES = 4096  # Linux n_tty receive buffer
DEFAULT_GPS_ORIGIN = (37.4275, -122.1697, 30.0)  # fixed geodetic origin of the local tangent plane (estimate)


def _get(mapping: Any, *path: str, default: Any = None) -> Any:
    for key in path:
        if not isinstance(mapping, dict) or key not in mapping:
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
        self.rng_radio, self.rng_imu, self.rng_gps, self.rng_encoder, self.rng_ina, self.rng_uart, self.rng_misc, _ = (
            np.random.default_rng(s) for s in streams)
        self.diagnostics: list[Diagnostic] = []
        self.assumptions: list[tuple[str, str]] = []
        self.step_index = 0
        self.duration = float(scenario.duration)
        initial = scenario.initial or {}
        pose = initial.get("pose", [0.0, 0.0, 0.0])
        self.initial_pose = (to_si(pose[0]), to_si(pose[1]), to_si(pose[2]) if len(pose) > 2 else 0.0)

        mission = scenario.mission or {}
        waypoints = mission.get("waypoints") or []
        self.vm = build_vehicle_model(project, self.initial_pose, spec.simulation.environment.obstacles, waypoints)
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
        routes = project.read_build_json("wiring/routes.json")
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
        self.radio = RadioLink(env, receiver_params, self.rng_radio)
        self.assumptions.extend(self.radio.assumptions())
        rx_uart = next((u for u in self.uarts.values() if u.component == self.receiver_id), None)
        self.rx_uart = rx_uart
        self.receiver = ElrsReceiver(self.radio, float(rx_uart.device_baud if rx_uart else 420000), self.rng_radio)
        self.rx_antenna_offset = np.zeros(3)
        if self.receiver_id:
            pos, _ = project.graph.component_pose(self.receiver_id)
            self.rx_antenna_offset = pos + np.array([0.0, 0.0, self.radio.antenna_height])

        origin = initial.get("gps_origin") or env.radio_environment.get("gps_origin") or DEFAULT_GPS_ORIGIN
        self.gps_origin = tuple(float(v) for v in (list(origin) + [DEFAULT_GPS_ORIGIN[2]])[:3])
        self.gps = GpsReceiver(_behavior_params(project, self.gps_id), self.gps_origin, self.rng_gps) if self.gps_id else None
        self.gps_uart = next((u for u in self.uarts.values() if u.component == self.gps_id), None)
        self.gps_antenna_offset = project.graph.component_pose(self.gps_id)[0] if self.gps_id else np.zeros(3)
        self.assumptions.append(("sensors", f"GPS local tangent plane: world +x east, +y north around "
                                            f"{self.gps_origin[0]:.5f}, {self.gps_origin[1]:.5f}; errors = Gauss-Markov "
                                            "(60 s) + white noise sized to the datasheet CEP."))

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
        self.encoder_line: tuple[str, int] | None = None
        self.encoder: HallEncoder | None = None
        self.encoder_joint: str | None = None
        for bmap in self.bindings.values():
            if bmap.interface == "gpio" and bmap.attachments:
                cid = bmap.attachments[0].component
                ctype = project.catalog.component(spec.components[cid].type)
                if ctype.behavior and ctype.behavior.model.startswith("encoder"):
                    params = _behavior_params(project, cid)
                    self.encoder = HallEncoder(int(params.get("pulses_per_wheel_rev", 1)), to_si(params.get("jitter", 0.0)),
                                               self.rng_encoder)
                    self.encoder_line = (bmap.device, int(bmap.channel or 0))
                    self.encoder_joint = self._nearest_drive_joint(cid)
                    self.assumptions.append(("sensors", f"Hall encoder magnets ride on {self.encoder_joint} (drive joint nearest "
                                                        f"to {cid})."))

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
        self._prev_v: np.ndarray | None = None
        self._prev_w: np.ndarray | None = None
        self._next_record = 0.0
        self._next_snapshot = 0.0
        self._next_cable = 0.0
        self.esc_pulse = 0.0
        self.servo_pulse = 0.0
        self.temperature_c = env.ambient_temperature_c
        self.on_brownout: Callable[[float], None] | None = None
        self.on_power_restored: Callable[[float], None] | None = None

    # ------------------------------------------------------------------ setup helpers
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
        try:
            from r3_wiring import CableSimulator  # type: ignore[attr-defined]
        except Exception as error:  # noqa: BLE001
            self._fallback_warning("cable_model_fallback", "r3_wiring.CableSimulator", error)
            return FallbackCableSimulator(self.project, routes, self.vm.geometry)
        try:
            return CableSimulator(self.project, routes)
        except Exception as error:  # noqa: BLE001
            self._fallback_warning("cable_model_fallback", "CableSimulator construction", error)
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
        for key, devices in self.pwm_devices.items():
            if component in devices:
                state = self.pwm_state.get(key)
                if state and state["enabled"] and state["period_ns"] > 0 and self.pi_powered:
                    return state["duty_ns"] / 1000.0
        return 0.0

    def step(self) -> None:
        dt, t0 = self.dt, self.t
        vehicle = self.vehicle
        # 1-2. software outputs -> device inputs.
        self.esc_pulse = self._pulse_for(self.esc_id)
        self.servo_pulse = self._pulse_for(self.servo_id)
        servo = vehicle.servos[0] if vehicle.servos else None
        motor_omega = vehicle.motor_omega(0) if self.vm.drives else 0.0
        activity = 0.0 if not self.pi_powered else (0.55 if self.software_running else 0.8)
        inputs = {
            self.esc_id: {"pulse_us": self.esc_pulse},
            self.motor_id: {"omega": motor_omega},
            self.servo_id: {"pulse_us": self.servo_pulse, "horn_torque": servo.last_torque if servo else 0.0,
                            "horn_speed": servo.omega if servo else 0.0},
            self.computer: {"activity": activity},
        }
        # 3. electrical network.
        out = self.electrical.step(dt, inputs)
        self.last_outputs = out
        torque = float(_first(out, [(self.motor_id, "torque")], 0.0))
        powered = _first(out, [(self.computer, "powered")])
        if powered is None:
            powered = float(_first(out, [("_nets", "5V")], 5.0)) >= self.brownout_voltage
        self._update_power(bool(powered), t0)
        servo_v = float(_first(out, [(self.servo_id, "voltage"), (self.servo_id, "v_supply"), (self.esc_id, "bec_voltage"),
                                     ("_nets", "BEC_6V")], 6.0))
        # 4. vehicle physics.
        if self.vm.drives:
            vehicle.set_motor_torque(0, torque)
        if self.vm.steering:
            vehicle.step_servo(0, dt, self.servo_pulse, servo_v)
        vehicle.step()
        self.step_index += 1
        t1 = self.t
        position = vehicle.position()
        rotation = vehicle.rotation()
        # 5. cables.
        if t1 + 1e-9 >= self._next_cable:
            self._next_cable += CABLE_PERIOD
            chassis = vehicle.chassis_state(self._prev_v, self._prev_w, CABLE_PERIOD)
            self._prev_v, self._prev_w = chassis["lin_vel"], chassis["ang_vel"]
            report = self.cables.step(CABLE_PERIOD, chassis, vehicle.moving_parts())
            self.wire_clearance = dict(getattr(report, "min_clearance", {}) or {})
            for wire, (distance, part) in self.wire_clearance.items():
                if distance < self.min_wire_clearance[0]:
                    self.min_wire_clearance = (float(distance), wire, part)
        # 6. radio.
        rx_powered = self.pi_powered and float(_first(out, [("_nets", "5V")], 5.0)) >= 4.5
        rx_pos = position + rotation @ self.rx_antenna_offset
        channels = self.pilot.channels_us(t1)
        frame_bytes = self.receiver.advance(t1, rx_pos, channels, rx_powered)
        if self.rx_uart is not None:
            self.rx_uart.push(frame_bytes, self.rng_uart)
        # 7. sensors.
        if self.gps is not None:
            gps_pos = position + rotation @ self.gps_antenna_offset
            gps_bytes = self.gps.advance(t1, gps_pos, vehicle.velocity(), self.pi_powered)
            if self.gps_uart is not None:
                self.gps_uart.push(gps_bytes, self.rng_uart)
        if self.imu is not None:
            self.imu.set_powered(self.pi_powered)
            acc, gyro = vehicle.imu()
            self.imu.update(dt, acc, gyro, self.temperature_c + 8.0)
        if self.ina is not None:
            self.ina.set_powered(self.pi_powered)
            branch = _first(out, [("_branches", self.hat_supply_wire or "", "current"), (self.board_id or "", "current_in"),
                                  (self.board_id or "", "current")], 0.0)
            bus_v = _first(out, [(self.board_id or "", "v_in"), ("_nets", "VBAT"), (self.battery_id, "v_terminal")], 0.0)
            self.ina.update(dt, float(branch), float(bus_v))
        if self.encoder is not None and self.encoder_joint is not None:
            edges = self.encoder.update(t0, t1, vehicle.joint_angle(self.encoder_joint))
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
        # 8. record.
        if t1 + 1e-9 >= self._next_record:
            self._next_record += RECORD_PERIOD
            self._record(out, position)
        if t1 + 1e-9 >= self._next_snapshot:
            self._next_snapshot += SNAPSHOT_PERIOD
            self.recorder.snapshots_t.append(t1)
            self.recorder.snapshots_qpos.append(vehicle.data.qpos.copy())

    def _update_power(self, powered: bool, t: float) -> None:
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

    def _record(self, out: dict[str, Any], position: np.ndarray) -> None:
        vehicle = self.vehicle
        sample = self.receiver.last_sample
        v = vehicle.velocity()
        battery_v = float(_first(out, [(self.battery_id, "v_terminal")], 0.0))
        self.recorder.add(
            t=self.t, x=position[0], y=position[1], z=position[2], yaw=vehicle.yaw(), speed=vehicle.forward_speed(),
            vx=v[0], vy=v[1],
            steer=vehicle.steer_angle(0) if self.vm.steering else 0.0,
            steer_target=0.5 * sum(vehicle.steer_targets[0]) if self.vm.steering else 0.0,
            horn=vehicle.servos[0].theta if vehicle.servos else 0.0,
            motor_omega=vehicle.motor_omega(0) if self.vm.drives else 0.0,
            motor_torque=vehicle.motor_torque[0] if self.vm.drives else 0.0,
            motor_current=float(_first(out, [(self.motor_id, "current")], 0.0)),
            esc_pulse=self.esc_pulse, servo_pulse=self.servo_pulse,
            battery_v=battery_v, battery_i=float(_first(out, [(self.battery_id, "current")], 0.0)),
            soc=float(_first(out, [(self.battery_id, "soc")], 0.0)),
            cell_v=float(_first(out, [(self.battery_id, "cell_voltage")], battery_v / max(self.cells, 1))),
            rail_5v=float(_first(out, [("_nets", "5V"), (self.computer, "v_in")], 0.0)),
            bec_v=float(_first(out, [("_nets", "BEC_6V"), (self.esc_id, "bec_voltage")], 0.0)),
            hat_current=float(_first(out, [("_branches", self.hat_supply_wire or "", "current"), (self.board_id or "", "current_in")], 0.0)),
            servo_current=float(_first(out, [(self.servo_id, "current")], 0.0)),
            pi_powered=1.0 if self.pi_powered else 0.0, software=1.0 if self.software_running else 0.0,
            rssi=sample.rx_power_dbm if sample else float("nan"), snr=sample.snr_db if sample else float("nan"),
            per=sample.per if sample else float("nan"), lq=float(self.receiver.lq),
            link=1.0 if self.receiver.connected else 0.0,
            tx_distance=float(np.linalg.norm(position[:2] - self.radio.tx_position[:2])),
            wire_clearance=min((d for d, _ in self.wire_clearance.values()), default=float("nan")),
            armed=1.0 if self.pilot.armed(self.t) else 0.0,
        )

    def advance_until(self, done: Callable[[], bool], limit: float | None = None) -> str:
        """Step until ``done()``; returns 'done' | 'brownout' | 'end' (scenario duration)."""
        end = self.duration if limit is None else min(self.duration, limit)
        while True:
            if done():
                return "done"
            if not self.pi_powered:
                return "brownout"
            if self.t >= end - 1e-9:
                return "end" if self.t >= self.duration - 1e-9 else "done"
            self.step()

    def log(self, level: str, message: str, fields: dict[str, Any]) -> None:
        self.recorder.logs.append({"t": round(self.t, 6), "level": level, "message": message, "fields": fields})

    def summary_json(self) -> str:
        return json.dumps({"t": self.t})

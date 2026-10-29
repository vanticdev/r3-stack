"""Regulators and loads: ``regulator.buck``, ``load.computer``, ``servo.pwm`` and
small constant-current loads (receiver, GNSS, encoder, IMU, power monitor)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..mna import Circuit, NumericElement, SpiceWriter, smooth_min, softplus, spice_smooth_min, spice_softplus
from .base import BehaviorModel, Event, Params, SupplyLoad, constant_current, first_order, voltage
from .battery import ctype_thermal
from .esc import RegulatorOutput

# Typical supply currents when the catalog gives none (A).
DEVICE_CURRENTS = {
    "ina226": (330e-6, "TI INA226 datasheet: IQ 330 uA typical"),
    "power_monitor.ina226": (330e-6, "TI INA226 datasheet: IQ 330 uA typical"),
    "mpu6050": (3.9e-3, "InvenSense MPU-6000/6050 datasheet: 3.9 mA gyro+accel"),
    "imu.mpu6050": (3.9e-3, "InvenSense MPU-6000/6050 datasheet: 3.9 mA gyro+accel"),
}


class BuckCore:
    """Synchronous buck converter between explicit nodes.

    V_out = min(V_set - R_o I, V_in - V_drop - R_drop I) with current-limit
    foldback; I_in = V_out I / (eta(I) V_in) + I_q (power balance).
    UVLO: off below ``uvlo_falling`` (default: the rated ``v_in_min``), back on
    above ``uvlo_rising``. Junction: T = T_amb + theta P_loss (first order)."""

    def __init__(self, owner: str, params: Params, vin: int, vout: int, gnd: int, thermal: dict[str, Any], ambient_c: float) -> None:
        self.owner = owner
        p = params
        self.v_out = p.get("v_out")
        self.i_max = p.get("i_max")
        self.v_in_min = p.get("v_in_min", 0.0)
        self.v_in_max = p.get("v_in_max", math.inf)
        self.uvlo_falling = p.get("uvlo_falling", self.v_in_min)
        self.uvlo_rising = p.get("uvlo_rising", self.uvlo_falling + 0.3)
        self.quiescent = p.get("quiescent_current", 0.0)
        self.r_out = p.get("output_resistance", 0.005)
        self.dropout = p.get("dropout_voltage", 0.1)
        self.r_dropout = p.get("dropout_resistance", 0.05)
        table = p.raw("efficiency")
        self.efficiency = np.asarray(table, dtype=float) if table else np.array([[0.0, 0.9], [1.0, 0.9]])
        self.theta = float(thermal.get("theta_ja_c_per_w", 40.0))
        self.max_temp_c = float(thermal.get("max_temp_c", 125.0))
        self.heat_capacity = p.get("heat_capacity_j_per_k", 2.0)
        self.vin, self.vout, self.gnd = vin, vout, gnd
        self.ambient_c = ambient_c
        self.s_enabled = True
        self.s_temperature = ambient_c
        self.s_overtemperature = False
        self._toggled = False

    def build(self, circuit: Circuit) -> None:
        self.branch = circuit.branch(f"{self.owner}#buck")
        self.element = circuit.add(RegulatorOutput(
            f"{self.owner} buck", self.vout, self.gnd, self.vin, self.branch, self.v_out, self.r_out, self.dropout,
            self.r_dropout, self.i_max, "switching", self.efficiency))
        fn, expr = constant_current(self.quiescent, 1.0)
        circuit.add(SupplyLoad(f"{self.owner} buck quiescent", self.vin, self.gnd, fn, expr))

    def begin_step(self) -> None:
        self.element.enabled = self.s_enabled
        self._toggled = False

    def resolve(self, x: np.ndarray) -> bool:
        if self._toggled:
            return False
        vin = voltage(x, self.vin, self.gnd)
        if self.s_enabled and vin < self.uvlo_falling:
            self.s_enabled = self.element.enabled = False
        elif not self.s_enabled and vin > self.uvlo_rising:
            self.s_enabled = self.element.enabled = True
        else:
            return False
        self._toggled = True
        return True

    def state(self, x: np.ndarray) -> dict[str, float]:
        vin = voltage(x, self.vin, self.gnd)
        vout = voltage(x, self.vout, self.gnd)
        i = float(x[self.branch]) if self.s_enabled else 0.0
        drawn = self.element.input_current(vout, vin, i) if self.s_enabled else 0.0
        p_in = vin * (drawn + self.quiescent)
        p_out = vout * i
        return {"v_in": vin, "v_out": vout, "i_out": i, "i_in": drawn + self.quiescent, "efficiency": self.element.eta(i),
                "power_in": p_in, "power_out": p_out, "power_loss": p_in - p_out, "enabled": self.s_enabled,
                "temperature_c": self.s_temperature, "uvlo_falling": self.uvlo_falling}

    def end_step(self, dt: float | None, x: np.ndarray, time: float) -> list[Event]:
        events: list[Event] = []
        state = self.state(x)
        if self._toggled:
            code = "regulator_uvlo" if not self.s_enabled else "regulator_restart"
            events.append(Event(time, code, self.owner, f"buck input {state['v_in']:.2f} V (UVLO {self.uvlo_falling} V)",
                                {"v_in": state["v_in"], "uvlo_falling": self.uvlo_falling, "uvlo_rising": self.uvlo_rising}))
        if dt is None:
            return events
        self.s_temperature = first_order(self.s_temperature, self.ambient_c + state["power_loss"] * self.theta, dt,
                                         self.theta * self.heat_capacity)
        hot = self.s_temperature > self.max_temp_c
        if hot and not self.s_overtemperature:
            events.append(Event(time, "regulator_overtemperature", self.owner, f"buck {self.s_temperature:.0f} C",
                                {"temperature_c": self.s_temperature, "limit": self.max_temp_c}))
        self.s_overtemperature = hot
        return events

    def snapshot(self) -> dict[str, Any]:
        return {"s_enabled": self.s_enabled, "s_temperature": self.s_temperature, "s_overtemperature": self.s_overtemperature}

    def restore(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)


class BuckRegulator(BehaviorModel):
    model = "regulator.buck"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        vin = self.require(self.pin_by_role("power_in"), "power_in")
        vout = self.require(self.pin_by_role("power_out"), "power_out")
        gnd = self.require(self.pin_by_role("ground"), "ground")
        self.core = BuckCore(self.id, self.params, vin.node, vout.node, gnd.node, ctype_thermal(self.ctype), self.ambient_c)

    def build(self, circuit: Circuit) -> None:
        self.core.build(circuit)

    def begin_step(self, dt: float | None, inputs: dict[str, Any], time: float) -> None:
        self.core.begin_step()

    def resolve(self, x: np.ndarray) -> bool:
        return self.core.resolve(x)

    def end_step(self, dt: float | None, x: np.ndarray, time: float) -> list[Event]:
        return self.core.end_step(dt, x, time)

    def outputs(self, x: np.ndarray) -> dict[str, Any]:
        return self.core.state(x)

    def snapshot(self) -> dict[str, Any]:
        return self.core.snapshot()

    def restore(self, state: dict[str, Any]) -> None:
        self.core.restore(state)


class ComputerSupply(NumericElement):
    """Constant-power computer input: I = (P + sum V_rail,set i_rail / eta) / V,
    resistive below ``v_knee``. Rail currents are unknowns of the rail sources."""

    def __init__(self, name: str, vin: int, gnd: int, rails: list[tuple[int, float]], v_knee: float, rail_efficiency: float) -> None:
        self.name = name
        self.vin, self.gnd = vin, gnd
        self.rails = rails  # (branch index, set voltage)
        self.rows = [vin, gnd]
        self.deps = [vin, gnd] + [branch for branch, _ in rails]
        self.v_knee = v_knee
        self.rail_efficiency = rail_efficiency
        self.power = 0.0

    def total_power(self, rail_currents: np.ndarray) -> float:
        return self.power + sum(v * max(i, 0.0) for (_, v), i in zip(self.rails, rail_currents)) / self.rail_efficiency

    def residual(self, v: np.ndarray) -> np.ndarray:
        vin = v[0] - v[1]
        power = self.total_power(v[2:])
        i = power / vin if vin >= self.v_knee else power * vin / (self.v_knee ** 2)
        return np.array([i, -i])

    def spice(self, writer: SpiceWriter, x: np.ndarray) -> None:
        terms = [f"{self.power:.12g}"]
        for branch, v_set in self.rails:
            if branch in writer.sense:  # rail enabled: its current is measured by a 0 V source
                terms.append(f"{v_set:.12g}*max(I({writer.sense[branch]}),0)/{self.rail_efficiency:.12g}")
        power = "(" + "+".join(terms) + ")"
        v = writer.v(self.vin, self.gnd)
        writer.comment(f"{self.name}: constant-power computer load")
        writer.add(f"{writer.name('B')} {writer.node(self.vin)} {writer.node(self.gnd)} I = "
                   f"({v} >= {self.v_knee:.12g} ? {power}/{v} : {power}*{v}/{self.v_knee ** 2:.12g})")


class ComputerLoad(BehaviorModel):
    """``load.computer``: P = P_idle + activity (P_active - P_idle) when booted,
    the midpoint while booting and ``reset_power_fraction`` P_idle in reset.
    Brownout below ``brownout_voltage`` -> reset; power returns above
    brownout + hysteresis and the OS needs ``boot_time`` before software runs.
    Output rails (``power_out`` pins, e.g. 3V3) are regulated sources whose
    power is drawn from the input with ``rail_efficiency``."""

    model = "load.computer"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        p = self.params
        self.p_idle = p.get("power_idle")
        self.p_active = p.get("power_active", self.p_idle)
        self.brownout_voltage = p.get("brownout_voltage")
        self.hysteresis = p.get("brownout_hysteresis", 0.1)
        self.boot_time = p.get("boot_time", 0.0)
        self.reset_fraction = p.get("reset_power_fraction", 0.3)
        self.rail_max = p.get("rail_3v3_max", 0.5)
        self.rail_efficiency = p.get("rail_efficiency", 0.9)
        self.rail_resistance = p.get("rail_output_resistance", 0.05)
        self.vin = self.require(self.pin_by_role("power_in"), "power_in")
        self.gnd = self.require(self.pin_by_role("ground"), "ground")
        self.rail_pins = self.pins_by_role("power_out")
        self.activity = 0.0
        self.s_powered = True
        self.s_booted = True
        self.s_boot_elapsed = 0.0
        self.s_brownouts = 0
        self._brownout_now = False

    def build(self, circuit: Circuit) -> None:
        self.rails: list[tuple[PinRefLike, int, RegulatorOutput]] = []
        for pin in self.rail_pins:
            branch = circuit.branch(f"{self.id}#{pin.name}")
            v_set = pin.voltage_nominal or 3.3
            rail = circuit.add(RegulatorOutput(f"{self.id} {pin.name} rail", pin.node, self.gnd.node, self.vin.node, branch,
                                               v_set, self.rail_resistance, 0.1, 0.0, self.rail_max, "none"))
            self.rails.append((pin, branch, rail))
        v_knee = 0.5 * (self.vin.voltage_nominal or 5.0)
        self.supply = circuit.add(ComputerSupply(f"{self.id} ({self.model})", self.vin.node, self.gnd.node,
                                                 [(b, r.v_set) for _, b, r in self.rails], v_knee, self.rail_efficiency))

    def initialize(self, initial: dict[str, Any]) -> None:
        if initial.get("cold_start"):
            self.s_booted = False
            self.s_boot_elapsed = 0.0

    def power(self) -> float:
        if not self.s_powered:
            return self.reset_fraction * self.p_idle
        if not self.s_booted:
            return 0.5 * (self.p_idle + self.p_active)
        return self.p_idle + self.activity * (self.p_active - self.p_idle)

    def begin_step(self, dt: float | None, inputs: dict[str, Any], time: float) -> None:
        self.activity = max(0.0, min(1.0, float(inputs.get("activity", self.activity if dt is not None else 0.0))))
        self.supply.power = self.power()
        for _, _, rail in self.rails:
            rail.enabled = self.s_powered

    def end_step(self, dt: float | None, x: np.ndarray, time: float) -> list[Event]:
        v = voltage(x, self.vin.node, self.gnd.node)
        self._brownout_now = v < self.brownout_voltage
        events: list[Event] = []
        if dt is None:
            return events
        if self.s_powered and self._brownout_now:
            self.s_powered = self.s_booted = False
            self.s_brownouts += 1
            events.append(Event(time, "brownout", self.id, f"supply {v:.3f} V below {self.brownout_voltage} V",
                                {"voltage": v, "threshold": self.brownout_voltage}))
        elif not self.s_powered and v >= self.brownout_voltage + self.hysteresis:
            self.s_powered, self.s_boot_elapsed = True, 0.0
            events.append(Event(time, "power_restored", self.id, f"supply {v:.3f} V, rebooting ({self.boot_time:.0f} s)",
                                {"voltage": v, "boot_time": self.boot_time}))
        elif self.s_powered and not self.s_booted:
            self.s_boot_elapsed += dt
            if self.s_boot_elapsed >= self.boot_time:
                self.s_booted = True
                events.append(Event(time, "boot_complete", self.id, "software running", {"boot_time": self.boot_time}))
        return events

    def outputs(self, x: np.ndarray) -> dict[str, Any]:
        v = voltage(x, self.vin.node, self.gnd.node)
        rails = {pin.name: {"voltage": voltage(x, pin.node, self.gnd.node), "current": float(x[branch]) if rail.enabled else 0.0}
                 for pin, branch, rail in self.rails}
        current = float(self.supply.evaluate(x)[0])
        powered = self.s_powered and v >= self.brownout_voltage
        return {"powered": powered, "booted": self.s_booted and powered, "software_running": self.s_booted and powered,
                "voltage": v, "brownout": self._brownout_now, "brownout_count": self.s_brownouts,
                "brownout_margin": v - self.brownout_voltage, "power": v * current, "current": current,
                "activity": self.activity, "rails": rails}


PinRefLike = Any


class ServoSupply(NumericElement):
    """Servo supply current from horn torque and speed via its DC motor
    referred to the horn: R = V_rated / (I_stall - I_idle), Kt = T_stall /
    (I_stall - I_idle), Ke = V_rated / w_no_load.

    I_m = min(|T| / Kt, (V - Ke |w|) / R)   (saturates at the supply)
    I_supply = I_idle + (I_m^2 R + Ke |w| I_m) / V   (power balance of the drive)
    At stall (w = 0, demanded torque >= available) this is I_idle + V / R."""

    def __init__(self, name: str, vin: int, gnd: int, r: float, kt: float, ke: float, idle: float) -> None:
        self.name = name
        self.vin, self.gnd = vin, gnd
        self.rows = [vin, gnd]
        self.deps = [vin, gnd]
        self.r, self.kt, self.ke, self.idle = r, kt, ke, idle
        self.torque = 0.0
        self.speed = 0.0
        self.v_knee = 1.0

    def motor_current(self, v: float) -> float:
        need = abs(self.torque) / self.kt
        cap = softplus((v - self.ke * abs(self.speed)) / self.r, 0.01)
        return max(0.0, smooth_min(need, cap, 0.01))

    def current(self, v: float) -> float:
        i_m = self.motor_current(v)
        power = i_m * i_m * self.r + self.ke * abs(self.speed) * i_m
        idle = self.idle if v >= self.v_knee else self.idle * max(v, 0.0) / self.v_knee
        return idle + power / max(v, self.v_knee)

    def residual(self, v: np.ndarray) -> np.ndarray:
        i = self.current(v[0] - v[1])
        return np.array([i, -i])

    def spice(self, writer: SpiceWriter, x: np.ndarray) -> None:
        v = writer.v(self.vin, self.gnd)
        emf = self.ke * abs(self.speed)
        cap = spice_softplus(f"({v}-{emf:.12g})/{self.r:.12g}", 0.01)
        i_m = f"max(0,{spice_smooth_min(f'{abs(self.torque) / self.kt:.12g}', cap, 0.01)})"
        power = f"({i_m}*{i_m}*{self.r:.12g}+{emf:.12g}*{i_m})"
        idle = f"({v} >= {self.v_knee} ? {self.idle:.12g} : {self.idle:.12g}*max({v},0)/{self.v_knee})"
        writer.comment(f"{self.name}: servo torque={self.torque:.6g} N m speed={self.speed:.6g} rad/s")
        writer.add(f"{writer.name('B')} {writer.node(self.vin)} {writer.node(self.gnd)} I = {idle}+{power}/max({v},{self.v_knee})")


class ServoPwm(BehaviorModel):
    model = "servo.pwm"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        p = self.params
        self.vin = self.require(self.pin_by_role("power_in"), "power_in")
        self.gnd = self.require(self.pin_by_role("ground"), "ground")
        self.v_rated = p.get("rated_voltage", self.vin.voltage_nominal or 6.0)
        self.stall_torque = p.get("stall_torque_at_6v")
        self.stall_current = p.get("stall_current_at_6v")
        self.idle = p.get("idle_current", 0.0)
        speed_60 = p.get("speed_60deg_at_6v")
        self.no_load_speed = (math.pi / 3.0) / speed_60
        self.r = self.v_rated / (self.stall_current - self.idle)
        self.kt = self.stall_torque / (self.stall_current - self.idle)
        self.ke = self.v_rated / self.no_load_speed
        self.pulse_min = p.get("min_pulse", 1000e-6)
        self.pulse_max = p.get("max_pulse", 2000e-6)
        self.travel = p.get("travel", math.pi / 2)
        self.v_min = self.vin.voltage_min or 0.8 * self.v_rated
        self.commanded_angle = 0.0

    def build(self, circuit: Circuit) -> None:
        self.element = circuit.add(ServoSupply(f"{self.id} ({self.model})", self.vin.node, self.gnd.node,
                                               self.r, self.kt, self.ke, self.idle))

    def begin_step(self, dt: float | None, inputs: dict[str, Any], time: float) -> None:
        self.element.torque = float(inputs.get("horn_torque", 0.0))
        self.element.speed = float(inputs.get("horn_speed", 0.0))
        pulse = inputs.get("pulse_us")
        if pulse is not None:
            seconds = float(pulse) * 1e-6
            center = 0.5 * (self.pulse_min + self.pulse_max)
            self.commanded_angle = (seconds - center) / (self.pulse_max - self.pulse_min) * self.travel

    def outputs(self, x: np.ndarray) -> dict[str, Any]:
        v = voltage(x, self.vin.node, self.gnd.node)
        e = self.element
        available = self.kt * max(0.0, (v - self.ke * abs(e.speed)) / self.r)
        return {"supply_voltage": v, "current": e.current(v), "max_torque": available,
                "no_load_speed": max(v, 0.0) / self.ke, "stall_current": self.idle + max(v, 0.0) / self.r,
                "commanded_angle": self.commanded_angle, "powered": v >= self.v_min,
                "saturated": abs(e.torque) / self.kt > e.motor_current(v) + 1e-3}


class ConstantLoad(BehaviorModel):
    """Small electronics load: constant current on its ``power_in`` pin(s),
    resistive below ``voltage_min`` (or 0.8 x nominal)."""

    def __init__(self, component_id: str, ctype: Any, params: dict[str, Any], ctx: Any, model: str = "") -> None:
        super().__init__(component_id, ctype, params, ctx)
        self.model = model
        self.vin = self.require(self.pin_by_role("power_in"), "power_in")
        self.gnd = self.require(self.pin_by_role("ground"), "ground")
        self.current_source = "catalog"
        for key in ("current_active", "current", "supply_current"):
            if self.params.has(key):
                self.current = self.params.get(key)
                break
        else:
            if model in DEVICE_CURRENTS:
                self.current, self.current_source = DEVICE_CURRENTS[model][0], DEVICE_CURRENTS[model][1]
            elif self.vin.current_max:
                self.current, self.current_source = self.vin.current_max, "pin current_max used as estimate"
            else:
                self.current, self.current_source = 0.01, "estimated 10 mA"
            self.params.estimated["current"] = self.current
        nominal = self.vin.voltage_nominal or 5.0
        self.v_min = self.vin.voltage_min or 0.8 * nominal

    def build(self, circuit: Circuit) -> None:
        fn, expr = constant_current(self.current, self.v_min)
        self.element = circuit.add(SupplyLoad(f"{self.id} ({self.model})", self.vin.node, self.gnd.node, fn, expr))

    def outputs(self, x: np.ndarray) -> dict[str, Any]:
        v = voltage(x, self.vin.node, self.gnd.node)
        return {"supply_voltage": v, "current": self.element.current(x), "powered": v >= self.v_min}

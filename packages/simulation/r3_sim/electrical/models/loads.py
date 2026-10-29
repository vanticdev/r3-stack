"""Regulators and loads: ``regulator.buck``, ``load.computer``, ``servo.pwm`` and
small constant-current loads (receiver, GNSS, encoder, IMU, power monitor)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..netlist import Netlist, Solution, fmt, smooth_min, softplus
from .base import BehaviorModel, Event, Params, constant_current, gated_power
from .battery import ctype_thermal
from .regulator import ENABLE_CARD, ENABLE_MODEL, efficiency_at, regulator_lines

# Typical supply currents when the catalog gives none (A).
DEVICE_CURRENTS = {
    "ina226": (330e-6, "TI INA226 datasheet: IQ 330 uA typical"),
    "power_monitor.ina226": (330e-6, "TI INA226 datasheet: IQ 330 uA typical"),
    "mpu6050": (3.9e-3, "InvenSense MPU-6000/6050 datasheet: 3.9 mA gyro+accel"),
    "imu.mpu6050": (3.9e-3, "InvenSense MPU-6000/6050 datasheet: 3.9 mA gyro+accel"),
}


class BuckCore:
    """Synchronous buck converter between ports of its owner's subcircuit.

    V_out = min(V_set - R_o I, V_in - V_drop - R_drop I) with current-limit
    foldback; I_in = V_out I / (eta(I) V_in) + I_q (power balance).
    UVLO is a SPICE ``sw`` comparator with hysteresis on V_in in series with
    the output: off below ``uvlo_falling`` (default: the rated ``v_in_min``),
    back on above ``uvlo_rising``. A second switch with the same control and
    model drives a 0/1 readout node (``reg_en``). Junction: thermal RC heated
    by P_in - P_out."""

    def __init__(self, owner: BehaviorModel, params: Params, thermal: dict[str, Any]) -> None:
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
        self.t_node = owner.local("t")
        owner.states[self.t_node] = owner.ambient_c
        self.s_enabled = True
        self.s_overtemperature = False
        self._toggled = False

    @property
    def s_temperature(self) -> float:
        return self.owner.states[self.t_node]

    def lines(self, net: Netlist, vin: str, vout: str, gnd: str) -> tuple[list[str], str | None]:
        """Subcircuit lines; returns (lines, node for the owner's ``reg_ctl``
        port in operating points, else None)."""
        initial = "on" if self.s_enabled else "off"
        control: str | None = None
        if net.transient:  # comparator with hysteresis on V_in
            model = f"swuvlo_{self.owner.inst[1:]}"
            center, half = 0.5 * (self.uvlo_rising + self.uvlo_falling), 0.5 * (self.uvlo_rising - self.uvlo_falling)
            net.model(model, f".model {model} sw vt={fmt(center)} vh={fmt(half)} ron=1e-5 roff=1e9")
            sense = (vin, gnd)
        else:  # operating points: enable from the active set in ``resolve`` (hysteresis is path dependent in DC)
            model = ENABLE_MODEL
            net.model(ENABLE_MODEL, ENABLE_CARD)
            sense = ("reg_ctl", "0")
            control = self.owner.control(net, "uvlo_enable", 1.0 if self.s_enabled else 0.0)
        lines = regulator_lines(net, "reg", vout, gnd, vin, self.v_out, self.r_out, self.dropout, self.r_dropout, self.i_max,
                                "switching", self.efficiency, enable=(*sense, model, initial))
        lines += [
            f"vregq {vin} reg_q dc 0",
            f"bregq reg_q {gnd} i = {constant_current(self.quiescent, 1.0, f'v({vin},{gnd})')}",
            # UVLO state readout: the same comparator switching a 1 V source
            "vreg_one reg_one 0 dc 1",
            f"sreg_read reg_one reg_en {sense[0]} {sense[1]} {model} {initial}",
            "rreg_read reg_en 0 1k",
            f"bregloss reg_ploss 0 v = v({vin},{gnd})*(i(vregin)+i(vregq))-v({vout},{gnd})*i(vreg)",
        ]
        self.owner.thermal(net, "t", self.heat_capacity, self.theta, "v(reg_ploss)", lines)
        net.state(self.t_node, self.owner.states[self.t_node])
        return lines, control

    def prepare(self) -> None:
        """Operating point: the UVLO enable starts from the current state."""
        self.owner.controls["uvlo_enable"] = 1.0 if self.s_enabled else 0.0
        self._toggled = False

    def resolve(self, sol: Solution, vin: str, gnd: str) -> bool:
        """Operating-point active set: off below ``uvlo_falling``, on above
        ``uvlo_rising``; True when the point must be re-solved."""
        if self._toggled:
            return False
        v_in = sol.v(vin, gnd)
        if self.s_enabled and v_in < self.uvlo_falling:
            self.s_enabled = False
        elif not self.s_enabled and v_in > self.uvlo_rising:
            self.s_enabled = True
        else:
            return False
        self._toggled = True
        self.owner.controls["uvlo_enable"] = 1.0 if self.s_enabled else 0.0
        return True

    def enabled(self, sol: Solution) -> bool:
        return sol.get(self.owner.internal("reg_en")) > 0.5

    def state(self, sol: Solution, vin: str, vout: str, gnd: str) -> dict[str, Any]:
        owner = self.owner
        v_in = sol.v(vin, gnd)
        v_out = sol.v(vout, gnd)
        i = sol.i(owner.sense("vreg"))
        i_in = sol.i(owner.sense("vregin")) + sol.i(owner.sense("vregq"))
        p_in, p_out = v_in * i_in, v_out * i
        return {"v_in": v_in, "v_out": v_out, "i_out": i, "i_in": i_in, "efficiency": efficiency_at(self.efficiency, i),
                "power_in": p_in, "power_out": p_out, "power_loss": sol.get(owner.internal("reg_ploss")),
                "enabled": self.enabled(sol), "temperature_c": sol.v(self.t_node), "uvlo_falling": self.uvlo_falling}

    def end_step(self, dt: float | None, sol: Solution, time: float, vin: str, gnd: str) -> list[Event]:
        events: list[Event] = []
        enabled = self.enabled(sol)
        v_in = sol.v(vin, gnd)
        if (dt is None and self._toggled) or (dt is not None and enabled != self.s_enabled):
            code = "regulator_uvlo" if not enabled else "regulator_restart"
            events.append(Event(time, code, self.owner.id, f"buck input {v_in:.2f} V (UVLO {self.uvlo_falling} V)",
                                {"v_in": v_in, "uvlo_falling": self.uvlo_falling, "uvlo_rising": self.uvlo_rising}))
        if dt is None:
            return events
        self.s_enabled = enabled
        temperature = sol.v(self.t_node)
        hot = temperature > self.max_temp_c
        if hot and not self.s_overtemperature:
            events.append(Event(time, "regulator_overtemperature", self.owner.id, f"buck {temperature:.0f} C",
                                {"temperature_c": temperature, "limit": self.max_temp_c}))
        self.s_overtemperature = hot
        return events

    def snapshot(self) -> dict[str, Any]:
        return {"s_enabled": self.s_enabled, "s_overtemperature": self.s_overtemperature}

    def restore(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)


class BuckRegulator(BehaviorModel):
    model = "regulator.buck"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.vin = self.require(self.pin_by_role("power_in"), "power_in")
        self.vout = self.require(self.pin_by_role("power_out"), "power_out")
        self.gnd = self.require(self.pin_by_role("ground"), "ground")
        self.core = BuckCore(self, self.params, ctype_thermal(self.ctype))

    def build(self, net: Netlist) -> None:
        name = f"regulator_buck_{self.inst[1:]}"
        net.model(ENABLE_MODEL, ENABLE_CARD)
        lines, control = self.core.lines(net, "vin", "vout", "vss")
        ports = ["vin", "vout", "vss", "t", "tamb"] + (["reg_ctl"] if control else [])
        nodes = [self.vin.node, self.vout.node, self.gnd.node, self.core.t_node, "tamb"] + ([control] if control else [])
        net.subckt(name, ports, lines, doc=f"{self.id}: buck regulator {self.core.v_out} V")
        net.add(f"{self.inst} {' '.join(nodes)} {name}")

    def begin_step(self, dt: float | None, inputs: dict[str, Any], time: float) -> None:
        if dt is None:
            self.core.prepare()

    def resolve(self, sol: Solution) -> bool:
        return self.core.resolve(sol, self.vin.node, self.gnd.node)

    def end_step(self, dt: float | None, sol: Solution, time: float) -> list[Event]:
        if dt is not None:
            self.capture_states(sol)
        return self.core.end_step(dt, sol, time, self.vin.node, self.gnd.node)

    def outputs(self, sol: Solution) -> dict[str, Any]:
        return self.core.state(sol, self.vin.node, self.vout.node, self.gnd.node)

    def snapshot(self) -> dict[str, Any]:
        return {**super().snapshot(), "core": self.core.snapshot()}

    def restore(self, state: dict[str, Any]) -> None:
        state = dict(state)
        self.core.restore(state.pop("core"))
        super().restore(state)


class ComputerLoad(BehaviorModel):
    """``load.computer``: P = P_idle + activity (P_active - P_idle) when booted,
    the midpoint while booting and ``reset_power_fraction`` P_idle in reset.
    Brownout (the minimum supply over the step's accepted ngspice points) below
    ``brownout_voltage`` -> reset; power returns above brownout + hysteresis and
    the OS needs ``boot_time`` before software runs. Output rails (``power_out``
    pins, e.g. 3V3) are regulated sources whose power is drawn from the input
    with ``rail_efficiency``. In SPICE: a constant-power B current source
    I = (P + sum V_rail i_rail / eta) / V, P from an ``EXTERNAL`` source set by the power-state logic.
    The input stage (PMIC) turns off smoothly below ``input_uvlo`` x nominal
    (default 0.6, estimate), see :func:`gated_power`."""

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
        self.input_uvlo = p.get("input_uvlo_fraction", 0.6)
        self.vin = self.require(self.pin_by_role("power_in"), "power_in")
        self.gnd = self.require(self.pin_by_role("ground"), "ground")
        self.rail_pins = self.pins_by_role("power_out")
        self.activity = 0.0
        self.s_powered = True
        self.s_booted = True
        self.s_boot_elapsed = 0.0
        self.s_brownouts = 0
        self._brownout_now = False

    def build(self, net: Netlist) -> None:
        name = f"load_computer_{self.inst[1:]}"
        net.model(ENABLE_MODEL, ENABLE_CARD)
        lines = ["bvsup vsup 0 v = v(vin,vss)"]
        ports, nodes = ["vin", "vss"], [self.vin.node, self.gnd.node]
        rail_terms = []
        for k, pin in enumerate(self.rail_pins):
            v_set = pin.voltage_nominal or 3.3
            port = f"rail{k}"
            lines += regulator_lines(net, f"r{k}", port, "vss", "vin", v_set, self.rail_resistance, 0.1, 0.0, self.rail_max,
                                     "none", enable=("en", "0", ENABLE_MODEL, "on"))
            rail_terms.append(f"{fmt(v_set)}*max(i(vr{k}),0)/{fmt(self.rail_efficiency)}")
            ports.append(port)
            nodes.append(pin.node)
        power = "+".join(["v(p)", *rail_terms])
        nominal = self.vin.voltage_nominal or 5.0
        lines += ["vsin vin vs2 dc 0",
                  f"bsup vs2 vss i = {gated_power(power, self.input_uvlo * nominal, 0.04 * nominal, 'v(vin,vss)')}"]
        ports += ["p", "en"]
        nodes += [self.control(net, "p", self.power()), self.control(net, "en", 1.0)]
        net.subckt(name, ports, lines, doc=f"{self.id}: computer load {self.p_idle:g}-{self.p_active:g} W (load.computer)")
        net.add(f"{self.inst} {' '.join(nodes)} {name}")

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
        self.controls["p"] = self.power()
        self.controls["en"] = 1.0 if self.s_powered else 0.0

    def watches(self) -> list[tuple[str, str]]:
        return [(self.internal("vsup"), "min")]

    def end_step(self, dt: float | None, sol: Solution, time: float) -> list[Event]:
        v = sol.v(self.vin.node, self.gnd.node)
        lowest = sol.reduced(self.internal("vsup"), "min")
        self._brownout_now = min(v, lowest) < self.brownout_voltage
        events: list[Event] = []
        if dt is None:
            return events
        if self.s_powered and self._brownout_now:
            self.s_powered = self.s_booted = False
            self.s_brownouts += 1
            events.append(Event(time, "brownout", self.id, f"supply {lowest:.3f} V below {self.brownout_voltage} V",
                                {"voltage": lowest, "threshold": self.brownout_voltage}))
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

    def outputs(self, sol: Solution) -> dict[str, Any]:
        v = sol.v(self.vin.node, self.gnd.node)
        rails = {pin.name: {"voltage": sol.v(pin.node, self.gnd.node), "current": sol.i(self.sense(f"vr{k}"))}
                 for k, pin in enumerate(self.rail_pins)}
        current = sol.i(self.sense("vsin"))
        powered = self.s_powered and v >= self.brownout_voltage
        return {"powered": powered, "booted": self.s_booted and powered, "software_running": self.s_booted and powered,
                "voltage": v, "brownout": self._brownout_now, "brownout_count": self.s_brownouts,
                "brownout_margin": v - self.brownout_voltage, "power": v * current, "current": current,
                "activity": self.activity, "rails": rails}


class ServoPwm(BehaviorModel):
    """Servo supply current from horn torque and speed (both from MuJoCo via
    ``EXTERNAL`` sources) through its DC motor referred to the horn:
    R = V_rated / (I_stall - I_idle), Kt = T_stall / (I_stall - I_idle),
    Ke = V_rated / w_no_load.

    I_m = min(|T| / Kt, (V - Ke |w|) / R)   (smooth; saturates at the supply)
    I_supply = I_idle + (I_m^2 R + Ke |w| I_m) / V   (power balance of the drive)
    At stall (w = 0, demanded torque >= available) this is I_idle + V / R."""

    model = "servo.pwm"
    ramped = ("torque", "speed")
    V_KNEE = 1.0

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
        self.torque = 0.0
        self.speed = 0.0

    def build(self, net: Netlist) -> None:
        name = f"servo_pwm_{self.inst[1:]}"
        v = "v(vin,vss)"
        emf = f"{fmt(self.ke)}*abs(v(speed))"
        cap = softplus(f"({v}-{emf})/{fmt(self.r)}", 0.01)
        i_m = f"max(0,{smooth_min(f'abs(v(torque))/{fmt(self.kt)}', cap, 0.01)})"
        idle = f"({v} >= {self.V_KNEE} ? {fmt(self.idle)} : {fmt(self.idle)}*max({v},0)/{self.V_KNEE})"
        lines = [
            f"bim im 0 v = {i_m}",
            "vsin vin vs2 dc 0",
            f"bsup vs2 vss i = {idle}+(v(im)*v(im)*{fmt(self.r)}+{emf}*v(im))/max({v},{self.V_KNEE})",
        ]
        nodes = [self.vin.node, self.gnd.node, self.control(net, "torque"), self.control(net, "speed")]
        net.subckt(name, ["vin", "vss", "torque", "speed"], lines,
                   doc=f"{self.id}: servo supply, R {self.r:.3g} ohm, Kt {self.kt:.3g} N m/A, Ke {self.ke:.3g} V s/rad")
        net.add(f"{self.inst} {' '.join(nodes)} {name}")

    def begin_step(self, dt: float | None, inputs: dict[str, Any], time: float) -> None:
        self.torque = float(inputs.get("horn_torque", 0.0))
        self.speed = float(inputs.get("horn_speed", 0.0))
        self.controls["torque"] = self.torque
        self.controls["speed"] = self.speed
        pulse = inputs.get("pulse_us")
        if pulse is not None and float(pulse) > 0.0:  # pulse <= 0: no signal, the servo holds its last command
            seconds = float(pulse) * 1e-6
            center = 0.5 * (self.pulse_min + self.pulse_max)
            self.commanded_angle = (seconds - center) / (self.pulse_max - self.pulse_min) * self.travel

    def outputs(self, sol: Solution) -> dict[str, Any]:
        v = sol.v(self.vin.node, self.gnd.node)
        i_m = sol.get(self.internal("im"))
        available = self.kt * max(0.0, (v - self.ke * abs(self.speed)) / self.r)
        return {"supply_voltage": v, "voltage": v, "current": sol.i(self.sense("vsin")), "motor_current": i_m,
                "max_torque": available, "no_load_speed": max(v, 0.0) / self.ke, "stall_current": self.idle + max(v, 0.0) / self.r,
                "commanded_angle": self.commanded_angle, "powered": v >= self.v_min,
                "saturated": abs(self.torque) / self.kt > i_m + 1e-3}


class ConstantLoad(BehaviorModel):
    """Small electronics load: constant current on its ``power_in`` pin,
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

    def build(self, net: Netlist) -> None:
        name = f"{self.model.replace('.', '_')}_{self.inst[1:]}"
        lines = ["vsin vin vs2 dc 0", f"bload vs2 vss i = {constant_current(self.current, self.v_min, 'v(vin,vss)')}"]
        net.subckt(name, ["vin", "vss"], lines, doc=f"{self.id}: {self.current * 1e3:.3g} mA constant-current load ({self.model})")
        net.add(f"{self.inst} {self.vin.node} {self.gnd.node} {name}")

    def outputs(self, sol: Solution) -> dict[str, Any]:
        v = sol.v(self.vin.node, self.gnd.node)
        return {"supply_voltage": v, "current": sol.i(self.sense("vsin")), "powered": v >= self.v_min}

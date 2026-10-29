"""``board.pcb``: a custom PCB's on-board regulator and loads (one ngspice subcircuit).

Read from the board's behavior params:

* ``regulator`` — a nested behavior (``model: regulator.buck``). Input pin:
  ``input_pin`` or the ``power_in`` pin with the highest nominal voltage;
  output: ``output_pin`` or the ``power_out`` pin closest to ``v_out``.
* Any nested dict with ``address``/``bus`` (an on-board IC, e.g. ``ina226``,
  ``mpu6050``) — constant-current load on ``supply_pin`` or the power pin
  closest to 3.3 V, current from ``current`` or a datasheet default.
* A nested dict with ``shunt`` (current monitor) — series shunt resistor
  between the regulator input pin and the regulator; reported as the
  monitor's reading.
"""

from __future__ import annotations

from typing import Any

from r3_model.units import to_si

from ..netlist import Netlist, Solution, fmt
from .base import BehaviorModel, Event, Params, PinRef, constant_current, spice_name
from .battery import ctype_thermal
from .loads import DEVICE_CURRENTS, BuckCore
from .regulator import ENABLE_CARD, ENABLE_MODEL


class BoardPcb(BehaviorModel):
    model = "board.pcb"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.gnd = self.require(self.pin_by_role("ground"), "ground")
        values = self.params.values
        self.regulator_params = values.get("regulator") if isinstance(values.get("regulator"), dict) else None
        self.devices = {k: v for k, v in values.items() if isinstance(v, dict) and k != "regulator" and ("address" in v or "bus" in v)}
        self.monitor = next((k for k, v in self.devices.items() if "shunt" in v), None)
        self.core: BuckCore | None = None
        self.vin_pin: PinRef | None = None
        self.vout_pin: PinRef | None = None
        self.shunt_value = 0.0
        self.device_info: dict[str, dict[str, Any]] = {}
        if self.regulator_params:
            reg = Params(f"{self.id}.regulator", self.regulator_params)
            v_out = reg.get("v_out")
            self.vin_pin = self._pin(self.regulator_params.get("input_pin"), ("power_in",), 0.0, highest=True)
            self.vout_pin = self._pin(self.regulator_params.get("output_pin"), ("power_out",), v_out)
            self.core = BuckCore(self, reg, ctype_thermal(self.ctype))
            if self.monitor:
                self.shunt_value = to_si(self.devices[self.monitor]["shunt"])
            self.params.estimated.update({f"regulator.{k}": v for k, v in reg.estimated.items()})
        for name, device in self.devices.items():
            supply = self._pin(device.get("supply_pin"), ("power_in", "power_out"), float(device.get("supply_voltage", 3.3)))
            if supply is None:
                continue
            if "current" in device:
                current, source = to_si(device["current"]), "catalog"
            elif name in DEVICE_CURRENTS:
                current, source = DEVICE_CURRENTS[name]
            else:
                current, source = 1e-3, "estimated 1 mA"
            if source != "catalog":
                self.params.estimated[f"{name}.current"] = current
            self.device_info[name] = {"supply_pin": supply.name, "current": current, "source": source,
                                      "v_knee": 0.8 * (supply.voltage_nominal or 3.3)}

    def _pin(self, name: str | None, roles: tuple[str, ...], target: float, highest: bool = False) -> PinRef | None:
        if name:
            return self.pins[name]
        candidates = [p for p in self.pins.values() if p.role in roles and p.voltage_nominal is not None]
        if not candidates:
            return None
        if highest:
            return max(candidates, key=lambda p: p.voltage_nominal)
        return min(candidates, key=lambda p: abs(p.voltage_nominal - target))

    @staticmethod
    def _port(pin: str) -> str:
        return "p_" + spice_name(pin)

    def build(self, net: Netlist) -> None:
        name = f"board_pcb_{self.inst[1:]}"
        used: dict[str, PinRef] = {self.gnd.name: self.gnd}
        lines: list[str] = []
        control: str | None = None
        gnd = self._port(self.gnd.name)
        if self.core is not None:
            net.model(ENABLE_MODEL, ENABLE_CARD)
            vin, vout = self._port(self.vin_pin.name), self._port(self.vout_pin.name)
            used[self.vin_pin.name], used[self.vout_pin.name] = self.vin_pin, self.vout_pin
            regulator_input = vin
            if self.monitor:
                regulator_input = "shunt_out"
                lines.append(f"rshunt {vin} shunt_out {fmt(self.shunt_value)}")
            core_lines, control = self.core.lines(net, regulator_input, vout, gnd)
            lines += core_lines
        for device, info in self.device_info.items():
            supply = self.pins[info["supply_pin"]]
            used[supply.name] = supply
            port, tag = self._port(supply.name), spice_name(device)
            lines += [f"vd_{tag} {port} d_{tag} dc 0",
                      f"bd_{tag} d_{tag} {gnd} i = {constant_current(info['current'], info['v_knee'], f'v({port},{gnd})')}"]
        if not lines:
            return
        ports = [self._port(p) for p in used] + ["t", "tamb"] + (["reg_ctl"] if control else [])
        nodes = [pin.node for pin in used.values()] + [self.core.t_node if self.core else self.local("t"), "tamb"]
        nodes += [control] if control else []
        if self.core is None:
            lines.append("rt t tamb 1")
        net.subckt(name, ports, lines, doc=f"{self.id}: board {', '.join(['buck'] * bool(self.core) + list(self.device_info))}")
        net.add(f"{self.inst} {' '.join(nodes)} {name}")

    def begin_step(self, dt: float | None, inputs: dict[str, Any], time: float) -> None:
        if dt is None and self.core is not None:
            self.core.prepare()

    def resolve(self, sol: Solution) -> bool:
        return self.core.resolve(sol, self.vin_pin.node, self.gnd.node) if self.core else False

    def end_step(self, dt: float | None, sol: Solution, time: float) -> list[Event]:
        if self.core is None:
            return []
        if dt is not None:
            self.capture_states(sol)
        return self.core.end_step(dt, sol, time, self.vin_pin.node, self.gnd.node)

    def outputs(self, sol: Solution) -> dict[str, Any]:
        out: dict[str, Any] = {"devices": {k: {"current": sol.i(f"v.{self.inst}.vd_{spice_name(k)}")} for k in self.device_info}}
        if self.core:
            regulator_input = self.internal("shunt_out") if self.monitor else self.vin_pin.node
            reg = self.core.state(sol, regulator_input, self.vout_pin.node, self.gnd.node)
            out["regulator"] = reg
            out["v_in"] = sol.v(self.vin_pin.node, self.gnd.node)
            out["v_out"] = reg["v_out"]
            out["regulator_temperature_c"] = reg["temperature_c"]
            out["uvlo"] = not reg["enabled"]
            if self.monitor:
                shunt_v = sol.v(self.vin_pin.node, self.internal("shunt_out"))
                current = shunt_v / self.shunt_value
                out[self.monitor] = {"current": current, "shunt_voltage": shunt_v, "bus_voltage": out["v_in"]}
                out[f"{self.monitor}_current"] = current
                out[f"{self.monitor}_bus_voltage"] = out["v_in"]
        return out

    def snapshot(self) -> dict[str, Any]:
        state = super().snapshot()
        if self.core:
            state["core"] = self.core.snapshot()
        return state

    def restore(self, state: dict[str, Any]) -> None:
        state = dict(state)
        core = state.pop("core", None)
        if self.core and core:
            self.core.restore(core)
        super().restore(state)

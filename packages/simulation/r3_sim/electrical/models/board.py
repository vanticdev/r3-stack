"""``board.pcb``: a custom PCB's on-board regulator and loads.

Read from the board's behavior params:

* ``regulator`` — a nested behavior (``model: regulator.buck``). Input pin:
  ``input_pin`` or the ``power_in`` pin with the highest nominal voltage;
  output: ``output_pin`` or the ``power_out`` pin closest to ``v_out``.
* Any nested dict with ``address``/``bus`` (an on-board IC, e.g. ``ina226``,
  ``mpu6050``) — constant-current load on ``supply_pin`` or the power pin
  closest to 3.3 V, current from ``current`` or a datasheet default.
* A nested dict with ``shunt`` (current monitor) — series shunt between the
  regulator input pin and the regulator; reported as the monitor's reading.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from r3_model.units import to_si

from ..mna import Circuit
from .base import BehaviorModel, Event, Params, SupplyLoad, constant_current, voltage
from .battery import ctype_thermal
from .loads import DEVICE_CURRENTS, BuckCore


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
        self.loads: dict[str, SupplyLoad] = {}
        self.device_info: dict[str, dict[str, Any]] = {}

    def _pin(self, name: str | None, roles: tuple[str, ...], target: float, highest: bool = False):
        if name:
            return self.pins[name]
        candidates = [p for p in self.pins.values() if p.role in roles and p.voltage_nominal is not None]
        if not candidates:
            return None
        if highest:
            return max(candidates, key=lambda p: p.voltage_nominal)
        return min(candidates, key=lambda p: abs(p.voltage_nominal - target))

    def build(self, circuit: Circuit) -> None:
        if self.regulator_params:
            reg = Params(f"{self.id}.regulator", self.regulator_params)
            v_out = reg.get("v_out")
            vin_pin = self._pin(self.regulator_params.get("input_pin"), ("power_in",), 0.0, highest=True)
            vout_pin = self._pin(self.regulator_params.get("output_pin"), ("power_out",), v_out)
            self.vin_pin, self.vout_pin = vin_pin, vout_pin
            regulator_input = vin_pin.node
            if self.monitor:
                shunt = to_si(self.devices[self.monitor]["shunt"])
                regulator_input = circuit.node(f"C:{self.id}.{vin_pin.name}#shunt")
                self.shunt_index = circuit.resistor(vin_pin.node, regulator_input, shunt, f"{self.id}:{self.monitor}_shunt")
                self.shunt_value = shunt
            self.regulator_input = regulator_input
            self.core = BuckCore(f"{self.id}.regulator", reg, regulator_input, vout_pin.node, self.gnd.node,
                                 ctype_thermal(self.ctype), self.ambient_c)
            self.core.build(circuit)
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
            fn, expr = constant_current(current, 0.8 * (supply.voltage_nominal or 3.3))
            self.loads[name] = circuit.add(SupplyLoad(f"{self.id}.{name}", supply.node, self.gnd.node, fn, expr))
            self.device_info[name] = {"supply_pin": supply.name, "current": current, "source": source}

    def begin_step(self, dt: float | None, inputs: dict[str, Any], time: float) -> None:
        if self.core:
            self.core.begin_step()

    def resolve(self, x: np.ndarray) -> bool:
        return self.core.resolve(x) if self.core else False

    def end_step(self, dt: float | None, x: np.ndarray, time: float) -> list[Event]:
        return self.core.end_step(dt, x, time) if self.core else []

    def outputs(self, x: np.ndarray) -> dict[str, Any]:
        out: dict[str, Any] = {"devices": {k: {"current": load.current(x)} for k, load in self.loads.items()}}
        if self.core:
            reg = self.core.state(x)
            out["regulator"] = reg
            out["v_in"] = voltage(x, self.vin_pin.node, self.gnd.node)
            out["v_out"] = reg["v_out"]
            out["regulator_temperature_c"] = reg["temperature_c"]
            out["uvlo"] = not reg["enabled"]
            if self.monitor:
                shunt_v = voltage(x, self.vin_pin.node, self.regulator_input)
                out[self.monitor] = {"current": shunt_v / self.shunt_value, "shunt_voltage": shunt_v,
                                     "bus_voltage": out["v_in"]}
                out[f"{self.monitor}_current"] = shunt_v / self.shunt_value
                out[f"{self.monitor}_bus_voltage"] = out["v_in"]
        return out

    def snapshot(self) -> dict[str, Any]:
        return self.core.snapshot() if self.core else {}

    def restore(self, state: dict[str, Any]) -> None:
        if self.core:
            self.core.restore(state)

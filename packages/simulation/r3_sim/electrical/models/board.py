"""``board.pcb``: a custom PCB's on-board regulators and loads (one ngspice subcircuit).

Read from the board's behavior params:

* ``regulator`` and any other nested behavior with ``model: regulator.buck`` or
  ``regulator.buck_boost`` (e.g. ``servo_regulator``) — a synchronous buck or a
  4-switch buck-boost each (:class:`BuckCore`; the buck-boost has no dropout).
  Input pin: ``input_pin`` or the ``power_in`` pin with the highest nominal
  voltage; output: ``output_pin`` or the ``power_out`` pin closest to ``v_out``.
  All converters share the board input path below.
* ``reverse_protection.rds_on`` — series P-FET resistance in the input path.
* A nested dict with ``shunt`` (current monitor) — series shunt resistor
  between the input pin and the converters; its reading is the whole board
  branch (every converter and on-board load behind it).
* ``input_capacitance`` — the converters' shared input capacitance (transient only):
  a list of ``{capacitance, esr}`` branches (e.g. the 220 uF hybrid-polymer bulk and the
  ceramic bank) at the converters' input node, behind the FET and the shunt. Each
  capacitor voltage is a state carried across transient restarts (its initial
  condition is the DC operating point the transient starts from), so a disconnected
  input or a brown-out discharges it realistically into the converters. The
  converters' output (hold-up) capacitance is modelled too (``hold_up_capacitance``).
* ``tvs`` — the input TVS: not part of the circuit at battery voltages (it
  conducts above its breakdown); checked statically against the converters'
  input absolute maximum (:meth:`BoardPcb.tvs_clamp`).
* Any nested dict with ``address``/``bus`` (an on-board IC, e.g. ``ina226``,
  ``mpu6050``) — constant-current load on ``supply_pin`` or the power pin
  closest to 3.3 V, current from ``current`` or a datasheet default. It returns to
  ``ground_pin`` when given (an isolated domain, e.g. a digital isolator's far side
  powered and referenced by another component), else to the board ground.
"""

from __future__ import annotations

from typing import Any

from r3_model.units import to_si

from ..netlist import Netlist, Solution, fmt
from .base import BehaviorModel, Event, Params, PinRef, constant_current, spice_name
from .battery import ctype_thermal
from .loads import DEVICE_CURRENTS, BuckCore
from .regulator import ENABLE_CARD, ENABLE_MODEL

PRIMARY = "regulator"


class BoardPcb(BehaviorModel):
    model = "board.pcb"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.gnd = self.require(self.pin_by_role("ground"), "ground")
        values = self.params.values
        self.regulator_params = values.get(PRIMARY) if isinstance(values.get(PRIMARY), dict) else None
        converters = {k: v for k, v in values.items() if isinstance(v, dict)
                      and (k == PRIMARY or v.get("model") in ("regulator.buck", "regulator.buck_boost"))}
        self.devices = {k: v for k, v in values.items() if isinstance(v, dict) and k not in converters
                        and ("address" in v or "bus" in v)}
        self.monitor = next((k for k, v in self.devices.items() if "shunt" in v), None)
        self.cores: dict[str, BuckCore] = {}
        self.outputs_of: dict[str, PinRef] = {}
        self.core: BuckCore | None = None  # the primary converter (``regulator``)
        self.vin_pin: PinRef | None = None
        self.vout_pin: PinRef | None = None
        self.shunt_value = 0.0
        self.fet_resistance = 0.0
        self.device_info: dict[str, dict[str, Any]] = {}
        thermal = ctype_thermal(self.ctype)
        ordered = sorted(converters, key=lambda k: (k != PRIMARY, k))
        for name in ordered:
            reg = Params(f"{self.id}.{name}", converters[name])
            prefix = "reg" if name == PRIMARY else spice_name(name)[:12]
            core = BuckCore(self, reg, thermal, prefix=prefix)
            out_pin = self._pin(converters[name].get("output_pin"), ("power_out",), core.v_out)
            if out_pin is None:
                continue
            self.cores[name] = core
            self.outputs_of[name] = out_pin
            self.params.estimated.update({f"{name}.{k}": v for k, v in reg.estimated.items()})
        if self.cores:
            first = next(iter(self.cores))
            self.core, self.vout_pin = self.cores[first], self.outputs_of[first]
            self.vin_pin = self._pin((self.regulator_params or {}).get("input_pin"), ("power_in",), 0.0, highest=True)
            if self.monitor:
                self.shunt_value = to_si(self.devices[self.monitor]["shunt"])
            protection = values.get("reverse_protection")
            if isinstance(protection, dict) and protection.get("rds_on") is not None:
                self.fet_resistance = to_si(protection["rds_on"])
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
            ground = device.get("ground_pin")
            self.device_info[name] = {"supply_pin": supply.name, "current": current, "source": source,
                                      "v_knee": 0.8 * (supply.voltage_nominal or 3.3),
                                      "ground_pin": str(ground) if ground else self.gnd.name}
        # Input capacitance branches (transient): (C, ESR, state node)
        self.input_caps: list[tuple[float, float, str]] = []
        caps = values.get("input_capacitance")
        for k, branch in enumerate(caps if isinstance(caps, list) else ([caps] if isinstance(caps, dict) else [])):
            c, esr = to_si(branch["capacitance"]), to_si(branch.get("esr", 0.01))
            node = self.local(f"cin{k}")
            self.input_caps.append((c, esr, node))
            self.states[node] = None  # set by the first transient solution (the DC start)

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

    # ------------------------------------------------------------------ static data
    def tvs_clamp(self, current: float) -> float | None:
        """Clamping voltage of the input TVS at ``current`` (A): the datasheet line from the maximum
        breakdown voltage to V_C at I_PP, V_C(I) = V_BR,max + (V_C - V_BR,max) I / I_PP (linear dynamic
        resistance, the usual first-order TVS model); None without ``tvs`` data."""
        tvs = self.params.values.get("tvs")
        if not isinstance(tvs, dict) or tvs.get("v_clamp") is None or tvs.get("i_pp") is None:
            return None
        v_c, i_pp = to_si(tvs["v_clamp"]), to_si(tvs["i_pp"])
        v_br = to_si(tvs.get("v_breakdown_max", tvs.get("v_breakdown_min", v_c)))
        return v_br + (v_c - v_br) * max(current, 0.0) / i_pp

    def build(self, net: Netlist) -> None:
        name = f"board_pcb_{self.inst[1:]}"
        used: dict[str, PinRef] = {self.gnd.name: self.gnd}
        lines: list[str] = []
        controls: list[tuple[str, str]] = []  # (port, top-level node): enables (op), capacitor states (tran)
        gnd = self._port(self.gnd.name)
        if self.cores:
            net.model(ENABLE_MODEL, ENABLE_CARD)
            vin = self._port(self.vin_pin.name)
            used[self.vin_pin.name] = self.vin_pin
            node = vin
            if self.fet_resistance:
                lines.append(f"rfet {node} fet_out {fmt(self.fet_resistance)}")
                node = "fet_out"
            if self.monitor:
                lines.append(f"rshunt {node} shunt_out {fmt(self.shunt_value)}")
                node = "shunt_out"
            for key, core in self.cores.items():
                pin = self.outputs_of[key]
                used[pin.name] = pin
                core_lines, control = core.lines(net, node, self._port(pin.name), gnd)
                lines += core_lines
                if control:
                    controls.append((core.ctl_port, control))
                controls += core.extra_ports(net)
            if net.transient:
                for k, (c, esr, state) in enumerate(self.input_caps):
                    lines += [f"rcin{k} {node} cin{k} {fmt(esr)}", f"ccin{k} cin{k} {gnd} {fmt(c)}"]
                    controls.append((f"cin{k}", state))
                    if self.states.get(state) is not None:
                        net.state(state, self.states[state])
        for device, info in self.device_info.items():
            supply = self.pins[info["supply_pin"]]
            used[supply.name] = supply
            ground_pin = self.pins[info["ground_pin"]]
            used[ground_pin.name] = ground_pin
            port, tag, ret = self._port(supply.name), spice_name(device), self._port(ground_pin.name)
            lines += [f"vd_{tag} {port} d_{tag} dc 0",
                      f"bd_{tag} d_{tag} {ret} i = {constant_current(info['current'], info['v_knee'], f'v({port},{ret})')}"]
        if not lines:
            return
        ports = [self._port(p) for p in used] + [c.t_port for c in self.cores.values()] + ["tamb"]
        nodes = [pin.node for pin in used.values()] + [c.t_node for c in self.cores.values()] + ["tamb"]
        if not self.cores:
            ports.insert(-1, "t")
            nodes.insert(-1, self.local("t"))
            lines.append("rt t tamb 1")
        ports += [port for port, _ in controls]
        nodes += [node for _, node in controls]
        doc = ", ".join([f"buck {k}" for k in self.cores] + list(self.device_info))
        net.subckt(name, ports, lines, doc=f"{self.id}: board {doc}")
        net.add(f"{self.inst} {' '.join(nodes)} {name}")

    def begin_step(self, dt: float | None, inputs: dict[str, Any], time: float) -> None:
        if dt is None:
            for core in self.cores.values():
                core.prepare()

    def _regulator_input(self) -> str:
        if self.monitor:
            return self.internal("shunt_out")
        if self.fet_resistance:
            return self.internal("fet_out")
        return self.vin_pin.node

    def resolve(self, sol: Solution) -> bool:
        if not self.cores:
            return False
        node = self._regulator_input()
        return any([core.resolve(sol, node, self.gnd.node) for core in self.cores.values()])

    def end_step(self, dt: float | None, sol: Solution, time: float) -> list[Event]:
        if not self.cores:
            return []
        if dt is not None:
            self.capture_states(sol)
        node = self._regulator_input()
        events: list[Event] = []
        for core in self.cores.values():
            events += core.end_step(dt, sol, time, node, self.gnd.node)
        return events

    def outputs(self, sol: Solution) -> dict[str, Any]:
        out: dict[str, Any] = {"devices": {k: {"current": sol.i(f"v.{self.inst}.vd_{spice_name(k)}")} for k in self.device_info}}
        if not self.cores:
            return out
        node = self._regulator_input()
        out["v_in"] = sol.v(self.vin_pin.node, self.gnd.node)
        for key, core in self.cores.items():
            reg = core.state(sol, node, self.outputs_of[key].node, self.gnd.node)
            out[key] = reg
            if key == PRIMARY or core is self.core:
                out["v_out"] = reg["v_out"]
                out["regulator_temperature_c"] = reg["temperature_c"]
                out["uvlo"] = not reg["enabled"]
            else:
                out[f"{key}_v_out"] = reg["v_out"]
                out[f"{key}_uvlo"] = not reg["enabled"]
        out["current_in"] = sum(out[key]["i_in"] for key in self.cores)
        if self.monitor:
            high = self.internal("fet_out") if self.fet_resistance else self.vin_pin.node
            shunt_v = sol.v(high, self.internal("shunt_out"))
            current = shunt_v / self.shunt_value
            bus = sol.v(self.internal("shunt_out"), self.gnd.node)  # INA226 VBUS senses the shunt's load side
            out[self.monitor] = {"current": current, "shunt_voltage": shunt_v, "bus_voltage": bus}
            out[f"{self.monitor}_current"] = current
            out[f"{self.monitor}_bus_voltage"] = bus
            out["current_in"] = current
        return out

    def snapshot(self) -> dict[str, Any]:
        state = super().snapshot()
        state["cores"] = {k: c.snapshot() for k, c in self.cores.items()}
        return state

    def restore(self, state: dict[str, Any]) -> None:
        state = dict(state)
        cores = state.pop("cores", {})
        for key, core_state in cores.items():
            if key in self.cores:
                self.cores[key].restore(core_state)
        super().restore(state)

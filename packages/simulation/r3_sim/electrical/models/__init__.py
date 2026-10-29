"""Electrical behavior models keyed by catalog ``behavior.model``."""

from __future__ import annotations

from typing import Any, Callable

from .base import BehaviorModel, BuildContext, Event
from .battery import BatteryLipo
from .board import BoardPcb
from .esc import BrushedEsc
from .fuse import FuseBlade
from .loads import BuckRegulator, ComputerLoad, ConstantLoad, ServoPwm
from .motor import DcBrushedMotor


def _constant(model: str) -> Callable[..., BehaviorModel]:
    def factory(component_id: str, ctype: Any, params: dict[str, Any], ctx: BuildContext) -> BehaviorModel:
        return ConstantLoad(component_id, ctype, params, ctx, model=model)

    return factory


MODELS: dict[str, Callable[..., BehaviorModel]] = {
    "battery.lipo": BatteryLipo,
    "esc.brushed": BrushedEsc,
    "motor.dc_brushed": DcBrushedMotor,
    "regulator.buck": BuckRegulator,
    "load.computer": ComputerLoad,
    "servo.pwm": ServoPwm,
    "fuse.blade": FuseBlade,
    "board.pcb": BoardPcb,
    "receiver.crsf": _constant("receiver.crsf"),
    "gps.nmea": _constant("gps.nmea"),
    "encoder.hall": _constant("encoder.hall"),
    "imu.mpu6050": _constant("imu.mpu6050"),
    "power_monitor.ina226": _constant("power_monitor.ina226"),
}

# Behavior models with no electrical terminals of their own.
NON_ELECTRICAL = {"tire.linear", "gearbox.differential", "passive"}

__all__ = ["MODELS", "NON_ELECTRICAL", "BehaviorModel", "BuildContext", "Event"]

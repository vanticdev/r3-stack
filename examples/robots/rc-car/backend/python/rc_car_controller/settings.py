"""Firmware configuration: defaults, config-file loading and bindings.

Sources, highest priority first:

1. ``--config PATH`` on the command line;
2. ``R3_FIRMWARE_CONFIG`` (path to a JSON file; used by the twin runner);
3. ``BLITZ_CONFIG_PATH`` (the watchdog's config file; the deploy supplier writes
   base64-encoded JSON, plain JSON is accepted too);
4. built-in defaults, equal to ``robot.yaml`` ``software.config`` at the time of writing.

A config document is either a bare ``software.config`` mapping or
``{"config": {...}, "bindings": {...}, "mission": {...}, "geodetic_origin": {...}}``.
Hardware bindings come from ``hal.bindings()`` when the HAL provides them (virtual
backend), else from the document, else from the defaults.
"""

from __future__ import annotations

import base64
import binascii
import copy
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

MODULE_NAME = "rc_car_controller"

DEFAULT_CONFIG: dict[str, Any] = {
    # robot.yaml software.config
    "control_rate_hz": 100,
    "mode": "autonomous",
    "radio_failsafe_timeout_s": 0.25,
    "failsafe_action": "brake",
    "low_voltage_cutoff_v": 6.6,
    "low_voltage_limit_v": 7.0,
    "max_cruise_speed_mps": 4.0,
    "esc_pulse_us": {"reverse": 1000, "neutral": 1500, "forward": 2000},
    "steering_pulse_us": {"left": 1722, "center": 1500, "right": 1278},  # CAD-solved linkage end points (+-30 deg horn)
    "max_steer_rad": 0.4021,  # CAD steering table (Ackermann-equivalent single-track angle)
    "wheelbase_m": 0.26,
    "wheel_radius_m": 0.055,
    "encoder_pulses_per_rev": 9.891,  # magnets on the spur shaft: carrier (mean wheel) revolutions
    "arm_channel": 5,                    # legacy; crsf_channels.arm takes precedence
    "crsf_channels": {"steering": 1, "throttle": 2, "arm": 5},
    # Firmware-level defaults (not in robot.yaml).
    "telemetry_rate_hz": 5,
    "imu_calibration_s": 0.5,
    "require_gps_fix_to_arm": True,
    "require_rtk_fix_to_arm": True,      # autonomy arms only on an RTK fixed solution (GGA quality 4; the RC car has an RTK rover)
    # 1-sigma horizontal position error per axis (m) by GGA fix quality when no matching GST is available:
    # 1 GNSS (CEP 1.5 m / 1.1774), 2 DGNSS, 4 RTK fixed (CEP 0.01 m + 1 ppm), 5 RTK float.
    "gps_sigma_by_quality_m": {"1": 1.3, "2": 0.6, "4": 0.02, "5": 0.4},
    "initial_heading_deg": 0.0,          # ENU, CCW from east; car faces +x at start
    "link_degraded_lq": 70,              # uplink LQ (%) below which autonomy slows down
    "link_degraded_speed_mps": 2.0,
    "ina226": {"address": 0x40, "shunt_ohm": 0.002, "max_current_a": 10.0},
    "mpu6050": {"address": 0x68},
    # Traction/launch control, motor current limit and gyro yaw assist; any
    # traction.TractionConfig field may be overridden here.
    "traction": {"enabled": True, "yaw_assist_enabled": True, "target_slip": 0.10,
                 "motor_current_limit_a": 50.0, "launch_slew_per_s": 3.0, "yaw_gain": 0.6,
                 "gear_ratio": 9.066},  # 18T pinion / 66T spur x 2.4727 (rear transmission catalog)
}

CHANNEL_ROLES = ("steering", "throttle", "arm")


def channel_map(config: Mapping[str, Any]) -> dict[str, int]:
    """CRSF channel numbers (1-based) by role from ``crsf_channels``.

    The legacy ``arm_channel`` key is honoured only when ``crsf_channels`` has no
    ``arm`` entry (the twin's pilot uses the same rule).
    """
    mapping = dict(config.get("crsf_channels") or {})
    if "arm" not in mapping and "arm_channel" in config:
        mapping["arm"] = config["arm_channel"]
    missing = [role for role in CHANNEL_ROLES if role not in mapping]
    if missing:
        raise ValueError(f"crsf_channels is missing {', '.join(missing)}")
    return {role: int(mapping[role]) for role in CHANNEL_ROLES}

DEFAULT_BINDINGS: dict[str, dict[str, Any]] = {
    "esc_pwm": {"interface": "pwm", "device": "pwmchip0", "channel": 2, "frequency": 50.0},
    "servo_pwm": {"interface": "pwm", "device": "pwmchip0", "channel": 3, "frequency": 50.0},
    "crsf": {"interface": "uart", "device": "/dev/ttyAMA0", "baud": 420000},
    "gps": {"interface": "uart", "device": "/dev/ttyAMA2", "baud": 115200},
    "i2c": {"interface": "i2c", "device": "/dev/i2c-1"},
    "encoder": {"interface": "gpio", "device": "gpiochip0", "channel": 17},
    # Failsafe heartbeat to the HAT's ATtiny412 arbiter (J8 pin 13): toggled once per control cycle; after
    # 100 ms without an edge the arbiter holds the ESC at neutral.
    "heartbeat": {"interface": "gpio", "device": "gpiochip0", "channel": 27},
}

_SI_PREFIX = {"": 1.0, "k": 1e3, "M": 1e6, "G": 1e9, "m": 1e-3, "u": 1e-6, "µ": 1e-6}


def quantity(value: Any) -> float:
    """Parse ``50``, ``"50 Hz"``, ``"1 kHz"`` or ``"420000"`` to a float in SI base units."""
    if isinstance(value, (int, float)):
        return float(value)
    match = re.fullmatch(r"\s*([-+0-9.eE]+)\s*([kMGmuµ]?)[A-Za-zΩ]*\s*", str(value))
    if not match:
        raise ValueError(f"not a quantity: {value!r}")
    return float(match.group(1)) * _SI_PREFIX[match.group(2)]


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


@dataclass
class FirmwareSettings:
    config: dict[str, Any]
    bindings: dict[str, dict[str, Any]]
    mission: dict[str, Any] | None = None
    geodetic_origin: dict[str, float] | None = None
    sources: list[str] = field(default_factory=list)


def decode_document(text: str) -> dict[str, Any]:
    """Parse JSON, or base64-encoded JSON as written by the BLITZ config supplier."""
    text = text.strip()
    if not text:
        return {}
    if not text.startswith("{"):
        try:
            text = base64.b64decode(text, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as error:
            raise ValueError("config is neither JSON nor base64 JSON") from error
    document = json.loads(text)
    if not isinstance(document, dict):
        raise ValueError("config document must be a JSON object")
    return document


def _config_path(argv_config: str | None, environ: Mapping[str, str]) -> tuple[str | None, str]:
    if argv_config:
        return argv_config, "argv"
    for variable in ("R3_FIRMWARE_CONFIG", "BLITZ_CONFIG_PATH"):
        if environ.get(variable):
            return environ[variable], variable
    return None, "defaults"


def load_settings(argv_config: str | None = None, environ: Mapping[str, str] | None = None,
                  hal_bindings: Mapping[str, Mapping[str, Any]] | None = None,
                  hal_document: Mapping[str, Any] | None = None) -> FirmwareSettings:
    """``hal_document`` is a config document offered by the HAL (a future ``hello``
    field); it is used only when no config file is given."""
    environ = os.environ if environ is None else environ
    path, source = _config_path(argv_config, environ)
    document: dict[str, Any] = {}
    sources = ["defaults"]
    if path is not None:
        file = Path(path)
        if file.is_file() and file.stat().st_size > 0:
            document = decode_document(file.read_text())
            sources.append(f"{source}:{path}")
    elif hal_document:
        document = dict(hal_document)
        sources.append("hal_config")
    if not isinstance(document.get("config"), dict):
        # A bare software.config. The twin's HAL offers one with the scenario's
        # "mission" and its "gps_origin" ({lat, lon, alt}) merged in
        # (r3_sim kernel.software_config); lift those out.
        flat = dict(document)
        mission, origin = flat.pop("mission", None), flat.pop("gps_origin", None)
        document = {"config": flat, "mission": mission or None,
                    "geodetic_origin": flat.pop("geodetic_origin", None) or origin or None}
    config = deep_merge(DEFAULT_CONFIG, document["config"])
    bindings = copy.deepcopy(DEFAULT_BINDINGS)
    if isinstance(document.get("bindings"), dict):
        bindings = deep_merge(bindings, document["bindings"])
        sources.append("document_bindings")
    if hal_bindings:
        bindings = deep_merge(bindings, {k: dict(v) for k, v in hal_bindings.items()})
        sources.append("hal_bindings")
    return FirmwareSettings(config, bindings, document.get("mission"), document.get("geodetic_origin"), sources)


def document_from_robot_yaml(robot_yaml: str | Path, scenario: str | None = None,
                             geodetic_origin: Mapping[str, float] | None = None) -> dict[str, Any]:
    """Build a firmware config document from ``robot.yaml`` (host-side; needs PyYAML).

    ``scenario`` adds that scenario's ``mission`` and its ``initial.mode`` override.
    """
    import yaml  # host tooling only; the firmware itself never imports yaml

    spec = yaml.safe_load(Path(robot_yaml).read_text())
    software = spec.get("software", {})
    config = dict(software.get("config", {}))
    bindings = {}
    for name, binding in software.get("bindings", {}).items():
        entry = {k: v for k, v in binding.items() if k in ("interface", "device", "channel", "baud", "frequency", "address")}
        if "frequency" in entry:
            entry["frequency"] = quantity(entry["frequency"])
        bindings[name] = entry
    document: dict[str, Any] = {"module": MODULE_NAME, "config": config, "bindings": bindings}
    if scenario is not None:
        matches = [s for s in spec.get("simulation", {}).get("scenarios", []) if s.get("id") == scenario]
        if not matches:
            raise KeyError(f"scenario {scenario!r} not in {robot_yaml}")
        chosen = matches[0]
        if "mode" in chosen.get("initial", {}):
            config["mode"] = chosen["initial"]["mode"]
        if chosen.get("mission"):
            document["mission"] = chosen["mission"]
        document["scenario"] = scenario
    if geodetic_origin is not None:
        document["geodetic_origin"] = dict(geodetic_origin)
    return document

"""Default remediation hints for diagnostic codes whose stage left ``hint`` empty."""

from __future__ import annotations

HINTS: dict[str, str] = {
    "binding_not_connected": "Trace the binding pin through the harness (r3 diagnose --json shows net members); fix the wire or the binding pin.",
    "binding_pin_function_missing": "Choose a computer pin whose catalog `functions` include the interface, then rewire.",
    "binding_pwm_channel_mismatch": "Set the binding `channel` to the pin's hardware PWM channel.",
    "binding_device_mismatch": "Use the Linux device the chosen pins are muxed to (see the computer's catalog software params).",
    "connector_mate_mismatch": "Mating halves must list each other in `mates_with`; pick the opposite gender/family.",
    "connector_multiply_mated": "Each connector mates once; add a splice to branch a circuit.",
    "unknown_connector": "Check the `<component|harness>.<connector>` reference against the catalog entry.",
    "wire_gauge_connector_mismatch": "Change the wire AWG or the connector type so the contact accepts the conductor.",
    "uart_tx_tx_conflict": "Swap the TX/RX wires: each TX must land on the other device's RX.",
    "input_without_driver": "Connect a driver to the input or check for swapped wires.",
    "power_conflict": "Two sources on one rail back-feed each other; remove one source or isolate it with a diode/ideal-diode controller.",
    "power_ground_short": "A supply is wired to ground; check the red/black wires of the harness.",
    "power_in_voltage_out_of_range": "Power the pin from a regulator inside its input range.",
    "power_in_overvoltage": "Rail maximum exceeds the pin's absolute maximum; add regulation or pick a higher-rated part.",
    "logic_level_overvoltage": "Insert a level shifter or divider, or use a device with matching logic levels.",
    "i2c_address_conflict": "Strap one device to an alternate address or move it to another I2C bus.",
    "component_interference": "Move one component by at least the reported depth, or update the catalog envelope if it is conservative.",
    "mass_exceeds_requirement": "Reduce mass (lighter battery/structure) or revise requirements.max_mass.",
    "stage_unavailable": "Install the stage's package or run a subset with --stage.",
    "stage_crashed": "Run the single stage with `r3 validate --stage <name>` and read the traceback in the hint.",
    "spec_conflict": "Re-read robot.yaml (r3_get_spec) and re-apply your change on top of the current revision.",
}


def hint_for(code: str, hint: str = "") -> str:
    return hint or HINTS.get(code, "")

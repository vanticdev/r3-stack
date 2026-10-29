"""Electrical reachability of software bindings to device pins.

A device only sees a software interface if the binding's computer pins are
connected to the device's pins in the derived net graph (crossing internal
links such as the HAT's series resistors). This is evaluated from the actual
harness and mate definitions, never from the binding's ``expects`` list, so a
mis-wired harness silently disconnects the device exactly as hardware would.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from r3_model.project import Project

#: Component kinds that terminate PWM signals (pass-through boards are excluded).
PWM_CONSUMER_KINDS = {"esc", "servo", "motor"}


@dataclass
class Attachment:
    component: str
    pin: str  # component pin name
    binding_pin: str  # computer pin ref the device pin is reached from


@dataclass
class BindingMap:
    name: str
    interface: str
    device: str
    channel: int | None
    computer: str
    attachments: list[Attachment] = field(default_factory=list)


def _reached(project: Project, binding_pin: str, component: str, pin: str) -> bool:
    try:
        return project.graph.connected(binding_pin, f"C:{component}.{pin}", through_internal=True)
    except KeyError:
        return False


def map_bindings(project: Project) -> dict[str, BindingMap]:
    """For every software binding, the device pins electrically reachable from its pins."""
    spec, catalog = project.spec, project.catalog
    out: dict[str, BindingMap] = {}
    for name, binding in spec.software.bindings.items():
        bmap = BindingMap(name, binding.interface, binding.device, binding.channel, binding.computer)
        for cid, comp in spec.components.items():
            if cid == binding.computer:
                continue
            ctype = catalog.component(comp.type)
            for pin_name, pin in ctype.pins.items():
                wanted = False
                if binding.interface == "pwm":
                    wanted = pin.role == "pwm_in" and ctype.kind in PWM_CONSUMER_KINDS
                elif binding.interface == "uart":
                    wanted = pin.role in ("uart_tx", "uart_rx") and ctype.kind != "pcb"
                elif binding.interface == "i2c":
                    wanted = pin.role in ("i2c_sda", "i2c_scl") and any(s.bus == "i2c" for s in ctype.software)
                elif binding.interface == "gpio":
                    wanted = pin.role == "gpio" and ctype.kind != "pcb"
                elif binding.interface == "spi":
                    wanted = pin.role.startswith("spi_")
                if not wanted:
                    continue
                for bpin in binding.pins:
                    if _reached(project, bpin, cid, pin_name):
                        bmap.attachments.append(Attachment(cid, pin_name, bpin))
        out[name] = bmap
    return out


def computer_pin_role(project: Project, pin_ref: str) -> str:
    """Role of a computer connector pin ref ``<component>.<connector>.<pin>``."""
    component, connector, number = pin_ref.split(".")
    ctype = project.catalog.component(project.spec.components[component].type)
    pin_name = ctype.connectors[connector].pins.get(number, "")
    pin = ctype.pins.get(pin_name)
    return pin.role if pin else ""


def uart_paths(project: Project, bmap: BindingMap) -> tuple[str | None, bool, bool]:
    """(device component, device->computer RX path ok, computer->device TX path ok)."""
    component = None
    rx_ok = tx_ok = False
    for att in bmap.attachments:
        role = project.catalog.component(project.spec.components[att.component].type).pins[att.pin].role
        computer_role = computer_pin_role(project, att.binding_pin)
        if role == "uart_tx" and computer_role == "uart_rx":
            component, rx_ok = att.component, True
        if role == "uart_rx" and computer_role == "uart_tx":
            component, tx_ok = component or att.component, True
    return component, rx_ok, tx_ok

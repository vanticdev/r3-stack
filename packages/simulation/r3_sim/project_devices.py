"""Project-owned device bridges; native domain simulators remain authoritative.

Factories receive DeviceContext and return an object implementing ProjectDevice.
They can integrate vendor SDK semantics with native simulator state without
editing the platform or generating external tool file formats.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from r3_model.diagnostics import Diagnostic


class ProjectDeviceConfigurationError(ValueError):
    """A project extension cannot provide its declared electrical factory."""


@runtime_checkable
class ProjectDevice(Protocol):
    def step(self, dt: float) -> None:
        """Advance device/register logic after the native physics/circuit step."""

    def request(self, operation: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Return JSON-compatible results; None means data is not ready yet."""


@dataclass
class DeviceContext:
    kernel: Any
    component: str
    params: dict[str, Any]

    @property
    def t(self) -> float:
        return self.kernel.t

    @property
    def model(self) -> Any:
        """The real mujoco.MjModel, for native public simulator operations."""
        return self.kernel.vehicle.model

    @property
    def data(self) -> Any:
        """The current mujoco.MjData, sampled after a completed native step."""
        return self.kernel.vehicle.data

    @property
    def powered(self) -> bool:
        ctype = self.kernel.project.catalog.component(self.kernel.project.spec.components[self.component].type)
        if not any(pin.role == "power_in" for pin in ctype.pins.values()):
            return True
        return bool(self.kernel.last_outputs.get(self.component, {}).get("powered", False))

    @property
    def bindings(self) -> dict[str, Any]:
        return {name: mapping for name, mapping in self.kernel.bindings.items()
                if mapping.computer == self.kernel.computer and any(a.component == self.component for a in mapping.attachments)}


def load_module(project: Any, name: str) -> Any:
    definition = project.spec.simulation.devices[name]
    path = (project.root / definition.source).resolve()
    base = (project.root / "simulation").resolve()
    if project.root not in base.parents or base not in path.parents or not path.is_file():
        raise ValueError(f"device {name}: source must be a file inside this project's simulation/ directory")
    digest = hashlib.sha256(str(path).encode() + path.read_bytes()).hexdigest()
    cache = getattr(project, "_device_modules", {})
    if digest not in cache:
        spec = importlib.util.spec_from_file_location(f"r3_project_device_{digest}", path)
        if spec is None or spec.loader is None:
            raise ValueError(f"device {name}: cannot load {definition.source}")
        module = importlib.util.module_from_spec(spec)
        # Compile current bytes rather than using mtime-based pyc caching: edits
        # within one filesystem clock tick must not reuse an old device model.
        sys.modules[spec.name] = module
        try:
            exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
        except BaseException:
            sys.modules.pop(spec.name, None)
            raise
        cache[digest] = module
        project._device_modules = cache
    return cache[digest]


def electrical_factory(project: Any, component: str, model: str) -> Any:
    if not model.startswith("project."):
        return None
    name = model.removeprefix("project.")
    definition = project.spec.simulation.devices.get(name)
    if definition is None or definition.component != component or not definition.electrical_factory:
        raise ProjectDeviceConfigurationError(f"{component}: {model} needs a matching simulation.devices electrical_factory")
    factory = getattr(load_module(project, name), definition.electrical_factory, None)
    if not callable(factory):
        raise ProjectDeviceConfigurationError(
            f"device {name}: {definition.source} must export callable {definition.electrical_factory}")
    return factory


def create_devices(kernel: Any) -> dict[str, tuple[DeviceContext, ProjectDevice]]:
    devices = {}
    for name, definition in kernel.project.spec.simulation.devices.items():
        if definition.component not in kernel.project.spec.components:
            raise ValueError(f"device {name}: unknown component {definition.component}")
        context = DeviceContext(kernel, definition.component, dict(definition.params))
        device = getattr(load_module(kernel.project, name), definition.factory)(context)
        if not isinstance(device, ProjectDevice) or not all(callable(getattr(device, method, None)) for method in ("step", "request")):
            raise TypeError(f"device {name}: factory must return step(dt) and request(operation, payload)")
        devices[name] = context, device
        if definition.provenance.status == "estimated":
            kernel.diagnostics.append(Diagnostic.warning("project_device_estimated", f"Project device {name} has estimated behavior",
                                                        subjects=[definition.component], data={"source": definition.source},
                                                        hint="Record sourced model parameters or measured calibration in simulation.devices provenance."))
        kernel.assumptions.append(("devices", f"Project device {name}: {definition.source}; project-authored device logic, "
                                    "not vendor calibration. Native simulator physics and standard checks remain in force."))
    return devices

"""Serialize a project’s declared software configuration for the deploy adapter."""
from __future__ import annotations
import yaml
from pathlib import Path


def document_from_robot_yaml(path: Path, scenario: str | None = None, origin: dict | None = None) -> dict:
    data = yaml.safe_load(path.read_text())
    software = data.get("software", {})
    bindings = {name: {k: v for k, v in binding.items() if k in ("interface", "device", "channel", "frequency", "baud")}
                for name, binding in software.get("bindings", {}).items()}
    for binding in bindings.values():
        if "frequency" in binding:
            from r3_model.units import to_si
            binding["frequency"] = to_si(binding["frequency"])
    return {"module": "bench_controller", "config": software.get("config", {}), "bindings": bindings}

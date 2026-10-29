"""BLITZ deployment for the R3 RC car: one ``rc_car_controller`` per Raspberry Pi 5.

Run from anywhere (paths resolve from this file):

    .venv/bin/python examples/robots/rc-car/backend/deploy.py            # discover + deploy
    .venv/bin/python examples/robots/rc-car/backend/deploy.py --device rc-car.local:9999
    .venv/bin/python examples/robots/rc-car/backend/deploy.py cache      # prepare offline caches

The BLITZ library is located at ``cicd/backend/deployment`` (or the legacy
``backend/deployment``) in an ancestor folder, or at ``$BLITZ_ROOT``.
Optional environment: ``R3_DEPLOY_SCENARIO`` embeds that ``robot.yaml`` scenario's
mission in the deployed config; ``R3_GEODETIC_ORIGIN=lat,lon`` sets the GPS origin.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
PROJECT = BACKEND.parent
MODULE_FOLDER = BACKEND / "python" / "rc_car_controller"


def find_blitz_root(start: Path = BACKEND) -> Path:
    """Folder that contains ``backend/deployment`` (prefers the ``cicd/`` layout)."""
    if os.environ.get("BLITZ_ROOT"):
        return Path(os.environ["BLITZ_ROOT"]).resolve()
    for folder in (start, *start.parents):
        for candidate in (folder / "cicd", folder):
            if (candidate / "backend" / "deployment" / "deployer.py").is_file():
                return candidate
    raise SystemExit("BLITZ not found: expected cicd/backend/deployment or backend/deployment above "
                     f"{start}, or set BLITZ_ROOT")


def find_r3_hal(start: Path = BACKEND) -> Path:
    if os.environ.get("R3_HAL_SOURCE"):
        return Path(os.environ["R3_HAL_SOURCE"]).resolve()
    for folder in start.parents:
        candidate = folder / "packages" / "hardware-interface" / "r3_hal"
        if (candidate / "__init__.py").is_file():
            return candidate
    raise SystemExit(f"r3_hal not found above {start}; set R3_HAL_SOURCE")


def stage_hal(module_folder: Path = MODULE_FOLDER) -> Path:
    """Copy ``r3_hal`` into the module's ``_vendor/`` so the bundle is self-contained.

    BLITZ bundles exactly the module folder (plus ``common/``); it has no way to
    declare an external Python source package, so the HAL travels with the module.
    ``_vendor/`` is generated and git-ignored.
    """
    destination = module_folder / "_vendor" / "r3_hal"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(find_r3_hal(), destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return destination


def _firmware_settings_module():
    spec = importlib.util.spec_from_file_location("rc_car_controller_settings", MODULE_FOLDER / "settings.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def firmware_document() -> dict:
    """Config document deployed to ``BLITZ_CONFIG_PATH`` (robot.yaml software section)."""
    origin = None
    if os.environ.get("R3_GEODETIC_ORIGIN"):
        lat, lon = (float(v) for v in os.environ["R3_GEODETIC_ORIGIN"].split(","))
        origin = {"lat": lat, "lon": lon}
    settings = _firmware_settings_module()
    return settings.document_from_robot_yaml(PROJECT / "robot.yaml", os.environ.get("R3_DEPLOY_SCENARIO"), origin)


def config_base64() -> str:
    """BLITZ config supplier: the watchdog stores this base64 text verbatim."""
    return base64.b64encode(json.dumps(firmware_document(), separators=(",", ":")).encode()).decode("ascii")


sys.path.insert(0, str(find_blitz_root()))

from backend.deployment.compilation.system_builder import DockerCacheInspectionError  # noqa: E402
from backend.deployment.compilation.util.systems import Architecture, LinuxDistro  # noqa: E402
from backend.deployment.deployer import BlitzNetworkDeployer, MissingDependencyCacheError  # noqa: E402
from backend.deployment.fleet import ExpectedSystemType, FleetValidationError  # noqa: E402
from backend.deployment.module.catalog import BackendModules  # noqa: E402
from backend.deployment.module.contracts import TargetRequirements  # noqa: E402
from backend.deployment.module.supported import SupportedModules  # noqa: E402
from backend.deployment.processes import ProcessPlan, WeightedProcess  # noqa: E402

modules = BackendModules(__file__)

# Raspberry Pi 5 running Raspberry Pi OS bookworm: arm64, os-release ID=debian VERSION_ID=12.
RASPBERRY_PI_5 = TargetRequirements(architectures=(Architecture.AARCH64,), distros=(LinuxDistro.DEBIAN_12,))

SIMULATION_ENABLED = False
SIMULATION_DEVICES: tuple[str, ...] = ()
FLEET_SIZE: int | None = None
DEVICE_PROFILES: tuple[ExpectedSystemType, ...] = ()


class ProcessType(WeightedProcess):
    RC_CAR_CONTROLLER = "rc-car-controller", 1.0


def get_modules() -> list[SupportedModules._Generic]:
    return [
        modules.python("rc_car_controller", process=ProcessType.RC_CAR_CONTROLLER, entrypoint="main.py",
                       targets=RASPBERRY_PI_5),
    ]


def pi_name_to_process_types(pi_names: list[str]) -> dict[str, list[ProcessType]]:
    # Every Pi in the fleet is one car's computer and runs exactly one controller.
    plan = ProcessPlan[ProcessType]()
    for name in pi_names:
        plan.pin(ProcessType.RC_CAR_CONTROLLER, name)
    return plan.assign(pi_names)


if __name__ == "__main__":
    stage_hal()
    os.chdir(PROJECT)  # BLITZ reads the project's single requirements.txt from the working directory
    config = (
        BlitzNetworkDeployer.Options()
        .set_local_backend_path(modules.backend_path)
        .set_simulation(SIMULATION_ENABLED)
        .set_simulation_devices(SIMULATION_DEVICES)
        .require_fleet_size(FLEET_SIZE)
        .require_device_profiles(DEVICE_PROFILES)
        .set_config_supplier(config_base64)
        .build()
    )
    try:
        BlitzNetworkDeployer.run_cli(get_modules(), pi_name_to_process_types, config=config)
    except (MissingDependencyCacheError, DockerCacheInspectionError, FleetValidationError) as error:
        raise SystemExit(str(error)) from None
    except subprocess.CalledProcessError as error:
        if error.cmd and error.cmd[0] == "docker":
            raise SystemExit(f"Docker command failed with exit code {error.returncode}; see the step output and log path above.") from None
        raise

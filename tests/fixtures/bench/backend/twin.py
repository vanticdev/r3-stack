"""Host and container adapters for the synthetic instrument firmware."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shlex
import shutil
import sys
import zipfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
PROJECT = BACKEND.parent
MODULE_NAME = "bench_controller"
MODULE_FOLDER = BACKEND / "python" / MODULE_NAME
IMAGE_TAG = "r3/bench-controller:sim"
BLITZ_INSTALL = "/opt/blitz"


def _repo_root() -> Path:
    for folder in BACKEND.parents:
        if (folder / "packages" / "hardware-interface" / "r3_hal").is_dir():
            return folder
    raise RuntimeError("repository root with packages/hardware-interface not found")


HAL_PACKAGE_ROOT = _repo_root() / "packages" / "hardware-interface"
if str(HAL_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(HAL_PACKAGE_ROOT))

from r3_hal import container  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_config(path: Path, scenario: str | None = None, origin: tuple[float, float] | None = None) -> Path:
    """Firmware config document for a scenario (robot.yaml software section + mission)."""
    settings = _load("bench_controller_settings", MODULE_FOLDER / "settings.py")
    document = settings.document_from_robot_yaml(
        PROJECT / "robot.yaml", scenario, {"lat": origin[0], "lon": origin[1]} if origin else None)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n")
    return path


def host_command(port: int, config: Path | None = None, host: str = "127.0.0.1",
                 python: str = sys.executable) -> tuple[list[str], dict[str, str], Path]:
    """(argv, environment, working directory) for the host-process mode."""
    environment = {
        "R3_HAL": "virtual",
        "R3_HAL_ENDPOINT": f"tcp://{host}:{int(port)}",
        "PYTHONPATH": os.pathsep.join([str(MODULE_FOLDER), str(HAL_PACKAGE_ROOT)]),
        "PYTHONUNBUFFERED": "1",
    }
    if config is not None:
        environment["R3_FIRMWARE_CONFIG"] = str(Path(config).resolve())
    return [python, "-u", str(MODULE_FOLDER / "main.py")], environment, MODULE_FOLDER


# Catalog capabilities a Raspberry Pi 5 on Pi OS bookworm reports (GPIO chardev,
# I2C/SPI dev nodes, V4L2 ISP/codec nodes); they are part of the BLITZ build key.
PI5_CAPABILITIES = (("gpio", "present"), ("i2c", "present"), ("spi", "present"), ("v4l2", "present"))


def _blitz_bundle_spec(staging: Path, allow_stale_dependencies: bool = False) -> container.RuntimeSpec:
    """Build the real BLITZ bundle for a Raspberry Pi 5 profile and describe how the
    watchdog would start it. Needs Docker and the BLITZ dependency cache (network
    on first use). ``allow_stale_dependencies`` explicitly accepts an older cached
    dependency image of the same profile, as BLITZ's deploy prompt does (safe here:
    the module has no third-party Python requirements)."""
    deploy = _load("bench_deploy", BACKEND / "deploy.py")
    deploy.stage_hal()
    from backend.deployment.bundler import CodeBundler
    from backend.deployment.compilation.system_builder import SystemBuilder
    from backend.deployment.compilation.util.systems import Architecture, LinuxDistro, PythonVersion, SystemId

    # Raspberry Pi OS bookworm on a Pi 5: glibc 2.36, aarch64, Debian 12, CPython 3.11.
    system = SystemId(c_lib_version="2.36", linux_distro=LinuxDistro.DEBIAN_12, architecture=Architecture.AARCH64,
                      python_version=PythonVersion(major=3, minor=11), capabilities=PI5_CAPABILITIES)
    previous = Path.cwd()
    os.chdir(PROJECT)  # BLITZ reads requirements.txt from the working directory
    try:
        bundler = CodeBundler(deploy.get_modules(), deploy.modules.backend_path, str(staging / "blitz-build"),
                              str(staging / "blitz-out"), system)
        bundler.prepare()
        builder = SystemBuilder(system, bundler.build_path, bundler.context_path, bundler.backend_local_path)
        dependency_image = None
        if not builder.dependencies_cached():
            stale = builder.stale_dependency_images() if allow_stale_dependencies else []
            if stale:
                dependency_image = stale[0]
                print(f"Using older cached BLITZ dependency image {dependency_image}", file=sys.stderr)
            else:
                builder.cache_dependencies()
        archive = Path(bundler.bundle(prepared=True, dependency_image=dependency_image))
    finally:
        os.chdir(previous)
    extracted = staging / "bundle"
    if extracted.exists():
        shutil.rmtree(extracted)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(extracted)
    children = [child for child in extracted.iterdir()]
    if len(children) == 1 and children[0].is_dir():  # archives wrap the bundle in its build-key folder
        extracted = children[0]
    module = deploy.get_modules()[0]
    local_root = str(extracted.resolve())

    def on_device(value: str) -> str:
        return value.replace(local_root, f"{BLITZ_INSTALL}/backend")

    environment = {key: on_device(value) for key, value in module.get_run_environment(local_root).items()}
    return container.RuntimeSpec(
        module=MODULE_NAME, bundle_dir=extracted,
        argv=[on_device(a) for a in module.get_run_argv(local_root, BLITZ_INSTALL)],
        workdir=on_device(module.get_run_directory(local_root)),
        environment=environment, labels={"org.r3.bundle": f"blitz:{archive.name}"})


def build_image(tag: str = IMAGE_TAG, blitz: bool = False, staging: Path | None = None,
                allow_stale_dependencies: bool = False) -> str:
    staging = Path(staging or PROJECT / "build" / "software" / "container")
    if blitz:
        spec = _blitz_bundle_spec(staging, allow_stale_dependencies)
    else:
        spec = container.spec_from_module(MODULE_FOLDER, staging)
    container.write_context(spec, staging / "context")
    return container.build_image(staging / "context", tag)


def container_command(port: int, config: Path | None = None, tag: str = IMAGE_TAG, name: str | None = None) -> list[str]:
    return container.run_command(tag, port, config=config, name=name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    config = commands.add_parser("config")
    config.add_argument("--scenario")
    config.add_argument("--out", type=Path, required=True)
    config.add_argument("--origin", help="geodetic origin lat,lon used by the twin's GPS model")
    image = commands.add_parser("image")
    image.add_argument("--tag", default=IMAGE_TAG)
    image.add_argument("--blitz", action="store_true", help="package the real BLITZ bundle into the image")
    image.add_argument("--allow-stale-dependencies", action="store_true",
                       help="accept an older cached BLITZ dependency image for the Pi 5 profile")
    for name in ("host-command", "container-command"):
        command = commands.add_parser(name)
        command.add_argument("--port", type=int, required=True)
        command.add_argument("--config", type=Path)
        command.add_argument("--tag", default=IMAGE_TAG)
    args = parser.parse_args(argv)
    if args.command == "config":
        origin = tuple(float(v) for v in args.origin.split(",")) if args.origin else None
        print(write_config(args.out, args.scenario, origin))
    elif args.command == "image":
        print(build_image(args.tag, args.blitz, allow_stale_dependencies=args.allow_stale_dependencies))
    elif args.command == "host-command":
        argv_, environment, cwd = host_command(args.port, args.config)
        print(f"cd {shlex.quote(str(cwd))} && " + " ".join(f"{k}={shlex.quote(v)}" for k, v in environment.items())
              + " " + shlex.join(argv_))
    else:
        print(shlex.join(container_command(args.port, args.config, args.tag)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

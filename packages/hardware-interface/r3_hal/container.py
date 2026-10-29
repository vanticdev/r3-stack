"""Raspberry Pi userspace container for running robot software against the twin.

The image is ``linux/arm64`` ``debian:bookworm-slim`` + ``python3`` (Debian 12,
glibc 2.36, CPython 3.11): the userspace of Raspberry Pi OS bookworm on a
Raspberry Pi 5. Docker provides the userspace only; all hardware access goes
through ``r3_hal``'s virtual backend to the digital twin over TCP.

The filesystem mirrors a BLITZ device so the program starts exactly as the
watchdog would start it:

=================================  ============================================
BLITZ device                       container
=================================  ============================================
``$BLITZ_PATH``                    ``/opt/blitz``
``$BLITZ_PATH/.venv/bin/python``   symlink to Debian ``/usr/bin/python3``
installed bundle ``backend/``      ``/opt/blitz/backend`` (module at
                                   ``backend/python/<module>``)
watchdog argv ``python -u main``   image ``ENTRYPOINT`` (same argv)
working dir = module folder        ``WORKDIR``
``BLITZ_*`` / ``PYTHONPATH`` env   image ``ENV`` (same values)
non-root device user               ``USER blitz`` (uid 1000)
``BLITZ_CONFIG_PATH``              ``R3_FIRMWARE_CONFIG`` mounted read-only
=================================  ============================================

The BLITZ build Dockerfile uses ``FROM --platform=linux/arm64 debian:12`` for the
same target profile (``LinuxDistro.DEBIAN_12``); this runtime image uses the slim
variant of the same release. The bundle can be the real BLITZ bundle (see
``examples/robots/rc-car/backend/sim_image.py``) or be composed directly from a
module folder with :func:`spec_from_module`.

CLI::

    python -m r3_hal.container build --module PATH [--tag TAG]
    python -m r3_hal.container command --tag TAG --port 7600 [--config FILE]
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

PLATFORM = "linux/arm64"
BASE_IMAGE = "debian:bookworm-slim"
BLITZ_PATH = "/opt/blitz"
CONFIG_MOUNT = "/etc/r3/firmware.json"
HAL_DIR = Path(__file__).resolve().parent


@dataclass
class RuntimeSpec:
    """What to put in the image and how to start it (BLITZ watchdog semantics)."""

    module: str
    bundle_dir: Path                       # contents become /opt/blitz/backend
    argv: list[str]
    workdir: str
    environment: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)


def _copy_tree(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", ".pytest_cache"))


def spec_from_module(module_dir: Path, staging_dir: Path, entrypoint: str = "main.py",
                     hal_dir: Path = HAL_DIR) -> RuntimeSpec:
    """Compose a BLITZ-shaped bundle from one Python module folder plus ``r3_hal``.

    ``r3_hal`` goes into the module's ``_vendor/`` folder, matching what the
    project's ``deploy.py`` stages before a BLITZ build.
    """
    module_dir = Path(module_dir).resolve()
    name = module_dir.name
    bundle = Path(staging_dir) / "bundle"
    if bundle.exists():
        shutil.rmtree(bundle)
    target = bundle / "python" / name
    _copy_tree(module_dir, target)
    vendor = target / "_vendor" / "r3_hal"
    if vendor.exists():
        shutil.rmtree(vendor)
    _copy_tree(hal_dir, vendor)
    module_path = f"{BLITZ_PATH}/backend/python/{name}"
    return RuntimeSpec(
        module=name, bundle_dir=bundle,
        argv=[f"{BLITZ_PATH}/.venv/bin/python", "-u", f"{module_path}/{entrypoint}"],
        workdir=module_path,
        environment={
            "PYTHONPATH": f"{module_path}:{BLITZ_PATH}/backend/shared",
            "BLITZ_PATH": BLITZ_PATH,
            "BLITZ_BUNDLE_FOLDER_PATH": f"{BLITZ_PATH}/backend",
            "BLITZ_MODULE_DIR": module_path,
            "BLITZ_RESOURCE_DIR": f"{BLITZ_PATH}/backend/resources/{name}",
            "BLITZ_LIB_DIR": f"{BLITZ_PATH}/backend/lib",
            "BLITZ_GENERATED_DIR": f"{BLITZ_PATH}/backend/shared/backend/generated",
        },
        labels={"org.r3.bundle": "module-folder"},
    )


def dockerfile(spec: RuntimeSpec, platform: str = PLATFORM, base_image: str = BASE_IMAGE) -> str:
    environment = {"R3_HAL": "virtual", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
                   "BLITZ_SYSTEM_NAME": "r3-twin", **spec.environment}
    labels = {"org.r3.module": spec.module, "org.r3.platform": platform, "org.r3.base": base_image,
              "org.r3.profile": "raspberry-pi-os-bookworm-arm64", **spec.labels}
    lines = [
        f"FROM --platform={platform} {base_image}",
        "RUN apt-get update \\",
        " && apt-get install -y --no-install-recommends python3 \\",
        " && rm -rf /var/lib/apt/lists/*",
        f"RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin blitz \\",
        f" && mkdir -p {BLITZ_PATH}/.venv/bin /etc/r3 \\",
        f" && ln -s /usr/bin/python3 {BLITZ_PATH}/.venv/bin/python",
        f"COPY --chown=blitz:blitz bundle {BLITZ_PATH}/backend",
        *[f"ENV {key}={json.dumps(value)}" for key, value in sorted(environment.items())],
        *[f"LABEL {key}={json.dumps(value)}" for key, value in sorted(labels.items())],
        f"WORKDIR {spec.workdir}",
        "USER blitz",
        f"ENTRYPOINT {json.dumps(spec.argv)}",
    ]
    return "\n".join(lines) + "\n"


def write_context(spec: RuntimeSpec, context_dir: Path, platform: str = PLATFORM) -> Path:
    context_dir = Path(context_dir)
    context_dir.mkdir(parents=True, exist_ok=True)
    bundle = context_dir / "bundle"
    if bundle.resolve() != Path(spec.bundle_dir).resolve():
        if bundle.exists():
            shutil.rmtree(bundle)
        _copy_tree(Path(spec.bundle_dir), bundle)
    (context_dir / "Dockerfile").write_text(dockerfile(spec, platform))
    (context_dir / "runtime-spec.json").write_text(json.dumps(
        {"module": spec.module, "argv": spec.argv, "workdir": spec.workdir, "environment": spec.environment,
         "platform": platform, "base_image": BASE_IMAGE}, indent=2) + "\n")
    return context_dir


def build_image(context_dir: Path, tag: str, platform: str = PLATFORM, log_path: Path | None = None) -> str:
    """``docker build``; raises CalledProcessError with the log path on failure."""
    command = ["docker", "build", "--platform", platform, "--tag", tag, str(context_dir)]
    log_path = Path(log_path or Path(context_dir) / "docker-build.log")
    with log_path.open("w") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, command, output=f"see {log_path}")
    return tag


def run_command(tag: str, port: int, host: str = "host.docker.internal", config: Path | None = None,
                name: str | None = None, platform: str = PLATFORM, extra_args: list[str] | None = None,
                environment: dict[str, str] | None = None) -> list[str]:
    """argv for ``docker run`` connecting the firmware to a twin listening on the host."""
    command = ["docker", "run", "--rm", "--platform", platform, "--add-host", "host.docker.internal:host-gateway",
               "-e", "R3_HAL=virtual", "-e", f"R3_HAL_ENDPOINT=tcp://{host}:{int(port)}"]
    if name:
        command += ["--name", name]
    if config is not None:
        command += ["-v", f"{Path(config).resolve()}:{CONFIG_MOUNT}:ro", "-e", f"R3_FIRMWARE_CONFIG={CONFIG_MOUNT}"]
    for key, value in (environment or {}).items():
        command += ["-e", f"{key}={value}"]
    return command + [tag, *(extra_args or [])]


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m r3_hal.container", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="build an image from a module folder")
    build.add_argument("--module", required=True, type=Path)
    build.add_argument("--entrypoint", default="main.py")
    build.add_argument("--tag", default=None)
    build.add_argument("--context", type=Path, default=None)
    show = commands.add_parser("command", help="print the docker run command")
    show.add_argument("--tag", required=True)
    show.add_argument("--port", type=int, required=True)
    show.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.command == "build":
        module = args.module.resolve()
        context = args.context or Path("build/software/container") / module.name
        spec = spec_from_module(module, context, args.entrypoint)
        write_context(spec, context)
        tag = args.tag or f"r3/{module.name.replace('_', '-')}:sim"
        build_image(context, tag)
        print(tag)
    else:
        print(shlex.join(run_command(args.tag, args.port, config=args.config)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

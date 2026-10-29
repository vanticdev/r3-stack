# R3 worker image

One image that runs the whole R3 pipeline headless, on servers and in GitHub
Actions. See [headless operation](../../docs/architecture/headless.md) for the
per-stage audit.

| Component | Version / source |
| --- | --- |
| Base | `ubuntu:24.04`, `linux/amd64` and `linux/arm64` |
| Python | 3.12 (Ubuntu) in `/opt/r3/venv`; deps pinned in `requirements.txt` |
| r3-platform | editable install of this repository at `/opt/r3/platform` (`r3`, `r3_*`) |
| MuJoCo | 3.17 wheel; offscreen GL via EGL (`libegl1`, Mesa) or OSMesa (`libosmesa6`) |
| Python libs | numpy, scipy, matplotlib (Agg), pillow, manifold3d, trimesh, mcp, kicad-python, pytest, BLITZ deps |
| ngspice | Ubuntu `ngspice` (batch binary) + `libngspice0` / `libngspice0-dev` (shared library) |
| KiCad | 10.0 from the official PPA `ppa:kicad/kicad-10.0-releases` (GUI + `kicad-cli`, symbols, footprints, templates) |
| KiCad IPC | `Xvfb` + `r3-kicad-headless` launcher |
| Freerouting | 2.5.0 release jar, `/opt/freerouting/freerouting.jar`, `freerouting` wrapper (CLI mode) |
| Java | `openjdk-25-jre-headless` (Freerouting 2.5 targets Java 25) |
| Docker | `docker-ce-cli` + buildx plugin (client only) |
| Other | `ffmpeg` (sim clips), Node.js 24 (web loader tests), `tini` (PID 1), `git`, `make` |
| Sionna RT | **optional layer**, off by default (`WITH_SIONNA=1`) |

The `kicad/kicad` image was not used as the base because it is published for
amd64 only. The KiCad PPA builds 10.0.x for both architectures.

## Build

From the repository root:

```sh
# native architecture, loaded into the local daemon
docker buildx build -f docker/r3-worker/Dockerfile -t r3-worker:dev --load .

# both architectures, pushed (CI does this in .github/workflows/r3-platform.yml)
docker buildx build --platform linux/amd64,linux/arm64 -f docker/r3-worker/Dockerfile \
  -t ghcr.io/<owner>/r3-worker:<tag> --push .
```

Build arguments:

| Argument | Default | Effect |
| --- | --- | --- |
| `KICAD_PPA` | `kicad/kicad-10.0-releases` | KiCad release series |
| `KICAD_3D_MODELS` | `0` | `1` adds `kicad-packages3d` (about 3.3 GB) for 3D renders and STEP with component models |
| `FREEROUTING_VERSION` | `2.5.0` | release jar to download |
| `JAVA_PACKAGE` | `openjdk-25-jre-headless` | JRE for Freerouting |
| `NODE_VERSION` | `24.22.2` | Node.js release tarball (checksum-verified) for the web app tests |
| `WITH_SIONNA` | `0` | `1` installs `sionna-rt` with Dr.Jit's CPU (LLVM) backend: `libllvm18`, `DRJIT_LIBLLVM_PATH` |
| `SIONNA_RT_VERSION` | `2.2.0` | Sionna RT version for the optional layer |

The image is about 3.95 GB on arm64 without 3D models. The largest layers are
KiCad and its wx/GTK/OCCT stack (1.1 GB), system tools plus GL and Java
(0.9 GB), the Python venv (0.57 GB), Node.js (0.14 GB), the Docker CLI
(0.12 GB) and Freerouting (65 MB). `WITH_SIONNA=1` adds about 0.6 GB.

## Run

```sh
docker run --rm r3-worker:dev                    # r3 doctor (default command)
docker run --rm -v "$PWD":/work r3-worker:dev r3 validate --stage model,wiring,electrical,sim
docker run --rm -v "$PWD":/opt/r3/platform -w /opt/r3/platform r3-worker:dev python -m pytest tests
```

Environment set by the image: `MPLBACKEND=Agg`, `OPENBLAS_NUM_THREADS=1`,
`OMP_NUM_THREADS=1`, `R3_FREEROUTING_JAR`, `R3_WORKER_IMAGE=1`. `MUJOCO_GL` is
left unset. `r3` picks EGL when a GPU device is passed through (`--gpus all`
or `--device /dev/dri`), else OSMesa. Set `MUJOCO_GL` to force one.

Onshape: pass `ONSHAPE_ACCESS_KEY` / `ONSHAPE_SECRET_KEY` (and optionally
`ONSHAPE_BASE_URL`, `ONSHAPE_API_AUTH=hmac|basic`). The browser bridge is not
available in the image.

## KiCad

* `kicad-cli` works directly: ERC, DRC, Gerbers, drill, STEP/GLB/STL, SVG/PDF,
  and `kicad-cli pcb render` (3D PNG).
* The IPC API needs the GUI app. Run it under Xvfb:

  ```sh
  r3-kicad-headless [--app kicad|pcbnew|eeschema] [FILE] -- python my_ipc_script.py
  ```

  The launcher seeds a first-run configuration so no dialog blocks startup: the
  global symbol, footprint and design-block tables, the API server enabled,
  update checks off. It then starts Xvfb and KiCad, waits for
  `/tmp/kicad/api*.sock`, exports `KICAD_API_SOCKET`, runs the command, and
  tears everything down. Without a command it keeps KiCad in the foreground.
* pcbnew's SWIG module is installed for the system interpreter (`/usr/bin/python3`),
  not the venv (for example, Specctra DSN export for Freerouting).

## Freerouting

```sh
freerouting -de board.dsn -do board.ses     # = java -Djava.awt.headless=true -jar ... --gui.enabled=false
```

## Docker for firmware containers

The image contains only the Docker client. Pick one way to reach a daemon:

* **Mounted host socket (recommended).** Run with
  `-v /var/run/docker.sock:/var/run/docker.sock`. GitHub Actions container jobs
  mount it automatically. Firmware containers then run as *siblings* on the
  host daemon. `r3_hal.container.run_command` detects this and joins the worker
  container's network namespace (`--network container:<id>`), so the firmware
  reaches the twin on 127.0.0.1. Bind-mount sources are translated to host paths
  through the worker's own mounts, so files to share must live under a mounted
  directory (for example the workspace). Override with
  `R3_DOCKER_NETWORK=host-gateway|<network>`. Root access to the host daemon is
  root on the host; use this only on trusted runners.
* **Docker-in-Docker.** Run a `docker:dind` sidecar (privileged) and set
  `DOCKER_HOST=tcp://dind:2375` (or TLS). This isolates the daemon from the
  host, at the cost of a privileged sidecar and a cold image cache. Firmware
  containers run inside the sidecar's network, so the twin endpoint must be
  reachable from it. Set `R3_DOCKER_NETWORK` and the twin host accordingly.

The firmware image is `linux/arm64`. On amd64 hosts, register QEMU once:
`docker run --privileged --rm tonistiigi/binfmt --install arm64`.

## Sionna RT (optional)

`--build-arg WITH_SIONNA=1` adds `sionna-rt` on Dr.Jit's LLVM (CPU) backend. It
is off by default while Sionna is integrated in `r3_sim` (separate work).
`r3 doctor` reports the Mitsuba variant it would use.

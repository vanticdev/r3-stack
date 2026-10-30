# Headless operation

R3 validation stages consume the pinned pull on a server with no display or interactive browser.
Native CAD authoring and pull need the signed-in onshape-native browser/companion.
The supported server environment is the R3 worker image
([`docker/r3-worker`](../../docker/r3-worker/README.md)). CI uses the same image:
`.github/workflows/r3-platform.yml` for this repository, and the scaffolded
`.github/workflows/r3.yml` for robot projects.

`r3 doctor` (CLI) and `r3_doctor` (MCP) report what is available in the current
environment and which stages can run headless. `r3 doctor --require model,sim`
exits 1 if any listed stage cannot. The report covers the MuJoCo GL backend and
renderer, libngspice, kicad-cli, KiCad IPC, Freerouting, Java, Docker, the
Onshape transport, and Sionna RT, with versions.

## Stage audit

| Stage | Display/browser dependency | Headless path |
| --- | --- | --- |
| `model` | matplotlib layout plot | Agg backend, forced in `model_stage.py`. |
| `cad` | No browser for validation. Native modeling/pull needs a signed-in browser companion. | Validate `cad/pulled` headlessly with OCP/STL. Missing, modified or incomplete pulls fail; no catalog fallback. |
| `pcb` | KiCad GUI for the IPC API | `kicad-cli` covers ERC/DRC, Gerbers, STEP/GLB, SVG/PDF and `pcb render` (3D PNG, no display needed). IPC API steps run under Xvfb through `r3-kicad-headless`. |
| `pcb` routing | Freerouting GUI | `freerouting -de board.dsn -do board.ses` (`--gui.enabled=false`, `java.awt.headless`). DSN export uses pcbnew's SWIG module (`/usr/bin/python3`). |
| `wiring` | matplotlib renders, MuJoCo cable | Agg is forced in `r3_wiring/render.py`. Cable physics needs no GL. |
| `electrical` | matplotlib plots, ngspice | Agg is forced. ngspice runs in batch mode (`-b`); libngspice is in the image. |
| `software` | Docker | The Docker CLI in the image uses the host daemon (mounted socket) or a DinD sidecar. arm64 images on amd64 hosts need QEMU binfmt. |
| `sim` | MuJoCo offscreen renders, matplotlib | `MUJOCO_GL` is chosen automatically (below). Plots use Agg. Ray-traced RF needs Sionna RT on CPU (LLVM) or a GPU; without it the analytic link model runs. |
| `report` | none | Static HTML + JSON. |

## MuJoCo rendering

MuJoCo fixes its GL backend when it is first imported. `r3_sim.headless.configure()`
runs first in the `r3` CLI, the MCP server and the simulation worker, and on any
`import r3_sim`. On Linux with no `DISPLAY`/`WAYLAND_DISPLAY` and no explicit
`MUJOCO_GL`, it chooses:

* `egl` when a GPU device node exists (`/dev/dri/renderD*`, `/dev/nvidia0`), so
  the vendor driver renders;
* otherwise `osmesa`. Both backends then run on Mesa's CPU rasterizer. Through
  EGL, llvmpipe showed shadow-map acne across the floor in the robot renders;
  OSMesa did not (worker image on arm64: EGL Mesa 25.2.8, OSMesa 25.1.7).

It also sets `MPLBACKEND=Agg`. Explicit settings always win. `r3 doctor` renders
a 32x32 test frame per backend and reports `GL_RENDERER` (for example `llvmpipe`).

## BLAS threads

The co-simulation solves many tiny linear systems per step. With OpenBLAS's
default thread pool (wheels of numpy/scipy on Linux), a 12 s scenario ran more
than ten times slower on a 16-core container than with one thread (29.7 s). The
worker image sets `OPENBLAS_NUM_THREADS=1` and `OMP_NUM_THREADS=1`. Set the same
on any other Linux server before Python starts. macOS uses Accelerate and is
unaffected.

## Onshape

Agents model native parts, mate connectors and assemblies through onshape-native. Pull uses
its authenticated loopback `/native/api/v17` UI export backend for STL/STEP and local/editor
routes for metadata and viewport capture. Public `/api` endpoints, API-key/session transports,
bodydetails, tessellatedfaces, massproperties, shadedviews and public translations are unavailable.
The updated extension and restarted companion are required; unsupported operations fail explicitly.

Select/create an immutable version before pulling. Only STL supports microversions; STEP
requires a version. A server without a native browser session can validate and simulate a
previously pulled, pinned copy. It cannot author or refresh CAD. Missing data remains an error.
Set `ONSHAPE_NATIVE_RUNTIME` to the paired bridge runtime. Agent sessions reuse the installed
plugin launcher; `R3_ONSHAPE_NATIVE_MCP_CONFIG` can supply its `.mcp.json` explicitly.

## KiCad IPC API under Xvfb

The IPC API (kicad-python / `kipy`) is served only by a running KiCad GUI.
`r3-kicad-headless` (in the image) does the following:

1. seeds a first-run configuration: global symbol, footprint and
   design-block library tables, the API server enabled, first-run prompts
   answered, and update checks off. A missing table opens KiCad 10's "KiCad
   Setup" wizard, which blocks the API forever;
2. starts Xvfb on a free display, then the KiCad app;
3. waits for `/tmp/kicad/api*.sock` and exports `KICAD_API_SOCKET`;
4. runs the given command, then stops KiCad and Xvfb.

```sh
r3-kicad-headless -- python -c "from kipy import KiCad; print(KiCad().get_version())"
# KiCad IPC version: 10.0.7 (10.0.7-10.0.7~ubuntu24.04.1)
```

## What still needs care

* **Onshape without a native browser session.** Consume an existing verified `cad/pulled`
  copy; new authoring/export is unavailable. Never fall back to public APIs or catalog shapes.
* **Firmware containers** need a Docker daemon. GitHub-hosted container jobs mount
  the runner's socket. Elsewhere, mount `/var/run/docker.sock` or run a
  `docker:dind` sidecar. linux/arm64 images on amd64 hosts need
  `tonistiigi/binfmt --install arm64` (privileged, once per host).
* **Sionna RT** is an optional image layer (`--build-arg WITH_SIONNA=1`). On CPU it
  runs through Dr.Jit's LLVM backend, which is much slower than CUDA.
* **Specctra DSN export** has no `kicad-cli` command in KiCad 10. It uses pcbnew's
  SWIG module (system `python3`), which KiCad has deprecated in favour of the
  IPC API; the IPC path runs under `r3-kicad-headless`.


> **Update (user directive):** KiCad never opens a window, not even under Xvfb.
> The IPC API (which needs the KiCad GUI) and the `r3-kicad-headless` launcher
> were removed. Board editing uses KiCad's bundled `pcbnew` Python module
> (`python3-pcbnew` from the KiCad PPA on Linux), and checks, netlists and
> exports use `kicad-cli`. `r3 doctor` reports this as `kicad-pcbnew`.

## Native CAD organization

The agent manages document tabs/folders, feature folders and assembly sidebars with
onshape-native's document and sidebar tools. These edits happen in Onshape and are never
regenerated from YAML. Pull reads the resulting solved assembly, native mates and connectors.
It does not create documents, geometry, interfaces or assembly placement from local plans.

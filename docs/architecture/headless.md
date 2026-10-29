# Headless operation

Every R3 stage runs on a server with no display and no interactive browser.
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
| `cad` | Onshape. The `onshape-native` bridge needs a logged-in browser. | Official API keys (`ONSHAPE_ACCESS_KEY`/`ONSHAPE_SECRET_KEY`, HMAC-signed requests). Images come from the REST shaded-view endpoints and are written to `build/cad/views/{iso,front,top,right}.png`, plus `assembly_*.png` when the document has an Assembly. Without keys, a current `cad_model.json` is reused; otherwise downstream stages fall back to catalog geometry. |
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
  EGL, llvmpipe showed shadow-map acne across the floor in the RC-car renders;
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

* `R3_ONSHAPE_TRANSPORT`: `auto` (default: the bridge when connected, else API
  keys), `api-keys`, `native` or `none`. CI sets `api-keys`.
* `ONSHAPE_API_AUTH`: `hmac` (default) or `basic`. Onshape's API-key docs
  describe basic auth as "for local testing only". The request signature
  (`Authorization: On <access>:HmacSHA256:<sig>`, with `Date` and `On-Nonce`
  headers) is used by default. Each redirect hop is signed again, as the docs
  require.
* `ONSHAPE_BASE_URL` for enterprise domains (default `https://cad.onshape.com`).
* `R3_CAD_VIEWS=0` skips the shaded views.
* Every request still passes `r3_onshape.policy.check_request` (no FeatureScript).

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

* **Onshape without API keys.** The browser bridge cannot run on a server. CI
  without keys reuses the committed or cached `cad_model.json`, or falls back
  to catalog geometry (`cad_geometry_estimated`).
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

## Onshape document organization (phase B)

The organized RC-car document (`r3_onshape.organized`) is built over REST with the
headless session or API keys. Everything that defines the CAD, places it and verifies it is
headless. Organizational edits that Onshape exposes only in its editor protocol are not.

**Headless over REST (server):**

* documents, Part Studios, Assemblies and versions: create, delete, version;
* the Variable Studio (`createVariableStudio`, `setVariables`) and its references from each
  Part Studio (`setVariableStudioReferences`); features use `#name` expressions;
* standard features (sketch, extrude with start offset, revolve, Hole, Mirror, mate
  connector) and part metadata (name, appearance, material, mass override, part number);
* assembly instances (including the linked parts-library version), occurrence transforms
  and mates. The solved occurrence transforms are read back from the assembly definition and
  compared with the compiled poses;
* `bodydetails`, fine tessellations and mass properties for the fit checks; shaded views for
  every Part Studio, subassembly and the top level;
* organization-check reads: feature names, types and expressions (`getPartStudioFeatures`),
  sketch solve status (`getSketchInfo` returns `sketchSolveStatus`, so `cad_unconstrained_sketch`
  is exact), part names, mate names, construction-plane counts, and document tab folders
  (`getDocumentContents` returns the `BTElementGroup` tree).

**Not in the REST API (done through the onshape-native bridge on a developer machine):**

| Operation | Native message | REST gap |
| --- | --- | --- |
| Create tab folders, move tabs into them | `ui.document.GBTUiEditElementGroups` | REST reads folders but cannot create or move them |
| Part Studio feature folders | `partstudiofolders.GBTUiCreateFolder` | `getPartStudioFeatures` omits folder nodes, so folder membership (`cad_feature_not_in_folder`) can only be read in the editor |
| Assembly instance and mate folders | `ui.assembly.GBTUiCreateAssemblyFolder` | the assembly definition has no sidebar folders (`cad_assembly_unfoldered` needs the editor) |
| Hide planes, sketches and mate connectors | `ui.GBTSetFeatureVisibility` inside `ui.assembly.GBTUiBatchVisibilityChange` | REST has no visibility field for Part Studio features; assembly visibility is read with the bridge's `display_state` |
| Flexible subassemblies | none found (REST or captured native) | subassemblies are inserted rigid; steering motion is verified by the linkage solver and the MuJoCo animation |

Sketches consumed by a feature are hidden by Onshape itself, so even a headless build shows
no sketch clutter. The phase B builder creates no construction planes at all:
`cad_excess_construction_planes` counts them over REST, and the only plane features are
Onshape's three default planes.

On a server without the bridge, the build and every REST check still run. The organization
writes are skipped and recorded in `cad_model.json` (`organization.organize.skipped`). The check
emits `cad_organization_check_partial`, listing the codes it could not evaluate. It does not
report them as passing.

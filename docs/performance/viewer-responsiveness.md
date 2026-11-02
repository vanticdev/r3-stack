# Viewer responsiveness investigation

Measured November 2, 2026 on an Apple M3 Max with headless Chrome using ANGLE
Metal, 1600 × 1000, device scale 1. API 8351 and Vite 5201 served an isolated
copy of rc-cool-car-23. The original project and existing servers were untouched.
The base is r3/integration with r3/web-speed and r3/memory-fix already merged.

The copy has 525 pulled parts and 1,619,643 native triangles. Inputs remain
unchanged. No decimation, geometry merging, qualification changes, deployment
version bump, or CSS changes were needed for the interaction improvement.

## Controlled before/after

Warm API caches, fresh browser context per run, shared HTTP cache between pages.
Times start at navigation. First/all geometry are animation-frame observations
of STL meshes attached to the scene, not a pixel classification or GPU fence.
FPS and p95 describe browser animation-frame cadence while orbiting the camera
and moving the pointer; Simulation also plays its real recording (525 bodies).
This recording has no cable tracks. Existing conductor unit tests still pass;
this benchmark does not qualify cable-rich simulation performance.

| Page | FPS before → after | p95 frame ms | Longest orbit/hover task ms | First geometry s | All parts s |
| --- | ---: | ---: | ---: | ---: | ---: |
| 3D | 50.2 → 59.8 | 33.4 → 16.8 | 117 → 0 | 5.91 → 5.19 | 12.46 → 12.15 |
| Wiring (X-ray) | 48.5 → 58.9 | 33.4 → 16.8 | 83 → 0 | 5.84 → 5.65 | 6.15 → 7.48 |
| Simulation playback | 27.5 → 58.9 | 83.4 → 16.8 | 116 → 0 | 15.57 → 14.27 | 16.09 → 16.1 |

Zero in the task column means no task met the browser's roughly 50 ms reporting
threshold. It does not mean zero main-thread work. Selection was measured
separately: the first Inspector row required 2,100 part render invocations before
and 2 after (development Strict Mode included); the longest task was 114 → 91 ms.
Hover/playing part render invocations fell to zero on all three pages.

| Page | JS heap MB before → after | Draw calls before → after | Load task max ms before → after |
| --- | ---: | ---: | ---: |
| 3D | 457 → 294 | 1138 → 613 | 729 → 550 |
| Wiring (X-ray) | 313 → 284 | 1138 → 1138 | 482 → 371 |
| Simulation playback | 469 → 363 | 1061 → 536 | 1158 → 286 |

Heap is an approximate `performance.memory` sample without forced GC. Draw calls
include grid, conductors, gizmo and transparency passes. X-ray keeps its original
draw cost. Full native triangle count is unchanged; ordinary 3D submitted
triangles dropped from 4,027,059 to 1,956,045 by removing wide edge overlays.
The fresh-context 3D transfer was approximately 27.8 MB of compressed artifact
bodies both before and after; compressed delivery was already enabled. Worker
requests are counted using Playwright request sizes because page resource timing
does not include dedicated-worker fetches. See viewer-benchmark.json for details.

## Root causes and changes

- Eager edge extraction and wide-line rendering for every normal component cost
  CPU time and 525 draw calls. Keep outlines for selected/diagnostic-highlighted
  parts; preserve the original meshes and identities for picking and transforms.
- Main-thread STL parsing and triangle-by-triangle picking stalled pointer work.
  Two reusable workers fetch/parse full STL geometry and build BVHs, transferring
  typed arrays, normals, sphere bounds and the picking tree without JSON encoding.
  Workers stop after the queue drains. Selected outlines are still computed on
  demand on the main thread.
- Hover and the playback clock rerendered every part and repeatedly marked
  materials for shader updates. Memoize parts/wires, stabilize callbacks and
  registrations, and only mark transparency changes for material recompilation.
- CPU profiling attributed about 570 ms to repeated Inspector diagnostic prefix
  scans. Index once by the immutable subject map, retaining pin ownership,
  duplicate elimination, severity ordering and dotted component IDs.
- Paused playback rebuilt cable tubes during camera movement. Skip unchanged
  timestamps, but reapply poses when newly shown groups/cables are registered.
- Demand rendering already existed. Keep it, and disable preserveDrawingBuffer.

Picking, part identity, selection, highlighting, X-ray, explode, layers, joint
transforms and playback retain their individual scene objects. Display geometry
never replaces any fit, wiring, simulation or release inputs.

## Limits and reproduction

The near-60 FPS interaction goal is met in this controlled sample, and no
interaction task exceeded 100 ms (selection max 91 ms). These are observations,
not a bound under machine load. A separate run overlapping source reload recorded
180 ms and was excluded from this controlled table. Initial load still has tasks
over 100 ms. Warm first geometry under two seconds is **not met**. All-parts
loading is also not materially faster, and Wiring became slower to finish loading
with bounded worker decoding. Concurrent browser model/CAD requests took about
5–6 seconds even though isolated warm HTTP calls were about 0.27 seconds.

Cold native-to-world projection preparation exceeded 90 seconds in the initial
probe. The existing projection metadata cache is process-local; pull-time prewarm
alone would not survive a new server process. Persistent validated conversion
caching and pull-time preparation remain unimplemented. Exact pulled files are
untouched; qualification remains pending where the project already reports it.
The native OBJ inspection-only viewer was smoke tested, not performance tuned.

With an isolated API and Vite running, from apps/web:

```sh
R3_MEASURE_PROJECT=rc-cool-car-23 node scripts/measure-viewer.mjs sample
```

Override R3_MEASURE_API, R3_MEASURE_URL, R3_MEASURE_OUTPUT and R3_MEASURE_ROUTES as
needed. R3_MEASURE_PROFILE=1 records Chrome load CPU profiles. The opt-in bridge
uses localStorage `r3.viewer.measure=1`; it releases scene references on unmount.
Raw measurements and CPU profiles for this run are in /tmp/r3-viewer-perf.

Validation: 9 affected Python tests; strict typecheck; 81 Vitest tests; production
build; 5 Playwright authority/native/worker-picking tests. The browser test checks
that the worker BVH exists, clicks the projected mesh to verify identity, and
exercises X-ray, explode and Fit. No release or numeric qualification was run.

# Web page loading investigation

Measured November 2, 2026 on a busy development machine (load averages 9–16),
with live autonomous runs left untouched. Before measurements used read-only
HTTP requests to the existing Vite/API servers. After measurements used a
separate API on 8331 and Vite on 5181 against a full directory copy of the
large project and an unbuilt bench fixture. No deployment build version change.

## Findings

The large project has 528 included YAML files, 210 MB of pulled inputs,
91 MB of viewer provenance JSON (including unnecessary body arrays for identity
checks), and 24,589 historical viewer-cache files. A profiled detail request
parsed the description six times and validated pulled geometry three times:
12.94 seconds with profiling enabled. Concurrent live requests took longer.
The spec response was 20.83 MB; most of its source-location map is unused by
the web application. React Query caching and three.js route splitting already
existed. Stages already run in worker processes and blocking API views already
run in the thread pool. Cold doctor probing can take 47 seconds but does not
gate the Overview's main content.

## Changes

- Cache parsed descriptions after comparing every included path and actual
  source bytes. Return independent mutable copies and reject changed aliases,
  escaping paths, deleted files and changed include graphs. Compute fingerprints
  without cloning the parsed description. Preserve concurrency SHA semantics.
- Cache only small viewer-proof identity fields by verified checksum; continue
  hashing native inputs on integrity checks. Avoid a duplicate pull validation
  when projecting an already verified freshly loaded native model.
- Encode large model, CAD and diagnostic JSON responses in the worker thread,
  avoiding FastAPI's recursive dictionary walk on the event loop.
- Reuse the loaded project in status/diagnostics. Keep a separate geometry-free
  project-list cache; list rows still calculate a current content fingerprint.
- Cache model/CAD HTTP display projections using current content fingerprints,
  resolved paths and inode/ctime/mtime/size dependencies. Track only the active
  viewer snapshot. Release checks and stage execution bypass this display cache.
- Request spec documents without the unused source-location map. Keep all source
  texts, editable data and revision checks. Full API documents remain the default.
- Render Diagnostics from its own query, with 3D context loaded on request or
  finding selection. Preserve highlighting, markers and the complete 3D viewer.

## HTTP timings

Seconds, TTFB approximately equal to total time on loopback. Before is the live
Vite proxy under concurrent page-query load; after is sequential warm requests
to the isolated API. These are API timings, not full geometry-render timings,
and the differing concurrency makes this directional rather than a controlled
speedup ratio.

| Page / critical API | Before | After warm |
| --- | ---: | ---: |
| Projects | 3.501 | 0.144–0.184 |
| Overview / project detail | 52.289 | 0.853–0.897 |
| 3D / model | 49.895 | 0.220–0.231 |
| 3D / CAD | 60.316 | 0.254–0.262 |
| Diagnostics | 21.899 | 0.098–0.100 |
| Agent / spec | 8.068 | 0.147–0.159 |
| Agent / history | 0.350 | 0.004–0.009 |

Spec bytes decreased from 20,826,440 to 3,922,671 (81%). Empty/unbuilt project
detail took 0.050 seconds initially and 0.120–0.126 seconds warm. A browser
navigation probe observed shell changes in approximately 33–39 ms, and repeat
visits reuse React Query data. No claim that mesh downloads/rendering finish
within that interval.

Cold limits remain explicit: the isolated API's first list request took
0.886 seconds; first large-project detail 2.606 seconds; first model projection
53.063 seconds and first CAD request 3.296 seconds. The subsecond target is met
for warm APIs, not for first-time conversion/rendering of hundreds of native
meshes. A changed CAD input correctly invalidates the display cache and can incur
that preparation cost again. No release, freshness or qualification checks are
reported as passing merely because a display was cached.

Apply by merging the change, restarting each API worker to load the Python code,
and refreshing Vite (or rebuilding the web bundle for production). The running
user servers were not restarted during this investigation.

The browser probe during concurrent validation also recorded 1.18 seconds for
Overview, 1.85 seconds for warm 3D API completion and 1.18 seconds for a list
refetch. Thus the subsecond goal is not a proven bound under all loaded-machine
conditions. The final serialization change moves large response encoding off
the event loop; the table above predates that last change.

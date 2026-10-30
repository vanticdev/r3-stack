# Wiring performance validation

Status: work in progress, stopped at the user's instruction. Do not merge. No further optimization or long validation was started after that instruction. Cold-stage and full-suite wall-time targets remain unmet under measured load; quiet-machine timing is unvalidated.

The implementation preserves optimizer settings, grids, physical limits and final audits. It adds exact geometry broad phases, an indexed triangle-sphere search, batched connector/occupancy queries, cached voxel fields, and content-addressed numeric solver states. Solver cache hits rebuild the complete result and current geometric witnesses. Cache reuse after remote geometry changes requires every voxel corner read throughout the original solve, including gradients, to match byte for byte. Set `R3_WIRING_CACHE=0` to disable caching.

Tests run on four pytest-xdist workers. Tests consuming the expensive session wiring fixture share one load group. Pytest is pinned below 9 because xdist 3.x grouping uses pytest 8's string node-ID API. No tests were removed or assertions weakened. Production routing remains sequential; independent spawned processes are tested for identical results and safe concurrent cache writes.

## Measurement conditions

Measurements continued while the joint-physics lane was running. No foreign process was stopped and no quiet CPU window was requested. This machine has 16 logical CPUs. Process CPU means user plus system time including descendant processes, rather than CPU percentage or wall time divided by worker count. `measure.py` uses `wait4` cumulative resource usage. The original canonical run was already running when CPU instrumentation was requested; its cumulative lifetime CPU was observed through macOS `proc_pid_rusage`, including child CPU. Its load observations cover the latter part of the run; its CPU counters cover the whole process lifetime. Raw profiles, logs, source snapshots and generated caches are ignored by Git.

Both canonical stages are profiled with cProfile, including the final outputs. Timing the full command also includes Python startup/profile export. Canonical and source-only pytest fixtures use different geometry; they are compared independently and never substituted for one another.

## Original canonical profile

The original source is `143518cb50dcf431d37e87c1935915ce2310982c`. Completed nested cumulative times in seconds:

| Cost | Seconds |
|---|---:|
| Constrained cable planning | 5067.38 |
| Native SLSQP (inside planning) | 4706.95 |
| Collision queries (inside other phases) | 404.42 |
| Support placement | 325.74 |
| Voxel routing, including fields and excluding planning | 114.62 |
| Voxel fields (inside voxel routing) | 22.92 |
| Slack simulation | 99.63 |
| PNG rendering and other output | 46.18 |

These costs overlap and must not be summed. The original thread-heavy cProfile lost the still-active renderer/parent frames; rendering/output is the independently measured stage-wall remainder after routing/checking, slack simulation and manufacturing. The raw profile's malformed aggregate total is not a runtime measurement. The complete stage did render its PNGs. Analytic optimizer Jacobians already existed, and rendering was already at the end; neither needed a behavior change.

## Equality and tests

The source-only full-suite fixture proof is in `wiring-speed-fixture-proof.json`: 50 wire and harness paths, zero maximum coordinate difference, byte-identical entire `routes.json`, all 120 diagnostics unchanged, and identical validity and release status. The complete result comparison ignores only start time, duration and implementation Git revision. Input hashes match. Numerical comparison uses relative tolerance 1e-7 and absolute tolerance 1e-9 m.

The final full suite passed **888 tests, with 3 skipped and 8 warnings in 2235.56 seconds**. This includes 21 added performance/correctness tests. Skips are the existing Docker-unavailable firmware checks and missing cached Pi 5 dependency image. Warnings are existing zero-volume connector-mesh centroid warnings. The 20-minute wall-time target was not met under load.

The canonical cold and cached rerun proofs are in `wiring-speed-proof.json` and `wiring-speed-warm-proof.json`. Both match the original byte for byte for all 50 paths and the entire routes file, with zero coordinate difference and all 126 diagnostics unchanged (43 errors, 69 warnings, 14 information findings). Release remains **unreleased** for the same design failures. All 271 input-file content hashes match. Neither an unreleased design nor an unchanged design error is treated as a stage crash or concealed by the cache.

The baseline suite passed **867 tests, with the same 3 skips and 8 warnings in 4769.72 seconds**. Both suites ran from cold temporary fixture roots and used the same Python environment. They started within one second of one another under the shared machine load. The optimized suite creates the expensive fixture once, on one worker; the baseline also creates it once. Final source-only fixture setup fell from 2528.07 to 1545.84 seconds. Exact baseline/optimized `--durations=40` results are preserved in `wiring-speed-test-durations.json`.

## Complete command measurements

CPU values include descendants; wall values include command startup and profile export. Load is the observed one-minute load average, shown as mean [minimum–maximum]. Process counts distinguish the benchmark's peak process-tree count from the total machine process-count range. Total machine counts include idle processes and are not counts of runnable CPUs. No CPU isolation was imposed.

| Run | Wall s | User s | System s | CPU total s | Load mean [range] | Tree peak / machine range |
|---|---:|---:|---:|---:|---|---|
| Original canonical | 5818.89 | 4048.23 | 18.28 | 4066.51 | 147.6 [59.9–272.4] | 2 / 937–1012 |
| Optimized canonical, empty cache | 5077.44 | 2819.33 | 53.56 | 2872.89 | 139.0 [22.8–272.4] | 2 / 930–1010 |
| Optimized canonical, populated cache | 259.74 | 348.44 | 65.90 | 414.34 | 18.3 [12.4–24.6] | 2 / 927–978 |
| Original full suite | 4771.87 | 3321.38 | 152.76 | 3474.14 | 133.0 [12.4–272.4] | 4 / 924–1010 |
| Optimized full suite | 2235.84 | 3208.99 | 118.05 | 3327.04 | 157.5 [109.7–272.4] | 13 / 946–1009 |

The cold canonical CPU reduction is **29.35% (1.42×)**; wall reduction is **12.74% (1.15×)**. The cached full-stage CPU reduction is **89.81% (9.81×)**; wall reduction is **95.54% (22.40×)**. Cache hits eliminate SLSQP calls while retaining the complete result audit, witness generation, slack simulation and PNG rendering. The second run had lower observed load; its wall improvement must not be attributed entirely to the code. The cold CPU comparison also remains a measurement under contention, not a forecast of quiet-machine wall time.

The full-suite wall reduction is **53.15% (2.13×)** and CPU reduction **4.23% (1.04×)** despite adding 21 tests. Independent expensive tests run concurrently, but the shared wiring fixture remains on one worker. Their individual wall times can increase under contention; durations are not independent CPU measurements.

**Targets under observed load:** the unchanged-input cached canonical stage meets 10 minutes (259.74 seconds); the empty-cache canonical stage does not (5077.44 seconds). The full suite does not meet 20 minutes (2235.84 seconds). CPU measurements do not establish either the cold-stage or full-suite quiet wall-time target. Further quiet timing is needed to assess those targets; this handoff does not claim they were achieved.

Cold profile collision-query cumulative time fell from 404.42 to 163.92 seconds; support placement fell from 325.74 to 132.09 seconds. Calls fell from 524.3 million to 215.6 million. Native SLSQP remains the cold bottleneck (4439.51 seconds under load). Unmodified slack simulation also ran faster in the cold run, demonstrating why phase wall times alone cannot isolate code gains. The warm profile reports zero native SLSQP calls. Its route/check parent frame is incomplete, so its rendering/output phase duration is marked unavailable rather than inferred from an inconsistent remainder. Independent `wait4` CPU and wall measurements are unaffected by cProfile's incomplete phase frames.

## Reproduction

Use the task's shared venv and package `PYTHONPATH`. Added development packages are pytest-xdist 3.8.0 and execnet 2.1.2; pytest 8.4.2 satisfies the committed pytest 8 compatibility constraint. No new production dependency was added.

```
# Source-only full suite, including the 40 slowest durations:
python -m pytest -q -p no:randomly tests --durations=40 -rs

# Complete canonical wiring stage with process-tree CPU/load measurement:
R3_SPEED_LABEL=recheck python .r3-runs/wiring-speed/measure.py --label recheck -- python .r3-runs/wiring-speed/profile_run.py

# The profiler executes this real stage call:
# run_stage(Project.load('examples/robots/rc-car'), 'wiring')
```

`R3_SPEED_PROJECT` selects an isolated project snapshot; the default is the canonical project above. Start with a fresh project's `build/wiring/.cache` for a cold measurement, then repeat against the same project for a warm measurement. Changing relevant solver inputs invalidates the matching plan; changing unread remote voxels permits deterministic reuse after byte-exact footprint validation. Disabling caching also disables voxel-field reuse and intra-run plan reuse, so it is not interchangeable with an initially empty cache.

Use `compare.py` to compare completed before/after snapshot folders, including their input hashes, complete routes, full diagnostics, validity and release status. The committed proof JSON files contain the full per-code diagnostic counts, load summaries, process counts, CPU methods and validation exit status. Large raw profiles and generated build artifacts are intentionally not committed.

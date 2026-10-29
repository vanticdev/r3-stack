# Requests from r3/mujoco-cables

Measured on `full_throttle` (10 s, RC car, this machine) after the merge of
r3/integration (ngspice, Sionna RT): loop 2.0 ms per 1 ms step. Per step:
Sionna RT radio 0.82 ms, ngspice electrical 0.72 ms, MuJoCo vehicle with cables
about 0.86 ms of worker time, of which about 0.3 ms stays on the critical path
after overlap.

1. **Radio (Sionna RT owner).** `SionnaPropagation._paths_at` traces about 40 ms per
   0.25 m x 15 deg cell and holds the GIL (Dr.Jit graph building). At 8 m/s that is
   about 1.15 s of tracing per simulated second, which caps the twin below real time
   whatever else it does. Each cell's trace is a deterministic function of its key,
   so a tracer *process* could prefetch cells ahead of the car (position + velocity
   lookahead) without changing results. Threads do not help (measured: no overlap).
2. **Electrical (ngspice owner).** 0.72 ms per step. The kernel overlaps
   `ElectricalSystem.step` of step k+1 with MuJoCo's `mj_step2` of step k. Exposing
   `solve_step` / `finish_step` (solve, then state update and outputs) would also
   let the bookkeeping half overlap `mj_step1`; the kernel uses them when present.
3. **Vehicle (sim owner).** The global `<option impratio="10">` (for tires) also
   applies to the cable friction contacts. Newton then needs about 8.6 iterations
   instead of 3.9 (constraint 501 vs 318 us per step). With impratio 1 the vehicle
   trajectory in `full_throttle` is unchanged (6.497 vs 6.498 m/s at 2.5 s). This is
   a tire-model decision, so it is left at 10.
4. **Design (wiring/router).** Real findings against the RC car: `W_MOT_B` is routed
   into the right-rear tire (router static clearance -2.8 mm, MuJoCo -0.8 mm at rest,
   frictionless rubbing contact while driving), and the encoder and GPS leads rest on
   the gearbox. Re-route, or add ties, before building.

# Contract / catalog requests from the electrical area

All are read today with defaults (recorded as `estimated_parameters` in
`build/electrical/operating_points.json`); none block the stage.

## r3_catalog.schema.ConnectorType
- Add optional `current_rating_burst: Quantity | None` (+ `burst_duration`).
  The stage reads it via `getattr`; without it, burst = 2 x continuous (the
  XT60 30/60 A and XT30 15/30 A datasheet ratio) is assumed for every family,
  which is a guess for bullets, JST and 2.54 mm headers.
  Values: xt60-* 60 A, xt30-* 30 A (datasheet).

## Catalog behavior params (additive)
- `esc-brushed-60a.behavior.params`: `bec_dropout` (V, now 1.0 estimated),
  `bec_current_limit` (A, now 1.5 x 2 A estimated), `burst_duration` (s for
  the 360 A burst, now 1.0 estimated). `heat_capacity_j_per_k` in `thermal`
  (now mass x 900 J/kgK).
- `motor-540-brushed.thermal.heat_capacity_j_per_k` (now mass x 500 J/kgK).
- `r3-carrier-hat.behavior.params.regulator`: `uvlo_falling` / `uvlo_rising`.
  Without them UVLO falling = `v_in_min` (6.0 V), which makes the HAT buck shut
  off at the 2S pack's 3.0 V/cell minimum under load (see `brownout_risk`).
  If the real part's UVLO is lower, record it; otherwise the design needs a
  lower-UVLO / buck-boost regulator.
- `r3-carrier-hat.behavior.params.ina226`: `shunt_location` (the twin places
  the 2 mOhm shunt in series with the buck input, i.e. it measures HAT current,
  not motor current).
- `fuse-atc-10a.behavior.params.min_melt_ratio` (now 1.25 estimated) or a
  time-current table.
- `lipo-2s-5000-25c.behavior.params.rc_pair`: state whether it is per cell
  (the twin assumes per cell, like `r_internal_per_cell`).

## Requirements
- `requirements.extra.duty_cycle: {cruise: 0.85, full_throttle: 0.05, idle: 0.10}`
  for the runtime estimate (that default is used now). A first-class
  `Requirements.duty_cycle` field would be cleaner.

## ngspice (r3/ngspice branch)
- **Worker image: ngspice shared library.** `r3_sim.electrical` now runs ngspice through
   `libngspice` (ctypes, official shared API). The R3 worker Docker image needs it installed:
   Debian bookworm `apt-get install libngspice0` (provides `/usr/lib/<arch>-linux-gnu/libngspice.so.0`,
   which is probed automatically; any other path via `R3_NGSPICE_LIBRARY`). Developed and tested
   against ngspice 47 (Homebrew `libngspice`). Without the library `ElectricalSystem(...)` raises
   `FileNotFoundError` and the kernel selects its labeled fallback.
- **Sim requests 1-2 are implemented** (`initial={"battery_soc": ...}` accepted by the
   constructor; `pulse_us <= 0` is signal loss for the ESC and the servo). Item 3: servo outputs
   now also carry `voltage`.

## From lane C (finish): RTK rover + correction radio on the 5 V rail
- `gps` is now `gnss-rtk-zed-f9p` (behavior `gps.rtk`, 0.12 A at 5 V, estimate; module 68 mA plus LNA/compass/regulator),
  replacing the M10 (0.03 A). The SiK 915 MHz correction radio `rtk_radio` (behavior `telemetry.sik`, `current_active`
  0.1 A, the datasheet TX worst case; 25 mA receive) is powered from the rover's 5 V pin through `rtk_radio_lead`.
  Total added load on the HAT 5 V rail: about +0.19 A. Both are registered as constant loads in
  `r3_sim/electrical/models/__init__.py` (two lines). Please re-verify the 5 V budget and the gps_lead
  (28 AWG) drop.
- **Fuse opening (lane C made a minimal blocking change in `electrical/models/fuse.py`):** the ideal `sw` switch
  jumped from 7 mOhm to 1 GOhm in one step while ~100 A flowed into the harness inductance. ngspice then stalled
  ("Timestep too small ... trouble with node v.xpi.vr0#branch" at the fuse-melt instant) once the RTK loads shifted
  the timing slightly, in `test_fuse_blows_on_short_and_pi_browns_out` on a fresh copy. The element is now a
  conductance blend G_cold (1 - x) + G_open x, with x the melted latch through an RC of `clearing_time` (default 50 us,
  an estimate of a blade fuse's melt/arc interval; overridable as a fuse behavior param). Please review; an arc-voltage
  model would be the next refinement (and item 24, the HAT bulk capacitance, still stands).
- **Test-order leak (pre-existing; it reproduces on 70bef0e):** running `tests/test_electrical_system.py` before
  `tests/test_electrical_stage.py` in one process makes the stage's `motor_stall@full` battery current come out
  ~1 A (should be > 10 x cruise). Some state persists across ElectricalSystem instances (ngspice library state, or a
  mutated cached catalog object). Alphabetical order hides it.

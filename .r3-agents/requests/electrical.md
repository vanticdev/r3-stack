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

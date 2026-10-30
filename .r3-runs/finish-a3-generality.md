# A3 code generality audit

Reviewed `git diff r3/integration -- packages services integrations apps` after
the 2026-10-30 generality directive. Layout dimensions, component choices and
join frames remain in robot.yaml and component catalog data.

Corrections made during the audit:

- Moved the connector proxy specifications out of Python into
  `r3_catalog/data/connector_proxies.yaml`, including the corrected XT90H contact
  pitch and envelope. The generic loader retains the 13 existing specifications
  exactly; it does not select by robot or harness ID.
- Moved the requested-support match distance (4 mm) and wire-capture allowance
  (2 mm) into the support hardware catalog with estimated provenance. Both
  checks retain their existing values. Required ties still need real hardware,
  installation access, retention and contact with the routed wires.
- Reused the existing distance-field cutoff constant instead of repeating its
  length in the new connector and required-support reservation paths.

The remaining added algorithms use declared connector frames, per-wire gauges,
catalog bend rules, authored waypoints, interface hole patterns, actual grid
bounds and support geometry. They contain no rc-car harness/component IDs or
vehicle-specific branches. Solver stopping constants and dimensionless angular
guards are numerical criteria, not design dimensions. Electrical smoothing is
generic to the existing regulator and servo behavior models.

Robot-independent regression fixtures cover connector clocking, branch
waypoints, folded paths, bounded optimization, real support placement, interface
pattern propagation and flexible cable ends. The new proxy-catalog test uses an
unrelated connector with asymmetric contacts. The audit's targeted run passed
10 tests in 1.57 s; all 13 migrated proxy specifications compared equal before
and after migration. Full-suite and integrated-stage evidence is reported
separately; these tests do not establish manufacturing release.

Follow-up after the measured-part merge: asymmetric mount patterns now accept
an explicit dimensionless 2 x 2 coordinate transform; the fixture test uses an
unrelated sensor. Source-only custom purchased parts now use Open CASCADE to
read unchanged STEP solids and analytic faces in both offline CAD and assembly
checks. A synthetic supplied plate proves the real bore, thickness and seat
face survive; source-only purchased parts cannot be silently mirrored. No robot,
harness, connector or component IDs occur in these new code paths. The models
extra now declares cadquery-ocp (already installed in the shared environment).

# A3 final code validation

Code at 7ea925d; later commits through 0226dba change reports only.

- `python -m pytest -q -p no:randomly tests`: **870 passed, 8 warnings in 2102.04 s (35:02)**. The warnings are the existing zero-volume centroid division warnings in eight connector mesh-mating fixtures. The full suite has zero failures, but it does **not** meet the finish brief's under-20-minute runtime target.
- The two unchanged `test_cad_phaseb.py` assembly-cleanliness tests pass in that run; their earlier targeted run was 2 passed in 17.20 s.
- `npm run typecheck` in `apps/web`: passed.
- `npm test` in `apps/web`: **8 files, 69 tests passed** (902 ms).
- Generic fabricated-solid fallback / router / reserved-destination regression run: **11 passed in 10.97 s**.
- A fresh project with no CAD export now completes initial topology routing without `wire_route_failed`; it still labels catalog geometry as estimated. That probe is not a release certificate.

The earlier full run had 9 failures and 858 passes. Subsequent fixes address all nine: preserve primitive fallback purchased models, retain explicit unverifiable-connector warnings in synthetic fixtures, verify the deliberate Pi socket seating gap, allow the straight pin fan-out before bundle bends, and use real authored openings in fabricated supports instead of filled envelope boxes. No manufacturing check or tolerance was reduced to satisfy tests.

The full-suite wiring fixture uses fresh catalog geometry. The separate canonical project audit uses the recomposed CAD and is reported in the A3 handoff; passing tests do not imply all example harnesses are valid.

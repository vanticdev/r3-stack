# Review 01: GPT-6 Astra (medium). Top 9 summary
1. CRITICAL: "touching" is not a verified mechanical connection. Model bearings, fasteners, shaft engagement, horn and rod ends; transmissions must resolve to a physical load path; check tolerance stacks and full steering travel.
2. CRITICAL: no release gate. Use verified/estimated/failed/not_run per requirement; a manifest links each requirement to versioned checks; missing evidence blocks a release.
3. CRITICAL: servo stall 2.5 A exceeds the 2 A BEC.
4. CRITICAL: the radio override and braking depend on the Pi. Timeouts are inconsistent (0.25/0.5/1.0 s); no 8 m/s stop test.
5. MAJOR: the CAD cache ignores joints and validator version. No microversion pin; name-keyed parts; _fs_id collisions.
6. MAJOR: provenance. Mass-matched density proves only consistency; mislabeled datasheet/measured entries; no uncertainty bounds.
7. MAJOR: graph strictness. Nonexistent pins create nodes, partial mates are accepted, and spec.py misses transmission refs, unique joint ids and mount cycles.
8. MAJOR: no antenna/mast/coax or harness tie hardware exists in the build.
9. MAJOR: scenario coverage. No 900 s endurance test, a single seed/temperature/friction, and no timestep-convergence checks.

# Review 01: GPT-6.1 Sol (medium). Top 10, verbatim summary

1. CRITICAL: no real motor→wheel torque path. The gearbox is a box, there is no pinion/spur/diff/hex/bearing interface, and the sim can propel an undrivable assembly.
2. CRITICAL: the Ackermann steering is declared, not derived. There is no horn, ball links or kingpins, and the 0.5 ratio does not come from geometry.
3. CRITICAL: contact is treated as attachment. There are no fasteners/straps/inserts, and no load-path check for braking, cornering or bumps.
4. CRITICAL: the failsafe depends on the Pi; a frozen Pi leaves PWM active. At 8 m/s, 0.25 s detection + μ=0.9 braking ≈ 5.6 m, more than the 3 m requirement.
5. MAJOR: the BEC is rated 2 A but the servo stall current is 2.5 A, an overload.
6. MAJOR: there is no acceptance gate. Estimate fallbacks can pass, no validator versioning, and no 900 s runtime test.
7. MAJOR: no swept-volume solid checks across steering travel, and no tolerances.
8. MAJOR: the encoder has no magnets/target geometry, so its 4 ppr is unsubstantiated.
9. MAJOR: mass-matched density proves consistency, not accuracy; inertia should come from rotating parts, with uncertainty bounds.
10. MAJOR: the CAD verification cache key ignores joints and validator version, and the Onshape microversion is not pinned.

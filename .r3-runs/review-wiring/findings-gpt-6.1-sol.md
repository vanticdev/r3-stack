# Wiring review: GPT-6.1 Sol (medium). Top 10, summary
1. CRITICAL: contact-level termination. The router strips `B.2` to `B`, so conductors share paths. Add numbered contact frames, wire-entry axes, insertion depth and connector roll; fan out per conductor.
2. CRITICAL: connectors are envelopes and cones. Use real MPNs plus vendor STEP/KiCad 3D models, mating transforms and insertion/removal envelopes; keep collision geometry separate.
3. CRITICAL: the Pi header is one 50.4×4.7×6 mm box with 22 mapped pins. Model all 40 contacts (physical, BCM and net) and render pin 1, socket engagement and occupancy conflicts.
4. CRITICAL: the clearance results are not credible (esc_motor −236.7 mm to knuckle_fl). Add nearest-point witnesses and collision overlays, validate units, transforms, closure and winding against analytic fixtures and MuJoCo exact distances.
5. CRITICAL: 57 bend violations and 32 intersections, yet the cut list is still written. Use curvature-constrained routing, optimize exits and branches jointly, recheck the swept solid, add per-wire validity, and gate manufacturing outputs.
6. MAJOR: ties are points, not hardware. Model supports (mount part, footprint, fasteners, bundle capacity, retention) and strain relief; drive the sim constraints from them.
7. MAJOR: cut length and slack use flat 15–30 mm values. Use per-span slack, service loops, strip and insertion dimensions and fan-out lengths, and calibrate the cable model.
8. MAJOR: harnesses ignore each other and ampacity is free-air. Add shared occupancy, segregation and edge protection, and feed bundled/enclosed derating into the electrical model.
9. MAJOR: no manufacturing package. Generate WireViz diagrams and BOM, terminals, tooling, connector faces, labels, dimensions, assembly sequence and continuity tests.
10. MAJOR: the UI edits only via/ties. Make it a contact-aware editor (repin, rotate, splices, supports, slack, undo) and verify against the live API.

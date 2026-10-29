# Design fixes queued for the next integrated attempt

From review 01 (GPT-6.1 Sol, GPT-6 Astra) and the agent findings:

1. Servo power. The BEC is rated 2 A; the servo stalls at 2.4–2.5 A, and the ESC then reaches 73 °C.
   Fix: put a dedicated servo regulator on the HAT (VBAT→6.0 V, ≥5 A, synchronous buck with
   current limit), and stop routing the ESC BEC to the servo. [electrical, pcb, robot.yaml]
2. XT60 at 60.6 A during motor stall, against a 60 A burst rating. Fix: limit motor current to 40 A in
   the ESC/firmware (stall protection), or derate the burst rating. [electrical, firmware]
3. Pi brownout at motor stall with an empty battery (HAT input 5.75 V, below the 6.0 V UVLO).
   Fix: a buck with a lower UVLO (≈4.5 V) plus hold-up bulk capacitance, and a firmware
   current limit. [pcb, electrical]
4. Motor return current through the ESC signal-lead ground (3.3 A against a 3 A pin rating).
   Fix: with the separate servo regulator, drop the BEC+ pin and keep a signal-only ground
   reference; avoid the ground loop. [robot.yaml harness]
5. An 18 AWG segment upstream of the fuse is unprotected. Fix: put the fuse at the splice
   (integral to the Y harness). [robot.yaml]
6. A failsafe independent of the Pi: a hardware watchdog/PWM arbiter on the HAT. It passes ESC
   PWM only while the Pi heartbeat GPIO toggles and CRSF is fresh; otherwise it forces the
   ESC to neutral/brake. [pcb, sim, firmware]
7. Stopping distance: at 8 m/s, detection plus braking ≈ 5.6 m against the 3 m requirement. Decide on a
   speed envelope: 3 m at the autonomous cruise speed (≤ 5 m/s), and a separate
   requirement for manual top speed. [requirements]
8. Timeouts are inconsistent (Pi 0.25 s, ESC 0.5 s, receiver 1.0 s). Document the chain and set the
   receiver failsafe to "no pulses". [catalog, firmware]
9. Release gate. Per requirement: verified / estimated / failed / not_run; a manifest with
   versioned checks; missing evidence blocks "fabrication ready". [orchestrator]
10. Scenario coverage: a 900 s endurance run, a seed sweep, friction and temperature sweeps, and
    a timestep-convergence check. [sim]
11. Graph strictness: nonexistent pins, partial mates, occupancy, mount cycles. [r3_model]

## From the fully coupled twin (firmware-traction agent)
12. Autonomous cross-track fails because of GPS bias: the estimate is up to 1.9 m off with a 1.5 m CEP receiver, while
    the controller tracks its estimate to 0.30 m rms. Fix: an RTK GNSS rover (u-blox ZED-F9P class) with RTCM
    corrections over the ELRS/telemetry link or a separate 915 MHz link from a base station. Model it in the twin
    (cm-level RTK fixed, float/fix transitions, and a correction-link dependency).
13. The encoder reads only the left rear wheel, so the open differential hides spin on the right. Fix: sense the
    differential carrier (the spur output), or put encoders on both wheels.
14. The sim evaluator starts measuring cross-track at the capture circle of waypoint 1, an artefact; it should
    start at the first path segment. Map full stick to 2012 µs.
15. `full_throttle` passes at 8.045 m/s, which is only just enough: the motor's no-load limit is about 8.05 m/s. Margin
    is close to zero. Consider a 9.0:1 final drive (pinion change) and re-verify current and thermals.

## Sim performance (MuJoCo-cables agent)
16. The coupled sim runs at 0.43× real time. Per 1 ms step: Sionna ray tracing 0.82 ms (~40 ms per trace while holding the
    GIL, re-traced every ~31 ms at 8 m/s) and ngspice 0.72 ms. Fix: precompute a Sionna radio map / path cache over the
    scenario's reachable area in parallel worker processes before the run, then do lookups during the run; run ngspice in
    a separate process with a shared-memory exchange if the GIL is contended. impratio=10 (tires) doubles the cable
    contact iterations; evaluate per-geom solref/solimp instead.
17. Wiring design: W_MOT_B is routed into the right-rear tire (−0.8 mm in MuJoCo), and the encoder/GPS leads rest on
    the gearbox. Reroute when the layout is converted to the assembly/join model (phase B).

## After phase B merges
18. Add `source:` slots to robot.yaml for pi (family raspberry-pi-5, ram >= 4 GB), receiver (crsf, 2.4 GHz,
    4.5–5.5 V), and wheels (group: front/rear, diameter 100–115 mm, width 40–50 mm, hub: 6 mm bore). Run
    `r3 source --all`, pin the choices, and import the Pi 5 STEP into the parts library.
19. Tests must generate their own build artifacts (or use fixtures) instead of reading `examples/robots/*/build`.
    Stale routes.json caused false sim failures twice.
20. The official Pi 5 STEP has a 1.28 mm PCB, but the catalog assumes 1.6 mm. That leaves a 0.22 mm gap between the header
    and socket faces (the 4.68 mm pin engagement is OK). Lower the Pi standoffs and the HAT by about 0.29 mm, per
    `.r3-agents/requests/connectors.md`. [mechanical, phase B follow-up]

## From the PCB rollout (pcb-rollout agent)
21. `pi_hang` FAILS: the arbiter sends a 1500 µs neutral 110 ms after the heartbeat stops, but the ESC coasts at neutral, so the car
    travels 5.44 m against the 3 m limit. Fix: the ATtiny412 arbiter outputs an active-brake pulse sequence first (QuicRun: a
    reverse pulse while moving forward = brake, held for N ms), then neutral. Update `r3_kicad.arbiter`, the twin model and the
    scenario. Alternatively enable the ESC drag brake (QuicRun 1060 supports drag brake %; model it in esc.brushed).
22. Ground loop: about 3.36 A of motor return current flows through the 22 AWG ESC signal-lead ground (header rated 3 A), because
    the HAT ground and the ESC ground are paralleled by that lead. Fix: isolate the ESC PWM on the HAT (digital isolator, e.g.
    ISO7710, with the ESC-side ground not connected to HAT GND) or use a single-point/star ground. Re-verify in ngspice.
23. Servo supply sags to 4.80 V at minimum charge (the TPS56528 is in dropout below about 7.8 V in); 4.52 V at motor stall with
    minimum charge. Fix: a buck-boost servo regulator (e.g. TPS63070 class, ≥3 A) or a servo rated 4.8–8.4 V straight off
    VBAT. Re-run servo stall at minimum charge.
24. The HAT input's 220 µF bulk cap is not modelled (an ngspice step-control stall when the input is disconnected). Model it with an
    ESR and an initial condition so brownout and hold-up are realistic.

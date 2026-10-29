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

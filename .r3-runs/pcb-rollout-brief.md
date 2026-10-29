# PCB rollout: apply the headless KiCad HAT across every domain

The board on `r3/integration` (`integrations/kicad`, `examples/robots/rc-car/pcb/carrier-hat`) implements:
- a low-UVLO 5.1 V buck with hold-up;
- a dedicated 6.0 V servo regulator;
- the ESC BEC left unconnected;
- an ATtiny412 failsafe arbiter on a GPIO27 heartbeat;
- INA226 and MPU-6050.

The rest of R3 still models the old HAT. Apply it everywhere, consistently:

1. **Catalog and spec.** Apply `.r3-agents/requests/pcb.md` sections 1–4:
   - the full `r3-carrier-hat` entry;
   - `InternalLink.kind: failsafe_arbiter`;
   - the `heartbeat` binding on `pi.J8.13` → `hat.HEARTBEAT`;
   - `hat.J_SERVO.2` = `SERVO_6V`;
   - remove the ESC BEC wire from the `esc_signal` harness, so the ESC→HAT 3-pin lead keeps signal and ground only (this also fixes the 3.3 A signal-ground loop the electrical twin found);
   - connector positions measured from the board;
   - the HAT pose at x = 100 mm.
   The `pcb` stage must report no `board_catalog_mismatch`.
2. **Graph.** `failsafe_arbiter` links count for signal continuity, like series resistors, and the model
   stage understands them.
3. **Electrical twin (ngspice, `r3_sim/electrical`).**
   - Model the two regulators from the catalog params: UVLO, current limit, efficiency, the dropout of the
     servo rail below about 7.8 V input, and the hold-up capacitance.
   - The servo is fed from SERVO_6V and no longer from the BEC.
   - Re-run the static stage. `bec_overload` should disappear; report the new servo-stall and
     motor-stall-at-minimum-charge results honestly.
   - Check the TVS clamp (SMAJ16A, 26 V clamp) against the regulators' input absolute maximum (20 V): a
     `tvs_clamp_exceeds_abs_max` diagnostic.
4. **Board fixes, regenerated headlessly with `python -m r3_kicad.flow`.**
   - Choose a TVS whose clamp is below 20 V at the expected surge current for a 2S (8.4 V max) pack, for
     example SMAJ10A (17.0 V clamp); justify the choice.
   - Fix the nine IPC-2152 neck warnings with wider copper, pours or stitching, and keep DRC/ERC at 0.
   - Keep the no-window guarantee.
5. **Failsafe arbiter in the twin.** Model the ATtiny412 behaviour exactly as specified (`r3_kicad.arbiter` exists):
   - the heartbeat is GPIO27 edges;
   - the timeout is 100 ms;
   - it outputs 1500 µs neutral and re-arms after 3 edges.
   The HAL server routes the Pi's ESC PWM through it.
6. **Firmware.** Toggle GPIO27 every control cycle (100 Hz) from the main loop, so a hung loop stops the
   heartbeat. Add the binding to the firmware config.
7. **New scenario `pi_hang`.**
   - Driving autonomously at cruise speed, the firmware process freezes. Use the kernel's fault injection
     or SIGSTOP; the PWM peripheral keeps outputting the last throttle.
   - Expected: the arbiter forces neutral within 100 ms of the last heartbeat, and the car stops.
   - Measure the stop distance from the fault, and add `stop_within_m_of_fault`.
8. **Stopping-distance requirement.** At 8 m/s, physics makes 3 m impossible. Make the requirement explicit
   and testable:
   - `failsafe_stop_distance: 3 m` applies at the autonomous maximum cruise speed;
   - add `failsafe_stop_distance_at_top_speed` with the value the twin proves achievable plus margin;
   - set the scenario speeds to match;
   - report the numbers. Also check the receiver/ESC/arbiter timeout chain (0.1 / 0.25 / 0.5 / 1.0 s) and
     set the receiver to "no pulses" failsafe.
9. **Tests and evidence.**
   - unit tests for the arbiter model, the regulator models and the graph semantics;
   - re-run `electrical`, `pcb`, `full_throttle`, `range_failsafe`, `pi_hang` and `low_battery` with the
     real firmware;
   - report every result honestly.

Always regenerate build artifacts the tests depend on in your own worktree (wiring/model/electrical)
before running `pytest`. Stale `build/` data has caused false failures.

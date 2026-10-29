# Contract requests from the `pcb` area (branch `r3/pcb-headless`)

The carrier HAT is produced headlessly by KiCad 10's own engine (`python -m r3_kicad.flow
examples/robots/rc-car/pcb/carrier-hat`; see `design-notes.md` in that folder). The board implements the
queued design fixes: a low-UVLO 5.1 V buck with hold-up, a dedicated 6.0 V servo regulator, the ESC BEC
left unconnected, a failsafe arbiter on a GPIO27 heartbeat, and the INA226/MPU-6050. With them the catalog
entry `r3-carrier-hat` no longer matches, so the `pcb` stage reports `board_catalog_mismatch`
until these changes are applied. `tests/test_pcb_stage.py::test_board_matches_catalog_with_requested_patch`
parses the YAML block below, applies it in memory, and checks that KiCad's netlist matches it exactly.

## 1. `packages/component-catalog/r3_catalog/data/components/rc_car.yaml`, component `r3-carrier-hat`

Replace the whole entry with:

```yaml
  - id: r3-carrier-hat
    kind: pcb
    name: R3 RC carrier HAT (custom KiCad board)
    description: >
      65 x 56.5 mm HAT made headlessly in KiCad 10. XT30 battery input -> DMP3013SFV reverse-polarity FET ->
      2 mOhm Kelvin shunt (INA226, I2C 0x40, HAT branch only) -> TPS568230 5.1 V / 5 A buck back-powering the Pi
      (VIN UVLO 4.4 V max, 220 uF hold-up) and TPS56528 6.0 V servo rail (5.5-7.8 A valley current limit);
      MPU-6050 IMU (I2C 0x68); ATtiny412 failsafe arbiter between the Pi's ESC PWM and the ESC (neutral after
      100 ms without a GPIO27 heartbeat); 220 Ohm series resistor on the servo PWM; encoder pull-up.
      The ESC BEC (J_ESC.2) is not connected.
    mechanical: {shape: box, size: [65 mm, 56.5 mm, 12 mm], mass: 0.028, color: "#0b4f2b"}
    pins:
      VBAT: {role: power_in, voltage_nominal: 7.4, voltage_min: 4.4, voltage_max: 17.0, current_max: 5.0}
      GND: {role: ground}
      5V: {role: power_out, voltage_nominal: 5.1, current_max: 5.0}
      3V3: {role: power_in, voltage_nominal: 3.3, current_max: 0.05}
      SERVO_6V: {role: power_out, voltage_nominal: 6.0, current_max: 5.0}
      ESC_BEC_NC: {role: nc}
      HEARTBEAT: {role: gpio, logic_level: 3.3}
      I2C_SDA: {role: i2c_sda, logic_level: 3.3}
      I2C_SCL: {role: i2c_scl, logic_level: 3.3}
      PI_ESC_PWM: {role: pwm_in, logic_level: 3.3}
      PI_SERVO_PWM: {role: pwm_in, logic_level: 3.3}
      ESC_PWM: {role: pwm_out, logic_level: 3.3}
      SERVO_PWM: {role: pwm_out, logic_level: 3.3}
      CRSF_TX: {role: uart_tx, logic_level: 3.3}
      CRSF_RX: {role: uart_rx, logic_level: 3.3}
      GPS_TX: {role: uart_tx, logic_level: 3.3}
      GPS_RX: {role: uart_rx, logic_level: 3.3}
      ENC: {role: gpio, logic_level: 3.3}
    connectors:
      J_GPIO:
        type: socket-2x20-2.54
        position: [0, 24.75 mm, 0]
        direction: [0, 0, -1]
        pins: {"1": 3V3, "2": 5V, "3": I2C_SDA, "4": 5V, "5": I2C_SCL, "6": GND, "7": GPS_TX, "8": CRSF_TX, "9": GND, "10": CRSF_RX,
               "11": ENC, "12": PI_ESC_PWM, "13": HEARTBEAT, "14": GND, "17": 3V3, "20": GND, "25": GND, "29": GPS_RX,
               "30": GND, "34": GND, "35": PI_SERVO_PWM, "39": GND}
      J_BATT: {type: xt30-male, position: [19.0 mm, -13.75 mm, 4.6 mm], direction: [1, 0, 0], pins: {"1": VBAT, "2": GND}}
      J_ESC: {type: header-1x3-2.54, position: [-16.96 mm, -25.25 mm, 10.1 mm], direction: [0, 0, 1], pins: {"1": ESC_PWM, "2": ESC_BEC_NC, "3": GND}}
      J_SERVO: {type: header-1x3-2.54, position: [-4.96 mm, -25.25 mm, 10.1 mm], direction: [0, 0, 1], pins: {"1": SERVO_PWM, "2": SERVO_6V, "3": GND}}
      J_RX: {type: jst-gh-4-header, position: [27.35 mm, 3.25 mm, 3.7 mm], direction: [1, 0, 0], pins: {"1": 5V, "2": CRSF_TX, "3": CRSF_RX, "4": GND}}
      J_GPS: {type: jst-gh-4-header, position: [-27.35 mm, 5.75 mm, 3.7 mm], direction: [-1, 0, 0], pins: {"1": 5V, "2": GPS_TX, "3": GPS_RX, "4": GND}}
      J_ENC: {type: jst-xh-3-header, position: [-22.5 mm, -4.75 mm, 4.9 mm], direction: [-1, 0, 0], pins: {"1": 3V3, "2": ENC, "3": GND}}
    internal_links:
      - {a: PI_ESC_PWM, b: ESC_PWM, kind: failsafe_arbiter, value: 0.1}    # value = heartbeat timeout (s)
      - {a: PI_SERVO_PWM, b: SERVO_PWM, kind: series_resistor, value: 220}
    behavior:
      model: board.pcb
      params:
        source: pcb/carrier-hat
        regulator: {model: regulator.buck, part: TPS568230, v_out: 5.105, i_max: 5.0, i_limit_min: 8.1, v_in_min: 4.4,
                    v_in_uvlo_falling: 3.8, v_in_max: 17.0, f_sw: 600 kHz, max_duty: 0.90, inductance: 2.2 uH,
                    efficiency: [[0.1, 0.80], [0.5, 0.95], [1.0, 0.96], [2.0, 0.97], [3.0, 0.97], [5.0, 0.96]],
                    quiescent_current: 0.003, hold_up_capacitance: 268 uF}
        servo_regulator: {model: regulator.buck, part: TPS56528, v_out: 6.05, i_max: 2.5, i_limit_min: 5.5, i_limit_max: 8.7,
                          v_in_min: 4.35, v_in_max: 17.0, f_sw: 650 kHz, max_duty: 0.80, inductance: 3.3 uH,
                          efficiency: [[0.1, 0.83], [0.5, 0.95], [1.0, 0.96], [2.0, 0.96], [2.5, 0.95]]}
        failsafe: {part: ATtiny412, heartbeat: HEARTBEAT, timeout: 0.1, neutral_us: 1500, frame: 0.02, rearm_edges: 3,
                   rearm_band_us: 50, supply: 3V3}
        reverse_protection: {part: DMP3013SFV, rds_on: 12 mohm}
        tvs: {part: SMAJ16A, v_breakdown_min: 17.8, v_clamp: 26.0, i_pp: 15.4}
        ina226: {address: 0x40, shunt: 2 mohm, bus: I2C, measures: hat_branch_only}
        mpu6050: {address: 0x68, bus: I2C}
        encoder_pullup: 10 kohm
        i2c_pullups: 4.7 kohm
        spice_model: build/pcb/carrier-hat/carrier-hat_power.cir   # subckt R3_CARRIER_HAT_POWER VBAT GND V5 V6
        mounting: {screw: ISO 4762 M2.5 socket head cap screw, holes: 4, pattern: [58 mm, 49 mm], drill: 2.7 mm,
                   head_diameter: 4.5 mm, head_height: 2.5 mm, print_clearance: 0.3 mm,
                   standoff: M2.5 x 11 mm hex 5.0 mm A/F}
    software:
      - {bus: i2c, address: 0x40, protocol: ina226}
      - {bus: i2c, address: 0x68, protocol: mpu6050}
      - {bus: gpio, protocol: heartbeat, params: {pin: HEARTBEAT, min_rate_hz: 20, timeout: 0.1}}
    thermal: {theta_ja_c_per_w: 55.0, max_temp_c: 125, notes: "TPS568230 on a 2-layer board (estimated); JEDEC 44.9 C/W"}
```

The efficiencies are estimates from the loss model in `r3_kicad.regulators.efficiency_curve`: datasheet
RDS(on), DCR and switching terms, plus an estimated core loss. The 6.0 V rail drops out below about
7.8 V input at 2.5 A (Dmax 0.80), so a nominal 2S pack gives about 5.9 V at the servo.

## 2. Schema: `InternalLink.kind` (`packages/component-catalog/r3_catalog/schema.py`)

Add `"failsafe_arbiter"` to the literal:

```python
kind: Literal["series_resistor", "fuse", "switch", "shunt", "wire", "level_shifter", "failsafe_arbiter"]
```

Meaning: `b` follows `a` only while the heartbeat pin toggles. Otherwise `b` carries 1500 us neutral
pulses (model: `r3_kicad.arbiter.FailsafeArbiter`). The interim alternative needs no schema change: `kind: switch`.

## 3. Connector positions (component frame: board centre origin, +y toward the GPIO edge)

The positions in section 1 are measured from the KiCad board: the pad centroid of each connector, plus the
connector type's mating-face height. `board.json` `connector_geometry` carries the measured values, and the
stage warns `pcb_connector_position_mismatch` while the catalog differs by more than 2 mm. J_GPIO sits at the
HAT-specification position (x = 0). The `hat` pose in robot.yaml is `x = 102.5 mm`. With J_GPIO at x = 0 it
becomes `x = 100 mm`, which puts the header on Pi J8.

## 4. `examples/robots/rc-car/robot.yaml` (no change in my area)

- Add a binding for the failsafe heartbeat:
  `heartbeat: {interface: gpio, computer: pi, pins: [pi.J8.13], device: gpiochip0, channel: 27, expects: [hat.HEARTBEAT]}`.
  The robot software must toggle GPIO27 every control cycle (100 Hz), or the ESC stays at neutral.
- The ESC harness wire on `hat.J_ESC.2` (the ESC's BEC) now lands on an unconnected pin and can be removed.
- `hat.J_SERVO.2` is now the regulated 6.0 V rail (`SERVO_6V`), not the BEC.

## 5. CAD area: screw attachment (user request, relayed)

The user wants the board attached by screws through its mounting holes, with caps that interfere with
nothing and minimal 3D-print clearance. The board side is done: KiCad rule areas on each hole keep parts out
of the cap-screw head plus print clearance (top, r = 2.55 mm) and out of the M2.5 hex standoff (bottom,
r = 2.885 mm), and keep copper out of the metal contact circles; DRC enforces both. `board.json` `mounting`
hands the CAD stage the hole positions (component frame), drill, screw head (4.5 x 2.5 mm), standoff and
`printed_cap.inner_diameter` (head + 2 x 0.3 mm FDM clearance, an estimate). Request: the CAD stage models
printed caps/bosses from these numbers and adds an interference check (cap vs. parts and cables).

## 6. Coordinator: sourcing parts from the web by specification (user request, relayed; proposal)

The user would like spec-based parts (wheels, radio, Raspberry Pi 5, ...) proposed from online sources and
then built into the robot. Proposed mechanism (catalog/orchestrator areas, not `pcb`):

1. **Requirement** in robot.yaml per component slot, instead of a fixed catalog id, e.g.
   `wheel: {requires: {kind: wheel, diameter: [60 mm, 70 mm], hub: 12 mm hex, count: 4}}`.
2. **Proposers**: adapters that query real sources and return candidates with datasheet parameters and
   provenance (URL, price, stock, date). Examples: Nexar/Octopart, DigiKey or Mouser for electronic parts
   (the PCB BOM already carries MPNs); vendor catalogs for hobby parts (wheels, ESCs, receivers); and
   manufacturer CAD (STEP) where available.
3. **Selection**: the agent ranks candidates against the requirement and the twin's checks (fit, mass,
   current, interfaces) and proposes one; the user approves or the agent picks.
4. **Build-up**: the chosen candidate becomes a project-local catalog entry (`catalog/components/*.yaml`)
   with `provenance: {status: vendor, source: ...}`, the vendor STEP or a parametric proxy, and estimated
   values labelled as such. The pipeline then runs unchanged.

## From phase B (mechanical/assembly, branch r3/phase-b)

1. **Mounting-hole keep-out vs printed screw caps.** The HAT screws now get printed PETG caps (catalog
   `screw-cap-m2.5-petg`: inner Ø = head 4.5 mm + 2 × 0.3 mm FDM clearance = 5.1 mm, 0.8 mm wall, outer
   Ø 6.7 mm). `board.json` `mounting.keepouts.top_components_radius` is 2.55 mm, but the cap needs
   **≥ 3.35 mm** (3.45 mm with a 0.1 mm margin). The assembly stage reports this as `cap_keepout_exceeded`
   (warning, data: `excess` 0.80 mm per hole). Request: widen the KiCad top-side rule areas around H1–H4 to a
   3.45 mm radius and regenerate `board.json`. The check reads the new value from `board.json` automatically.
2. **`connectors.J_GPIO.position`** in the `r3-carrier-hat` catalog entry is `[0, 24.5 mm, 0]`, but `board.json`
   `connector_geometry.J_GPIO.pad_centroid` is `[0, 24.75 mm]` (`deviation_xy` 0.25 mm). Phase B set the HAT's
   mechanical solids, features and interfaces to the board.json values (holes at y +24.75/−24.25, socket at
   24.75). The electrical connector position belongs to the PCB rollout's edit of the HAT entry: please set
   it to 24.75 mm there.

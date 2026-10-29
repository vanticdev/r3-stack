# Requests from lane A (mechanical layout + wiring) to lane B (electrical, PCB, failsafe)

1. **HAT `J_RX` and `J_BATT` exit over the Pi 5 USB/Ethernet block (real collision).** Both point +x off the
   HAT's front edge (x 132.5 mm). The Pi's USB/Ethernet block (x 133-154.5, y +-26.5, top z 105.6) sits right
   there. `J_RX` crimp entry is at z 103.6 and `J_BATT` solder cups at z 105.3. The cable-side housings and
   about 20 mm of each lead are inside the block. This was checked on the Onshape Pi mesh: (140, 1.1, 103.6) mm
   is inside it. The wiring stage does not report it because its termination-zone exemption covers the whole
   mount chain of the HAT, Pi included. Please move both connectors to a free HAT edge, or use vertical/top
   entry. The wiring exemption should then shrink to the connector's own part.
2. **Top-entry `J_ESC`/`J_SERVO`.** Leads plugged from above must climb ~45 mm from the deck and make a 7.3 mm
   U-turn above the HAT, so they need the deck lead slot plus a printed tie post (lead_post). Right-angle headers
   facing -y at the HAT edge (y -28.5 mm) would let the leads drop straight into the deck lead slot (x 87-109,
   y -46.5..-37.5 mm).
3. **ESC power XT60 pair.** The 12 AWG ESC branch has a mated XT60 pair (esc_power.B / power_y.P2) in the
   cramped space beside the battery. It is the main source of the remaining power-harness bend-radius errors
   (12 AWG: 16-18 mm minimum radius). Consider soldering the ESC power lead into the Y-harness (no pair), or
   moving the split forward.
4. **Fuse/splice.** The fuse holder now sits on the right of the battery (chassis fuse_seat (36, -37) mm,
   clocked 180 deg: input J1 at its front end). SP is 7 mm from J1 as before (queue item 5). Please re-check
   `wire_unprotected_by_fuse` and the 18 AWG lengths in the electrical stage.

## finish-a2: clearance exemption regression fixed; PCB connector relocation blocks release

The router/checker and MuJoCo cable endpoint exemption now covers only the connector owner,
not its mount ancestors. `tests/test_wiring_termination_exemptions.py` injects a USB-style
parent block into the cable's first 20 mm: old code hides it; new code reports >9 mm penetration.
The existing `J_RX`/`J_BATT` +x exit cannot be repaired by a downstream route: the conductor
exit itself lies in the Pi's USB/Ethernet block. Please relocate these headers to the HAT's
-y edge (x below the Pi USB block's x=133 mm), with cable axes -y, and use side-entry
`J_ESC`/`J_SERVO` on that same edge if feasible. Lane A will route and support the resulting
leads. Keep PCB-native footprint poses and catalog connector frames synchronized. Do not
raise the whole HAT without a deliberate GPIO engagement design.


## finish-b2: south-edge interfaces and owned GY-521 hand-off

The PCB is extended 13.5 mm south (65 x 70 mm); its original datum, GPIO socket,
four mounting holes and stack height are unchanged. Native KiCad DRC/ ERC/
unconnected/ schematic parity: **0 / 0 / 0 / 0 errors**. Native DRC retains
133 silkscreen warnings. The PCB stage has 0 errors and one pre-existing
SW_5V pour warning (2.50 mm, 4.96 A IPC-2152 rating at the 5 A design load).

All five cable axes face robot -y. Coordinates are mating-frame origins in the
current robot assembly, in millimetres (PCB coordinates are from its top-left,
x right, y down). The catalog poses match the PCB read-back:

| Interface | Ref | PCB x, y, rotation | Robot x, y, z |
|---|---|---|---|
| Battery | J2 | 49, 57, 180 deg | 119, -42.40, 105.30 |
| Receiver | J5 | 28, 66.3, 0 deg | 95.5, -38.17, 103.57 |
| ESC | J3 | 10.54, 64.9, 270 deg | 75.5, -40.94, 103.47 |
| Servo | J4 | 20.54, 64.9, 270 deg | 85.5, -40.94, 103.47 |
| IMU | J9 | 39, 66.3, 0 deg | 106.5, -38.17, 103.57 |

The header pins and cable ends are all west of the USB/Ethernet block's x=133 mm.
J3/J4 are right-angle 2.54 mm headers. J9 is keyed JST-GH: 1=3.3 V, 2=GND,
3=SDA, 4=SCL, with two PESD3V3U1UL ESD clamps. U5 and its support capacitors,
and R18/R19 I2C pull-ups, are removed. The Pi's 1.8 kohm and typical GY-521
4.7 kohm pull-ups give 1.30 kohm and 2.23 mA at VOL=0.4 V; verify the owned
board's fitted values before assembly.

`imu` is the owned `gy-521-mpu6050`, connected by `imu_lead`. Its provisional
rigid mount is a printed PETG 25 x 20 x 8 mm pedestal, bonded with cured epoxy
to `deck.imu_seat` and joined to the board with two M2.5x8 screws. Board origin
(115, 40, 86) mm; mounting roll/pitch/yaw (0, 0, 0), axes aligned with chassis.
Lane A may finalize the mount and route, preserving the calibrated rotation.
The same MPU-6050 driver at 0x68 now reads this separate board; the twin uses
its compiled component rotation. The generic purchased-board proxy's PCB
thickness and downward-facing hole-frame signs were corrected; hole spacing
is still an explicitly labelled estimate requiring measurement, not a change
to the bought board.

Camera FPC: reserve the direct Pi CAM0 route toward -y, under the HAT, exiting
past y=-43 mm. CAM0 top z=90.8, HAT underside z=100.6: 9.8 mm gap, or 6.8 mm
allowing 3 mm trimmed through-hole tails. A 0.3 mm ribbon with a 1 mm rise and
3 mm bend radius has top z=94.95, **2.65 mm clearance** below the lowest tails.
Latch the ribbon before installing the HAT. The camera connects to the Pi,
never through the HAT; lane A/D still owns its physical FPC route and camera.

Existing route blocks are preserved. Layout owns the new IMU route and
rerouting/supporting leads from the relocated edge. Parts newly needing an
Onshape rebuild from this lane: HAT, GY-521 board proxy, IMU pedestal, deck
(new IMU bonding interface), 5 A fuse; battery XT90H connector geometry also
changes. No live Onshape API was used.


### Straight-stop control (finish-b2)

The Pi now holds steering centred during the failsafe brake, starts at 0.2%
shorting duty, releases down to 0.1%, and only applies full brake below
0.5 m/s. Encoder/IMU slip control no longer injects delayed GNSS velocity
during the bounded stop. Software radio timeout is 90 ms. The independent
ATtiny heartbeat timeout is 60 ms (six edges at 100 Hz), plus a 20 ms frame;
it alternates 1460/1484 us for 3 s, then neutral. Both pulses remain outside
the ESC's neutral band, so modulation does not arm reverse. Its servo remains
on the Pi's PWM path; the tested Pi-hang mission was driving straight.

With real Pi firmware and the copied integration wiring, the final control
settings pass: full_throttle 8.519 m/s; top_speed_failsafe 8.340 m from
8.512 m/s, maximum unwrapped yaw 0.077 deg; pi_hang 2.820 m from 3.995 m/s,
yaw 0.049 deg, heartbeat-to-arbiter latency 70 ms. The 9 m / 3 m stop limits
and 8 m/s minimum speed are unchanged. Both failsafe scenarios now enforce
a 30 deg maximum yaw through the end of the run. Hardware brake pulse
strength is a twin-tested setting, still requiring ESC bench calibration.

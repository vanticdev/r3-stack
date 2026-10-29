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

# Requests from lane C (navigation/sensing/sim) to lane A (mechanical layout + wiring)

## RTK GNSS hardware (finish brief goal 1; design-fix-queue item 12)

Lane C added an RTK solution. Its minimal mechanical integration is in `r3/finish-c`; please own the final layout.

- **Rover:** `gps` is now `gnss-rtk-zed-f9p` (Holybro H-RTK F9P Ultralight: 36.5 x 47 x 16 mm envelope, 21.4 g;
  the height is an estimate). It is taped (`tape_gps`, 34 x 44 mm) to a new `gearbox-spur-diff.lid_pad` interface, the
  flat 55 x 60.5 mm gearbox lid at z = 88 mm. This is the only clear, sky-facing surface the footprint fits:
  - on the deck the rear strip is 36.5 mm deep between the gearbox (z <= 88 mm behind x = 30 mm) and the Pi (x >= 66.5 mm);
  - the front corners are taken by the receiver and the standoff caps.
  `tape_gps` and `gps` moved to the `Rear drivetrain` CAD subassembly. Please confirm or re-place (an antenna mast, or
  a deck extension, would also do). Keep the antenna's sky view clear and keep it >= 40 mm from the Pi 5 (an L1
  interference source).
- **Correction radio:** `rtk_radio` = `telemetry-sik-915-100mw` (Holybro SiK V3, 53 x 28 x 10.7 mm, datasheet), stands
  on edge on the right deck edge: new deck interface `upper-deck-petg.rtk_radio_pad` (deck frame (46.5, -49.15, 3) mm),
  new radio interface `side`, tape `tape_rtk_radio` 50 x 10 mm. The antenna jack is at the front (+x); the 2 dBi hinged
  dipole stands vertical. Check its sweep against the front-right wheel at full lock.
- **Harness:** `rtk_radio_lead` (4 x 28 AWG, both ends `integral`, i.e. the Holybro GH pigtails): gps.J2 (UART2:
  5V, RX2, TX2, GND) to rtk_radio.J1 (GH-6: 1 5V, 2 RX, 3 TX, 6 GND). It needs a route. Both rover connectors now face
  +x (forward, toward the HAT).
- **CAD:** two new purchased parts and one new deck interface, plus the gearbox lid pad, so the Onshape build must
  include them. The model stage falls back to envelope interference (75 pre-existing false positives) until
  `cad_model.json` covers every component. Assembly stage: 0 errors with these parts.

## Sourcing slots (goal 3)
`source:` blocks were added to `pi`, `receiver` and the four wheels (group `front_wheels` / `rear_wheels`) only. Poses,
joins and types are unchanged; the pinned choices are the existing catalog parts (see `r3.lock.yaml`).

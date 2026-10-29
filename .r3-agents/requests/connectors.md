# Connectors area: contract / catalog changes made for the connector and pin foundation

These were made directly because the task required them. Please review them when merging.

1. **Catalog schema (additive, optional fields).** `ConnectorType.geometry`
   (`ConnectorGeometry`: model, footprint, pins with position/axis/tip/pad/wire_entry),
   `ComponentType.model` (`ModelRef`: kicad | vendor | kicad_board | proxy), and
   `ConnectorInstance.x/embedded/source`. A new generated file,
   `connector_geometry.yaml`, is loaded by `load_catalog`.
2. **ConnectorInstance.position semantics.** With geometry, `position` is the
   mating-face centre of the contact array, not the board seat. Pi `J8` moved
   from z = 1.6 to 3.88 mm (the top of the insulator in the official STEP). The
   HAT connectors now come from the KiCad board (`source: kicad:Jn`).
3. **New connector types.** `xt30pw-male` (J_BATT is the right-angle XT30PW-M on
   the board) and `jst-xh-3-header-ra` (J_ENC is the side-entry S3B-XH-A). The
   cable-side `mates_with` lists were extended to match.
4. **HAT pose.** `robot.yaml` sets `hat` y = -0.25 mm. The KiCad board puts its
   holes at y = +24.75 / -24.25 (HAT+ spec: 3.5 mm from the top edge), so the
   socket was 0.25 mm off the Pi header. The catalog HAT holes, socket box and
   pocket were updated to match.
5. **Open: Pi PCB thickness.** The official Raspberry Pi 5 STEP has a 1.28 mm
   laminate (header seat 1.34 mm above the PCB bottom). The catalog solid is
   1.6 mm, and the standoff stack (`pi_s*` at z = 89.6) assumes 1.6 mm. The
   connectors stage reports `component_model_dimension_mismatch`, and the faces
   of J8 and the HAT socket sit 0.22 mm apart. Proposal: set the `pcb` solid to
   1.31 mm and lower `pi_s*`/`hat_*`/`hat` by 0.29 mm.
6. **Open: HAT pin nets.** The KiCad board leaves J_ESC pin 2 unconnected, and
   J_SERVO pin 2 is `SERVO_6V`, not `BEC_6V`. These are already reported by the
   PCB stage as `board_catalog_mismatch`.
7. **routes.json (additive).** Per wire, `ends` (pin endpoints) and `fanout`.
   `path` now runs pin to pin, and `r3_wiring.mujoco_cables.core_path` recovers
   the shared bundle core.

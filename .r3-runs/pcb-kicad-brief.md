# Carrier HAT in KiCad, the way a human engineer does it

Supersedes the earlier PCB brief's implementation. The earlier attempt on branch
`r3/pcb` (commits a2399f9..1b00beb) hand-wrote KiCad s-expressions and implemented
its own router and DRC. Under the tool-use policy in COMMON.md that is not allowed.
Its tool-independent parts may be reused after review:
- `board.json` export;
- IPC-2152 current-capacity math;
- the catalog-consistency check;
- BOM;
- the SPICE subcircuit;
- the buck design calculations and design notes.

## Tooling
- **KiCad 10:** Homebrew is installing `/Applications/KiCad` over a slow link (~2 h).
  The CLI is `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`. KiCad's
  bundled Python and `pcbnew` module are inside the app bundle.
- **IPC API client:** `kicad-python` (`kipy`) 0.8.0 is installed in the shared venv.
  Enable the IPC API in KiCad preferences (Preferences → Plugins → Enable IPC API
  server). It is a GUI process; launch it with `open -a KiCad` / the pcbnew binary.
  You may automate the preference file.
- **Freerouting:** download the release jar into `~/.cache/r3/freerouting/`. Java 22 is
  available. Round trip: Specctra DSN export, then route, then SES import, all through
  KiCad.

While KiCad is downloading, do everything that doesn't need it:
- read the kipy docs and source in the venv;
- plan the schematic: choose real library symbols and footprints from KiCad's official
  library names, and verify the names once the libraries are installed;
- write the automation code and its unit tests;
- fetch Freerouting.
Check `ls /Applications/KiCad` periodically. Do not busy-wait.

## Workflow (mirror what a human does)
1. **New project.** Create it the way KiCad does: `kicad-cli` cannot create projects,
   so use KiCad's project template, or pcbnew/eeschema's own save path through its
   API.
2. **Schematic capture.**
   - Place symbols from KiCad's official libraries (e.g.
     `Connector_Generic:Conn_02x20_Odd_Even`, `Sensor_Motion:MPU-6050`,
     `Sensor_Energy:INA226`, a TI buck regulator symbol or its closest library
     equivalent).
   - Assign library footprints (`Connector_PinSocket_2.54mm`, `Connector_JST`,
     `Connector_AMASS` for XT30, `Package_DFN_QFN`, `Resistor_SMD`, `Capacitor_SMD`,
     `Inductor_SMD`).
   - Connect the nets with wires/labels.
   - Use whatever official automation KiCad 10 offers for the schematic editor. If the
     IPC API does not cover schematics, use eeschema's supported scripting route, or
     drive the GUI as a human would. Document which path you used and why.
3. **ERC** with `kicad-cli sch erc`. Fix the schematic until it reports 0 errors.
4. **Update PCB from Schematic**, the same operation as the editor's F8 / netlist
   import.
5. **Board setup:** the 65 × 56.5 mm Raspberry Pi HAT outline and mounting holes, net
   classes and track widths taken from your IPC-2152 calculation, and design rules for
   a typical 2-layer fab (JLCPCB/PCBWay standard capabilities).
6. **Placement** through the IPC API (footprint positions/rotations). Put the
   connectors at the catalog positions (the `r3-carrier-hat` connectors) and follow
   good practice: buck loop compact, decoupling caps near their pins.
7. **Routing.** Power and critical traces are routed deliberately through the IPC API
   (tracks/vias) with the computed widths, like a human routing power first. Then
   Freerouting handles the remaining signals via DSN/SES round-trip through KiCad.
   Refill zones: a GND pour on both layers.
8. **DRC** with `kicad-cli pcb drc` (schematic parity on). Iterate placement and
   routing until 0 errors and 0 unconnected items.
9. **Outputs:** Gerbers, drill files, pick-and-place, BOM, STEP (3D models for CAD),
   `kicad-cli pcb render` top/bottom PNGs, and a netlist.

## R3 stage (`integrations/kicad/r3_kicad/stage.py`, `run(project) -> StageResult`)
- Re-run the KiCad automation, and reuse the results if the inputs are unchanged.
- Run ERC/DRC.
- Read back the netlist and board (through kicad-cli exports / the IPC API) and write
  `build/pcb/<board>/board.json` exactly per `docs/architecture/r3-pipeline.md`: nets,
  connector pin→net maps, and traces with IPC-2152 max current per net segment.
- Check that the connector pin→net maps equal the catalog (`board_catalog_mismatch`).
- Record the ERC/DRC counts and every artifact path.
- Provide `board.step` for CAD.

## Design requirements (unchanged from the original brief, plus queued fixes)
- XT30 battery input with reverse-polarity protection.
- A synchronous buck VBAT (6–17 V) → 5.1 V / 5 A back-powering the Pi 5V pins. Use a
  part with UVLO ≤ 4.5 V and add bulk hold-up capacitance: the electrical twin found
  the Pi browns out at motor stall with a 6.0 V UVLO.
- NEW: a dedicated servo regulator VBAT → 6.0 V, ≥ 5 A peak, with current limit,
  feeding J_SERVO.2. The ESC BEC (J_ESC.2) must no longer feed the servo; leave
  J_ESC.2 unconnected (NC) on the board.
- NEW: a hardware failsafe arbiter. A small MCU (e.g. ATtiny1616 or RP2040) or a
  logic+timer circuit passes the Pi's ESC PWM through only while a heartbeat GPIO from
  the Pi toggles. On timeout (≤ 100 ms) it forces a 1500 µs neutral pulse. Pick a real
  part and document its behavior; the twin will model it. It uses a spare Pi GPIO
  (GPIO27 on header pin 13) for the heartbeat.
- INA226 with a 2 mΩ shunt (0x40). Note: it currently sees only the HAT branch.
  Document that limitation.
- MPU-6050 (0x68), 4.7 kΩ I²C pull-ups, 220 Ω PWM series resistors, a 10 kΩ encoder
  pull-up, and decoupling.
- Write any catalog pin/net changes these require (servo regulator, ESC BEC NC,
  heartbeat pin) to `.r3-agents/requests/pcb.md`, with the exact YAML, so the
  coordinator can apply them.

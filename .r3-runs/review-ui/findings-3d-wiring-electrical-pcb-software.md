# 3D (GPT-6 Astra)
1. On mobile there's no visible robot. Use a full-width canvas, a nav drawer, and the inspector as a bottom sheet.
2. Red translucent interference boxes ignore the Issues toggle and have no legend. Add an "Interferences" toggle with a count and outline style; selecting one highlights both parts and opens its diagnostic.
3. The inspector wastes 340 px on stats and "Click a part"; "CAD meshes 182 / 56" contradicts "56/56". Use a searchable parts list with an Errors filter; for a selected part, show its diagnostics first.
4. The toolbar looks like tabs but holds independent switches; "Joins" vs "Joints" is easy to misread. Use a "Show" menu (Wires, Connector frames, Mounts and contacts, Joint axes, Error highlights), a separate "Move joints" control, and X-ray/Explode/Fit grouped as view controls.
5. The header is a parade of pipeline badges. Show the displayed model's revision as Current or Out of date, and collapse stage results into "2 failed stages".
# Wiring
1. Mobile: the workspace disappears. Use a nav drawer, a single-column issue summary, the harness selector before the viewer, and tables that scroll inside their own containers.
2. "131 diagnostics" is a statistic with no route to a fix. Use an inventory line plus the worst failure (wire, obstacle, measured vs 8 mm) with "Locate interference" and "View errors" actions, and label stale results explicitly.
3. The 3D view favours the robot over the wires. Frame the selected harness, fade unrelated geometry, thicken its route and mark the min-clearance point; keep "Fit harness / Show obstacles / Edit route" visible, put other layers in a menu, and add an XYZ mm point inspector in edit mode.
4. The harness table is squeezed and clips its error column. Make it a selection list (name, wires, worst clearance) sorted by severity, with details in an inspector; write "1 tie".
5. The lower tabs change scope invisibly: Wires is filtered by harness, BOM is global, Slack uses its own scenario. Add a persistent scope selector, label global sections, and rename "Slack & vibration" to "Cable motion".
# Electrical
1. Mobile is a clipped desktop page. Use a drawer, one status summary, stacked KPIs, and tables with a pinned id column.
2. The battery selector implies a scope the KPIs don't follow (full SOC vs motor-stall minimum vs full throttle). Separate "Worst cases across all battery states" from "Selected operating point", with the selectors together and the condition repeated in each heading.
3. Identical KPI cards bury the failures. Lead with "Pi supply reaches 0 V at motor stall / minimum charge" and "BEC exceeds its 2 A rating by 0.39 A", linked to their circuits; move the passing checks to a secondary row.
4. The power tree is hard to trace: crossing curves, truncated labels, the receiver clipped. Use orthogonal directional connections, rail junctions and labels clear of wires, short names with details on select, and the operating-point control beside the diagram.
5. The rails table claims limits without showing violations: "3.3–3.3 V" with 3.47 V unflagged, overvoltage never flagged, and "measured" used for simulated values. Show nominal vs tolerance ("not specified" if absent), flag both directions, call it "Calculated rail voltages", and move pins into row details.
# PCB
1. Mobile: the sidebar takes 216 of 390 px. Use a drawer and give the content full width.
2. "Board not built yet" hides the whole workspace, including the selector, diagnostics and any artifacts that do exist. Render each available artifact independently and mark only the missing ones.
3. The empty state is implementation docs ("KiCad headless: pcbnew module + kicad-cli", "missing: board_json"). Say "Board data unavailable for carrier-hat", show the last-run outcome, and offer "Build PCB" and "View build log", with toolchain details behind an expander.
4. Cryptic pipeline pills; the PCB status is hidden in hover text. Show an explicit PCB status by the board name and the relevant blockers.
5. KPI cards turn verification into decoration, and an empty pin map shows "0/0 matches catalog". Use a checks list (Passed / Failed / Not run / No pins available) with freshness, emphasise mismatches, and use quiet checkmarks.
# Software
1. Mobile leaves 174 px. Use a drawer, a one-row header, and expandable binding rows.
2. "Device pins reached" vs "Expected" name the same connection differently, with internal HAT nodes in view. Show `esc.J_SIG.1 · SIG`, one route per Pi pin, the path in an expander, and the physical header pin plus GPIO number.
3. Green "connected" claims more than it proves. Call it "Netlist match" with the checked revision and freshness, and use plain interface text instead of info pills.
4. The firmware log buries events in tiny telemetry dumps. Call it "Simulation firmware log" with the scenario and run metadata, give filters their own toolbar, use aligned columns, collapse telemetry fields, and show warning/error counts with jump-to-first.
5. "Firmware modules" is a non-interactive chip cloud. Use a module record (target, language, entry point, folder) and an expandable "9 source files" list with real links; drop the redundant subtitle.

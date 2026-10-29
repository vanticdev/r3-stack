# Simulation (GPT-6 Astra)
1. Mobile is crushed. Use a drawer, a full-width scenario selector, single-column results.
2. "No 3D frames" gets the biggest surface, with "Follow vehicle" still active and a speculative "MuJoCo unavailable?". Use a compact "3D recording unavailable; time-series available" notice, hide the camera controls, and move the charts up.
3. A "stale" card next to green passes tells conflicting stories. Say "Previous run: 2 checks passed · inputs changed" with run time, revision and "Rerun full throttle".
4. Checks and metrics read like an API dump (min_top_speed_mps, "8 / expect 8", "link lost null"). Write "Top speed: 8.0 m/s · required ≥ 8.0 m/s", format charge as 99.6%, say "not recorded", lead with failures and margins, group by subsystem.
5. The synced inspection is buried. Use a compact scenario selector, a shared playback bar beside the charts, check timestamps that seek all views, pointer scrubbing, and selectable chart groups.
# Diagnostics
1. Mobile is cropped. Use a drawer, a full-width list, and an Issues/3D toggle; drop the fixed 560 px column.
2. "wiring 151" is a wall of identical cards. Subgroup by failure type ("Cable interference · 24") with compact rows (cable, part, penetration, time); show the explanation and fix for the selected row only.
3. The 3D view makes the whole robot red. Keep unselected geometry neutral, highlight the selected cable and part, mark the location, add "Locate in 3D" per finding, and put the selection context above the viewport.
4. Backend codes outrank fixable info. Lead with "Battery lead interferes with front-left knuckle · 83.3 mm · 0.844 s", with ids in copyable details.
5. Freshness is hidden in hover chips. Say "Wiring results are out of date" with a rerun action; put the hash and stage list in run details.
# Agent
1. Mobile is unusable. Use a menu, full-width chat/changes tabs, and a sticky composer.
2. Two big empty states bury the task. Use one wide composer, "What should change?"; reveal the changes pane after the first edit, with a resizable split.
3. It looks like chat but every Send replaces the session. Keep history, distinguish Reply from New task, and hide starter suggestions after the first message.
4. Implementation details outrank outcomes ("headlessly", "CLIs", "MCP tools", "model (optional)", "ok"/"failed"). Say "Describe a robot change. The agent edits robot.yaml and runs checks."; lead with changes made, checks passed and errors remaining; metadata goes in details.
5. Hardcoded bold example prompts look like diagnosed recommendations. Show at most two quiet labeled examples, or suggestions generated from real diagnostics with evidence links.
# Deploy
1. Mobile is squeezed. Use a drawer, stacked targets, and a visible action below the summary.
2. Target descriptions overflow their cards. Wrap them, use regular weight and radio indicators, label them "Simulation" and "Physical robots", with tech details in an expander.
3. "Every discovered target" doesn't name the destination. List the discovered robots and readiness, and repeat the destinations and bundle revision in the confirmation ("Deploy to 3 robots").
4. The release-gate verdict has no next step, and the override appears only after a rejection. Add destination-specific readiness with linked blocking checks, failures separated from unrun checks, and the permitted override shown up front.
5. The header competes with the workflow. Show identity plus one readiness summary; make "Run pipeline" secondary and the deploy action primary.
# Settings + shell
1. The mobile shell (215 px sidebar) leaves no room. Use a drawer, a compact header, a collapsed status summary, one column.
2. The doctor repeats symptoms ×8. Lead with the root cause ("Build service unavailable — stages cannot execute") and the fix, with affected stages grouped under it; "Check environment again".
3. The header is a strip of unexplained, non-interactive badges plus a hash. Use an expandable summary with explicit labels ("Model failed", "Wiring results outdated") linking to evidence, the hash in revision details, and consistent domain names (cad/model vs "3D").
4. "Settings · r3 doctor" mixes preferences with environment and is named differently in two places. Use one workspace-level "Environment" destination with blocking checks first, and the theme in one place.
5. The Agents card shows keys as identities ("⚠ 0", "⚠ 1"). Show the real agent names, availability and setup action, or one honest message.

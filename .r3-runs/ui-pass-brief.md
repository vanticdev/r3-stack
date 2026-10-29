# UI pass: 12 independent GPT-6 Astra screen reviews (60 findings), consolidated

The user asked for each screen to be reviewed for what is "confusing / too cluttery / looks like AI". All
findings are in `.r3-runs/review-ui/findings-*.md`, and the screenshots are in `.r3-runs/review-ui/*.png`.
Treat every finding as required unless it is factually wrong; if so, explain why in your report.

## A. Shell fixes: every reviewer raised these, so fix them once
1. **Responsive shell.** Below about 900 px the 216 px sidebar becomes a drawer behind a compact one-row
   header. Content gets the full width, with single-column stacks, tables scrolling inside their own
   containers with a pinned id column, and bottom sheets for inspectors.
2. **Kill the stage-pill parade.** The header shows the project identity plus ONE explicit status summary
   ("Not ready · 2 stages failed · wiring results outdated"), expandable into labelled stage rows linking to
   their evidence. The input hash goes into a copyable "Revision details" disclosure. Stage names match the
   navigation everywhere (3D vs cad/model, Simulation vs sim).
3. **Freshness is explicit everywhere.** Results computed from older inputs say "Previous result … ·
   inputs changed · Rerun <thing>", separate from pass/fail. Never a hover-only clock icon.
4. **Engineering language, not implementation language.** Human labels with units ("Top speed: 8.0 m/s ·
   required ≥ 8.0 m/s", "99.6 %", "not recorded"). Backend codes, ids, hashes, JSON paths and toolchain
   details move into expandable details with copy buttons. Drop pitch sentences, filler subtitles, generic
   icons and decorative KPI cards. Lead with the failures and the action that fixes them.
5. **One primary action per screen.** No duplicated Run buttons. The theme toggle lives in one place.
   "Environment" is one workspace-level destination (it replaces "Doctor"/"Settings · r3 doctor") that
   leads with the root cause and its fix.
6. **Missing data never hides available data.** Each artifact renders independently, and an empty input
   never shows a fake pass (such as "0/0 matches catalog").

## B. Per-screen fixes
Implement the screen-specific items in each findings file:
- Projects: a list plus a create dialog.
- Overview: a one-line readiness summary and human-readable evidence rows, grouped by requirement.
- 3D:
  - a Show menu, a separate Move joints control, and view controls;
  - an interference toggle with a legend;
  - a parts list with an Errors filter in the inspector.
- Wiring: harness-focused framing, a severity-sorted selection list, a scope selector, "Cable motion".
- Electrical:
  - separate worst-case and selected-operating-point sections;
  - a readable orthogonal power tree;
  - correct rail-limit flags in both directions, with "Calculated".
- PCB: independent artifact sections, Build PCB / View build log, a checks list.
- Software: a physical-destination-first binding map, "Netlist match" with freshness, a readable firmware log, a module record.
- Simulation:
  - a compact scenario selector and a shared playback bar beside the charts;
  - check timestamps that seek every view, human-readable metrics, a compact no-3D notice.
- Diagnostics: subgrouped compact rows with human headlines, "Locate in 3D", neutral geometry except the selection.
- Agent: a composer-first layout, persistent history (Reply vs New task), outcome-first results, quiet examples generated from real diagnostics.
- Deploy: radio targets with wrapped text, named destinations and bundle revision, destination-specific readiness with the override shown up front.

## C. Wire it to the REAL backend (required)
The frontend was built against its own mock. The real `r3 serve` API (merged in `r3/integration`, contract
in `docs/architecture/api.md`, OpenAPI at `/api/openapi.json`) differs. Known examples: `inputs` and the
fingerprint are objects, not strings, and the power-tree/operating-point shapes differ, so the Overview, 3D
and Electrical pages crash on live data.
- Generate the TS types from the live OpenAPI, and make every page work against a running `r3 serve`. The
  mock layer must produce exactly the same shapes, by recording real API responses as fixtures.
- Proxy bug: in `mock/backend.ts` the dev proxy calls `req.on("close", … upstream.destroy())`, which fires
  as soon as a GET body is read and kills every proxied request ("socket hang up"). Use `res.on("close")`.
- Playwright smoke tests run against BOTH a real `r3 serve` (TestClient-style fixture project) and the mock.
  Assert no console errors, and save screenshots to `apps/web/test-results/screenshots/` at 1600 px and 390 px,
  light and dark.

Verification: re-capture every screen at desktop and mobile widths in both themes, and include them in the
final report with a checklist mapping each of the 60 findings to done / changed / disputed (with reason).

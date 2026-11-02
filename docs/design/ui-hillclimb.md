# Light-theme hill climb

1440 × 900 headless Chromium, real API and copied populated project. Screenshots
are external under `~/.t3/userdata/ui-hillclimb/round-N/{before,after}/`.
No runs, model downloads, deployments or agent sessions were started.

Scores are manual design judgments, not user-study results. Tells use the checklist
IDs; fewer is better. H/D/T/C rate hierarchy, density, typography and contrast
from 1 (poor) to 5 (excellent). Acceptance requires fewer tells or better ratings
without a visible usability regression. The same reviewer evaluated both sides.
3D initially exceeded the capture readiness budget: its shell is scored, not its
unresolved model. Wiring geometry eventually loaded. These are not rendering
performance qualifications. Missing connector artifacts return existing 404s.

## Round 1 — contrast and workbench hierarchy (accepted)

Darken light-theme secondary/status/action ink, distinguish readiness headlines,
flatten readiness containers, quiet repeated stale labels, and reduce pill/box
rounding. All CSS is light-theme scoped; status labels and icons remain intact.

| Page | Before tells | After tells | Before H/D/T/C | After H/D/T/C |
| --- | --- | --- | --- | --- |
| Projects | 2 (CH) | 0 (—) | 3/4/3/2 | 4/4/4/4 |
| Model bank | 4 (CHBF) | 1 (F) | 2/2/2/2 | 3/2/4/4 |
| Environment | 3 (CHB) | 0 (—) | 3/4/3/2 | 4/4/4/4 |
| Overview | 4 (CHBP) | 0 (—) | 3/4/3/2 | 4/4/4/4 |
| 3D model | 1 (C) | 0 (—) | 3/3/3/2 | 3/3/4/4 |
| Wiring | 3 (CHB) | 0 (—) | 3/4/3/2 | 4/4/4/4 |
| Electrical | 3 (CHB) | 0 (—) | 3/4/3/2 | 4/4/4/4 |
| PCB | 2 (CE) | 1 (E) | 3/3/3/2 | 3/3/4/4 |
| Parts | 2 (CP) | 0 (—) | 3/4/3/2 | 3/4/4/4 |
| Software | 3 (CHB) | 0 (—) | 3/4/3/2 | 4/4/4/4 |
| Simulation | 4 (CHBP) | 0 (—) | 3/3/3/2 | 4/3/4/4 |
| Diagnostics | 1 (C) | 0 (—) | 4/4/3/2 | 4/4/4/4 |
| Agent | 2 (CH) | 0 (—) | 3/3/3/2 | 4/3/4/4 |
| Deploy | 3 (CHB) | 0 (—) | 3/4/3/2 | 4/4/4/4 |

Total tells: 37 → 2. Remaining F: Model bank layout/forms; E: centered PCB empty state.

Validation: typecheck, 80 unit tests and production build passed. No pageerror
was recorded on any of the 14 routes. Measured contrast: muted text 3.96:1
before; new muted text ≥5.10:1 across neutral surfaces; primary button 5.43:1;
link 5.25:1, success 5.06:1, error 5.16:1 on surface-3. This checks the changed
palette, not full-app WCAG certification. Focus outlines remain in place.

## Round 2 — catalog and empty-state finish

Fix the edge-to-edge catalog, wrap long names within their own column, show
persistent required/optional form labels, and align empty-state guidance with
its section. Strengthen light control boundaries. Keep requests, field names,
required flags, selection and downloads unchanged. Catalog layout corrections
also apply in dark mode; its palette remains unchanged.

| Page | Before tells → after | Before H/D/T/C | After H/D/T/C |
| --- | --- | --- | --- |
| Projects | 0 → 0 | 4/4/4/4 | 4/4/4/4 |
| Model bank | 1 → 0 | 3/2/4/4 | 4/4/4/4 |
| Environment | 0 → 0 | 4/4/4/4 | 4/4/4/4 |
| Overview | 0 → 0 | 4/4/4/4 | 4/4/4/4 |
| 3D model | 0 → 0 | 3/3/4/4 | 3/3/4/4 |
| Wiring | 0 → 0 | 4/4/4/4 | 4/4/4/4 |
| Electrical | 0 → 0 | 4/4/4/4 | 4/4/4/4 |
| PCB | 1 → 0 | 3/3/4/4 | 4/4/4/4 |
| Parts | 0 → 0 | 3/4/4/4 | 3/4/4/4 |
| Software | 0 → 0 | 4/4/4/4 | 4/4/4/4 |
| Simulation | 0 → 0 | 4/3/4/4 | 4/3/4/4 |
| Diagnostics | 0 → 0 | 4/4/4/4 | 4/4/4/4 |
| Agent | 0 → 0 | 4/3/4/4 | 4/3/4/4 |
| Deploy | 0 → 0 | 4/4/4/4 | 4/4/4/4 |

Accepted: total tells 2 → 0 within this checklist and captured states. Zero is not a claim of perfect design. Typecheck, 80 unit tests and production build passed. Label persistence, focus, selection and mobile catalog checks passed in both themes. No API or test IDs changed.

## Round 3 — global density trial (rejected)

Trial reduced check and table vertical padding to 3px, page gaps to 8px and
section-header padding to 6px. More rows fit, but multi-line electrical records
sit too close to separators, and requirement explanations lose breathing room.
No checklist tells were removed. Reverted the entire trial; no code commit.
Typecheck, 80 unit tests and build also passed for the trial.

| Page | Tells before → trial | Before H/D/T/C | Trial H/D/T/C |
| --- | --- | --- | --- |
| Projects | 0 → 0 | 4/4/4/4 | 4/4/4/4 |
| Model bank | 0 → 0 | 4/4/4/4 | 4/4/4/4 |
| Environment | 0 → 0 | 4/4/4/4 | 4/4/4/4 |
| Overview | 0 → 0 | 4/4/4/4 | 4/3/4/4 |
| 3D model | 0 → 0 | 3/3/4/4 | 3/3/4/4 |
| Wiring | 0 → 0 | 4/4/4/4 | 4/4/4/4 |
| Electrical | 0 → 0 | 4/4/4/4 | 4/3/4/4 |
| PCB | 0 → 0 | 4/4/4/4 | 4/4/4/4 |
| Parts | 0 → 0 | 3/4/4/4 | 3/4/4/4 |
| Software | 0 → 0 | 4/4/4/4 | 4/4/4/4 |
| Simulation | 0 → 0 | 4/3/4/4 | 4/2/4/4 |
| Diagnostics | 0 → 0 | 4/4/4/4 | 4/4/4/4 |
| Agent | 0 → 0 | 4/3/4/4 | 4/3/4/4 |
| Deploy | 0 → 0 | 4/4/4/4 | 4/4/4/4 |

First consecutive round without an accepted improvement.

## Round 4 — remove section boxes (rejected)

Trial removed all card borders/backgrounds and header separators in light mode.
Some pages look quieter, but electrical table headers become floating white
bands and the catalog/form split loses definition. No further checklist tells
were removed. Reverted; the narrower containment changes from round 1 remain.

| Page | Tells before → trial | Before H/D/T/C | Trial H/D/T/C |
| --- | --- | --- | --- |
| Projects | 0 → 0 | 4/4/4/4 | 4/4/4/4 |
| Model bank | 0 → 0 | 4/4/4/4 | 3/4/4/4 |
| Environment | 0 → 0 | 4/4/4/4 | 4/4/4/4 |
| Overview | 0 → 0 | 4/4/4/4 | 3/4/4/4 |
| 3D model | 0 → 0 | 3/3/4/4 | 3/3/4/4 |
| Wiring | 0 → 0 | 4/4/4/4 | 4/4/4/4 |
| Electrical | 0 → 0 | 4/4/4/4 | 3/4/4/4 |
| PCB | 0 → 0 | 4/4/4/4 | 4/4/4/4 |
| Parts | 0 → 0 | 3/4/4/4 | 3/4/4/4 |
| Software | 0 → 0 | 4/4/4/4 | 4/4/4/4 |
| Simulation | 0 → 0 | 4/3/4/4 | 4/3/4/4 |
| Diagnostics | 0 → 0 | 4/4/4/4 | 4/4/4/4 |
| Agent | 0 → 0 | 4/3/4/4 | 4/3/4/4 |
| Deploy | 0 → 0 | 4/4/4/4 | 4/4/4/4 |

Second consecutive round without accepted improvement: stop at four rounds.
Final retained scores are round 2, with 37 → 2 → 0 flagged tells across the
captured states. Average H/D/T/C: 3.00/3.57/2.93/2.00 initially,
3.86/3.79/4.00/4.00 finally. These are subjective comparisons using a deliberately
narrow checklist; they do not establish that every UI state is free of defects.

## Evidence and boundaries

Each round has before/after PNGs for all 14 routes at 1440 × 900. Round 2 also
has all 14 routes in dark mode. External capture scripts and JSON pageerror
records sit beside the images. Catalog focus, filled labels, selection and
390px layouts were additionally checked in both themes, without submitting.

Later capture passes wait for project status and reuse the browser page to avoid
repeated cold API loads. Early images occasionally omit still-loading header
metadata or geometry. Only loaded UI chrome is scored; loading completion,
newly visible mesh parts and simulation playback are not credited as design
improvements. No claim of viewer performance qualification is made.

The final changes touch only ModelBankPage, theme CSS and these review documents.
No viewer source, API contracts, test IDs or deployment build version changed.
Both requested setup branches were already ancestors of integration. The source
project was copied, never edited. Existing server ports were not used.

Final validation after both reversions: typecheck passed; all 80 unit tests
passed; production build passed. All nine capture sets (126 route captures)
recorded zero uncaught pageerrors. Known Three.js CommonJS deprecation warnings
remain in the test runner. Screenshot checks are not a substitute for full
interaction coverage or an end-to-end CAD qualification.

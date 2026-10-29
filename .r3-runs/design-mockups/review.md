# Visual review

Reviewed each full-size generated image directly. The review covered page purpose, readable interface text, sensible controls, units and inequalities, fixture consistency, selected navigation, and the excluded visual tropes. No existing application screenshots or markup were viewed. These are visual references rather than exact geometry or implementation specifications; the written design language is authoritative for tokens, shell dimensions and production icon consistency.

| Page | Review outcome and revisions |
| --- | --- |
| Projects | Accepted. List and creation form both visible; blocked/stale/not-configured distinguished. |
| Model bank | Accepted after two GPT edits. Changed search to an empty prompt, cleared the geometry filter to All, and removed invented micro-lettering on rendered component housings. Provenance and feature evidence readable. |
| Environment | Accepted after two GPT edits. Removed invented navigation items and duplicate selection, then corrected a spelling error introduced in the edit. Missing radio capability and its impact remain explicit. |
| Overview | Accepted after one GPT edit. Removed an incomplete blocker count and made runtime evidence Estimated, matching Electrical. Readable values and limits. |
| 3D model | Accepted. Large assembly inspection view, joint range, mass provenance and explicitly unverified GPS proxy. |
| Wiring | Accepted. Routed 184 mm plus 28 mm allowance equals 212 mm cut length. Dynamic 2.1 mm clearance fails the 5.0 mm minimum; static and dynamic values are distinct. |
| Electrical | Accepted after one GPT edit. Removed prompt-like instructional footer and labelled omitted return paths. Current, supply and drop comparisons have correct directions; fuse coordination is still a review action. |
| PCB | Accepted. Readable DRC/ERC counts, 0.12 mm warning against a 0.15 mm requirement, selected connector and net mapping. |
| Parts | Accepted after one GPT edit. Removed a leaked prompt label in the breadcrumb. Owned items contribute zero purchase cost; priced rows total USD 105 and the unpriced GPS remains explicit. |
| Software | Accepted after one GPT edit. Removed a leaked prompt label. HAL mappings, source location and timestamped failsafe logs are readable. |
| Simulation | Accepted. All seven requested scenarios visible. Speed declines after link loss; the link-state trace is binary. The 4 m/s to 0 ramp over about 1.2 s corresponds to the illustrated 2.4 m stop distance. This is a historical illustrative recording, separate from current environment availability and stale release evidence. |
| Diagnostics | Accepted. Four findings match the displayed severity totals; selected finding shows subject, time, measurement, limit and actionable hint. Illustrative codes are identified as such. |
| Agent | Accepted after one GPT edit. Replaced leaked breadcrumb metadata and shorthand diff with real tie_points coordinates in metres. Markdown, before/after values, tool results and stale evidence remain clear. |
| Deploy | Accepted. Two selected scenarios match preflight; virtual HAL and physical deployment are clearly distinguished. Prior-run log is illustrative; no live fleet inventory is invented. |

No card grids, hero gradients, glowing accents, emoji, sparkle or decorative charts were accepted. Small rendering variations in navigation widths and line icons are normal for independently generated references; implementation should use the common written system rather than reproduce those variations.

## Artifact validation

Validation passed: exactly fourteen unique PNG files decoded successfully at 1536 × 1024. Inventory, design language, README, prompt history and this review are present. `image-manifest.json` records file dimensions, byte counts and SHA-256 digests. All README links resolve. The staged change contains only this design-reference folder; no apps/web changes are present. git diff --cached --check passed. No application code or runtime behavior changes are involved, so application tests and deployment version bumps do not apply.

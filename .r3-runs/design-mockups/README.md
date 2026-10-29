# R3 design references — Engineering bench

Fourteen original desktop mockups, derived from page purposes and API data rather than the existing UI. [Functional inventory](inventory.md) was written before visual work; [design language](design-language.md) establishes the common system. No app source was changed.

The visual language uses warm paper, petroleum navigation, thin rules, legible engineering tables, rectangular controls and restrained semantic color. Numbers sit beside units, requirements and evidence states. CAD and simulation are working surfaces rather than decorative robot pictures.

**All data is illustrative.** These images are design references, not actual validation reports, procurement quotes, fabrication drawings or live device state. Agent shows a later hypothetical fix; the other project pages share the preceding clearance failure. Diagnostic codes not explicitly present in the guide are illustrative examples, not proposed API changes. Display affordances such as filtering, pin inspection and layer selection describe the intended design; they do not claim existing implementation support.

## 01 · Projects

[01-projects.png](01-projects.png)

A project ledger and adjacent creation form support both resuming and starting work without changing context. The expanded project row explains its release blocker directly; this replaces generic project cards and ornamental progress scores.

## 02 · Model bank

[02-model-bank.png](02-model-bank.png)

Search results sit beside the selected part's geometry and validation evidence. Exact identity, source and reviewed mounting features carry more weight than a product thumbnail, so the catalog behaves as an engineering reference instead of a storefront.

## 03 · Environment

[03-environment.png](03-environment.png)

Tool versions and stage capability form a readable health ledger with a selected problem and its impact. The missing radio worker is explicit even while other tools are available; there is no misleading aggregate health gauge.

## 04 · Overview

[04-overview.png](04-overview.png)

The release gate leads with blockers, measured values and limits, followed by a stage ledger and a concrete next action. It answers whether fabrication evidence is sufficient instead of reporting a decorative completion percentage.

## 05 · 3D model

[05-3d-model.png](05-3d-model.png)

A large neutral CAD viewport shares the workspace with a component tree and joint inspector. Selection, joint range and geometry provenance take precedence over cinematic lighting; unverified GPS geometry stays visibly provisional.

## 06 · Wiring

[06-wiring.png](06-wiring.png)

Physical harness routes, a moving-part clearance callout and synchronized slack playback explain the failure spatially. The numerical inspector and cut list connect inspection to manufacture, rather than treating wiring as a decorative network graph.

## 07 · Electrical

[07-electrical.png](07-electrical.png)

An orthogonal power path and operating-point table show how current and voltage move through the robot. Each measurement has a relevant limit; fuse coordination remains a separate curve review instead of an unjustified green badge.

## 08 · PCB

[08-pcb.png](08-pcb.png)

A board-scale inspection surface is paired with DRC/ERC findings and connector-to-net mappings. The warning points at a real fabrication concern, and export is distinct from whole-robot release; the board is a working artifact rather than a beauty render.

## 09 · Parts

[09-parts.png](09-parts.png)

A purchase ledger keeps owned, sourced and to-buy parts visible together with model readiness. A partial priced subtotal and an explicit unpriced GPS prevent false cost precision; candidate requirements occupy the inspector instead of shopping-style tiles.

## 10 · Software

[10-software.png](10-software.png)

Modules, HAL bindings and physical endpoint reachability form the main hierarchy. A compact timestamped log connects runtime behavior to a scenario, giving firmware engineers traceability instead of generic code-activity metrics.

## 11 · Simulation

[11-simulation.png](11-simulation.png)

Scenario selection, 3D playback, checks and synchronized time-series occupy one investigation workspace. Speed and radio-link traces explain a failsafe event with units and a common time cursor; the plots exist to answer a test question.

## 12 · Diagnostics

[12-diagnostics.png](12-diagnostics.png)

A sortable-style findings ledger leads to a detailed numerical inspector and a domain-specific repair action. Stable codes, subjects and thresholds make the page useful for both humans and agents without collapsing all problems into a vague health score.

## 13 · Agent

[13-agent.png](13-agent.png)

Editorial chat typography supports real markdown tables, evidence rows and a compact spec diff. The visible distinction between a successful local routing fix and stale release evidence keeps the collaboration accountable; there are no decorative bot avatars or oversized message bubbles.

## 14 · Deploy

[14-deploy.png](14-deploy.png)

Explicit target selection, scenario scope, module plan and preflight state precede the deployment action. Simulation is visibly separate from physical fleet confirmation, and the design does not invent live fleet inventory; the outcome is a readable log rather than a celebratory dashboard.

## Generation and review

- Tool: built-in `image_gen.imagegen` (tool identifier `image_gen__imagegen`).
- Model: the built-in GPT image generator; the tool does not expose a model/version identifier. No unverified model name is asserted.
- One independent initial image-generation request per page, followed by targeted edits where needed; no screenshot or existing UI was supplied as a reference.
- Full prompts: [prompts.json](prompts.json), combining `preamble` with each page's third array entry and the applicable `generation_suffix`; `corrections` records the nine targeted GPT edits.
- Target: 1536 × 1024 desktop PNG; actual dimensions and review results recorded in [review.md](review.md).
- No local raster composition or text replacement is used. All final page images are GPT-generated.
- No deployment version bump or application tests are needed for these design-only artifacts. Validation covers image decoding, coverage, visual inspection, links, and Git scope.

# Deterministic wire routing

Agents declare electrical connectivity and mechanical constraints in robot.yaml,
then call `r3_route_wires`. This wraps the existing wiring stage, including voxel
search, bundle planning, support placement, cut lengths, clearance checks and
dynamic cable checks. It generates build/wiring/routes.json and stage diagnostics.
The model does not compute wire coordinates. Missing geometry or failed checks
remain blockers; an emitted path is not a physical safety certificate.

## Graph topology search

`r3_route_topologies` exposes `r3_wiring.topology.route_topologies` for a supplied,
collision-pruned undirected graph. Each edge has from/to node IDs and positive
cost (environmental cost times physical length); each cable has id/from/to.
The software returns alternative node paths and nondominated cost pairs.

Reference: Karlsson et al., *Automatic cable harness layout routing in a
customizable 3D environment*, DOI 10.1016/j.cad.2023.103671.
Accessible manuscript: https://arxiv.org/html/2311.09061v1

## Algorithms

The implementation uses the published paper's weighted cable/union-edge objective
(equations 1–2), iterative marginal-cost cable rerouting (algorithms 2–3), and
joint branch-point moves (section 3.1). Branch moves sum shortest-distance fields
from the fixed incident ends, then use multi-source Dijkstra to choose both ends
of a shared bundle and its connecting path. Every proposal is reconstructed into
complete cables and accepted only if simple, permitted and strictly better under
the actual union-edge objective. Overlapping arms cannot manufacture an improvement.

Alpha-bounded loopless paths seed two stable cable orders (section 3.3). We use
Yen's graph k-shortest-path enumeration rather than the geometric path-diversity
generator cited by the paper. Paths meet the alpha cost bound, but are not claimed
to be spatially diverse. Projected Lagrangian subgradient search (algorithm 4)
adds primal seeds and lower bounds, with nonnegative edge multipliers projected
onto sum(lambda)=w_B*c. Step sizes use the best available primal bound; stagnation
reduces the step scale. Budgets and convergence status are explicit.

Each candidate exposes cable and union-edge costs, objective improvement history,
shared edges and convergence status. The result also records all searches and
per-weight lower/upper bounds and relative gaps. Bounds apply only to the supplied
graph, never to the whole continuous design space or physical safety. No industrial
benchmark or performance equivalence to the authors' C++ implementation is claimed.

## Robot geometry integration

`HarnessRouter` invokes `topology_geometry.optimize_voxel_routes` after finding
initial voxel paths and before `_sequence` constructs bundle segments. It derives
a finite graph from the CAD distance fields in a corridor around those paths.
Costs combine environmental preferences, step length and plate-crossing costs.
Per-branch blocked masks preserve clearance and nonincident connector exclusions;
plate crossings obey their axis restrictions, and diagonal edges use a conservative
supercover rule. The chosen candidate feeds existing fan-out construction, smoothing,
numerical planning, supports, cut-length generation and physical/dynamic validation.
Branches retain conductor multiplicity in the cable-length objective.

The software selects a balanced candidate; no LLM supplies its path points. The
existing web wiring-stage action and `r3_route_wires` both run this pipeline.
Evidence is saved in `build/wiring/topology.json`, `routing_detail.json`, and stage
result metrics, alongside the usual `routes.json` consumed by the viewer.

Default refinement budget: 6000 graph vertices, 2-voxel corridor margin, 3 alpha
paths, 5 dual iterations, 5 HRH sweeps, bundle weights 0.25 and 0.65. The standalone
graph tool defaults to 7 paths, 40 dual iterations and 30 sweeps. This bounds resource
use and does not imply convergence or global optimality. `RouterOptions.topology`,
`topology_max_nodes` and `topology_margin` control production refinement.

Ordered authored waypoints stay fixed in the original routing path and are listed
as excluded from automatic topology refinement. With fewer than two eligible
branches refinement is unnecessary. If the graph budget is exceeded or no candidate
is found, the router preserves initial paths and emits an explicit warning. All
physical checks still apply. A skipped refinement must not be presented as an
optimized topology. Independent-lane routing remains a separate constrained mode.

Run targeted tests:
`.venv/bin/python -m pytest tests/test_wiring_topology.py tests/test_wiring_topology_geometry.py`.
Tests compare lower bounds with exhaustive optima, exercise joint moves that escape
single-cable local minima, verify repeatability, and check obstacle/terminal,
waypoint and budget handling.

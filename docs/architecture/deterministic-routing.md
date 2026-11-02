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

Implemented subset: weighted total cable edge cost plus union edge cost
(equations 1–2), and iterative single-cable rerouting (algorithms 2–3).
Each cable is removed from occupancy before computing its marginal shortest
path. Only strict global improvements are accepted. Two deterministic initial
orders, bounded single-edge-exclusion seeds and multiple bundle weights produce
deduplicated nondominated candidates. The edge-exclusion seeds are an adaptation,
not the paper's alpha-shortest-path initializer.
The sweep budget and convergence flag make bounded termination explicit.

This is not the complete paper implementation: joint branch-point optimization,
alpha-shortest-path initialization, Lagrangian subgradient search, certified dual
bounds and the industrial benchmarks are not implemented. Graph topology search
is separate from the production voxel router; it does not automatically import
its candidate paths into the physical planner. Do not describe its costs as
physical metres unless all edge costs are actual lengths. Do not treat its
candidates as clearance/bend/capacity/dynamic certification.

Run targeted tests: `.venv/bin/python -m pytest tests/test_wiring_topology.py`.

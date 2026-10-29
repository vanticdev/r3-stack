# V1 repository layout

This layout prepares BLITZ for an integrated robotics development platform.
Only the existing deployment stack is implemented. All new application,
service, package, integration, and example folders contain documentation only.
Folder names establish ownership, not a choice of language, framework, hosting
provider, or independent microservice deployment.

## Applications and execution

| Directory | Responsibility |
| --- | --- |
| `apps/web/` | The single user interface for projects, robot editing, wiring, simulation results, and deployment. |
| `services/orchestrator/` | Project-facing API (`r3` CLI, MCP server, HTTP API `r3 serve`: see [api.md](api.md)), versioned input resolution, dependency checks, job scheduling, and artifact/result references. |
| `services/simulation-worker/` | Execute scheduled simulations with pinned inputs and return results and diagnostics. |

The standalone marketing site has been removed. Any future public pages belong
in the same web application. Workers own job execution; orchestration owns job
lifecycle. These boundaries do not require separate hosted services for v1.

## Shared domain packages

| Directory | Responsibility |
| --- | --- |
| `packages/robot-model/` | Declarative robot specification, stable identities, frames and units, versioned artifact references, and the derived dependency/component graph. |
| `packages/component-catalog/` | The small supported set of component metadata, pinouts, ratings, mechanical properties, and behavior-model references. |
| `packages/wiring/` | Electrical connectivity and physical cable routing as separate representations linked through connection identities. |
| `packages/simulation/` | Simulation time, event ordering, simulator coupling, engine adapters, validation outputs, and reproducibility metadata. |
| `packages/hardware-interface/` | Narrow software-facing contracts and eventual virtual/physical adapters for supported hardware operations. |

The robot model is the shared domain foundation. Other packages consume its
contracts; it must not depend on the web application, service entrypoints, or
provider-specific integrations. Services compose packages and integrations.
The web application presents project state and job results through service APIs.

These packages are platform source, distinct from user programs deployed onto
robots. Robot module source and assets still belong under
`backend/<language>/<module-folder>` or the configured installer backend root,
as required by [BLITZ values](../../values.md).

## External integrations

| Directory | Responsibility |
| --- | --- |
| `integrations/onshape/` | Import versioned CAD references, geometry, assembly relationships, and available engineering metadata. |
| `integrations/kicad/` | Reserve an electronics import boundary for schematics, netlists, board geometry, and manufacturing references. KiCad remains a provisional choice. |
| `integrations/github/` | Connect repository revisions and workflow events to orchestration and report validation results. |

Provider adapters translate external formats into shared contracts. Domain
packages must not rely on provider-specific response shapes. Importing CAD or
PCB data does not itself provide a validated physical or electrical model.

## Existing deployment stack

`backend/`, `watchdog/`, `proto/`, `scripts/`, `docker/`, `ops/`, `__tests__/`,
and the root Python/build configuration retain their existing locations.
Python imports, installers, Gradle integration, source discovery, and test
fixtures depend on these paths. Moving them would require a separate code
migration, beyond this structure-only preparation.

The existing watchdog remains the target process manager. The future hardware
interface package does not replace it. Existing Docker fleet tests exercise
deployment; they are not the planned multi-domain digital twin.

Existing deployment guides now live in `docs/deployment/`. Architecture
decisions belong in `docs/architecture/`; future reproducible robot examples
belong in `examples/robots/`.

## Project data and generated artifacts

Git-backed robot projects will hold authored specifications, wiring definitions,
software source, and immutable references to external design revisions. The
component graph is derived from that specification rather than maintained as
a second independently editable source of truth.

Large CAD exports, meshes, simulation recordings, logs, telemetry, and build
outputs should remain generated artifacts outside tracked source. Object
storage and retention policy are still undecided. Credentials, device
identities, caches, and local environments must remain untracked.

## Decisions left for implementation

- Specification schema, language choices, and cross-language contract generation.
- Stable CAD/component identity and external revision reconciliation.
- Physics engine, simulation clock, solver coupling, and determinism policy.
- Supported hardware operations and how software connects to virtual devices.
- Initial component catalog, reference robot, and validation acceptance criteria.
- Job transport, persistence, artifact storage, and deployment infrastructure.

Start with a small end-to-end robot workflow. Static cable routes and conservative
clearance checks can precede flexible-cable physics. Docker can host userspace
software but does not emulate board hardware. Simulation results must identify
their model assumptions; successful simulation does not guarantee a successful
physical build.

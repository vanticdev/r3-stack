# Orchestrator (`r3_orchestrator`)

The `r3` CLI and the R3 MCP server that humans, CI, Codex and Claude Code use
to work on a robot project. Both expose the same operations (`api.py`) and
return the same structured diagnostics. GitHub is the source of truth
(`integrations/github`); this package schedules stages, persists results
under `build/<stage>/result.json`, and writes the report.

## Stages

`r3 validate` runs these in order, importing each module lazily. A module that
is not installed yields a `stage_unavailable` warning, not a crash; a stage that
raises yields `stage_crashed`.

| Stage | Module |
| --- | --- |
| model | `r3_orchestrator.model_stage` |
| cad | `r3_onshape.stage` |
| pcb | `r3_kicad.stage` |
| wiring | `r3_wiring.stage` |
| electrical | `r3_sim.electrical.stage` |
| sim | `r3_sim.stage` (scenario ids passed as `scenarios=[...]` if `run` accepts them, and always as `$R3_SCENARIOS`) |
| report | `r3_orchestrator.report_stage` → `build/report/index.html`, `build/report/summary.json` |

The `model` stage checks connector mates, wire gauge against connector
contacts, binding reachability (`binding_not_connected` lists the actual net
members), pin roles (`power_conflict`, `uart_tx_tx_conflict`,
`input_without_driver`), rail and logic voltages, I²C addresses per bus, Pi
pin functions against binding interfaces (PWM channel, UART/I²C controller,
Linux device), static component interference (exact convex overlap volume),
and total mass. It writes `build/model/graph.json` (nets, component envelopes,
wires, rails, buses, mass budget) and `build/model/layout.png`.

## CLI

```bash
r3 validate [--stage model,wiring] [--scenario id] [--json]
r3 cad sync | r3 pcb build | r3 wiring route | r3 sim run [--scenario id]
r3 report            # regenerate build/report from stored results
r3 status            # revision, git state, lock pins, per-stage status (stale if robot.yaml changed)
r3 diagnose          # errors grouped by subject, with hints
r3 deploy --target sim [--scenario id]
r3 deploy --target fleet --yes [--force] [-- <backend/deploy.py args>]
r3 create <name> [--from rc-car|blank] [--dir .] [--github [--private|--public]]
```

`--project/-C` selects the project (default `$R3_PROJECT`, then the nearest
directory with a `robot.yaml`). `python -m r3_orchestrator` works too. Exit
codes: 0 ok, 1 errors, 2 usage error or refused action (missing `--yes`,
validation required before a fleet deploy).

## MCP server

`python -m r3_orchestrator.mcp_server` (stdio). Tools: `r3_status`,
`r3_validate`, `r3_run_stage`, `r3_get_diagnostics`, `r3_diagnose`,
`r3_get_spec`, `r3_patch_spec`, `r3_list_catalog`, `r3_run_scenario`,
`r3_get_artifact`, `r3_create_project`, and the gated `deploy_fleet` and
`create_github_repo`.

- Design and simulation tools are free. `deploy_fleet` (physical robots) and
  `create_github_repo` (external account) refuse unless `confirm=true`, and
  carry `destructive_hint` annotations.
- `r3_patch_spec(patch, expected_sha256)` applies an RFC 7386 JSON merge patch
  to `robot.yaml`. The SHA must match the file (from `r3_get_spec`), so
  concurrent Codex/Claude edits fail loudly with `spec_conflict` instead of
  overwriting each other. The result is validated against `r3/robot@1` before
  writing (`spec_invalid` otherwise). Graph problems such as dangling connectors
  come back as `graph_diagnostics`. Comments and unchanged lines are preserved
  byte for byte (requires `ruamel.yaml`).

### Register in Claude Code

Project scope: copy [`examples/mcp.json`](examples/mcp.json) to `.mcp.json` in
the project root (projects scaffolded by `r3 create` already have one), or:

```bash
claude mcp add r3 -e R3_PROJECT=$PWD/examples/robots/rc-car -- \
  $PWD/.venv/bin/python -m r3_orchestrator.mcp_server
```

### Register in Codex

```bash
codex mcp add r3 --env R3_PROJECT=$PWD/examples/robots/rc-car -- \
  $PWD/.venv/bin/python -m r3_orchestrator.mcp_server
```

or append [`examples/codex-config.toml`](examples/codex-config.toml) to
`~/.codex/config.toml`.

## Tests

```bash
.venv/bin/python -m pytest tests/test_orchestrator_*.py
node --test apps/web/tests/loader.test.mjs
```

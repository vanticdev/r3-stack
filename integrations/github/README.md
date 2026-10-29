# GitHub integration (`r3_github`)

GitHub is the source of truth for an R3 robot: the repository holds
`robot.yaml`, sources and `r3.lock.yaml`; CI runs the pipeline and publishes
`build/` as an artifact. Live simulation state is never committed.

- `scaffold.create_project(target, template="rc-car")` writes a project
  repository: `robot.yaml` (renamed, comments kept), `r3.lock.yaml` (platform
  revision pin), `cad/`, `pcb/<board>/`, `catalog/`, `backend/deploy.py` (BLITZ
  hooks generated from `software.modules`) and module stubs, `.gitignore`
  (`build/`), `AGENTS.md`/`CLAUDE.md`, `.mcp.json`, `README.md` and
  `.github/workflows/r3.yml`.
- The workflow installs `r3-platform` from `R3_PLATFORM_URL` (default
  `git+https://github.com/vanticdev/r3-stack.git`, overridable with the
  environment variable at scaffold time or the repository variable in CI), runs
  `r3 validate --json`, uploads `build/`, writes a job summary from
  `build/report/summary.json`, and fails on errors. Onshape sync runs only when
  `ONSHAPE_ACCESS_KEY`/`ONSHAPE_SECRET_KEY` secrets exist; KiCad only when
  `kicad-cli` is on the runner.
- `lock.pin(root, section, revision, **fields)` (plus `pin_onshape`,
  `pin_board`) records external revisions; every stage result copies them into
  `inputs`.
- `scaffold.create_remote(name, directory, private=True)` commits and runs
  `gh repo create --source --push`. It is the only external side effect and
  runs only when called explicitly (`r3 create --github`, or the MCP
  `create_github_repo` tool with `confirm=true`).

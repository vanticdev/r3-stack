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
- The workflow runs inside the R3 worker image
  (`ghcr.io/vanticdev/r3-worker:<platform revision>`, override with the
  repository variable `R3_WORKER_IMAGE` or `$R3_WORKER_IMAGE_REPO` at scaffold
  time; see `docker/r3-worker`), so KiCad, ngspice, MuJoCo GL and Freerouting
  are present and every stage runs headless. It prints `r3 doctor`, runs
  `r3 validate --json`, uploads `build/`, writes a job summary from
  `build/report/summary.json` (plus the worker environment), and fails on
  errors. Onshape sync runs only when the `ONSHAPE_ACCESS_KEY` and
  `ONSHAPE_SECRET_KEY` secrets exist (API keys, HMAC-signed). Setting the
  repository variable `R3_PLATFORM_OVERRIDE=true` installs `r3-platform` from
  `R3_PLATFORM_URL`@`R3_PLATFORM_REF` over the image's copy.
- `lock.pin(root, section, revision, **fields)` (plus `pin_onshape`,
  `pin_board`) records external revisions; every stage result copies them into
  `inputs`.
- `scaffold.create_remote(name, directory, private=True)` commits and runs
  `gh repo create --source --push`. It is the only external side effect and
  runs only when called explicitly (`r3 create --github`, or the MCP
  `create_github_repo` tool with `confirm=true`).

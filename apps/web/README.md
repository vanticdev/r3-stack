# Platform web application

The single R3 web interface. Today it is a static dashboard seed with no
build step: `index.html`, `app.js` (three.js from a CDN via an import map) and
`loader.js` (pure data loading, also tested under Node).

It loads a project's `build/report/summary.json` plus `build/model/graph.json`
and `build/wiring/routes.json`, and shows:

- a 3D view of component envelopes in the robot frame (+z up); components named
  by error/warning diagnostics are outlined, interference regions are red;
- wire routes from `routes.json` as tubes (or straight lines between connector
  ends until the wiring stage has run); click a part or wire for details;
- stage status, filterable diagnostics (click to highlight subjects), scenario
  results and every PNG under `build/`.

```bash
.venv/bin/python -m r3_orchestrator validate -C examples/robots/rc-car
.venv/bin/python -m http.server 8000      # from the repository root
open "http://localhost:8000/apps/web/index.html?build=/examples/robots/rc-car/build/"
```

Without a server, use **Open files…** and pick `summary.json`, `graph.json`
and `routes.json`.

Validation: `node --test apps/web/tests/loader.test.mjs` (also run by
`tests/test_orchestrator_report.py`).

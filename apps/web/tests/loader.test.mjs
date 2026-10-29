// Run: node --test apps/web/tests/loader.test.mjs
// R3_BUILD_DIR selects the build/ directory to load (default: the bundled fixture).
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { buildScene, diagnosticsBySubject, loadBuild, sortedDiagnostics, validateSummary } from "../loader.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const buildDir = process.env.R3_BUILD_DIR || path.join(here, "fixture-build");
const expectRoutes = process.env.R3_EXPECT_ROUTES !== "0";

// fetch() over the filesystem, so the loader is exercised exactly as in the browser.
async function fileFetch(url) {
  try {
    const text = await readFile(url, "utf8");
    return { ok: true, status: 200, json: async () => JSON.parse(text) };
  } catch {
    return { ok: false, status: 404, json: async () => null };
  }
}

test("loads summary, graph and routes from a build directory", async () => {
  const data = await loadBuild(buildDir, fileFetch);
  assert.equal(data.summary.schema, "r3/report-summary@1");
  assert.ok(data.graph, "graph.json loaded");
  if (expectRoutes) assert.ok(data.routes, "routes.json loaded");
  const model = buildScene(data);
  assert.ok(model.components.length > 0);
  for (const c of model.components) {
    assert.equal(c.xyz.length, 3);
    assert.ok(["box", "cylinder", "custom"].includes(c.shape));
  }
  assert.ok(model.wires.length > 0);
  for (const w of model.wires) assert.ok(w.path.length >= 2);
  console.log(JSON.stringify({ components: model.components.length, wires: model.wires.length, routed: model.routed,
                               flagged: model.components.filter((c) => c.severity === "error").map((c) => c.id) }));
});

test("flags components named by error diagnostics", async () => {
  const data = await loadBuild(buildDir, fileFetch);
  const model = buildScene(data);
  const errorSubjects = new Set(data.summary.diagnostics.filter((d) => d.severity === "error").flatMap((d) => d.subjects));
  for (const c of model.components) assert.equal(c.severity === "error", errorSubjects.has(c.id), c.id);
});

test("missing summary is a clear error; missing optional artifacts are not", async () => {
  await assert.rejects(loadBuild("/nonexistent/build", fileFetch), /report\/summary\.json/);
  assert.throws(() => validateSummary({ schema: "other" }), /unsupported summary schema/);
  const only = { summary: { schema: "r3/report-summary@1", robot: {}, counts: {}, stages: [], diagnostics: [] }, graph: null, routes: null };
  assert.deepEqual(buildScene(only), { components: [], wires: [], interference: [], routed: false });
});

test("diagnostic filtering and indexing", () => {
  const diags = [
    { severity: "warning", stage: "sim", code: "b", message: "", subjects: ["x"] },
    { severity: "error", stage: "model", code: "a", message: "", subjects: ["x", "y"] },
  ];
  assert.deepEqual(sortedDiagnostics(diags).map((d) => d.code), ["a", "b"]);
  assert.deepEqual(sortedDiagnostics(diags, { severity: "warning" }).map((d) => d.code), ["b"]);
  assert.equal(diagnosticsBySubject(diags).x.length, 2);
});

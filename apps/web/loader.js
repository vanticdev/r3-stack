// Loads an R3 project's build/ outputs and turns them into a plain scene model.
// No DOM or three.js here, so the logic runs (and is tested) under Node too.
//
// Inputs (paths relative to the project's build/ directory):
//   report/summary.json   schema r3/report-summary@1 (required)
//   model/graph.json      component envelopes + wire metadata (optional)
//   wiring/routes.json    routed 3D wire paths (optional; straight lines otherwise)

export const SUMMARY_SCHEMA = "r3/report-summary@1";

const WIRE_COLORS = {
  red: 0xd62728, black: 0x222222, white: 0xeeeeee, green: 0x2ca02c, blue: 0x1f77b4,
  yellow: 0xe8c31a, orange: 0xff7f0e, brown: 0x8c564b, purple: 0x9467bd, grey: 0x888888, gray: 0x888888,
};

export function joinUrl(base, relative) {
  if (!base) return relative;
  return base.endsWith("/") ? base + relative : `${base}/${relative}`;
}

async function fetchJson(fetchImpl, url, required) {
  let response;
  try {
    response = await fetchImpl(url);
  } catch (error) {
    if (required) throw new Error(`cannot fetch ${url}: ${error.message}`);
    return null;
  }
  if (!response.ok) {
    if (required) throw new Error(`cannot fetch ${url}: HTTP ${response.status}`);
    return null;
  }
  return response.json();
}

/** Fetch summary.json plus the artifacts it references from a build/ base URL. */
export async function loadBuild(buildUrl, fetchImpl = globalThis.fetch) {
  const summary = await fetchJson(fetchImpl, joinUrl(buildUrl, "report/summary.json"), true);
  validateSummary(summary);
  const artifacts = summary.artifacts || {};
  const [graph, routes] = await Promise.all([
    fetchJson(fetchImpl, joinUrl(buildUrl, artifacts.graph || "model/graph.json"), false),
    fetchJson(fetchImpl, joinUrl(buildUrl, artifacts.routes || "wiring/routes.json"), false),
  ]);
  return { summary, graph, routes, buildUrl };
}

export function validateSummary(summary) {
  if (!summary || typeof summary !== "object") throw new Error("summary.json is not an object");
  if (summary.schema !== SUMMARY_SCHEMA) throw new Error(`unsupported summary schema ${summary.schema}; expected ${SUMMARY_SCHEMA}`);
  for (const key of ["robot", "counts", "stages", "diagnostics"]) {
    if (!(key in summary)) throw new Error(`summary.json is missing "${key}"`);
  }
  return summary;
}

/** Diagnostics indexed by every subject they mention. */
export function diagnosticsBySubject(diagnostics) {
  const index = {};
  for (const d of diagnostics || []) {
    for (const subject of d.subjects || []) (index[subject] ||= []).push(d);
  }
  return index;
}

function worstSeverity(list) {
  if (!list || !list.length) return null;
  if (list.some((d) => d.severity === "error")) return "error";
  if (list.some((d) => d.severity === "warning")) return "warning";
  return "info";
}

/** Build the renderable scene: component boxes/cylinders and wire polylines (robot frame, metres). */
export function buildScene({ summary, graph, routes }) {
  const bySubject = diagnosticsBySubject(summary.diagnostics);
  const components = Object.entries((graph && graph.components) || {}).map(([id, c]) => ({
    id, kind: c.kind, name: c.name, type: c.type, shape: c.shape, size: c.size, axis: c.axis || "z",
    xyz: c.xyz, rpy: c.rpy || [0, 0, 0], color: c.color || "#888888", moving: !!c.moving, mass: c.mass,
    severity: worstSeverity(bySubject[id]), diagnostics: bySubject[id] || [],
  }));
  const wireMeta = (graph && graph.wires) || {};
  const routed = (routes && routes.wires) || {};
  const wires = [];
  for (const [id, meta] of Object.entries(wireMeta)) {
    const route = routed[id];
    const path = route && Array.isArray(route.path) && route.path.length >= 2
      ? route.path
      : (meta.ends || []).every((e) => Array.isArray(e)) && (meta.ends || []).length === 2 ? meta.ends : null;
    if (!path) continue;
    wires.push({
      id, harness: meta.harness, awg: meta.awg, signal: meta.signal, routed: !!route, path,
      color: WIRE_COLORS[(meta.color || "").toLowerCase()] ?? 0x999999,
      length: route ? route.routed_length : null,
      severity: worstSeverity(bySubject[id]), diagnostics: bySubject[id] || [],
    });
  }
  // Routed wires that the graph does not know about (e.g. older graph.json) still render.
  for (const [id, route] of Object.entries(routed)) {
    if (wireMeta[id] || !Array.isArray(route.path) || route.path.length < 2) continue;
    wires.push({ id, harness: route.harness, awg: route.awg, routed: true, path: route.path, color: 0x999999,
                 length: route.routed_length ?? null, severity: worstSeverity(bySubject[id]), diagnostics: bySubject[id] || [] });
  }
  const interference = ((graph && graph.interference) || []).map((o) => ({ a: o.a, b: o.b, center: o.center, extent: o.extent, volume: o.volume }));
  return { components, wires, interference, routed: Object.keys(routed).length > 0 };
}

/** Diagnostics sorted worst-first, optionally filtered. */
export function sortedDiagnostics(diagnostics, { severity = "", stage = "", text = "" } = {}) {
  const rank = { error: 0, warning: 1, info: 2 };
  const needle = text.toLowerCase();
  return (diagnostics || [])
    .filter((d) => (!severity || d.severity === severity) && (!stage || d.stage === stage))
    .filter((d) => !needle || [d.code, d.message, ...(d.subjects || [])].join(" ").toLowerCase().includes(needle))
    .sort((a, b) => rank[a.severity] - rank[b.severity] || a.stage.localeCompare(b.stage) || a.code.localeCompare(b.code));
}

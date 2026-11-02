// Turns a project's build/ outputs into a plain scene model (components, wires, interference).
// Ported from the original static dashboard's loader.js. Dependency-free (type imports only), so it
// also runs directly under `node --test` (tests/loader.test.mjs, used by tests/test_orchestrator_report.py).
//
// Inputs (paths relative to the project's build/ directory):
//   report/summary.json   schema r3/report-summary@1 (required by loadBuild)
//   model/graph.json      component envelopes + wire metadata (optional)
//   wiring/routes.json    routed 3D wire paths (optional; straight lines otherwise)
import type { Diagnostic, ModelGraph, RoutesJson, RoutingDetail, Severity, Vec3 } from "../api/types.ts";

export const SUMMARY_SCHEMA = "r3/report-summary@1";

export const WIRE_COLORS: Record<string, number> = {
  red: 0xd62728, black: 0x2a2a2a, white: 0xeeeeee, green: 0x2ca02c, blue: 0x1f77b4,
  yellow: 0xe8c31a, orange: 0xff7f0e, brown: 0x8c564b, purple: 0x9467bd, grey: 0x888888, gray: 0x888888,
};

export function wireColor(name: string | undefined | null): number {
  return WIRE_COLORS[(name ?? "").toLowerCase()] ?? 0x999999;
}

/** Approximate outer radius of silicone wire by AWG, metres (12 AWG ≈ 2 mm). */
export function awgRadius(awg: number): number {
  return Math.max(0.0005, 0.002 * Math.pow(0.89, awg - 12));
}

export interface ReportSummary {
  schema: string;
  robot: { name?: string; description?: string; version?: string };
  counts: Partial<Record<Severity, number>>;
  stages: { stage: string; status: string; ok: boolean | null; stale?: boolean | null; counts?: Partial<Record<Severity, number>> }[];
  diagnostics: Diagnostic[];
  artifacts?: { graph?: string | null; routes?: string | null; images?: string[]; [key: string]: unknown };
  scenarios?: { id: string; passed: boolean; metrics?: Record<string, unknown> }[];
  [key: string]: unknown;
}

export interface BuildData {
  summary: ReportSummary;
  graph: ModelGraph | null;
  routes: RoutesJson | null;
  buildUrl?: string;
}

type FetchLike = (url: string) => Promise<{ ok: boolean; status: number; json(): Promise<unknown> }>;

export function joinUrl(base: string, relative: string): string {
  if (!base) return relative;
  return base.endsWith("/") ? base + relative : `${base}/${relative}`;
}

async function fetchJson(fetchImpl: FetchLike, url: string, required: boolean): Promise<unknown> {
  let response;
  try {
    response = await fetchImpl(url);
  } catch (error) {
    if (required) throw new Error(`cannot fetch ${url}: ${(error as Error).message}`);
    return null;
  }
  if (!response.ok) {
    if (required) throw new Error(`cannot fetch ${url}: HTTP ${response.status}`);
    return null;
  }
  return response.json();
}

/** Fetch summary.json plus the artifacts it references from a build/ base URL. */
export async function loadBuild(buildUrl: string, fetchImpl: FetchLike = globalThis.fetch as unknown as FetchLike): Promise<BuildData> {
  const summary = validateSummary(await fetchJson(fetchImpl, joinUrl(buildUrl, "report/summary.json"), true));
  const artifacts = summary.artifacts ?? {};
  const [graph, routes] = await Promise.all([
    fetchJson(fetchImpl, joinUrl(buildUrl, artifacts.graph || "model/graph.json"), false),
    fetchJson(fetchImpl, joinUrl(buildUrl, artifacts.routes || "wiring/routes.json"), false),
  ]);
  return { summary, graph: graph as ModelGraph | null, routes: routes as RoutesJson | null, buildUrl };
}

export function validateSummary(summary: unknown): ReportSummary {
  if (!summary || typeof summary !== "object") throw new Error("summary.json is not an object");
  const record = summary as Record<string, unknown>;
  if (record.schema !== SUMMARY_SCHEMA) throw new Error(`unsupported summary schema ${String(record.schema)}; expected ${SUMMARY_SCHEMA}`);
  for (const key of ["robot", "counts", "stages", "diagnostics"]) {
    if (!(key in record)) throw new Error(`summary.json is missing "${key}"`);
  }
  return summary as ReportSummary;
}

/** Diagnostics indexed by every subject they mention. */
export function diagnosticsBySubject(diagnostics: readonly Diagnostic[] | undefined): Record<string, Diagnostic[]> {
  const index: Record<string, Diagnostic[]> = {};
  for (const d of diagnostics ?? []) for (const subject of d.subjects ?? []) (index[subject] ??= []).push(d);
  return index;
}

export function worstSeverity(list: readonly Pick<Diagnostic, "severity">[] | undefined): Severity | null {
  if (!list || !list.length) return null;
  if (list.some((d) => d.severity === "error")) return "error";
  if (list.some((d) => d.severity === "warning")) return "warning";
  return "info";
}

export interface SceneComponent {
  id: string;
  kind: string;
  name: string;
  type: string;
  shape: string;
  size: number[];
  axis: "x" | "y" | "z";
  xyz: Vec3;
  rpy: Vec3;
  color: string;
  moving: boolean;
  mass: number;
  mount: string;
  severity: Severity | null;
  diagnostics: Diagnostic[];
}

export interface SceneWire {
  id: string;
  harness: string;
  awg: number;
  signal?: string;
  routed: boolean;
  path: Vec3[];
  color: number;
  colorName: string;
  radius: number;
  length: number | null;
  severity: Severity | null;
  diagnostics: Diagnostic[];
}

/** Pin display lines fill the unrouted view; computed harness paths retain authority. */
export function withConnectorDisplayPaths(wires: SceneWire[], pins: Record<string, { display_path?: Vec3[]; od?: number }>): SceneWire[] {
  return wires.map((wire) => {
    const pin = pins[wire.id];
    return !wire.routed && pin?.display_path?.length
      ? { ...wire, path: pin.display_path, radius: pin.od ? pin.od / 2 : wire.radius }
      : wire;
  });
}

export interface SceneModel {
  components: SceneComponent[];
  wires: SceneWire[];
  interference: { a: string; b: string; center: Vec3; extent: Vec3; volume: number }[];
  routed: boolean;
}

/** Build the renderable scene: component envelopes and wire polylines (robot frame, metres). */
export function buildScene({ summary, graph, routes, routingDetail = null, diagnostics }: {
  summary?: Pick<ReportSummary, "diagnostics"> | null;
  graph: ModelGraph | null;
  routes: RoutesJson | null;
  routingDetail?: RoutingDetail | null;
  diagnostics?: Diagnostic[];
}): SceneModel {
  const bySubject = diagnosticsBySubject(diagnostics ?? summary?.diagnostics);
  const components: SceneComponent[] = Object.entries(graph?.components ?? {}).map(([id, c]) => ({
    id, kind: c.kind, name: c.name, type: c.type, shape: c.shape, size: c.size, axis: c.axis || "z",
    xyz: c.xyz, rpy: c.rpy || [0, 0, 0], color: c.color || "#888888", moving: !!c.moving, mass: c.mass, mount: c.mount,
    severity: worstSeverity(bySubject[id]), diagnostics: bySubject[id] ?? [],
  }));
  const wireMeta = graph?.wires ?? {};
  const routed = routes?.wires ?? {};
  const detail = routingDetail?.wires ?? {};
  const wires: SceneWire[] = [];
  const add = (id: string, harness: string, awg: number, colorName: string, signal: string | undefined, path: Vec3[], isRouted: boolean,
               length: number | null) => {
    const od = detail[id]?.od;
    wires.push({
      id, harness, awg, signal, routed: isRouted, path, color: wireColor(colorName), colorName,
      radius: od ? od / 2 : awgRadius(awg), length,
      severity: worstSeverity(bySubject[id]), diagnostics: bySubject[id] ?? [],
    });
  };
  for (const [id, meta] of Object.entries(wireMeta)) {
    const route = routed[id];
    const ends = meta.ends ?? [];
    const path = route && Array.isArray(route.path) && route.path.length >= 2
      ? route.path
      : ends.length === 2 && ends.every((e) => Array.isArray(e)) ? (ends as Vec3[]) : null;
    if (!path) continue;
    add(id, meta.harness, meta.awg, meta.color ?? detail[id]?.color ?? "", meta.signal, path, !!route, route ? route.routed_length : null);
  }
  // Routed wires that the graph does not know about (e.g. older graph.json) still render.
  for (const [id, route] of Object.entries(routed)) {
    if (wireMeta[id] || !Array.isArray(route.path) || route.path.length < 2) continue;
    add(id, route.harness, route.awg, detail[id]?.color ?? "", detail[id]?.signal, route.path, true, route.routed_length ?? null);
  }
  const interference = (graph?.interference ?? []).map((o) => ({ a: o.a, b: o.b, center: o.center, extent: o.extent, volume: o.volume }));
  return { components, wires, interference, routed: Object.keys(routed).length > 0 };
}

const RANK: Record<Severity, number> = { error: 0, warning: 1, info: 2 };

/** Diagnostics sorted worst-first, optionally filtered. */
export function sortedDiagnostics(diagnostics: readonly Diagnostic[] | undefined,
                                  { severity = "", stage = "", text = "" }: { severity?: string; stage?: string; text?: string } = {}): Diagnostic[] {
  const needle = text.toLowerCase();
  return (diagnostics ?? [])
    .filter((d) => (!severity || d.severity === severity) && (!stage || d.stage === stage))
    .filter((d) => !needle || [d.code, d.message, ...(d.subjects ?? [])].join(" ").toLowerCase().includes(needle))
    .sort((a, b) => (RANK[a.severity] ?? 3) - (RANK[b.severity] ?? 3) || a.stage.localeCompare(b.stage) || a.code.localeCompare(b.code));
}

/** Map diagnostic subjects (component ids, pins like "esc.BEC", wire ids, harness ids, "a|b" contact keys)
 * to the components and wires the 3D view can highlight. Operating-point subjects ("op:...") are ignored. */
export function resolveSubjects(subjects: readonly string[], scene: Pick<SceneModel, "components" | "wires">):
    { components: Set<string>; wires: Set<string> } {
  const componentIds = new Set(scene.components.map((c) => c.id));
  const wireIds = new Set(scene.wires.map((w) => w.id));
  const harnesses = new Map<string, string[]>();
  for (const w of scene.wires) (harnesses.get(w.harness) ?? harnesses.set(w.harness, []).get(w.harness)!).push(w.id);
  const components = new Set<string>();
  const wires = new Set<string>();
  const visit = (subject: string) => {
    if (!subject || subject.startsWith("op:")) return;
    if (subject.includes("|")) return subject.split("|").forEach((s) => visit(s.split("#")[0]!));
    if (componentIds.has(subject)) return void components.add(subject);
    if (wireIds.has(subject)) return void wires.add(subject);
    const harness = harnesses.get(subject);
    if (harness) return harness.forEach((w) => wires.add(w));
    const head = subject.split(".")[0]!;
    if (componentIds.has(head)) components.add(head);
    else if (harnesses.has(head)) harnesses.get(head)!.forEach((w) => wires.add(w));
  };
  subjects.forEach(visit);
  return { components, wires };
}

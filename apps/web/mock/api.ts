// Dev/preview mock of the R3 API contract (.r3-runs/ui-brief.md), serving REAL data read from
// each project's build/ artifacts. Mutations (spec PATCH, runs, agent sessions, deploys) never touch
// the repository: spec patches live in memory and runs replay the existing stage results.
// Every response carries `X-R3-Mock: 1`, so the UI can tell the user.
import { spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { createReadStream, existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { mkdir, readFile, stat } from "node:fs/promises";
import type { IncomingMessage, ServerResponse } from "node:http";
import path from "node:path";

import { parse as parseYaml, parseDocument, type Document } from "yaml";

import type {
  AgentEvent, BindingChain, Catalog, CatalogComponent, Diagnostic, ModelGraph, OperatingPointsJson, PcbBoard,
  ProjectDetail, ProjectSummary, RequirementEvidence, RobotSpec, RunEvent, RunInfo, ScenarioListItem, ScenarioSummary,
  SpecJoint, StageResult, StageStatus,
} from "../src/api/types.ts";
import { STAGES } from "../src/api/types.ts";
import {
  bindingChains, connectorRatings, evidenceCounts, fabricationReady, parseCsv, powerTree, wireRatings,
} from "../src/lib/domain.ts";
import { quantity, quantityUnit, vector } from "../src/lib/units.ts";

type Next = (error?: unknown) => void;
type Json = Record<string, unknown>;

class HttpError extends Error {
  readonly status: number;
  readonly code: string;
  readonly data: Json;

  constructor(status: number, code: string, message: string, data: Json = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.data = data;
  }
}

export interface MockOptions {
  repoRoot: string;
  /** Directories whose children with a robot.yaml are projects (default: examples/robots). */
  projectRoots?: string[];
  cacheDir: string;
  python?: string;
  /** Delay between replayed run/agent events, ms. */
  eventDelay?: number;
}

const MIME: Record<string, string> = {
  ".json": "application/json", ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml", ".stl": "model/stl",
  ".mp4": "video/mp4", ".csv": "text/csv; charset=utf-8", ".txt": "text/plain; charset=utf-8", ".log": "text/plain; charset=utf-8",
  ".md": "text/markdown; charset=utf-8", ".html": "text/html; charset=utf-8", ".xml": "application/xml", ".zip": "application/zip",
  ".cir": "text/plain; charset=utf-8",
};

const sha256 = (text: string | Buffer) => createHash("sha256").update(text).digest("hex");
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function createMockApi(options: MockOptions) {
  const repoRoot = path.resolve(options.repoRoot);
  const roots = (options.projectRoots ?? [path.join(repoRoot, "examples", "robots")]).map((r) => path.resolve(repoRoot, r));
  const delay = options.eventDelay ?? 250;
  const jsonCache = new Map<string, { mtime: number; value: unknown }>();
  const specOverlay = new Map<string, string>(); // pid -> patched robot.yaml text (memory only)
  const runs = new Map<string, RunInfo & { events: RunEvent[]; done: boolean; waiters: Set<() => void> }>();
  const sessions = new Map<string, { pid: string; events: AgentEvent[]; done: boolean; cancelled: boolean; waiters: Set<() => void> }>();
  const conversions = new Map<string, Promise<unknown>>();

  // ------------------------------------------------------------------ file helpers
  function projects(): { id: string; dir: string }[] {
    const out: { id: string; dir: string }[] = [];
    for (const root of roots) {
      if (!existsSync(root)) continue;
      for (const entry of readdirSync(root, { withFileTypes: true })) {
        if (entry.isDirectory() && existsSync(path.join(root, entry.name, "robot.yaml"))) out.push({ id: entry.name, dir: path.join(root, entry.name) });
      }
    }
    return out.sort((a, b) => a.id.localeCompare(b.id));
  }

  function projectDir(pid: string): string {
    const found = projects().find((p) => p.id === pid);
    if (!found) throw new HttpError(404, "project_not_found", `no project ${pid}`, { pid });
    return found.dir;
  }

  function readJson<T>(file: string): T | null {
    let mtime: number;
    try {
      mtime = statSync(file).mtimeMs;
    } catch {
      return null;
    }
    const cached = jsonCache.get(file);
    if (cached && cached.mtime === mtime) return cached.value as T;
    // Python's json.dumps writes NaN/Infinity for non-finite floats; map them to null.
    const text = readFileSync(file, "utf8").replace(/\bNaN\b|-?\bInfinity\b/g, "null");
    const value = JSON.parse(text) as T;
    jsonCache.set(file, { mtime, value });
    return value;
  }

  const build = (pid: string, ...parts: string[]) => path.join(projectDir(pid), "build", ...parts);
  const artifactUrl = (pid: string, rel: string) => `/api/projects/${encodeURIComponent(pid)}/artifacts/${rel.split("/").map(encodeURIComponent).join("/")}`;

  function images(pid: string, dir: string): { name: string; url: string }[] {
    const full = build(pid, dir);
    if (!existsSync(full)) return [];
    return readdirSync(full).filter((f) => f.endsWith(".png")).sort().map((f) => ({ name: f.replace(/\.png$/, ""), url: artifactUrl(pid, `${dir}/${f}`) }));
  }

  function specText(pid: string): string {
    return specOverlay.get(pid) ?? readFileSync(path.join(projectDir(pid), "robot.yaml"), "utf8");
  }

  function spec(pid: string): RobotSpec {
    return parseYaml(specText(pid)) as RobotSpec;
  }

  function fingerprint(pid: string): string {
    return sha256(specText(pid)).slice(0, 16);
  }

  function results(pid: string): Record<string, StageResult> {
    const out: Record<string, StageResult> = {};
    for (const stage of STAGES) {
      const result = readJson<StageResult>(build(pid, stage, "result.json"));
      if (result) out[stage] = result;
    }
    return out;
  }

  function stageStatuses(pid: string): StageStatus[] {
    const current = fingerprint(pid);
    const all = results(pid);
    return STAGES.map((stage) => {
      const r = all[stage];
      if (!r) return { stage, status: "missing", ok: null, stale: null, counts: {} };
      const stale = !!r.inputs?.robot_yaml_sha256 && r.inputs.robot_yaml_sha256 !== current;
      const counts: StageStatus["counts"] = {};
      for (const d of r.diagnostics ?? []) counts[d.severity] = (counts[d.severity] ?? 0) + 1;
      return { stage, status: stale ? "stale" : r.ok ? "ok" : "failed", ok: r.ok, stale, counts, duration_s: r.duration_s ?? null };
    });
  }

  function allDiagnostics(pid: string): Diagnostic[] {
    const seen = new Set<string>();
    const out: Diagnostic[] = [];
    for (const [stage, r] of Object.entries(results(pid))) {
      if (stage === "report") continue; // the report re-lists every other stage's diagnostics
      for (const d of r.diagnostics ?? []) {
        const key = `${d.stage ?? stage}|${d.code}|${d.subjects.join(",")}|${d.message}`;
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({ ...d, stage: d.stage ?? stage, data: d.data ?? {}, subjects: d.subjects ?? [] });
      }
    }
    return out;
  }

  function scenarioSummary(pid: string, scenario: string): ScenarioSummary | null {
    return readJson<ScenarioSummary>(build(pid, "sim", scenario, "summary.json"));
  }

  // ------------------------------------------------------------------ release gate (evidence matrix)
  function releaseGate(pid: string): ProjectDetail["release"] {
    const s = spec(pid);
    const current = fingerprint(pid);
    const all = results(pid);
    const graph = readJson<ModelGraph>(build(pid, "model", "graph.json"));
    const op = readJson<OperatingPointsJson>(build(pid, "electrical", "operating_points.json"));
    const slack = readJson<{ worst?: { min_distance: number; wire: string; component: string } }>(build(pid, "wiring", "slack_sim.json"));
    const routing = readJson<{ wire_min_clearance?: Record<string, { distance: number; component: string }> }>(build(pid, "wiring", "routing_detail.json"));
    const items: RequirementEvidence[] = [];
    const staleOf = (stage: string) => {
      const fp = all[stage]?.inputs?.robot_yaml_sha256;
      return fp ? fp !== current : false;
    };
    const fpOf = (stage: string) => all[stage]?.inputs?.robot_yaml_sha256 ?? null;
    const push = (id: string, label: string, raw: unknown, stage: string, check: string, value: number | null, passes: (v: number, limit: number) => boolean,
                  artifact: string, opts: { estimated?: boolean; unit?: string; note?: string } = {}) => {
      const limit = quantity(raw);
      const status: RequirementEvidence["status"] = value === null || !all[stage] ? "not_run"
        : staleOf(stage) ? "stale" : !passes(value, limit) ? "failed" : opts.estimated ? "estimated" : "verified";
      items.push({
        id, kind: "requirement", label, requirement: typeof raw === "number" || typeof raw === "string" ? raw : null, unit: opts.unit ?? quantityUnit(raw),
        status, note: opts.note,
        evidence: value === null || !all[stage] ? null : { stage, check, value, limit, unit: opts.unit, artifact, fingerprint: fpOf(stage) },
      });
    };
    const reqs = s.requirements ?? {};
    const scenarioMetric = (scenario: string, key: string): number | null => {
      const v = scenarioSummary(pid, scenario)?.metrics?.[key];
      return typeof v === "number" ? v : null;
    };
    for (const [key, raw] of Object.entries(reqs)) {
      switch (key) {
        case "max_mass":
          push(key, "Total mass", raw, "model", "graph.mass.total", graph?.mass?.total ?? null, (v, l) => v <= l, "model/graph.json",
               { unit: "kg", estimated: ((graph?.mass as { estimated_fraction?: number } | undefined)?.estimated_fraction ?? 0) > 0 });
          break;
        case "top_speed":
          push(key, "Top speed (full_throttle)", raw, "sim", "full_throttle.top_speed_mps", scenarioMetric("full_throttle", "top_speed_mps"),
               (v, l) => v >= l, "sim/full_throttle/summary.json", { unit: "m/s" });
          break;
        case "min_runtime":
          push(key, "Runtime (duty-cycle mix)", raw, "electrical", "runtime.runtime", op?.runtime?.runtime ?? null, (v, l) => v >= l,
               "electrical/operating_points.json", { unit: "s" });
          break;
        case "max_wire_voltage_drop_fraction": {
          let worst: number | null = null;
          for (const point of Object.values(op?.operating_points ?? {})) {
            if (point.kind !== "continuous") continue;
            for (const h of Object.values(point.harness_drop ?? {})) worst = Math.max(worst ?? 0, h.fraction);
          }
          push(key, "Harness voltage drop (continuous)", raw, "electrical", "harness_drop.fraction", worst, (v, l) => v <= l,
               "electrical/operating_points.json", { unit: "" });
          break;
        }
        case "min_wire_clearance_moving": {
          const routed = Object.values(routing?.wire_min_clearance ?? {}).map((c) => c.distance);
          const values = [...routed, ...(slack?.worst ? [slack.worst.min_distance] : [])];
          push(key, "Wire clearance to moving parts (routed + slack sim)", raw, "wiring", "min(wire_min_clearance, slack_sim.worst)",
               values.length ? Math.min(...values) : null, (v, l) => v >= l, "wiring/slack_sim.json", { unit: "m" });
          break;
        }
        case "max_component_temperature_c": {
          const temps = Object.values(op?.thermal ?? {}).flatMap((t) => [t.temperature_c, t.steady_temperature_c])
            .filter((t): t is number => typeof t === "number");
          push(key, "Peak component temperature", raw, "electrical", "thermal.*.temperature_c", temps.length ? Math.max(...temps) : null,
               (v, l) => v <= l, "electrical/operating_points.json", { unit: "°C" });
          break;
        }
        case "radio_range":
          push(key, "Radio range (range_failsafe)", raw, "sim", "range_failsafe.link_lost_distance_m",
               scenarioMetric("range_failsafe", "link_lost_distance_m"), (v, l) => v >= l, "sim/range_failsafe/summary.json", { unit: "m" });
          break;
        case "failsafe_stop_distance":
          push(key, "Failsafe stop distance", raw, "sim", "range_failsafe.stop_distance_m", scenarioMetric("range_failsafe", "stop_distance_m"),
               (v, l) => v <= l, "sim/range_failsafe/summary.json", { unit: "m" });
          break;
        default:
          items.push({ id: key, kind: "requirement", label: key, requirement: raw as string, status: "not_run", evidence: null,
                       note: "no check maps to this requirement in the mock" });
      }
    }
    for (const scenario of s.simulation?.scenarios ?? []) {
      const summary = scenarioSummary(pid, scenario.id);
      const stale = !!summary?.inputs?.robot_yaml_sha256 && summary.inputs.robot_yaml_sha256 !== current;
      for (const [key, expected] of Object.entries(scenario.expect ?? {})) {
        const check = summary?.checks?.find((c) => c.key === key);
        items.push({
          id: `${scenario.id}.${key}`, kind: "expectation", scenario: scenario.id, label: `${scenario.id}: ${key}`,
          requirement: expected as number, status: !check ? "not_run" : stale ? "stale" : check.pass ? "verified" : "failed",
          evidence: check ? { stage: "sim", check: key, value: check.measured as number, limit: check.expected as number,
                              artifact: `sim/${scenario.id}/summary.json`, fingerprint: summary?.inputs?.robot_yaml_sha256 ?? null } : null,
          ...(check?.detail ? { note: check.detail } : {}),
        });
      }
    }
    return { fabrication_ready: fabricationReady(items), current_fingerprint: current, counts: evidenceCounts(items), items };
  }

  // ------------------------------------------------------------------ catalog
  function catalog(): Catalog {
    const dataDir = path.join(repoRoot, "packages", "component-catalog", "r3_catalog", "data");
    const components: Record<string, CatalogComponent> = {};
    const componentsDir = path.join(dataDir, "components");
    for (const file of existsSync(componentsDir) ? readdirSync(componentsDir).filter((f) => f.endsWith(".yaml")) : []) {
      const doc = parseYaml(readFileSync(path.join(componentsDir, file), "utf8")) as { components?: CatalogComponent[] };
      for (const c of doc.components ?? []) components[c.id] = c;
    }
    const list = (file: string, key: string, id: (x: Json) => string) => {
      const full = path.join(dataDir, file);
      if (!existsSync(full)) return {};
      const doc = parseYaml(readFileSync(full, "utf8")) as Record<string, Json[]>;
      return Object.fromEntries((doc[key] ?? []).map((x) => [id(x), x]));
    };
    return {
      components,
      connectors: list("connectors.yaml", "connectors", (x) => String(x.id)),
      wires: list("wires.yaml", "wires", (x) => `awg${String(x.awg)}`),
    };
  }

  // ------------------------------------------------------------------ recording conversion
  function python(): string {
    if (options.python) return options.python;
    if (process.env.R3_PYTHON) return process.env.R3_PYTHON;
    const candidates = [path.join(repoRoot, ".venv", "bin", "python")];
    try {
      // In a git worktree the shared venv lives next to the main checkout's .git directory.
      const gitFile = path.join(repoRoot, ".git");
      if (statSync(gitFile).isFile()) {
        const gitdir = readFileSync(gitFile, "utf8").match(/gitdir:\s*(.+)/)?.[1]?.trim();
        if (gitdir) candidates.push(path.join(path.resolve(repoRoot, gitdir), "..", "..", "..", ".venv", "bin", "python"));
      }
    } catch { /* not a git checkout */ }
    return candidates.find((c) => existsSync(c)) ?? "python3";
  }

  function pythonPath(): string {
    const pyproject = path.join(repoRoot, "pyproject.toml");
    const text = existsSync(pyproject) ? readFileSync(pyproject, "utf8") : "";
    const block = text.match(/pythonpath\s*=\s*\[([\s\S]*?)\]/)?.[1] ?? "";
    const dirs = [...block.matchAll(/"([^"]+)"/g)].map((m) => path.join(repoRoot, m[1]!));
    return [...dirs, process.env.PYTHONPATH ?? ""].filter(Boolean).join(path.delimiter);
  }

  async function recording(pid: string, scenario: string, hz: number): Promise<unknown> {
    const npz = build(pid, "sim", scenario, "recording.npz");
    if (!existsSync(npz) || !existsSync(build(pid, "sim", scenario, "vehicle.xml"))) return null;
    const out = path.join(options.cacheDir, pid, `${scenario}@${hz}hz.json`);
    const fresh = existsSync(out) && statSync(out).mtimeMs >= statSync(npz).mtimeMs;
    if (!fresh) {
      const key = out;
      if (!conversions.has(key)) {
        conversions.set(key, (async () => {
          await mkdir(path.dirname(out), { recursive: true });
          const script = path.join(repoRoot, "apps", "web", "scripts", "convert_recording.py");
          await new Promise<void>((resolve, reject) => {
            const child = spawn(python(), [script, projectDir(pid), scenario, "--hz", String(hz), "--out", out],
                                { cwd: repoRoot, env: { ...process.env, PYTHONPATH: pythonPath(), MUJOCO_GL: process.env.MUJOCO_GL ?? "disable" } });
            let stderr = "";
            child.stderr.on("data", (chunk: Buffer) => (stderr += chunk.toString()));
            child.on("error", reject);
            child.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`convert_recording.py exited ${code}: ${stderr.slice(-800)}`))));
          });
        })().finally(() => conversions.delete(key)));
      }
      try {
        await conversions.get(key);
      } catch (error) {
        throw new HttpError(500, "recording_conversion_failed", (error as Error).message, { scenario });
      }
    }
    return JSON.parse(await readFile(out, "utf8"));
  }

  // ------------------------------------------------------------------ endpoint payloads
  function projectSummary(id: string): ProjectSummary {
    const s = spec(id);
    const report = readJson<{ ok: boolean; counts: Json; generated_at?: number }>(build(id, "report", "summary.json"));
    return {
      id, name: s.robot?.name ?? id, path: path.relative(repoRoot, projectDir(id)), description: s.robot?.description?.trim(),
      last_run: report ? { status: report.ok ? "succeeded" : "failed", finished_at: report.generated_at ?? null } : null,
      ok: report?.ok ?? null, counts: (report?.counts ?? {}) as ProjectSummary["counts"],
    };
  }

  function projectDetail(pid: string): ProjectDetail {
    const s = spec(pid);
    const report = readJson<{ ok: boolean; counts: Json; generated_at?: number }>(build(pid, "report", "summary.json"));
    const cad = readJson<{ source?: { url?: string } }>(build(pid, "cad", "cad_model.json"));
    const lockFile = path.join(projectDir(pid), "r3.lock.yaml");
    return {
      id: pid, name: s.robot?.name ?? pid, path: path.relative(repoRoot, projectDir(pid)),
      spec: {
        name: s.robot?.name ?? pid, description: s.robot?.description?.trim(), version: s.robot?.version,
        requirements: (s.requirements ?? {}) as Json, components: Object.keys(s.components ?? {}).length,
        harnesses: s.electrical?.harnesses?.length ?? 0, scenarios: (s.simulation?.scenarios ?? []).map((x) => x.id),
      },
      fingerprint: fingerprint(pid),
      lock: existsSync(lockFile) ? (parseYaml(readFileSync(lockFile, "utf8")) as Json) : null,
      stages: stageStatuses(pid),
      report: report ? { ok: report.ok, counts: report.counts as never, generated_at: report.generated_at } : null,
      release: releaseGate(pid),
      links: { onshape: cad?.source?.url ?? null, github: null },
    };
  }

  function model(pid: string) {
    const graph = readJson<ModelGraph>(build(pid, "model", "graph.json"));
    if (!graph) throw new HttpError(404, "artifact_missing", "build/model/graph.json is missing; run the model stage", { stage: "model" });
    const s = spec(pid);
    const joints: SpecJoint[] = (s.mechanical?.joints ?? []).map((j) => ({
      ...j, origin: vector(j.origin), axis: vector(j.axis),
      limits: Array.isArray(j.limits) ? [quantity(j.limits[0]), quantity(j.limits[1])] : null,
    }));
    const transmissions = (s.mechanical?.transmissions ?? []).map((t) => ({
      ...t, params: Object.fromEntries(Object.entries(t.params ?? {}).map(([k, v]) => [k, quantity(v)])),
    }));
    return {
      graph, assembly: readJson(build(pid, "model", "assembly.json")),
      poses: Object.fromEntries(Object.entries(graph.components).map(([id, c]) => [id, { xyz: c.xyz, rpy: c.rpy }])),
      joints, transmissions,
    };
  }

  function cad(pid: string) {
    const cadModel = readJson<{ source?: { url?: string }; parts: Record<string, { mesh?: string }> }>(build(pid, "cad", "cad_model.json"));
    const meshes: Record<string, string> = {};
    for (const [id, part] of Object.entries(cadModel?.parts ?? {})) {
      if (part.mesh && existsSync(build(pid, "cad", part.mesh))) meshes[id] = artifactUrl(pid, `cad/${part.mesh}`);
    }
    return {
      cad_model: cadModel, meshes, onshape_url: cadModel?.source?.url ?? null,
      views: [...images(pid, "cad"), ...images(pid, "cad/views")],
    };
  }

  function csv(file: string): Record<string, string>[] {
    return existsSync(file) ? parseCsv(readFileSync(file, "utf8")) : [];
  }

  function wiring(pid: string) {
    const routes = readJson<{ clearances?: unknown[] }>(build(pid, "wiring", "routes.json"));
    return {
      routes, routing_detail: readJson(build(pid, "wiring", "routing_detail.json")),
      bom: csv(build(pid, "wiring", "harness_bom.csv")), clearances: routes?.clearances ?? [],
      slack_sim: readJson(build(pid, "wiring", "slack_sim.json")), images: images(pid, "wiring"),
    };
  }

  function electrical(pid: string) {
    const op = readJson<OperatingPointsJson>(build(pid, "electrical", "operating_points.json"));
    const graph = readJson<ModelGraph>(build(pid, "model", "graph.json"));
    return {
      operating_points: op, wires: wireRatings(op), connectors: connectorRatings(op),
      power_tree: powerTree(graph), rails: graph?.rails ?? [], images: images(pid, "electrical"),
    };
  }

  function pcb(pid: string): { boards: PcbBoard[] } {
    const dir = build(pid, "pcb");
    if (!existsSync(dir)) return { boards: [] };
    const cat = catalog();
    const s = spec(pid);
    const pcbDiagnostics = results(pid).pcb?.diagnostics ?? [];
    const boards: PcbBoard[] = [];
    for (const name of readdirSync(dir)) {
      const board = readJson<PcbBoard & { files?: Record<string, string>; renders?: Record<string, string> }>(path.join(dir, name, "board.json"));
      if (!board) continue;
      const toArtifact = (p: string) => artifactUrl(pid, p.replace(/^build\//, ""));
      const catalogType = s.components?.[board.component]?.type;
      const catalogConnectors = (catalogType ? cat.components[catalogType]?.connectors : undefined) ?? {};
      const pinMap: PcbBoard["pin_map"] = [];
      for (const [connector, pins] of Object.entries(board.connectors ?? {})) {
        const expected = catalogConnectors[connector]?.pins ?? {};
        const ids = [...new Set([...Object.keys(pins), ...Object.keys(expected)])].sort((a, b) => Number(a) - Number(b) || a.localeCompare(b));
        for (const pin of ids) {
          const boardNet = pins[pin] ?? null;
          const catalogNet = expected[pin] ?? null;
          pinMap.push({ connector, pin, board_net: boardNet, catalog_net: catalogNet, match: catalogNet === null || boardNet === catalogNet });
        }
      }
      const gerbers = board.files?.gerbers;
      boards.push({
        ...board,
        renders: Object.fromEntries(Object.entries(board.renders ?? {}).map(([k, v]) => [k, toArtifact(v)])),
        gerber_url: gerbers && existsSync(path.join(projectDir(pid), gerbers)) ? toArtifact(gerbers) : null,
        bom: csv(path.join(dir, name, "bom.csv")),
        pin_map: pinMap,
        diagnostics: pcbDiagnostics.filter((d) => d.subjects.includes(board.component) || d.subjects.includes(name) || !d.subjects.length),
      });
    }
    return { boards };
  }

  function software(pid: string) {
    const s = spec(pid);
    const graph = readJson<ModelGraph & { edges?: [] }>(build(pid, "model", "graph.json"));
    const bindings: BindingChain[] = graph ? bindingChains(s.software?.bindings ?? {}, graph) : [];
    const latest = (s.simulation?.scenarios ?? [])
      .map((x) => ({ id: x.id, file: build(pid, "sim", x.id, "summary.json") }))
      .filter((x) => existsSync(x.file))
      .sort((a, b) => statSync(b.file).mtimeMs - statSync(a.file).mtimeMs)[0];
    const summary = latest ? scenarioSummary(pid, latest.id) : null;
    const logFile = latest ? build(pid, "sim", latest.id, "software.log") : "";
    return {
      bindings,
      modules: (s.software?.modules ?? []).map((m) => {
        const dir = path.join(projectDir(pid), "backend", m.language ?? "python", m.name);
        return { ...m, path: path.relative(repoRoot, dir), files: existsSync(dir) ? readdirSync(dir).filter((f) => !f.startsWith("__")).sort() : [] };
      }),
      firmware_logs: summary?.firmware_logs ?? [],
      logs_source: latest ? `sim/${latest.id}/summary.json` : null,
      software_log: logFile && existsSync(logFile) ? readFileSync(logFile, "utf8") : null,
    };
  }

  function scenarios(pid: string): { scenarios: ScenarioListItem[] } {
    const current = fingerprint(pid);
    return {
      scenarios: (spec(pid).simulation?.scenarios ?? []).map((x) => {
        const summary = scenarioSummary(pid, x.id);
        return {
          id: x.id, description: x.description, duration: quantity(x.duration), expect: x.expect,
          last: summary ? { passed: summary.passed, checks: summary.checks, metrics: summary.metrics,
                            stale: !!summary.inputs?.robot_yaml_sha256 && summary.inputs.robot_yaml_sha256 !== current } : null,
        };
      }),
    };
  }

  async function scenario(pid: string, id: string, hz: number) {
    const summary = scenarioSummary(pid, id);
    if (!summary) throw new HttpError(404, "scenario_not_run", `scenario ${id} has no results; run it first`, { scenario: id });
    const rendersDir = build(pid, "sim", id, "renders");
    const files = existsSync(rendersDir) ? readdirSync(rendersDir).sort() : [];
    const { firmware_logs: _logs, ...rest } = summary;
    void _logs;
    return {
      summary: rest,
      recording: await recording(pid, id, hz),
      renders: [...files.filter((f) => f.endsWith(".png")).map((f) => ({ name: f, url: artifactUrl(pid, `sim/${id}/renders/${f}`) })),
                ...images(pid, `sim/${id}`)],
      clip_url: files.includes("clip.mp4") ? artifactUrl(pid, `sim/${id}/renders/clip.mp4`) : null,
    };
  }

  // ------------------------------------------------------------------ spec patch (memory only)
  const isPlain = (v: unknown): v is Json => !!v && typeof v === "object" && !Array.isArray(v);
  const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

  /** Set `next` at `path`, touching only nodes that change, so comments and flow style survive (like
   * the real API's ruamel round-trip). Lists are replaced per element, which equals RFC 7386 replacement. */
  function assign(doc: Document, path: (string | number)[], current: unknown, next: unknown): void {
    if (same(current, next)) return;
    if (isPlain(current) && isPlain(next)) {
      for (const key of Object.keys(current)) if (!(key in next)) doc.deleteIn([...path, key]);
      for (const [key, value] of Object.entries(next)) assign(doc, [...path, key], current[key], value);
    } else if (Array.isArray(current) && Array.isArray(next) && current.length === next.length) {
      next.forEach((value, i) => assign(doc, [...path, i], current[i], value));
    } else {
      doc.setIn(path, next);
    }
  }

  /** RFC 7386 JSON merge patch applied to a YAML document. */
  function mergeInto(doc: Document, path: (string | number)[], current: unknown, patch: Json): void {
    for (const [key, value] of Object.entries(patch)) {
      const existing = isPlain(current) ? current[key] : undefined;
      if (value === null) doc.deleteIn([...path, key]);
      else if (isPlain(value) && isPlain(existing)) mergeInto(doc, [...path, key], existing, value);
      else assign(doc, [...path, key], existing, value);
    }
  }

  function patchSpec(pid: string, body: Json) {
    const text = specText(pid);
    const current = sha256(text);
    if (!body.patch || typeof body.patch !== "object" || Array.isArray(body.patch)) {
      throw new HttpError(400, "patch_invalid", "a merge patch must be a JSON object");
    }
    if (body.sha256 !== current) {
      throw new HttpError(409, "spec_conflict", "robot.yaml changed since it was read; re-read it and re-apply the patch",
                          { expected_sha256: body.sha256 as string, current_sha256: current });
    }
    const doc = parseDocument(text);
    mergeInto(doc, [], doc.toJS(), body.patch as Json);
    const next = doc.toString({ lineWidth: 0 });
    specOverlay.set(pid, next);
    return { ok: true, sha256: sha256(next), previous_sha256: current, changed: next !== text, graph_diagnostics: [], mock_in_memory: true };
  }

  // ------------------------------------------------------------------ runs (replayed from existing results)
  function startRun(pid: string, body: Json): { run_id: string } {
    const all = results(pid);
    const requested = Array.isArray(body.stages) && body.stages.length ? (body.stages as string[]) : STAGES.filter((s) => all[s]);
    const run_id = `mock-${randomUUID().slice(0, 8)}`;
    const run = { run_id, project: pid, status: "queued" as RunInfo["status"], stages: requested, scenario: (body.scenario as string) ?? null,
                  created_at: Date.now() / 1000, started_at: null as number | null, finished_at: null as number | null, current_stage: null as string | null,
                  events: [] as RunEvent[], done: false, waiters: new Set<() => void>() };
    runs.set(run_id, run);
    const emit = (event: RunEvent) => {
      run.events.push({ run_id, t: Date.now() / 1000, ...event });
      for (const w of run.waiters) w();
    };
    void (async () => {
      await sleep(delay);
      run.status = "running";
      run.started_at = Date.now() / 1000;
      let ok = true;
      for (const [index, stage] of requested.entries()) {
        run.current_stage = stage;
        emit({ type: "stage_started", stage });
        emit({ type: "log", stage, level: "info", message: `mock API: replaying build/${stage}/result.json (no stage executed; start r3 serve to run it)` });
        await sleep(delay);
        const result = all[stage];
        for (const d of (result?.diagnostics ?? []).filter((x) => x.severity === "error").slice(0, 3)) emit({ type: "diagnostic", stage, diagnostic: d });
        emit({ type: "progress", stage, fraction: (index + 1) / requested.length });
        emit({ type: "stage_finished", stage, ok: result?.ok ?? false, ...(result ? {} : { message: "no result.json" }) });
        ok &&= result?.ok ?? false;
        await sleep(delay / 2);
      }
      run.status = ok ? "succeeded" : "failed";
      run.finished_at = Date.now() / 1000;
      run.current_stage = null;
      emit({ type: "run_finished", status: run.status, ok });
      run.done = true;
      for (const w of run.waiters) w();
    })();
    return { run_id };
  }

  // ------------------------------------------------------------------ agent (scripted transcript, clearly marked)
  function startAgent(pid: string, body: Json): { session_id: string } {
    const prompt = String(body.prompt ?? "").trim();
    if (!prompt) throw new HttpError(400, "prompt_required", "prompt must not be empty");
    const provider = body.provider === "codex" ? "codex" : "claude";
    const session_id = `mock-agent-${randomUUID().slice(0, 8)}`;
    const session = { pid, events: [] as AgentEvent[], done: false, cancelled: false, waiters: new Set<() => void>() };
    sessions.set(session_id, session);
    const emit = (event: AgentEvent) => {
      session.events.push({ session_id, t: Date.now() / 1000, mock: true, ...event });
      for (const w of session.waiters) w();
    };
    void (async () => {
      const step = async (event: AgentEvent) => {
        if (session.cancelled) return false;
        await sleep(delay);
        emit(event);
        return true;
      };
      const command = provider === "claude" ? "claude -p --output-format stream-json" : "codex exec --json";
      await step({ type: "session_started", provider, model: body.model ?? null, command, cwd: path.relative(repoRoot, projectDir(pid)) });
      await step({ type: "message", role: "assistant",
                   text: `Mock agent (no r3 serve): the real API runs \`${command}\` headlessly with the R3 MCP server. ` +
                         "This scripted session reads the spec and applies one illustrative in-memory patch so the live diff can be reviewed." });
      const doc = { sha256: sha256(specText(pid)) };
      await step({ type: "tool_call", tool: "r3_get_spec", input: { section: "electrical.harnesses" } });
      await step({ type: "tool_result", tool: "r3_get_spec", output: { sha256: doc.sha256, harnesses: spec(pid).electrical?.harnesses?.length ?? 0 } });
      const harnesses = spec(pid).electrical?.harnesses ?? [];
      const lead = harnesses.find((h) => h.id === "battery_lead") ?? harnesses[0];
      if (lead && !session.cancelled) {
        const patched = harnesses.map((h) => (h.id === lead.id ? { ...h, route: { ...(h.route ?? {}), slack: "10 mm" } } : h));
        const patch = { electrical: { harnesses: patched } };
        await step({ type: "tool_call", tool: "r3_patch_spec", input: { patch: { [`electrical.harnesses[id=${lead.id}].route.slack`]: "10 mm" }, sha256: doc.sha256 } });
        const result = patchSpec(pid, { patch, sha256: doc.sha256 });
        await step({ type: "tool_result", tool: "r3_patch_spec", output: result });
        await step({ type: "spec_changed", sha256: result.sha256 });
        const { run_id } = startRun(pid, { stages: ["wiring", "electrical"] });
        await step({ type: "tool_call", tool: "r3_run", input: { stages: ["wiring", "electrical"] } });
        await step({ type: "run_started", run_id });
        const run = runs.get(run_id)!;
        while (!run.done && !session.cancelled) await sleep(delay);
        await step({ type: "run_finished", run_id, status: run.status });
        await step({ type: "message", role: "assistant",
                     text: `Set ${lead.id} route slack to 10 mm and re-ran wiring + electrical (mock replay of existing results). ` +
                           "With the real backend the stages re-run and their diagnostics update here." });
      }
      if (session.cancelled) emit({ type: "session_finished", status: "cancelled" });
      else emit({ type: "session_finished", status: "completed" });
      session.done = true;
      for (const w of session.waiters) w();
    })();
    return { session_id };
  }

  // ------------------------------------------------------------------ HTTP plumbing
  function send(res: ServerResponse, status: number, body: unknown): void {
    const text = JSON.stringify(body);
    res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store", "X-R3-Mock": "1" });
    res.end(text);
  }

  async function body(req: IncomingMessage): Promise<Json> {
    const chunks: Buffer[] = [];
    for await (const chunk of req) chunks.push(chunk as Buffer);
    if (!chunks.length) return {};
    try {
      return JSON.parse(Buffer.concat(chunks).toString("utf8")) as Json;
    } catch {
      throw new HttpError(400, "invalid_json", "request body is not JSON");
    }
  }

  async function stream<T>(req: IncomingMessage, res: ServerResponse, source: { events: T[]; done: boolean; waiters: Set<() => void> },
                           name: (e: T) => string): Promise<void> {
    res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-store", Connection: "keep-alive", "X-R3-Mock": "1" });
    let sent = 0;
    let closed = false;
    req.on("close", () => (closed = true));
    while (!closed) {
      while (sent < source.events.length) {
        const event = source.events[sent++]!;
        res.write(`event: ${name(event)}\ndata: ${JSON.stringify(event)}\n\n`);
      }
      if (source.done) break;
      await new Promise<void>((resolve) => {
        const wake = () => { source.waiters.delete(wake); resolve(); };
        source.waiters.add(wake);
        setTimeout(wake, 15000);
      });
      if (!closed && sent >= source.events.length && !source.done) res.write(": keep-alive\n\n");
    }
    res.end();
  }

  async function artifact(req: IncomingMessage, res: ServerResponse, pid: string, rel: string): Promise<void> {
    const root = path.join(projectDir(pid), "build");
    const full = path.resolve(root, rel);
    if (!full.startsWith(root + path.sep)) throw new HttpError(400, "path_invalid", "artifact path escapes build/");
    const info = await stat(full).catch(() => null);
    if (!info?.isFile()) throw new HttpError(404, "artifact_missing", `build/${rel} does not exist`, { path: rel });
    const type = MIME[path.extname(full).toLowerCase()] ?? "application/octet-stream";
    const range = req.headers.range?.match(/bytes=(\d*)-(\d*)/);
    if (range) {
      const start = range[1] ? Number(range[1]) : 0;
      const end = range[2] ? Math.min(Number(range[2]), info.size - 1) : info.size - 1;
      res.writeHead(206, { "Content-Type": type, "Content-Range": `bytes ${start}-${end}/${info.size}`, "Accept-Ranges": "bytes",
                           "Content-Length": end - start + 1, "X-R3-Mock": "1" });
      createReadStream(full, { start, end }).pipe(res);
      return;
    }
    res.writeHead(200, { "Content-Type": type, "Content-Length": info.size, "Accept-Ranges": "bytes", "Cache-Control": "no-cache", "X-R3-Mock": "1" });
    createReadStream(full).pipe(res);
  }

  async function handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const url = new URL(req.url ?? "/", "http://mock");
    const parts = url.pathname.replace(/^\/api\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
    const method = req.method ?? "GET";
    const [head, pid, section, ...rest] = parts;

    if (head === "doctor" && method === "GET") return send(res, 200, doctor());
    if (head === "projects" && !pid) {
      if (method === "GET") return send(res, 200, { projects: projects().map((p) => projectSummary(p.id)) });
      if (method === "POST") {
        const b = await body(req);
        throw new HttpError(501, "mock_read_only", `the mock API cannot scaffold "${String(b.name ?? "")}"; start r3 serve to create projects`);
      }
    }
    if (head === "projects" && pid) {
      if (!section && method === "GET") return send(res, 200, projectDetail(pid));
      if (section === "spec" && method === "GET") {
        const text = specText(pid);
        return send(res, 200, { yaml: text, sha256: sha256(text), spec: parseYaml(text) });
      }
      if (section === "spec" && method === "PATCH") return send(res, 200, patchSpec(pid, await body(req)));
      if (section === "catalog") return send(res, 200, catalog());
      if (section === "model") return send(res, 200, model(pid));
      if (section === "cad") return send(res, 200, cad(pid));
      if (section === "wiring") return send(res, 200, wiring(pid));
      if (section === "electrical") return send(res, 200, electrical(pid));
      if (section === "pcb") return send(res, 200, pcb(pid));
      if (section === "software") return send(res, 200, software(pid));
      if (section === "sim" && !rest.length) return send(res, 200, scenarios(pid));
      if (section === "sim" && rest[0]) return send(res, 200, await scenario(pid, rest[0], Math.max(1, Math.min(120, Number(url.searchParams.get("hz") ?? 30)))));
      if (section === "diagnostics") return send(res, 200, { diagnostics: allDiagnostics(pid) });
      if (section === "artifacts" && rest.length) return artifact(req, res, pid, rest.join("/"));
      if (section === "runs" && method === "POST") return send(res, 202, startRun(pid, await body(req)));
      if (section === "deploy" && method === "POST") {
        const b = await body(req);
        if (b.target !== "sim" && b.target !== "fleet") throw new HttpError(400, "target_invalid", "target must be sim or fleet");
        if (b.target === "fleet" && b.confirm !== true) {
          throw new HttpError(409, "confirmation_required", "deploying to the fleet changes real robots; resend with confirm: true", { target: "fleet" });
        }
        const { run_id } = startRun(pid, { stages: ["software"] });
        return send(res, 202, { ok: true, target: b.target, run_id,
                                message: `mock API: no deployment performed (r3 serve would run the BLITZ ${String(b.target)} deploy)` });
      }
      if (section === "agent" && method === "POST") return send(res, 202, startAgent(pid, await body(req)));
    }
    if (head === "runs" && pid) {
      const run = runs.get(pid);
      if (!run) throw new HttpError(404, "run_not_found", `no run ${pid}`);
      if (!section) {
        const { events: _e, waiters: _w, done: _d, ...info } = run;
        void _e; void _w; void _d;
        return send(res, 200, info);
      }
      if (section === "events") return stream(req, res, run, (e) => e.type);
    }
    if (head === "agent" && pid) {
      const session = sessions.get(pid);
      if (!session) throw new HttpError(404, "session_not_found", `no agent session ${pid}`);
      if (section === "events") return stream(req, res, session, (e) => e.type);
      if (section === "cancel" && method === "POST") {
        session.cancelled = true;
        return send(res, 200, { ok: true });
      }
    }
    throw new HttpError(404, "not_found", `${method} ${url.pathname} is not part of the API`);
  }

  function doctor() {
    const stages = ["model", "cad", "pcb", "wiring", "electrical", "software", "sim", "report"];
    return {
      ok: true, mock: true,
      environment: { python: "n/a (mock)", platform: `${process.platform} ${process.arch}`, display: false, worker_image: false, ci: !!process.env.CI },
      tools: {
        "r3-api": { name: "r3-api", available: false, version: null, detail: "r3 serve is not reachable; the dev server answers from build/ artifacts", data: {} },
        "recording-converter": { name: "recording-converter", available: existsSync(python()) || python() === "python3", version: null,
                                 detail: `${python()} apps/web/scripts/convert_recording.py (MuJoCo forward kinematics)`, data: {} },
      },
      stages: stages.map((stage) => ({ stage, headless: true, level: "degraded", missing: ["r3 serve"],
                                       note: "Mock API: shows the existing build/ results; runs are replayed, not executed." })),
      agents: { claude: { available: false, detail: "needs r3 serve" }, codex: { available: false, detail: "needs r3 serve" } },
    };
  }

  /** Connect-style middleware for Vite's dev and preview servers. */
  return function mockApi(req: IncomingMessage, res: ServerResponse, next: Next): void {
    if (!req.url?.startsWith("/api/") && req.url !== "/api") return next();
    handle(req, res).catch((error: unknown) => {
      if (res.headersSent) return res.end();
      if (error instanceof HttpError) return send(res, error.status, { code: error.code, message: error.message, data: error.data });
      send(res, 500, { code: "mock_error", message: (error as Error).message ?? String(error), data: {} });
    });
  };
}

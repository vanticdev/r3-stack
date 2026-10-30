// Dev/preview mock of the R3 API (docs/architecture/api.md), serving REAL data read from each
// project's build/ artifacts in the same shapes as `r3 serve` (api/projects.py). Mutations never touch
// the repository: spec patches live in memory, runs replay the existing stage results, and the agent is
// a scripted, clearly labelled session. Every response carries `X-R3-Mock: 1` so the UI can say so.
// The release gate here is an approximation of r3_orchestrator/release.py for development only.
import { spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { createReadStream, existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { mkdir, readFile, stat } from "node:fs/promises";
import type { IncomingMessage, ServerResponse } from "node:http";
import path from "node:path";

import { Document, isMap, isScalar, isSeq, parse as parseYaml, parseDocument, stringify as stringifyYaml, type Node as YamlNode } from "yaml";

import type {
  AgentEvent, Diagnostic, Evidence, FileLink, ModelGraph, OperatingPointsJson, PcbBoard, ProjectDetail, ProjectSummary, Release,
  ReleaseItem, RobotSpec, RunEvent, RunInfo, ScenarioLast, StageResult, StageRow,
} from "../src/api/types.ts";
import { STAGES } from "../src/api/types.ts";
import { bindingPinMaps, evidenceCounts, fabricationReady, itemStatus, parseCsv, severityCounts, unifiedDiff } from "../src/lib/domain.ts";
import { quantity, vector } from "../src/lib/units.ts";

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
const IMAGE = /\.(png|jpe?g|svg|gif)$/i;

const sha256 = (text: string | Buffer) => createHash("sha256").update(text).digest("hex");
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const isPlain = (v: unknown): v is Json => !!v && typeof v === "object" && !Array.isArray(v);
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** csv.DictReader rows with numbers parsed, as api/projects.py `_csv_rows`. */
function csvRows(file: string): Record<string, string | number>[] | null {
  if (!existsSync(file)) return null;
  return parseCsv(readFileSync(file, "utf8")).map((row) => Object.fromEntries(Object.entries(row).map(([k, v]) => [
    k, /^-?\d+$/.test(v) ? Number(v) : /^-?\d+\.\d*(e-?\d+)?$/.test(v) ? Number(v) : v,
  ])));
}

export function createMockApi(options: MockOptions) {
  const repoRoot = path.resolve(options.repoRoot);
  const roots = (options.projectRoots ?? [path.join(repoRoot, "examples", "robots")]).map((r) => path.resolve(repoRoot, r));
  const delay = options.eventDelay ?? 250;
  const jsonCache = new Map<string, { mtime: number; value: unknown }>();
  const specOverlay = new Map<string, string>(); // pid -> patched robot.yaml text (memory only)
  type Stream<T> = { events: T[]; done: boolean; waiters: Set<() => void> };
  type MockRun = Stream<RunEvent> & { info: Omit<RunInfo, "events"> };
  const runs = new Map<string, MockRun>();
  const sessions = new Map<string, { pid: string; status: string; cancelled: boolean; info: Json } & Stream<AgentEvent>>();
  const conversions = new Map<string, Promise<unknown>>();

  // ------------------------------------------------------------------ file helpers
  function projects(): { id: string; dir: string; root: string }[] {
    const out: { id: string; dir: string; root: string }[] = [];
    for (const root of roots) {
      if (!existsSync(root)) continue;
      for (const entry of readdirSync(root, { withFileTypes: true })) {
        if (entry.isDirectory() && existsSync(path.join(root, entry.name, "robot.yaml"))) out.push({ id: entry.name, dir: path.join(root, entry.name), root });
      }
    }
    return out.sort((a, b) => a.id.localeCompare(b.id));
  }

  function projectDir(pid: string): string {
    const found = projects().find((p) => p.id === pid);
    if (!found) throw new HttpError(404, "project_not_found", `no project ${pid}`, { project: pid });
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
    // Python's json.dumps writes NaN/Infinity for non-finite floats; the API maps them to null.
    const value = JSON.parse(readFileSync(file, "utf8").replace(/\bNaN\b|-?\bInfinity\b/g, "null")) as T;
    jsonCache.set(file, { mtime, value });
    return value;
  }

  const build = (pid: string, ...parts: string[]) => path.join(projectDir(pid), "build", ...parts);
  const artifactUrl = (pid: string, rel: string) => `/api/projects/${encodeURIComponent(pid)}/artifacts/${rel.split("/").map(encodeURIComponent).join("/")}`;
  const link = (pid: string, rel: string): FileLink => ({ path: rel, url: artifactUrl(pid, rel), name: path.basename(rel).replace(/\.[^.]+$/, "").replace(/_/g, " ") });

  function files(pid: string, dir: string, pattern: RegExp, recursive = true): string[] {
    const root = build(pid);
    const out: string[] = [];
    const walk = (rel: string) => {
      const full = path.join(root, rel);
      if (!existsSync(full)) return;
      for (const entry of readdirSync(full, { withFileTypes: true })) {
        const child = rel ? `${rel}/${entry.name}` : entry.name;
        if (entry.isDirectory()) { if (recursive) walk(child); } else if (pattern.test(entry.name)) out.push(child);
      }
    };
    walk(dir);
    return out.sort();
  }

  function specText(pid: string): string {
    return specOverlay.get(pid) ?? readFileSync(path.join(projectDir(pid), "robot.yaml"), "utf8");
  }
  const spec = (pid: string) => parseYaml(specText(pid)) as RobotSpec;
  const fingerprint = (pid: string) => ({ robot_yaml_sha256: sha256(specText(pid)).slice(0, 16) });
  const isStale = (inputs: Json | null | undefined, current: { robot_yaml_sha256: string }) =>
    !!inputs?.robot_yaml_sha256 && inputs.robot_yaml_sha256 !== current.robot_yaml_sha256;

  /** build/<stage>/result.json with status, stale, hints and artifact URLs (api/projects.py `_stage_block`). */
  function result(pid: string, stage: string): StageResult | null {
    const data = readJson<StageResult>(build(pid, stage, "result.json"));
    if (!data) return null;
    const unavailable = (data.diagnostics ?? []).some((d) => d.code === "stage_unavailable");
    return {
      ...data,
      status: unavailable ? "unavailable" : data.ok ? "ok" : "failed",
      stale: isStale(data.inputs, fingerprint(pid)),
      diagnostics: (data.diagnostics ?? []).map((d) => ({ ...d, stage: d.stage ?? stage, data: d.data ?? {}, subjects: d.subjects ?? [], hint: d.hint ?? "" })),
      artifacts: (data.artifacts ?? []).map((a) => ({ ...a, url: artifactUrl(pid, a.path) })),
    };
  }

  function results(pid: string): Record<string, StageResult> {
    const out: Record<string, StageResult> = {};
    for (const stage of STAGES) { const r = result(pid, stage); if (r) out[stage] = r; }
    return out;
  }

  function stageRows(pid: string): StageRow[] {
    const all = results(pid);
    return STAGES.filter((s) => s !== "report").map((stage) => {
      const r = all[stage];
      if (!r) return { stage, status: "missing", ok: null, stale: null, counts: {} };
      return { stage, status: r.status ?? (r.ok ? "ok" : "failed"), ok: r.ok, stale: r.stale ?? false, counts: severityCounts(r.diagnostics),
               duration_s: r.duration_s ?? null, started_at: r.started_at ?? null, metrics: r.metrics, artifacts: r.artifacts };
    });
  }

  function allDiagnostics(pid: string): Diagnostic[] {
    const seen = new Set<string>();
    const out: Diagnostic[] = [];
    for (const [stage, r] of Object.entries(results(pid))) {
      if (stage === "report") continue; // the report re-lists every other stage's diagnostics
      for (const d of r.diagnostics) {
        const key = `${d.stage}|${d.code}|${d.subjects.join(",")}|${d.message}`;
        if (!seen.has(key)) { seen.add(key); out.push(d); }
      }
    }
    return out;
  }

  // ------------------------------------------------------------------ release gate (approximates release.py)
  function release(pid: string): Release {
    const s = spec(pid);
    const current = fingerprint(pid);
    const all = results(pid);
    const graph = readJson<ModelGraph>(build(pid, "model", "graph.json"));
    const op = readJson<OperatingPointsJson>(build(pid, "electrical", "operating_points.json"));
    const items: ReleaseItem[] = [];
    const evidence = (stage: string, check: string, value: unknown, limit: unknown, passed: boolean | null, artifact: string | null,
                      inputs: Json | null | undefined, extra: Partial<Evidence> = {}): Evidence => ({
      stage, check, value, limit, passed, artifact, inputs: (inputs ?? {}) as Record<string, string>,
      estimated: false, stale: isStale(inputs, current), detail: "", ...extra,
    });
    const item = (id: string, kind: ReleaseItem["kind"], subject: string, target: unknown, ev: Evidence[], message = ""): void => {
      items.push({ id, kind, subject, target, evidence: ev, status: itemStatus(ev), message });
    };
    const summaryOf = (scenario: string) => readJson<{ checks?: { key: string; expected: unknown; measured: unknown; pass: boolean; detail?: string }[];
                                                       metrics?: Json; inputs?: Json }>(build(pid, "sim", scenario, "summary.json"));
    const check = (scenario: string, key: string) => {
      const summary = summaryOf(scenario);
      const c = summary?.checks?.find((x) => x.key === key);
      return c ? evidence("sim", `${scenario}.${key}`, c.measured, c.expected, c.pass, `sim/${scenario}/summary.json`, summary?.inputs, { detail: c.detail ?? "" }) : null;
    };
    for (const [key, raw] of Object.entries(s.requirements ?? {})) {
      const target = quantity(raw);
      const ev: Evidence[] = [];
      let message = "";
      switch (key) {
        case "max_mass":
          if (graph?.mass && all.model) {
            const estimated = (graph.mass.estimated_fraction ?? 0) > 0;
            ev.push(evidence("model", "total_mass", graph.mass.total, target, graph.mass.total <= target, "model/graph.json", all.model.inputs, { estimated }));
            if (estimated) message = `${Math.round((graph.mass.estimated_fraction ?? 0) * 100)}% of the mass is estimated`;
          }
          break;
        case "top_speed": {
          const c = check("full_throttle", "min_top_speed_mps");
          if (c) { ev.push(c); message = c.detail; }
          break;
        }
        case "min_runtime":
          if (op?.runtime && all.electrical) ev.push(evidence("electrical", "runtime", op.runtime.runtime, target, op.runtime.runtime >= target, "electrical/operating_points.json", all.electrical.inputs));
          break;
        case "radio_range": { const c = check("range_failsafe", "link_lost_beyond_m"); if (c) ev.push(c); break; }
        case "failsafe_stop_distance": { const c = check("range_failsafe", "stop_within_m_of_link_loss"); if (c) ev.push(c); break; }
        case "max_wire_voltage_drop_fraction":
          if (all.electrical) {
            const exceeded = all.electrical.diagnostics.filter((d) => d.code === "voltage_drop_exceeded");
            const worst = Math.max(0, ...exceeded.map((d) => Number(d.data.fraction ?? 0)));
            ev.push(evidence("electrical", "voltage_drop_fraction", exceeded.length ? worst : null, target, exceeded.length === 0, "electrical/operating_points.json", all.electrical.inputs));
            if (exceeded.length) message = `${exceeded.length} harness(es) exceed the limit`;
          }
          break;
        case "min_wire_clearance_moving": {
          const moving = all.wiring?.metrics?.wire_min_clearance_moving as Record<string, number> | undefined;
          if (moving && all.wiring) {
            const worst = Math.min(...Object.values(moving));
            ev.push(evidence("wiring", "wire_min_clearance_moving", worst, target, worst >= target, "wiring/routes.json", all.wiring.inputs));
          }
          for (const scenario of s.simulation?.scenarios ?? []) {
            const c = check(scenario.id, "min_wire_clearance_m");
            if (c) ev.push(c);
          }
          break;
        }
        case "max_component_temperature_c": {
          const temps = Object.entries(op?.thermal ?? {}).map(([id, t]) => [id, t.temperature_c ?? t.steady_temperature_c] as const)
            .filter((x): x is readonly [string, number] => typeof x[1] === "number").sort((a, b) => b[1] - a[1]);
          if (temps[0] && all.electrical) {
            ev.push(evidence("electrical", `temperature_c.${temps[0][0]}`, temps[0][1], target, temps[0][1] <= target, "electrical/operating_points.json", all.electrical.inputs));
            message = `hottest component ${temps[0][0]}`;
          }
          break;
        }
        default:
          message = "no checker for this requirement";
      }
      item(`requirement.${key}`, "requirement", key, Number.isFinite(target) ? target : raw, ev, message);
    }
    for (const scenario of s.simulation?.scenarios ?? []) {
      for (const [key, expected] of Object.entries(scenario.expect ?? {})) {
        const c = check(scenario.id, key);
        item(`scenario.${scenario.id}.${key}`, "scenario_expectation", scenario.id, expected, c ? [c] : [], c?.detail ?? "");
      }
    }
    for (const stage of STAGES.filter((x) => x !== "report")) {
      const r = all[stage];
      const errors = r ? r.diagnostics.filter((d) => d.severity === "error").length : null;
      const estimated = !!r?.diagnostics.some((d) => d.code.endsWith("_estimated"));
      item(`stage.${stage}`, "stage", stage, "no errors",
           r ? [evidence(stage, "errors", errors, 0, errors === 0, `${stage}/result.json`, r.inputs, { estimated })] : [],
           r ? "" : "stage has not run");
    }
    return {
      schema: "r3/release@1", fabrication_ready: fabricationReady(items), counts: evidenceCounts(items),
      blocking: items.filter((i) => i.status !== "verified").map((i) => i.id), inputs: current, items,
    };
  }

  // ------------------------------------------------------------------ catalog (raw YAML -> api.list_catalog rows)
  function catalogData() {
    const dataDir = path.join(repoRoot, "packages", "component-catalog", "r3_catalog", "data");
    const components: Json[] = [];
    const dir = path.join(dataDir, "components");
    for (const file of existsSync(dir) ? readdirSync(dir).filter((f) => f.endsWith(".yaml")) : []) {
      components.push(...((parseYaml(readFileSync(path.join(dir, file), "utf8")) as { components?: Json[] }).components ?? []));
    }
    const list = (file: string, key: string): Json[] => {
      const full = path.join(dataDir, file);
      return existsSync(full) ? ((parseYaml(readFileSync(full, "utf8")) as Record<string, Json[]>)[key] ?? []) : [];
    };
    return { components, connectors: list("connectors.yaml", "connectors"), wires: list("wires.yaml", "wires") };
  }

  function catalog() {
    const raw = catalogData();
    return {
      ok: true,
      components: raw.components.map((c) => {
        const mech = (c.mechanical ?? {}) as Json;
        return {
          id: c.id, kind: c.kind, name: c.name, part_number: c.part_number ?? "", mass: quantity(mech.mass),
          size: Array.isArray(mech.size) ? mech.size.map(quantity) : null, shape: mech.shape ?? null,
          connectors: Object.fromEntries(Object.entries((c.connectors ?? {}) as Record<string, Json>).map(([k, v]) => [k, v.type])),
          pins: Object.fromEntries(Object.entries((c.pins ?? {}) as Record<string, Json>).map(([k, v]) => [k, v.role])),
          behavior: ((c.behavior ?? null) as Json | null)?.model ?? null,
          provenance: ((mech.provenance ?? c.provenance ?? null) as Json | null)?.status ?? "datasheet",
        };
      }),
      connectors: raw.connectors.map((c) => ({ id: c.id, family: c.family, gender: c.gender, mates_with: c.mates_with, pin_count: c.pin_count,
                                               current_rating_per_pin: c.current_rating_per_pin, awg: [c.awg_min, c.awg_max] })),
      wires: raw.wires.map((w) => ({ awg: w.awg, ampacity: w.ampacity, resistance_per_m: quantity(w.resistance_per_m), outer_diameter: quantity(w.outer_diameter) })),
    };
  }

  // ------------------------------------------------------------------ recording conversion (scripts/convert_recording.py)
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

  async function recording(pid: string, scenario: string, hz: number, cables: boolean): Promise<Json | null> {
    const npz = build(pid, "sim", scenario, "recording.npz");
    if (!existsSync(npz) || !existsSync(build(pid, "sim", scenario, "vehicle.xml"))) return null;
    const out = path.join(options.cacheDir, pid, `${scenario}@${hz}hz.json`);
    const script = path.join(repoRoot, "apps", "web", "scripts", "convert_recording.py");
    // Re-convert when the recording or the converter changed.
    const newest = Math.max(statSync(npz).mtimeMs, existsSync(script) ? statSync(script).mtimeMs : 0);
    if (!(existsSync(out) && statSync(out).mtimeMs >= newest)) {
      if (!conversions.has(out)) {
        conversions.set(out, (async () => {
          await mkdir(path.dirname(out), { recursive: true });
          await new Promise<void>((resolve, reject) => {
            const child = spawn(python(), [script, projectDir(pid), scenario, "--hz", String(hz), "--out", out],
                                { cwd: repoRoot, env: { ...process.env, PYTHONPATH: pythonPath(), MUJOCO_GL: process.env.MUJOCO_GL ?? "disable" } });
            let stderr = "";
            child.stderr.on("data", (chunk: Buffer) => (stderr += chunk.toString()));
            child.on("error", reject);
            child.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`convert_recording.py exited ${code}: ${stderr.slice(-800)}`))));
          });
        })().finally(() => conversions.delete(out)));
      }
      try {
        await conversions.get(out);
      } catch (error) {
        throw new HttpError(500, "internal_error", (error as Error).message, { scenario });
      }
    }
    const data = JSON.parse(await readFile(out, "utf8")) as Json & { frames?: Json | null };
    if (!cables && data.frames) data.frames = { ...data.frames, cables: null };
    return data;
  }

  // ------------------------------------------------------------------ views
  function summaryEntry(p: { id: string; dir: string; root: string }): ProjectSummary {
    const s = spec(p.id);
    const all = results(p.id);
    const stages = Object.fromEntries(STAGES.filter((x) => x !== "report").map((x) => [x, all[x]?.status ?? "missing"]));
    const list = Object.values(all);
    const failed = Object.entries(stages).filter(([, v]) => v === "failed").map(([k]) => k);
    return {
      id: p.id, name: s.robot?.name ?? p.id, path: p.dir, description: s.robot?.description ?? "", version: s.robot?.version ?? "",
      robot_yaml_sha256: fingerprint(p.id).robot_yaml_sha256, stages,
      last_run: list.length ? {
        status: failed.length ? "failed" : "ok", finished_at: Math.max(...list.map((r) => (r.started_at ?? 0) + (r.duration_s ?? 0))),
        failed_stages: failed, stale: list.some((r) => r.stale), counts: severityCounts(list.flatMap((r) => (r.stage === "report" ? [] : r.diagnostics))),
      } : null,
      onshape_url: readJson<{ source?: { url?: string } }>(build(p.id, "cad", "cad_model.json"))?.source?.url ?? null, error: null,
    };
  }

  function detail(pid: string): ProjectDetail {
    const s = spec(pid);
    const lockFile = path.join(projectDir(pid), "r3.lock.yaml");
    const lock = existsSync(lockFile) ? ((parseYaml(readFileSync(lockFile, "utf8")) as Json | null) ?? {}) : {};
    const cad = readJson<{ source?: { url?: string } }>(build(pid, "cad", "cad_model.json"));
    return {
      id: pid, project: projectDir(pid), robot: s.robot, fingerprint: fingerprint(pid), inputs: fingerprint(pid), git: null, lock,
      summary: { components: Object.keys(s.components ?? {}).length, harnesses: s.electrical?.harnesses?.length ?? 0,
                 wires: (s.electrical?.harnesses ?? []).reduce((n, h) => n + h.wires.length, 0),
                 bindings: Object.keys(s.software?.bindings ?? {}).length, scenarios: (s.simulation?.scenarios ?? []).map((x) => x.id) },
      stages: stageRows(pid), results: results(pid),
      report: readJson(build(pid, "report", "summary.json")),
      report_url: existsSync(build(pid, "report", "index.html")) ? artifactUrl(pid, "report/index.html") : null,
      release: release(pid),
      links: { onshape: ((lock.cad as Json | undefined)?.url as string | undefined) ?? cad?.source?.url ?? null, github: null },
    };
  }

  function model(pid: string) {
    const s = spec(pid);
    const graph = readJson<ModelGraph>(build(pid, "model", "graph.json"));
    const assembly = readJson(build(pid, "model", "assembly.json"));
    const rawCatalog = catalogData().components;
    const components = Object.fromEntries(Object.entries(s.components ?? {}).map(([id, c]) => {
      const entry = rawCatalog.find((x) => x.id === c.type);
      const mech = (entry?.mechanical ?? {}) as Json;
      return [id, { type: c.type, pose: { xyz: vector(c.pose?.xyz ?? [0, 0, 0]), rpy: vector(c.pose?.rpy ?? [0, 0, 0]) }, mount: c.mount ?? null, label: "",
                    kind: entry?.kind ?? null, name: entry?.name ?? null, shape: mech.shape ?? null,
                    size: Array.isArray(mech.size) ? mech.size.map(quantity) : null, mass: quantity(mech.mass) }];
    }));
    const joints = (s.mechanical?.joints ?? []).map((j) => ({
      ...j, origin: vector(j.origin), axis: vector(j.axis), limits: Array.isArray(j.limits) ? [quantity(j.limits[0]), quantity(j.limits[1])] : null,
    }));
    return {
      graph, assembly, components, joints, derived_joints: [], compiled: !!assembly,
      images: ["model/layout.png", "model/assembly_graph.png"].filter((p) => existsSync(build(pid, p))).map((p) => link(pid, p)),
      results: { assembly: result(pid, "assembly"), model: result(pid, "model") },
      missing: [["graph", graph], ["assembly", assembly]].filter(([, v]) => !v).map(([k]) => k as string),
    };
  }

  function cad(pid: string) {
    const cadModel = readJson<{ source?: Json; parts?: Record<string, { mesh?: string }> }>(build(pid, "cad", "cad_model.json"));
    const meshes: Record<string, string> = {};
    for (const [id, part] of Object.entries(cadModel?.parts ?? {})) {
      if (part.mesh && existsSync(build(pid, "cad", part.mesh))) meshes[id] = artifactUrl(pid, `cad/${part.mesh}`);
    }
    for (const mesh of files(pid, "cad/meshes", /\.(stl|glb|obj)$/i)) meshes[path.basename(mesh).replace(/\.[^.]+$/, "")] ??= artifactUrl(pid, mesh);
    const source = cadModel?.source ?? {};
    const document = (source.url as string | undefined) ?? (source.document_id && source.workspace_id
      ? `https://cad.onshape.com/documents/${String(source.document_id)}/w/${String(source.workspace_id)}` : null);
    return {
      cad_model: cadModel, meshes, onshape: { document, part_studio: (source.url as string | undefined) ?? null, lock: {} },
      views: files(pid, "cad", IMAGE).map((p) => link(pid, p)), result: result(pid, "cad"), missing: cadModel ? [] : ["cad_model"],
    };
  }

  function wiring(pid: string) {
    const s = spec(pid);
    const routes = readJson<{ clearances?: unknown[] }>(build(pid, "wiring", "routes.json"));
    const detail_ = readJson(build(pid, "wiring", "routing_detail.json"));
    const slack = readJson<Json>(build(pid, "wiring", "slack_sim.json"));
    const r = result(pid, "wiring");
    const metrics = (r?.metrics ?? {}) as Json;
    const timeline = (slack?.timeline as Json | undefined) ?? readJson(build(pid, "wiring", "slack_timeline.json"));
    return {
      routes, routing_detail: detail_, bom: csvRows(build(pid, "wiring", "harness_bom.csv")),
      bom_url: existsSync(build(pid, "wiring", "harness_bom.csv")) ? artifactUrl(pid, "wiring/harness_bom.csv") : null,
      clearances: { static: routes?.clearances ?? [], moving: metrics.wire_min_clearance_moving ?? null,
                    worst_static: metrics.worst_static_clearance ?? null, worst_dynamic: metrics.worst_dynamic_clearance ?? null,
                    requirement: quantity(s.requirements?.min_wire_clearance_moving ?? 0.008) },
      slack_sim: slack, slack_timeline: timeline ?? null,
      harnesses: Object.fromEntries((s.electrical?.harnesses ?? []).map((h, index) => [h.id, {
        index, wires: h.wires.map((w) => w.id), connectors: Object.keys(h.connectors ?? {}),
        route: { via: (h.route?.via ?? []).map(vector), tie_points: (h.route?.tie_points ?? []).map(vector),
                 slack: quantity(h.route?.slack ?? 0.015), keep_away: h.route?.keep_away ?? [] }, jacket: h.jacket ?? "none",
      }])),
      images: files(pid, "wiring", IMAGE).map((p) => link(pid, p)), result: r,
      missing: [["routes", routes], ["routing_detail", detail_], ["slack_sim", slack], ["slack_timeline", timeline]].filter(([, v]) => !v).map(([k]) => k as string),
    };
  }

  function electrical(pid: string) {
    const points = readJson<OperatingPointsJson>(build(pid, "electrical", "operating_points.json"));
    const graph = readJson<ModelGraph>(build(pid, "model", "graph.json"));
    const worst = (points?.worst_case ?? {}) as NonNullable<OperatingPointsJson["worst_case"]> & { harness_voltage_drop?: unknown };
    const num = (v: unknown) => (typeof v === "number" ? v : null);
    return {
      operating_points: points,
      table: Object.entries(points?.operating_points ?? {}).map(([id, p]) => ({
        id, operating_point: p.operating_point, battery_state: p.battery_state, kind: p.kind, soc: p.soc,
        battery_current: num(p.components.battery?.current), battery_voltage: num(p.components.battery?.v_terminal),
        motor_current: num(p.components.motor?.current), rails: p.nets ?? null,
      })),
      wires: Object.entries(worst.wires ?? {}).map(([id, w]) => ({ id, ...w, burst_ratio: w.burst_ratio ?? null,
                                                                   continuous_ratio: w.ampacity ? w.continuous_current / w.ampacity : null })),
      connectors: Object.entries(worst.connectors ?? {}).map(([id, c]) => ({
        id, ...c, continuous_ratio: c.rating ? c.continuous_current / c.rating : null,
        burst_ratio: c.burst_rating && c.burst_current !== undefined ? c.burst_current / c.burst_rating : null,
      })),
      power_tree: { rails: graph?.rails ?? [], fuses: points?.fuses ?? null, brownout: points?.brownout ?? null, harness_voltage_drop: worst.harness_voltage_drop ?? null },
      runtime: points?.runtime ?? null, thermal: points?.thermal ?? null,
      images: files(pid, "electrical", IMAGE).map((p) => link(pid, p)), result: result(pid, "electrical"),
      missing: points ? [] : ["operating_points"],
    };
  }

  function pcb(pid: string): { boards: PcbBoard[]; result: StageResult | null } {
    const s = spec(pid) as RobotSpec & { pcb?: { boards?: { component: string; source: string; [k: string]: unknown }[] } };
    const rawCatalog = catalogData().components;
    const boards = (s.pcb?.boards ?? []).map((board) => {
      const name = path.basename(board.source);
      const data = readJson<Json & { connectors?: Record<string, Record<string, string>>; erc?: never; drc?: never }>(build(pid, "pcb", name, "board.json"));
      const all = files(pid, `pcb/${name}`, /.*/);
      const gerbers = all.find((f) => f.endsWith(".zip") && /gerber/i.test(f)) ?? all.find((f) => f.endsWith(".zip"));
      const bom = all.find((f) => /bom/i.test(f) && f.toLowerCase().endsWith(".csv"));
      const entry = rawCatalog.find((x) => x.id === s.components?.[board.component]?.type);
      const catalogPins = Object.fromEntries(Object.entries((entry?.connectors ?? {}) as Record<string, { pins?: Record<string, string> }>)
        .map(([conn, c]) => [conn, Object.fromEntries(Object.entries(c.pins ?? {}).map(([k, v]) => [String(k), String(v)]))]));
      return {
        board: name, component: board.component, spec: board, board_json: data as PcbBoard["board_json"],
        renders: all.filter((f) => IMAGE.test(f)).map((p) => link(pid, p)), erc: data?.erc ?? null, drc: data?.drc ?? null,
        connectors: data?.connectors ?? null, catalog_connectors: catalogPins,
        bom: bom ? csvRows(build(pid, bom)) : ((data?.bom as PcbBoard["bom"]) ?? null),
        gerbers_url: gerbers ? artifactUrl(pid, gerbers) : null, files: all.map((p) => link(pid, p)), missing: data ? [] : ["board_json"],
      };
    });
    return { boards, result: result(pid, "pcb") };
  }

  function software(pid: string) {
    const s = spec(pid);
    const graph = readJson<ModelGraph>(build(pid, "model", "graph.json"));
    const logs = files(pid, "sim", /^summary\.json$/).map((rel) => {
      const summary = readJson<Json>(build(pid, rel)) ?? {};
      const log = path.join(path.dirname(build(pid, rel)), "software.log");
      return {
        scenario: (summary.scenario as string) ?? path.basename(path.dirname(rel)), firmware_logs: summary.firmware_logs ?? [], software: summary.software ?? null,
        software_log_url: existsSync(log) ? artifactUrl(pid, `${path.dirname(rel)}/software.log`) : null,
        software_log_tail: existsSync(log) ? readFileSync(log, "utf8").slice(-8000) : "",
      };
    });
    return {
      // r3 serve returns the parsed spec (SI numbers, explicit nulls); normalize the raw YAML the same way.
      bindings: graph ? bindingPinMaps(Object.fromEntries(Object.entries(s.software?.bindings ?? {}).map(([k, b]) => [k, {
        ...b, channel: b.channel ?? null, baud: (b.baud as number | undefined) ?? null,
        frequency: b.frequency === undefined || b.frequency === null ? null : quantity(b.frequency),
      }])), graph) : [],
      modules: (s.software?.modules ?? []).map((m) => {
        const rel = path.join("backend", m.language ?? "python", m.name);
        const dir = path.join(projectDir(pid), rel);
        const list: string[] = [];
        const walk = (d: string) => {
          for (const e of existsSync(d) ? readdirSync(d, { withFileTypes: true }) : []) {
            if (e.name === "__pycache__") continue;
            const full = path.join(d, e.name);
            if (e.isDirectory()) walk(full); else list.push(path.relative(projectDir(pid), full));
          }
        };
        walk(dir);
        return { ...m, path: rel, exists: existsSync(dir), files: list.sort() };
      }),
      config: s.software?.config ?? {}, firmware_logs: logs, deploy_log_url: null,
    };
  }

  function scenarioLast(pid: string, id: string): ScenarioLast | null {
    const data = readJson<Json>(build(pid, "sim", id, "summary.json"));
    if (!data) return null;
    const renders = files(pid, `sim/${id}/renders`, /.*/, false);
    return {
      passed: (data.passed as boolean) ?? null, checks: (data.checks as ScenarioLast["checks"]) ?? null, metrics: (data.metrics as Json) ?? null,
      fallbacks: (data.fallbacks as Json) ?? null, stale: isStale(data.inputs as Json, fingerprint(pid)), inputs: (data.inputs as Record<string, string>) ?? null,
      summary_url: artifactUrl(pid, `sim/${id}/summary.json`),
      plots: files(pid, `sim/${id}`, /\.png$/i, false).map((p) => link(pid, p)),
      renders: renders.filter((f) => /\.png$/i.test(f)).map((p) => link(pid, p)),
      clip_url: renders.filter((f) => /\.(mp4|webm)$/i.test(f)).map((p) => artifactUrl(pid, p))[0] ?? null,
      recording: existsSync(build(pid, "sim", id, "recording.npz")), diagnostics: (data.diagnostics as Diagnostic[]) ?? [],
    };
  }

  function simList(pid: string) {
    return {
      scenarios: (spec(pid).simulation?.scenarios ?? []).map((x) => ({ ...x, duration: quantity(x.duration), last: scenarioLast(pid, x.id) })),
      result: result(pid, "sim"),
      overview_url: existsSync(build(pid, "sim", "overview.png")) ? artifactUrl(pid, "sim/overview.png") : null,
    };
  }

  async function simDetail(pid: string, id: string, hz: number, cables: boolean) {
    const scenario = spec(pid).simulation?.scenarios?.find((x) => x.id === id);
    if (!scenario && !existsSync(build(pid, "sim", id))) {
      throw new HttpError(404, "scenario_not_found", `no scenario '${id}' in robot.yaml or build/sim`, { scenarios: (spec(pid).simulation?.scenarios ?? []).map((x) => x.id) });
    }
    return {
      scenario: scenario ? { ...scenario, duration: quantity(scenario.duration) } : { id }, last: scenarioLast(pid, id),
      summary: readJson(build(pid, "sim", id, "summary.json")), recording: await recording(pid, id, hz, cables),
    };
  }

  // ------------------------------------------------------------------ spec patch (memory only, formatting preserved)
  interface Edit { start: number; end: number; text: string }

  /** Flow-style YAML for a value spliced into existing text (valid in any value position). */
  const inline = (value: unknown) => stringifyYaml(value, { collectionStyle: "flow", lineWidth: 0 }).trimEnd();

  /** Text edits that turn `node` (holding `current`) into `next`, touching only what changed, so comments,
   * flow style and line wrapping elsewhere survive (as the real API's ruamel round-trip does). */
  function edits(source: string, node: YamlNode | null | undefined, current: unknown, next: unknown): Edit[] | null {
    if (same(current, next)) return [];
    if (!node?.range) return null;
    const whole = (): Edit[] => [{ start: node.range![0], end: node.range![1], text: inline(next) }];
    if (isScalar(node)) return whole();
    if (isMap(node) && isPlain(current) && isPlain(next)) {
      if (Object.keys(current).some((k) => !(k in next))) return whole();
      const out: Edit[] = [];
      for (const [key, value] of Object.entries(next)) {
        const pair = node.items.find((p) => isScalar(p.key) && String(p.key.value) === key);
        if (pair) {
          const sub = edits(source, pair.value as YamlNode | null, current[key], value);
          if (!sub) return whole();
          out.push(...sub);
        } else if (node.flow || !node.items.length) {
          return whole();
        } else {
          // New key in a block map: a new line after the map, at the indentation of its keys.
          const first = node.items[0]!.key as YamlNode;
          const lineStart = source.lastIndexOf("\n", first.range![0] - 1) + 1;
          const indent = " ".repeat(first.range![0] - lineStart);
          const end = node.range[1];
          const at = source[end - 1] === "\n" ? end - 1 : end;
          out.push({ start: at, end: at, text: `\n${indent}${key}: ${inline(value)}` });
        }
      }
      return out;
    }
    if (isSeq(node) && Array.isArray(current) && Array.isArray(next) && current.length === next.length) {
      const out: Edit[] = [];
      for (let i = 0; i < next.length; i++) {
        const sub = edits(source, node.items[i] as YamlNode | null, current[i], next[i]);
        if (!sub) return whole();
        out.push(...sub);
      }
      return out;
    }
    return whole();
  }

  function mergePatchValue(target: unknown, patch: unknown): unknown {
    if (!isPlain(patch)) return patch;
    const out: Json = isPlain(target) ? { ...target } : {};
    for (const [k, v] of Object.entries(patch)) { if (v === null) delete out[k]; else out[k] = mergePatchValue(out[k], v); }
    return out;
  }

  /** RFC 7386 merge patch applied to robot.yaml text with minimal edits; falls back to a full re-render. */
  function applyPatch(text: string, patch: Json): string {
    const doc = parseDocument(text);
    const current = doc.toJS() as unknown;
    const next = mergePatchValue(current, patch);
    const planned = edits(text, doc.contents as YamlNode, current, next);
    if (planned) {
      let out = text;
      for (const e of planned.sort((a, b) => b.start - a.start)) out = out.slice(0, e.start) + e.text + out.slice(e.end);
      if (same(parseYaml(out), next)) return out;
    }
    return new Document(next).toString({ lineWidth: 0 });
  }

  function specDocument(pid: string) {
    const text = specText(pid);
    return { yaml: text, sha256: sha256(text), spec: parseYaml(text), path: path.join(projectDir(pid), "robot.yaml") };
  }

  function patchSpec(pid: string, body: Json) {
    const text = specText(pid);
    const current = sha256(text);
    if (!isPlain(body.patch)) throw new HttpError(422, "patch_invalid", "a merge patch must be a JSON object");
    if (body.sha256 !== current) {
      throw new HttpError(409, "spec_conflict", "robot.yaml changed since it was read; re-read it and re-apply the patch",
                          { expected_sha256: body.sha256 as string, current_sha256: current });
    }
    const next = applyPatch(text, body.patch);
    if (!body.dry_run) specOverlay.set(pid, next);
    const patched = sha256(next);
    return { ...specDocument(pid), ok: true, dry_run: !!body.dry_run, changed: next !== text, previous_sha256: current,
             sha256: body.dry_run ? current : patched, patched_sha256: patched, preserves_comments: true, graph_diagnostics: [], mock_in_memory: true };
  }

  // ------------------------------------------------------------------ runs (replayed from existing results)
  function startRun(pid: string, body: Json, kind = "validate") {
    const all = results(pid);
    const requested = Array.isArray(body.stages) && body.stages.length ? (body.stages as string[]) : STAGES.filter((s) => s !== "report" && all[s]);
    const stages = [...requested.filter((s) => s !== "report"), "report"];
    const scenarios = (body.scenarios as string[] | undefined) ?? (body.scenario ? [body.scenario as string] : null);
    const run_id = `mock-${randomUUID().slice(0, 8)}`;
    let seq = 0;
    const info: Omit<RunInfo, "events"> = {
      run_id, project: pid, kind, status: "queued", stages, scenarios, current_stage: null, created_at: Date.now() / 1000,
      started_at: null, finished_at: null, result: null,
    };
    const run: MockRun = { info, events: [], done: false, waiters: new Set() };
    runs.set(run_id, run);
    const emit = (event: RunEvent) => {
      run.events.push({ seq: seq++, time: Date.now() / 1000, run_id, ...event });
      for (const w of run.waiters) w();
    };
    emit({ type: "run_queued", project: pid, kind, stages, scenarios });
    void (async () => {
      await sleep(delay);
      info.status = "running";
      info.started_at = Date.now() / 1000;
      emit({ type: "run_started", project: pid, stages, scenarios });
      for (const [index, stage] of stages.entries()) {
        info.current_stage = stage;
        emit({ type: "stage_started", stage, index, total: stages.length });
        emit({ type: "log", stage, stream: "logging", level: "info",
               line: `mock API: replaying build/${stage}/result.json (no stage executed; start r3 serve to run it)` });
        await sleep(delay);
        const r = all[stage];
        for (const d of (r?.diagnostics ?? []).filter((x) => x.severity === "error").slice(0, 3)) emit({ type: "diagnostic", stage, diagnostic: d });
        emit({ type: "stage_finished", stage, status: r?.status ?? "missing", ok: r?.ok ?? false, stale: r?.stale ?? false,
               counts: r ? severityCounts(r.diagnostics) : {}, duration_s: r?.duration_s ?? 0 });
        await sleep(delay / 2);
      }
      const failed = stages.filter((s) => all[s] && !all[s].ok && s !== "report");
      const gate = release(pid);
      info.status = failed.length ? "failed" : "succeeded";
      info.finished_at = Date.now() / 1000;
      info.current_stage = null;
      info.result = { status: info.status, ok: !failed.length, stages, release: { fabrication_ready: gate.fabrication_ready, counts: gate.counts, blocking: gate.blocking } };
      emit({ type: "run_finished", ...info.result });
      run.done = true;
      for (const w of run.waiters) w();
    })();
    return { run_id, status: "queued", events_url: `/api/runs/${run_id}/events`, status_url: `/api/runs/${run_id}` };
  }

  const runInfo = (run: MockRun): RunInfo => ({ ...run.info, events: run.events.length });

  // ------------------------------------------------------------------ agent (scripted transcript, clearly labelled)
  function startAgent(pid: string, body: Json) {
    const prompt = String(body.prompt ?? "").trim();
    if (!prompt) throw new HttpError(422, "request_invalid", "prompt must not be empty", { errors: [{ loc: ["body", "prompt"], msg: "too short" }] });
    if ([...sessions.values()].some((s) => s.pid === pid && !s.done)) throw new HttpError(409, "agent_busy", "an agent session is already running for this project");
    const provider = body.provider === "codex" ? "codex" : "claude";
    const session_id = `mock-agent-${randomUUID().slice(0, 8)}`;
    let seq = 0;
    const info = { session_id, project: pid, provider, model: (body.model as string) ?? null, prompt, created_at: Date.now() / 1000, finished_at: null,
                   returncode: null, result: "", error: null };
    const session = { pid, status: "running", cancelled: false, info, events: [] as AgentEvent[], done: false, waiters: new Set<() => void>() };
    sessions.set(session_id, session);
    const emit = (event: AgentEvent) => {
      session.events.push({ seq: seq++, time: Date.now() / 1000, session_id, mock: true, ...event });
      for (const w of session.waiters) w();
    };
    void (async () => {
      const step = async (event: AgentEvent) => { if (session.cancelled) return; await sleep(delay); emit(event); };
      const baseline = specText(pid);
      const command = provider === "claude" ? ["claude", "-p", "--output-format", "stream-json", "--mcp-config", ".mcp.json"] : ["codex", "exec", "--json", "-"];
      await step({ type: "session_started", project: pid, provider, model: info.model, cwd: projectDir(pid), command, cli_version: "mock" });
      await step({ type: "message", role: "assistant",
                   text: "Mock agent session (r3 serve is not running). The real API runs the provider's CLI headlessly with the R3 MCP server; " +
                         "this scripted session reads the spec and applies one illustrative in-memory patch so the transcript and live diff can be reviewed." });
      const doc = specDocument(pid);
      await step({ type: "tool_call", id: "t1", name: "r3_get_spec", server: "r3", input: { section: "electrical.harnesses" } });
      await step({ type: "tool_result", id: "t1", name: "r3_get_spec", ok: true, content: JSON.stringify({ sha256: doc.sha256, harnesses: (doc.spec as RobotSpec).electrical?.harnesses?.length ?? 0 }) });
      const harnesses = (doc.spec as RobotSpec).electrical?.harnesses ?? [];
      const lead = harnesses.find((h) => h.id === "supply") ?? harnesses[0];
      if (lead && !session.cancelled) {
        const patch = { electrical: { harnesses: harnesses.map((h) => (h.id === lead.id ? { ...h, route: { ...(h.route ?? {}), slack: "10 mm" } } : h)) } };
        await step({ type: "tool_call", id: "t2", name: "r3_patch_spec", server: "r3", input: { patch: { [`electrical.harnesses[${lead.id}].route.slack`]: "10 mm" }, sha256: doc.sha256 } });
        const patched = patchSpec(pid, { patch, sha256: doc.sha256 });
        await step({ type: "tool_result", id: "t2", name: "r3_patch_spec", ok: true, content: JSON.stringify({ ok: true, sha256: patched.sha256, changed: patched.changed }) });
        const diff = unifiedDiff(baseline, specText(pid));
        await step({ type: "spec_changed", previous_sha256: doc.sha256, sha256: patched.sha256, diff, cumulative_diff: diff });
        await step({ type: "tool_call", id: "t3", name: "r3_run_stage", server: "r3", input: { stages: ["wiring", "electrical"] } });
        await step({ type: "run_started", tool_call_id: "t3", tool: "r3_run_stage", stages: ["wiring", "electrical"], scenarios: [] });
        const { run_id } = startRun(pid, { stages: ["wiring", "electrical"] });
        const run = runs.get(run_id)!;
        while (!run.done && !session.cancelled) await sleep(delay);
        await step({ type: "run_finished", tool_call_id: "t3", tool: "r3_run_stage", ok: run.info.status === "succeeded", stages: ["wiring", "electrical"] });
        await step({ type: "tool_result", id: "t3", name: "r3_run_stage", ok: true, content: JSON.stringify({ run: run.info.status, stages: ["wiring", "electrical"] }) });
        await step({ type: "message", role: "assistant", text: `Set ${lead.id} route slack to 10 mm and re-ran wiring + electrical (mock replay of the existing results).` });
      }
      const final = unifiedDiff(baseline, specText(pid));
      session.status = session.cancelled ? "cancelled" : "completed";
      Object.assign(info, { finished_at: Date.now() / 1000, returncode: session.cancelled ? -15 : 0, result: session.cancelled ? "" : "done" });
      emit({ type: "session_finished", status: session.status, returncode: info.returncode, result: info.result, error: null, spec_changed: !!final, cumulative_diff: final });
      session.done = true;
      for (const w of session.waiters) w();
    })();
    return { session_id, status: "starting", provider, events_url: `/api/agent/${session_id}/events`, status_url: `/api/agent/${session_id}` };
  }

  function doctor() {
    const stages = ["model", "cad", "pcb", "wiring", "electrical", "software", "sim", "report"];
    return {
      ok: true, mock: true, checked_at: Date.now() / 1000,
      environment: { python: "n/a (mock API)", platform: `${process.platform} ${process.arch}`, display: false, worker_image: false, ci: !!process.env.CI },
      tools: {
        "r3-api": { name: "r3-api", available: false, version: null, detail: "r3 serve is not reachable; the dev server answers from build/ artifacts", data: {} },
        "recording-converter": { name: "recording-converter", available: true, version: null,
                                 detail: `${python()} apps/web/scripts/convert_recording.py (MuJoCo forward kinematics)`, data: {} },
      },
      stages: stages.map((stage) => ({ stage, headless: true, level: "degraded", missing: ["r3 serve"],
                                       note: "Mock API: shows the existing build/ results; runs are replayed, not executed." })),
      agents: ["claude", "codex"].map((provider) => ({ provider, available: false, path: null, version: null, detail: "mock API: agent CLIs run through r3 serve" })),
    };
  }

  // ------------------------------------------------------------------ HTTP plumbing
  function send(res: ServerResponse, status: number, body: unknown): void {
    res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store", "X-R3-Mock": "1" });
    res.end(JSON.stringify(body));
  }

  async function body(req: IncomingMessage): Promise<Json> {
    const chunks: Buffer[] = [];
    for await (const chunk of req) chunks.push(chunk as Buffer);
    if (!chunks.length) return {};
    try {
      return JSON.parse(Buffer.concat(chunks).toString("utf8")) as Json;
    } catch {
      throw new HttpError(422, "request_invalid", "request body is not JSON");
    }
  }

  async function stream<T extends { type: string; seq?: number }>(res: ServerResponse, source: Stream<T>): Promise<void> {
    res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-store", Connection: "keep-alive", "X-R3-Mock": "1" });
    let sent = 0;
    let closed = false;
    res.on("close", () => (closed = true));
    while (!closed) {
      while (sent < source.events.length) {
        const event = source.events[sent++]!;
        res.write(`id: ${event.seq ?? sent}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`);
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
    const full = path.resolve(root, rel.replace(/^build\//, ""));
    if (!full.startsWith(root + path.sep)) throw new HttpError(403, "artifact_outside_build", "artifact path escapes build/");
    const info = await stat(full).catch(() => null);
    if (!info) throw new HttpError(404, "artifact_missing", `build/${rel} does not exist`, { path: rel });
    if (info.isDirectory()) return send(res, 200, { path: rel, kind: "directory", entries: files(pid, rel, /.*/) });
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
    if (head === "agent" && pid === "providers") return send(res, 200, doctor().agents);
    if (head === "projects" && !pid) {
      if (method === "GET") {
        const list = projects();
        return send(res, 200, { projects: list.map(summaryEntry), roots: roots, templates: [...list.map((p) => p.id), "blank"] });
      }
      if (method === "POST") {
        const b = await body(req);
        if (b.github && b.confirm !== true) throw new HttpError(428, "confirmation_required", "creating a GitHub repository needs confirm: true");
        throw new HttpError(503, "mock_read_only", `the mock API cannot scaffold "${String(b.name ?? "")}"; start r3 serve to create projects`);
      }
    }
    if (head === "projects" && pid) {
      if (!section && method === "GET") return send(res, 200, detail(pid));
      if (section === "release") return send(res, 200, release(pid));
      if (section === "spec" && method === "GET") return send(res, 200, specDocument(pid));
      if (section === "spec" && method === "PATCH") return send(res, 200, patchSpec(pid, await body(req)));
      if (section === "catalog") return send(res, 200, catalog());
      if (section === "model") return send(res, 200, model(pid));
      if (section === "cad") return send(res, 200, cad(pid));
      if (section === "wiring") return send(res, 200, wiring(pid));
      if (section === "electrical") return send(res, 200, electrical(pid));
      if (section === "pcb") return send(res, 200, pcb(pid));
      if (section === "software") return send(res, 200, software(pid));
      if (section === "sim" && !rest.length) return send(res, 200, simList(pid));
      if (section === "sim" && rest[0]) {
        const hz = Math.max(0.5, Math.min(200, Number(url.searchParams.get("hz") ?? 30)));
        return send(res, 200, await simDetail(pid, rest[0], hz, url.searchParams.get("cables") !== "false"));
      }
      if (section === "diagnostics") {
        const diagnostics = allDiagnostics(pid);
        const byStage: Record<string, Json> = {};
        for (const d of diagnostics) ((byStage[d.stage] ??= { error: 0, warning: 0, info: 0 }) as Record<string, number>)[d.severity]! += 1;
        return send(res, 200, { diagnostics, counts: severityCounts(diagnostics), by_stage: byStage, stages: stageRows(pid), inputs: fingerprint(pid) });
      }
      if (section === "artifacts" && rest.length) return artifact(req, res, pid, rest.join("/"));
      if (section === "runs" && method === "POST") return send(res, 202, startRun(pid, await body(req)));
      if (section === "runs" && method === "GET") return send(res, 200, [...runs.values()].filter((r) => r.info.project === pid).map(runInfo));
      if (section === "deploy" && method === "POST") {
        const b = await body(req);
        if (b.target !== "sim" && b.target !== "fleet") throw new HttpError(422, "request_invalid", "target must be sim or fleet");
        if (b.target === "fleet" && b.confirm !== true) {
          throw new HttpError(428, "confirmation_required", "fleet deployment changes software on physical robots; resend with confirm: true", { target: "fleet" });
        }
        if (b.target === "fleet") throw new HttpError(503, "mock_read_only", "the mock API never deploys to a fleet; start r3 serve");
        return send(res, 202, { ok: true, target: "sim", ...startRun(pid, { stages: ["software", "sim"], scenarios: b.scenarios }, "deploy_sim") });
      }
      if (section === "agent" && method === "POST") return send(res, 202, startAgent(pid, await body(req)));
      if (section === "agent" && method === "GET") return send(res, 200, [...sessions.values()].filter((s) => s.pid === pid).map((s) => ({ ...s.info, status: s.status, events: s.events.length })));
    }
    if (head === "runs" && pid) {
      const run = runs.get(pid);
      if (!run) throw new HttpError(404, "run_not_found", `no run ${pid}`);
      if (!section) return send(res, 200, runInfo(run));
      if (section === "events") return stream(res, run);
      if (section === "cancel" && method === "POST") return send(res, 200, runInfo(run));
    }
    if (head === "agent" && pid) {
      const session = sessions.get(pid);
      if (!session) throw new HttpError(404, "agent_session_not_found", `no agent session ${pid}`);
      if (!section) return send(res, 200, { ...session.info, status: session.status, events: session.events.length });
      if (section === "events") return stream(res, session);
      if (section === "cancel" && method === "POST") {
        session.cancelled = true;
        return send(res, 200, { ...session.info, status: "cancelled", events: session.events.length });
      }
    }
    throw new HttpError(404, "not_found", `${method} ${url.pathname} is not part of the API`);
  }

  /** Connect-style middleware for Vite's dev and preview servers. */
  return function mockApi(req: IncomingMessage, res: ServerResponse, next: Next): void {
    if (!req.url?.startsWith("/api/") && req.url !== "/api") return next();
    handle(req, res).catch((error: unknown) => {
      if (res.headersSent) return res.end();
      if (error instanceof HttpError) return send(res, error.status, { code: error.code, message: error.message, data: error.data });
      send(res, 500, { code: "internal_error", message: (error as Error).message ?? String(error), data: {} });
    });
  };
}

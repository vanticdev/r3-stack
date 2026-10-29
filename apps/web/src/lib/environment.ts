// Root causes of an environment's limitations, from the `r3 doctor` payload: one entry per missing
// tool, with the stages it affects grouped under it (instead of one symptom per stage).
import type { DoctorPayload, DoctorTool, StageReadiness } from "../api/types.ts";
import { stageLabel } from "./labels.ts";

export interface EnvironmentCause {
  /** the missing tool or service, as the doctor names it */
  missing: string;
  tool: DoctorTool | null;
  /** "Build service unavailable — stages cannot execute" */
  headline: string;
  /** how to fix it */
  fix: string;
  /** worst effect on the affected stages */
  level: "unavailable" | "degraded";
  stages: StageReadiness[];
}

const normalize = (name: string) => name.toLowerCase().replace(/[^a-z0-9]/g, "");

/** Known tools: what losing them means, and the fix. The doctor's own detail is shown alongside. */
const KNOWN: Record<string, { what: string; fix: string }> = {
  r3serve: { what: "Build service unavailable", fix: "Start the R3 API: `r3 serve --projects-root examples/robots`. Until then results are read from build/ and runs are replayed." },
  r3api: { what: "Build service unavailable", fix: "Start the R3 API: `r3 serve --projects-root examples/robots`." },
  freerouting: { what: "Freerouting is not installed", fix: "Download the Freerouting jar and set R3_FREEROUTING_JAR to its path (Java 21+ is required)." },
  java: { what: "Java is not installed", fix: "Install a Java 21+ runtime for Freerouting." },
  docker: { what: "Docker daemon unavailable", fix: "Start Docker (or point DOCKER_HOST at a daemon) to build and run firmware containers." },
  kicadcli: { what: "kicad-cli is not installed", fix: "Install KiCad 10; kicad-cli runs ERC, DRC and exports headless." },
  kicadpcbnew: { what: "KiCad's pcbnew module is not available", fix: "Install KiCad 10 and point R3 at its bundled Python (pcbnew module)." },
  mujoco: { what: "MuJoCo is not installed", fix: "pip install mujoco in the R3 environment; set MUJOCO_GL=egl on headless Linux." },
  libngspice: { what: "ngspice shared library missing", fix: "Install ngspice with its shared library (libngspice)." },
  ngspice: { what: "ngspice is not installed", fix: "Install ngspice." },
  onshape: { what: "Onshape is not configured", fix: "Set the Onshape API keys (ONSHAPE_ACCESS_KEY / ONSHAPE_SECRET_KEY)." },
  sionnart: { what: "Sionna RT is not installed", fix: "pip install sionna-rt for ray-traced radio; otherwise the analytic link model is used." },
  ffmpeg: { what: "ffmpeg is not installed", fix: "Install ffmpeg to encode simulation clips." },
  matplotlib: { what: "matplotlib is not installed", fix: "pip install matplotlib for stage plots." },
};

function findTool(doctor: DoctorPayload, missing: string): DoctorTool | null {
  const key = normalize(missing);
  return Object.entries(doctor.tools).find(([k, t]) => normalize(k) === key || normalize(t.name) === key)?.[1] ?? null;
}

export function environmentCauses(doctor: DoctorPayload): EnvironmentCause[] {
  const byMissing = new Map<string, StageReadiness[]>();
  for (const stage of doctor.stages) {
    if (stage.level === "full") continue;
    for (const missing of stage.missing.length ? stage.missing : ["(unspecified)"]) {
      (byMissing.get(missing) ?? byMissing.set(missing, []).get(missing)!).push(stage);
    }
  }
  const causes = [...byMissing.entries()].map(([missing, stages]): EnvironmentCause => {
    const tool = findTool(doctor, missing);
    const known = KNOWN[normalize(missing)];
    const level = stages.some((s) => s.level === "unavailable") ? "unavailable" : "degraded";
    const effect = stages.length > 2 ? "stages cannot execute" : `${stages.map((s) => stageLabel(s.stage)).join(" and ")} ${level === "unavailable" ? "cannot run" : "runs with reduced fidelity"}`;
    return {
      missing, tool, level, stages,
      headline: `${known?.what ?? `${missing} is missing`} — ${normalize(missing).startsWith("r3") ? "stages cannot execute" : effect}`,
      fix: known?.fix ?? (tool?.detail || `Install ${missing}.`),
    };
  });
  // Widest impact first: unavailable before degraded, then by the number of affected stages.
  return causes.sort((a, b) => (a.level === b.level ? 0 : a.level === "unavailable" ? -1 : 1) || b.stages.length - a.stages.length);
}

/** Tools the doctor reports missing that no stage depends on (optional extras). */
export function optionalMissing(doctor: DoctorPayload, causes: readonly EnvironmentCause[]): DoctorTool[] {
  const used = new Set(causes.map((c) => c.tool?.name));
  return Object.values(doctor.tools).filter((t) => !t.available && !used.has(t.name));
}

export const AGENT_NAMES: Record<string, string> = { claude: "Claude Code", codex: "Codex" };
export const AGENT_SETUP: Record<string, string> = {
  claude: "Install Claude Code (`npm install -g @anthropic-ai/claude-code`), then run `claude` once to sign in.",
  codex: "Install the Codex CLI (`npm install -g @openai/codex`), then run `codex login`.",
};

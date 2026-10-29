// Typed client for the R3 API. Same origin by default: `r3 serve` hosts the built app,
// and the Vite dev/preview server proxies /api to it (or to the mock layer, see mock/).
import type {
  AgentCreated, AgentProviderInfo, AgentRequest, AgentStatus, ApiErrorBody, CadResponse, CatalogResponse, CreateProjectRequest,
  CreateProjectResponse, DeployRequest, DeployResponse, DiagnosticsResponse, DoctorPayload, ElectricalResponse, ModelResponse,
  PcbResponse, ProjectDetail, ProjectList, RunCreated, RunInfo, RunRequest, ScenarioResponse, SimListResponse, SoftwareResponse,
  SpecDocument, SpecPatchResponse, WiringResponse,
} from "./types.ts";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly data: Record<string, unknown>;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message || body.code);
    this.status = status;
    this.code = body.code;
    this.data = body.data ?? {};
  }
}

type Listener = (mock: boolean) => void;

/** Tracks whether responses come from the dev mock layer (header `X-R3-Mock: 1`). */
export const backendMode = {
  mock: null as boolean | null,
  listeners: new Set<Listener>(),
  set(mock: boolean) {
    if (this.mock === mock) return;
    this.mock = mock;
    for (const listener of this.listeners) listener(mock);
  },
};

const BASE = (import.meta.env.VITE_R3_API_BASE as string | undefined) ?? "";

export function apiUrl(path: string): string {
  return path.startsWith("http") ? path : `${BASE}${path}`;
}

async function request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(apiUrl(path), {
      method,
      signal,
      headers: body === undefined ? { Accept: "application/json" } : { Accept: "application/json", "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    throw new ApiError(0, { code: "network_error", message: `cannot reach the R3 API (${(error as Error).message})` });
  }
  backendMode.set(response.headers.get("x-r3-mock") === "1");
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { code: "invalid_json", message: text.slice(0, 200) };
    }
  }
  if (!response.ok) {
    const error = (payload && typeof payload === "object" ? payload : {}) as Partial<ApiErrorBody> & { detail?: unknown };
    // FastAPI's default error shape is {detail}; the R3 contract is {code, message, data}.
    const detail = error.detail && typeof error.detail === "object" ? (error.detail as Partial<ApiErrorBody>) : null;
    throw new ApiError(response.status, {
      code: error.code ?? detail?.code ?? `http_${response.status}`,
      message: error.message ?? detail?.message ?? (typeof error.detail === "string" ? error.detail : response.statusText),
      data: error.data ?? detail?.data ?? {},
    });
  }
  return payload as T;
}

const enc = encodeURIComponent;
const project = (pid: string) => `/api/projects/${enc(pid)}`;

/** Agent CLI availability: the doctor reports a provider list (or, from older servers, a map). */
export function agentProviders(doctor: DoctorPayload | undefined): AgentProviderInfo[] {
  const agents = doctor?.agents;
  if (!agents) return [];
  return Array.isArray(agents) ? agents : Object.entries(agents).map(([provider, info]) => ({ provider, ...info }));
}

export const api = {
  doctor: (signal?: AbortSignal, refresh = false) => request<DoctorPayload>("GET", `/api/doctor${refresh ? "?refresh=true" : ""}`, undefined, signal),
  projects: (signal?: AbortSignal) => request<ProjectList>("GET", "/api/projects", undefined, signal),
  createProject: (body: CreateProjectRequest) => request<CreateProjectResponse>("POST", "/api/projects", body),
  project: (pid: string, signal?: AbortSignal) => request<ProjectDetail>("GET", project(pid), undefined, signal),
  spec: (pid: string, signal?: AbortSignal) => request<SpecDocument>("GET", `${project(pid)}/spec`, undefined, signal),
  patchSpec: (pid: string, patch: Record<string, unknown>, sha256: string, dryRun = false) =>
    request<SpecPatchResponse>("PATCH", `${project(pid)}/spec`, { patch, sha256, dry_run: dryRun }),
  catalog: (pid: string, signal?: AbortSignal) => request<CatalogResponse>("GET", `${project(pid)}/catalog`, undefined, signal),
  model: (pid: string, signal?: AbortSignal) => request<ModelResponse>("GET", `${project(pid)}/model`, undefined, signal),
  cad: (pid: string, signal?: AbortSignal) => request<CadResponse>("GET", `${project(pid)}/cad`, undefined, signal),
  wiring: (pid: string, signal?: AbortSignal) => request<WiringResponse>("GET", `${project(pid)}/wiring`, undefined, signal),
  electrical: (pid: string, signal?: AbortSignal) => request<ElectricalResponse>("GET", `${project(pid)}/electrical`, undefined, signal),
  pcb: (pid: string, signal?: AbortSignal) => request<PcbResponse>("GET", `${project(pid)}/pcb`, undefined, signal),
  software: (pid: string, signal?: AbortSignal) => request<SoftwareResponse>("GET", `${project(pid)}/software`, undefined, signal),
  scenarios: (pid: string, signal?: AbortSignal) => request<SimListResponse>("GET", `${project(pid)}/sim`, undefined, signal),
  scenario: (pid: string, scenario: string, hz = 30, signal?: AbortSignal) =>
    request<ScenarioResponse>("GET", `${project(pid)}/sim/${enc(scenario)}?hz=${hz}`, undefined, signal),
  diagnostics: (pid: string, signal?: AbortSignal) => request<DiagnosticsResponse>("GET", `${project(pid)}/diagnostics`, undefined, signal),
  artifactUrl: (pid: string, path: string) => apiUrl(`${project(pid)}/artifacts/${path.split("/").map(enc).join("/")}`),
  startRun: (pid: string, body: RunRequest) => request<RunCreated>("POST", `${project(pid)}/runs`, body),
  run: (rid: string, signal?: AbortSignal) => request<RunInfo>("GET", `/api/runs/${enc(rid)}`, undefined, signal),
  cancelRun: (rid: string) => request<RunInfo>("POST", `/api/runs/${enc(rid)}/cancel`, {}),
  runEventsUrl: (rid: string) => apiUrl(`/api/runs/${enc(rid)}/events`),
  deploy: (pid: string, body: DeployRequest) => request<DeployResponse>("POST", `${project(pid)}/deploy`, body),
  agentSessions: (pid: string, signal?: AbortSignal) => request<AgentStatus[]>("GET", `${project(pid)}/agent`, undefined, signal),
  startAgent: (pid: string, body: AgentRequest) => request<AgentCreated>("POST", `${project(pid)}/agent`, body),
  agentEventsUrl: (sid: string) => apiUrl(`/api/agent/${enc(sid)}/events`),
  cancelAgent: (sid: string) => request<AgentStatus>("POST", `/api/agent/${enc(sid)}/cancel`, {}),
};

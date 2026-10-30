// Typed contract of the R3 HTTP API (`r3 serve`). Mirrors docs/architecture/api.md and the pydantic
// models in services/orchestrator/r3_orchestrator/api/server.py (OpenAPI at /api/openapi.json); the
// domain views follow api/projects.py. Units are SI. Response objects may gain fields, so every
// interface is open-ended where the backend says `extra="allow"`. Missing inputs are `null` and listed
// in `missing`; views never fail because a stage has not run.

export type Vec3 = [number, number, number];
/** World pose: position (m) + quaternion (w, x, y, z), as MuJoCo xpos/xquat. */
export type Pose7 = [number, number, number, number, number, number, number];
export type Severity = "error" | "warning" | "info";
export type StageName = "assembly" | "model" | "cad" | "fabrication" | "pcb" | "wiring" | "connectors" | "electrical" | "software" | "sim" | "tests" | "report";
/** Raw robot.yaml values may be quantities ("20 mm"); the API's parsed `spec` holds SI numbers. */
export type Quantity = number | string;

export const STAGES: StageName[] = ["assembly", "model", "cad", "fabrication", "pcb", "wiring", "connectors", "electrical", "software", "sim", "tests", "report"];

/** Every API error body. */
export interface ApiErrorBody {
  code: string;
  message: string;
  data?: Record<string, unknown>;
}

export interface Diagnostic {
  code: string;
  severity: Severity;
  message: string;
  subjects: string[];
  data: Record<string, unknown>;
  stage: string;
  hint?: string;
}

export interface Assumption {
  domain?: string;
  statement: string;
  stage?: string;
}

export interface ArtifactRef {
  path: string;
  kind: string;
  description?: string;
  url?: string;
}

/** A file under build/ as returned by the views (`images[]`, `plots[]`, `renders[]`, `views[]`). */
export interface FileLink {
  path: string;
  url: string;
  name: string;
}

/** `not_configured`: the spec has nothing for the stage yet (blocks release); `not_applicable`: nothing to build (passes). */
export type StageStatusValue = "ok" | "failed" | "unavailable" | "missing" | "not_configured" | "not_applicable";

/** `build/<stage>/result.json` plus `status`, `stale`, diagnostic hints and artifact URLs. */
export interface StageResult {
  stage: StageName | string;
  ok: boolean;
  status?: StageStatusValue;
  stale?: boolean;
  started_at?: number;
  duration_s?: number;
  diagnostics: Diagnostic[];
  artifacts: ArtifactRef[];
  assumptions: Assumption[];
  metrics: Record<string, unknown>;
  inputs?: Record<string, string | null>;
}

export type Fingerprint = Record<string, string>;

// ------------------------------------------------------------------ doctor
export interface DoctorTool {
  name: string;
  available: boolean;
  version: string | null;
  detail: string;
  data: Record<string, unknown>;
}
export interface StageReadiness {
  stage: string;
  headless: boolean;
  level: "full" | "degraded" | "unavailable";
  missing: string[];
  note: string;
}
/** GET /api/agent/providers row (also `agents` in the doctor payload). */
export interface AgentProviderInfo {
  provider: string;
  available: boolean;
  path?: string | null;
  version?: string | null;
  detail?: string;
}
export interface DoctorPayload {
  ok: boolean;
  environment: { python: string; platform: string; display: boolean; worker_image: boolean; ci: boolean };
  tools: Record<string, DoctorTool>;
  stages: StageReadiness[];
  checked_at?: number;
  agents?: AgentProviderInfo[] | Record<string, Omit<AgentProviderInfo, "provider">>;
  mock?: boolean;
}

// ------------------------------------------------------------------ projects
export type RunStatusValue = "queued" | "running" | "succeeded" | "failed" | "cancelled" | "error";

export interface ProjectSummary {
  id: string;
  name: string;
  path: string;
  description: string;
  version: string;
  robot_yaml_sha256?: string | null;
  stages: Record<string, StageStatusValue | string>;
  last_run: {
    status: "ok" | "failed";
    finished_at?: number | null;
    failed_stages?: string[];
    stale?: boolean;
    counts?: Partial<Record<Severity, number>>;
    run_id?: string;
    run_status?: RunStatusValue;
  } | null;
  onshape_url?: string | null;
  /** Set when robot.yaml does not load: the concise `project_invalid` message. */
  error?: string | null;
  /** The full `project_invalid` error body; `data` follows `ProjectInvalidData`. */
  error_detail?: ApiErrorBody | null;
}
/** One problem of a project that does not load; `path` is the dotted field path (`components.gps.type`). */
export interface ProjectLoadIssue {
  path: string;
  message: string;
  type: string;
}
/** `data` of a `project_invalid` error: the first few issues, their total and the full text. */
export interface ProjectInvalidData {
  project?: string;
  file?: string;
  exception?: string;
  errors: ProjectLoadIssue[];
  total: number;
  details: string;
}
export interface ProjectList {
  projects: ProjectSummary[];
  roots: string[];
  templates: string[];
}

export interface CreateProjectRequest {
  name: string;
  template: string;
  github?: { private: boolean; description?: string };
  confirm?: boolean;
}
export interface CreateProjectResponse {
  ok?: boolean;
  id: string;
  path: string;
  files?: string[];
  github?: Record<string, unknown> | null;
}

export type EvidenceStatus = "verified" | "estimated" | "failed" | "not_run" | "stale";

export interface Evidence {
  stage: string;
  check: string;
  passed: boolean | null;
  value: unknown;
  limit: unknown;
  artifact: string | null;
  inputs: Record<string, string>;
  estimated: boolean;
  stale: boolean;
  detail: string;
}

/** One row of the release gate (`r3_orchestrator/release.py`). */
export interface ReleaseItem {
  /** requirement.<name>, scenario.<scenario>.<key> or stage.<name> */
  id: string;
  kind: "requirement" | "scenario_expectation" | "stage";
  subject: string;
  target: unknown;
  status: EvidenceStatus;
  evidence: Evidence[];
  message: string;
}

export interface Release {
  schema: string;
  fabrication_ready: boolean;
  counts: Partial<Record<EvidenceStatus, number>>;
  blocking: string[];
  inputs: Fingerprint;
  items: ReleaseItem[];
}

export interface StageRow {
  stage: string;
  status: StageStatusValue | string;
  ok: boolean | null;
  stale?: boolean | null;
  counts?: Partial<Record<Severity, number>>;
  duration_s?: number | null;
  started_at?: number | null;
  metrics?: Record<string, unknown>;
  artifacts?: ArtifactRef[];
}

export interface ProjectDetail {
  id: string;
  /** Absolute project directory. */
  project: string;
  robot: { name: string; description?: string; version?: string };
  fingerprint: Fingerprint;
  inputs?: Fingerprint;
  git?: { branch?: string | null; commit?: string | null; dirty_files?: number } | null;
  lock?: Record<string, unknown> | null;
  summary: { components?: number; harnesses?: number; wires?: number; boards?: number; bindings?: number; scenarios?: string[] };
  stages: StageRow[];
  results: Record<string, StageResult>;
  report: { ok: boolean; counts: Partial<Record<Severity, number>>; generated_at?: number; [key: string]: unknown } | null;
  report_url?: string | null;
  release: Release;
  links: { onshape?: string | null; github?: string | null };
  ok?: boolean;
}

export interface SpecDocument {
  files?: Record<string, { sha256: string; yaml: string }>;
  sources?: { path: (string | number)[]; file: string; source_path: (string | number)[] }[];
  read_only?: string[];
  yaml: string;
  sha256: string;
  spec: RobotSpec;
  path?: string;
}

export interface SpecPatchResponse {
  files_changed?: string[];
  ok: boolean;
  dry_run?: boolean;
  changed?: boolean;
  previous_sha256?: string;
  sha256: string;
  patched_sha256?: string;
  preserves_comments?: boolean;
  graph_diagnostics?: Diagnostic[];
  yaml?: string;
  spec?: RobotSpec;
}

// ------------------------------------------------------------------ robot.yaml (parsed, SI)
export interface RouteHint {
  via?: Quantity[][];
  tie_points?: Quantity[][];
  slack?: Quantity;
  keep_away?: string[];
}
export interface SpecWire {
  id: string;
  from: string;
  to: string;
  awg: number;
  color: string;
  signal?: string;
}
export interface SpecHarness {
  id: string;
  description?: string;
  connectors: Record<string, { type: string; mates?: string | null }>;
  splices?: Record<string, unknown>;
  wires: SpecWire[];
  route?: RouteHint;
  jacket?: string;
}
export interface SpecJoint {
  id: string;
  type: "revolute" | "continuous" | "prismatic" | "fixed" | string;
  parent: string;
  child: string;
  origin: Vec3;
  axis: Vec3;
  limits?: [number, number] | null;
}
export interface SpecTransmission {
  id: string;
  type: string;
  actuator?: string;
  joints: string[];
  ratio?: number;
  params?: Record<string, Quantity>;
}
export interface SpecBinding {
  interface: string;
  computer: string;
  pins: string[];
  device?: string;
  channel?: number | null;
  expects?: string[];
  [key: string]: unknown;
}
export interface SpecScenario {
  id: string;
  description?: string;
  duration: Quantity;
  expect?: Record<string, unknown>;
  [key: string]: unknown;
}
export interface RobotSpec {
  schema?: string;
  robot: { name: string; description?: string; version?: string };
  requirements?: Record<string, unknown>;
  components: Record<string, { type: string; mount?: string | null; pose?: { xyz?: Quantity[]; rpy?: Quantity[] } }>;
  mechanical?: { joints?: SpecJoint[]; transmissions?: SpecTransmission[] };
  electrical?: { harnesses?: SpecHarness[]; ground_net?: string; direct_mates?: { a: string; b: string }[] };
  software?: {
    modules?: { name: string; language?: string; runs_on?: string; entrypoint?: string }[];
    bindings?: Record<string, SpecBinding>;
    config?: Record<string, unknown>;
  };
  simulation?: { scenarios?: SpecScenario[]; [key: string]: unknown };
  [key: string]: unknown;
}

// ------------------------------------------------------------------ catalog
/** `GET .../catalog` component row (api.list_catalog), SI units. */
export interface CatalogComponent {
  id: string;
  kind: string;
  name: string;
  part_number?: string;
  mass: number | null;
  size: number[] | null;
  shape: string | null;
  /** connector name -> connector type */
  connectors: Record<string, string>;
  /** pin name -> role */
  pins: Record<string, string>;
  behavior?: string | null;
  /** provenance status: datasheet | measured | estimated | ... */
  provenance?: string | null;
}
export interface CatalogResponse {
  components: CatalogComponent[];
  connectors: Record<string, unknown>[];
  wires: Record<string, unknown>[];
}

// ------------------------------------------------------------------ model
export interface GraphComponent {
  type: string;
  kind: string;
  name: string;
  mount: string;
  shape: "box" | "cylinder" | "custom" | string;
  size: number[];
  axis: "x" | "y" | "z";
  xyz: Vec3;
  rpy: Vec3;
  color: string;
  moving: boolean;
  mass: number;
  aabb_min?: Vec3;
  aabb_max?: Vec3;
}
export interface GraphWire {
  harness: string;
  awg: number;
  color: string;
  signal?: string;
  from: string;
  to: string;
  ends?: [Vec3, Vec3];
  ends_estimated?: boolean;
}
export interface GraphEndpoint {
  type: string;
  position: Vec3;
  direction: Vec3;
  owner_kind: string;
}
export interface ModelGraph {
  components: Record<string, GraphComponent>;
  wires: Record<string, GraphWire>;
  endpoints?: Record<string, GraphEndpoint>;
  nets: Record<string, string[]>;
  net_names: Record<string, string>;
  edges?: { a: string; b: string; kind: string; link_kind?: string; ref?: string; value?: number }[];
  pins?: Record<string, { role: string; passthrough: boolean; connector_pins: string[] }>;
  rails?: { source: string; nominal: number; min: number; max: number; sinks: string[] }[];
  interference?: { a: string; b: string; center: Vec3; extent: Vec3; volume: number }[];
  mass?: { total: number; limit?: number; estimated_fraction?: number; by_component?: Record<string, { mass: number; source: string }> };
  diagnostics?: Diagnostic[];
}
/** `build/model/assembly.json` (assembly stage; absent for specs without `assembly:`). */
export type AssemblyInterface = { component?: string; kind?: string } & (
  { frame: number[][] } | { origin: Vec3; z: Vec3; x?: Vec3 }
);
export interface AssemblyJson {
  root?: string;
  joints?: SpecJoint[];
  joins?: { id: string; a: string; b: string; kind: string; joint?: string | null }[];
  interfaces?: Record<string, AssemblyInterface>;
  [key: string]: unknown;
}
export interface ModelComponent {
  type: string;
  pose: { xyz: Vec3; rpy: Vec3 };
  mount: string | null;
  label?: string | null;
  kind: string | null;
  name: string | null;
  shape: string | null;
  size: number[] | null;
  mass: number | null;
}
export interface ModelResponse {
  graph: ModelGraph | null;
  assembly: AssemblyJson | null;
  components: Record<string, ModelComponent>;
  joints: SpecJoint[];
  derived_joints: SpecJoint[];
  compiled?: boolean;
  images: FileLink[];
  results?: Record<string, StageResult | null>;
  missing: string[];
}

// ------------------------------------------------------------------ cad
export interface CadPart {
  part_name?: string;
  /** "fastener" for screws/nuts; those are not graph components and carry their `owner`. */
  kind?: string;
  owner?: string;
  mass: number;
  com?: Vec3;
  inertia?: number[];
  bbox_min?: Vec3;
  bbox_max?: Vec3;
  material?: string;
  density?: number;
  mesh?: string;
  mesh_triangles?: number;
  mass_status?: string;
}
export interface CadResponse {
  authority?: { ready: boolean; code: string; message: string; hint?: string; subjects?: string[] };
  cad_model: {
    source?: { provider?: string; url?: string; document_id?: string; [key: string]: unknown };
    parts: Record<string, CadPart>;
    contacts?: { contacts?: [string, string, number, number][] } | [string, string, number, number][];
  } | null;
  /** component id -> URL of its STL (robot frame, metres). */
  meshes: Record<string, string>;
  onshape: { document?: string | null; part_studio?: string | null; lock?: Record<string, unknown> };
  views: FileLink[];
  result?: StageResult | null;
  missing: string[];
}

// ------------------------------------------------------------------ wiring
export interface RoutedWire {
  harness: string;
  awg: number;
  path: Vec3[];
  routed_length: number;
  cut_length: number;
}
export interface RoutesJson {
  wires: Record<string, RoutedWire>;
  harnesses: Record<string, { bundle_path: Vec3[]; length: number; tie_points: Vec3[] }>;
  clearances: { wire: string; component: string; min_distance: number }[];
}
export interface RoutingDetailWire {
  harness: string;
  color: string;
  signal?: string;
  from: string;
  to: string;
  od: number;
  slack?: number;
  failed?: boolean;
  min_bend_radius?: number;
  bends?: { position: Vec3; radius: number; required: number; conductors?: number }[];
}
export interface RoutingDetail {
  wires: Record<string, RoutingDetailWire>;
  wire_min_clearance?: Record<string, { distance: number; component: string; position?: Vec3 }>;
  harness_mass?: Record<string, { mass: number; tie_count?: number }>;
  [key: string]: unknown;
}
export interface SlackSim {
  requirement: number;
  worst?: { wire: string; min_distance: number; component: string; time: number };
  profile?: Record<string, number>;
  wires: Record<string, { min_distance: number; component: string; time: number; sags_into_moving?: boolean; below_requirement?: boolean }>;
}
export interface WiringResponse {
  routes: RoutesJson | null;
  routing_detail: RoutingDetail | null;
  bom: Record<string, string | number>[] | null;
  bom_url?: string | null;
  clearances: {
    static: RoutesJson["clearances"];
    /** wire id -> min clearance to moving parts, m */
    moving: Record<string, number> | null;
    worst_static: { wire: string; distance: number; component: string } | null;
    worst_dynamic: { wire: string; min_distance: number; component: string; time: number } | null;
    requirement: number;
  };
  slack_sim: SlackSim | null;
  /** Cable timeline of the slack sim, `null` until the wiring stage persists it. */
  slack_timeline: { t: number[]; cables: Record<string, { rgba?: number[] | null; points: Vec3[][] }> } | null;
  harnesses: Record<string, { index: number; wires: string[]; connectors: string[]; route: RouteHint; jacket?: string }>;
  images: FileLink[];
  result?: StageResult | null;
  missing: string[];
}

// ------------------------------------------------------------------ electrical
export interface OperatingPoint {
  operating_point: string;
  battery_state: string;
  soc: number;
  kind: string;
  components: Record<string, Record<string, unknown>>;
  nets: Record<string, number>;
  wires: Record<string, { current: number; voltage_drop: number; ampacity: number; awg: number; harness: string; length: number }>;
  contacts: Record<string, { current: number; rating: number; burst_rating?: number; voltage_drop?: number }>;
  harness_drop?: Record<string, { drop: number; supply: number; fraction: number }>;
  pin_currents?: Record<string, number>;
  events?: unknown[];
}
export interface RuntimeInfo {
  runtime: number;
  requirement: number;
  capacity_ah?: number;
  cutoff_voltage?: number;
  curve?: { soc: number; time: number; average_current: number; cruise_voltage: number; full_throttle_voltage: number }[];
}
export interface OperatingPointsJson {
  schema?: string;
  operating_points: Record<string, OperatingPoint>;
  worst_case?: {
    wires?: Record<string, { continuous_current: number; continuous_point: string; ampacity: number; burst_current?: number; burst_point?: string; burst_ratio?: number; awg: number; harness: string }>;
    connectors?: Record<string, { continuous_current: number; continuous_point: string; rating: number; burst_rating?: number; burst_current?: number; burst_point?: string }>;
  };
  runtime?: RuntimeInfo;
  thermal?: Record<string, Record<string, unknown>>;
  brownout?: Record<string, unknown>;
  fuses?: Record<string, Record<string, unknown>>;
  battery_states?: Record<string, number>;
  [key: string]: unknown;
}
/** `wires[]` / `connectors[]` rows of the electrical view (worst_case entries plus ratios). */
export interface CurrentRow {
  id: string;
  continuous_current: number;
  continuous_point?: string;
  continuous_ratio: number | null;
  burst_current?: number | null;
  burst_point?: string;
  burst_ratio?: number | null;
  /** wires */
  ampacity?: number;
  awg?: number;
  harness?: string;
  /** connectors */
  rating?: number;
  burst_rating?: number;
}
export interface ElectricalTableRow {
  id: string;
  operating_point: string;
  battery_state: string;
  kind: string;
  soc: number;
  battery_current: number | null;
  battery_voltage: number | null;
  motor_current: number | null;
  rails: Record<string, number> | null;
}
export interface Rail {
  source: string;
  nominal: number;
  min: number;
  max: number;
  sinks: string[];
}
export interface ElectricalResponse {
  operating_points: OperatingPointsJson | null;
  table: ElectricalTableRow[];
  wires: CurrentRow[];
  connectors: CurrentRow[];
  power_tree: { rails: Rail[]; fuses?: unknown; brownout?: unknown; harness_voltage_drop?: unknown };
  runtime: RuntimeInfo | null;
  thermal: Record<string, Record<string, unknown>> | null;
  images: FileLink[];
  result?: StageResult | null;
  missing: string[];
}

// ------------------------------------------------------------------ pcb
export interface PcbBoardJson {
  board: string;
  component: string;
  outline?: { width: number; height: number };
  nets?: Record<string, string[]>;
  connectors?: Record<string, Record<string, string>>;
  validation?: Record<string, unknown>;
  [key: string]: unknown;
}
export interface PcbCheck {
  errors: number | null;
  warnings?: number | null;
  source?: string;
}
export interface PcbBoard {
  board: string;
  component: string;
  spec: Record<string, unknown>;
  board_json: PcbBoardJson | null;
  renders: FileLink[];
  erc: PcbCheck | null;
  drc: PcbCheck | null;
  /** board pin -> net, per connector */
  connectors: Record<string, Record<string, string>> | null;
  /** catalog connector pin number -> component pin name */
  catalog_connectors: Record<string, Record<string, string>>;
  bom: Record<string, string | number>[] | null;
  gerbers_url: string | null;
  files: FileLink[];
  missing: string[];
}
export interface PcbResponse {
  boards: PcbBoard[];
  result?: StageResult | null;
}

// ------------------------------------------------------------------ software
export interface BindingPin {
  pin: string;
  node?: string;
  net?: string;
  net_name?: string;
  /** component pins on the same net */
  devices?: string[];
  /** component pins reached through internal links (series resistors) */
  reaches?: string[];
  error?: string;
}
export interface Binding extends SpecBinding {
  handle: string;
  pin_map: BindingPin[];
  expects_reached: { pin: string; reached: boolean }[];
}
export interface FirmwareLogLine {
  t: number;
  level: string;
  message: string;
  fields?: Record<string, unknown>;
}
export interface SoftwareResponse {
  bindings: Binding[];
  modules: { name: string; language?: string; runs_on?: string; entrypoint?: string; path?: string; exists?: boolean; files?: string[] }[];
  config?: Record<string, unknown>;
  firmware_logs: { scenario: string; firmware_logs: FirmwareLogLine[]; software?: unknown; software_log_url?: string | null; software_log_tail?: string }[];
  deploy_log_url?: string | null;
}

// ------------------------------------------------------------------ simulation
export interface ScenarioCheck {
  key: string;
  expected: unknown;
  measured: unknown;
  pass: boolean;
  detail?: string;
}
export interface ScenarioLast {
  passed: boolean | null;
  checks: ScenarioCheck[] | null;
  metrics: Record<string, unknown> | null;
  fallbacks?: Record<string, unknown> | null;
  stale: boolean;
  inputs?: Record<string, string> | null;
  summary_url?: string;
  plots: FileLink[];
  renders: FileLink[];
  clip_url: string | null;
  recording: boolean;
  diagnostics: Diagnostic[];
}
export interface ScenarioListItem extends SpecScenario {
  last: ScenarioLast | null;
}
export interface SimListResponse {
  scenarios: ScenarioListItem[];
  result?: StageResult | null;
  overview_url?: string | null;
}
export interface PlaybackFrames {
  root_body?: string;
  t: number[];
  format?: string;
  /** body -> per-frame world pose */
  bodies: Record<string, Pose7[]>;
  /** component -> MuJoCo body that carries it */
  component_bodies: Record<string, string>;
  /** Wire id -> conductor dimensions and deformed pin-to-pin display path. */
  cables: Record<string, { rgba: number[] | null; points: Vec3[][]; wire_id?: string; bundle?: string; harness?: string; od?: number; color?: string }> | null;
  /** Body rest poses (q = 0), when the server provides them. */
  rest?: Record<string, Pose7>;
  error?: string;
}
export interface PlaybackRecording {
  hz: number;
  duration: number;
  channels: string[];
  series: Record<string, (number | null)[]> & { t: number[] };
  firmware_logs?: FirmwareLogLine[];
  events?: unknown[];
  frames: PlaybackFrames | null;
}
export interface ScenarioResponse {
  scenario: SpecScenario | { id: string };
  last: ScenarioLast | null;
  summary: Record<string, unknown> | null;
  recording: PlaybackRecording | null;
}

// ------------------------------------------------------------------ diagnostics
export interface DiagnosticsResponse {
  diagnostics: Diagnostic[];
  counts?: Partial<Record<Severity, number>>;
  by_stage?: Record<string, Record<Severity, number>>;
  stages?: unknown[];
  inputs?: Fingerprint;
}

// ------------------------------------------------------------------ runs & events
export interface RunRequest {
  stages?: string[];
  scenario?: string;
  scenarios?: string[];
}
export interface RunCreated {
  run_id: string;
  status: string;
  events_url: string;
  status_url: string;
}
export interface RunInfo {
  run_id: string;
  project: string;
  kind: string;
  status: RunStatusValue;
  stages: string[] | null;
  scenarios: string[] | null;
  current_stage: string | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  events: number;
  result: Record<string, unknown> | null;
}
export type RunEventType = "run_queued" | "run_started" | "stage_started" | "log" | "progress" | "diagnostic" | "stage_finished" | "run_finished";
/** SSE `data`: {seq, type, time, run_id, ...payload}; see docs/architecture/api.md "Run events". */
export interface RunEvent {
  type: RunEventType | string;
  seq?: number;
  time?: number;
  run_id?: string;
  stage?: string;
  index?: number;
  total?: number;
  stream?: "stdout" | "stderr" | "logging";
  line?: string;
  level?: string;
  message?: string;
  ok?: boolean;
  status?: string;
  counts?: Partial<Record<Severity, number>>;
  diagnostic?: Diagnostic;
  release?: { fabrication_ready: boolean; counts: Record<string, number>; blocking: string[] };
  error?: { type?: string; message?: string; traceback?: string };
  [key: string]: unknown;
}

export interface DeployRequest {
  target: "sim" | "fleet";
  confirm?: boolean;
  scenarios?: string[];
  force?: boolean;
}
export interface DeployResponse {
  ok: boolean;
  target: "sim" | "fleet";
  run_id?: string | null;
  events_url?: string;
  command?: string[] | string;
  returncode?: number;
  log?: string;
  stdout_tail?: string;
  stderr_tail?: string;
  message?: string;
  [key: string]: unknown;
}

export type AgentProvider = "claude" | "codex";
export interface AgentRequest {
  prompt: string;
  provider: AgentProvider;
  model?: string;
}
export interface AgentCreated {
  session_id: string;
  status: string;
  provider: string;
  events_url: string;
}
export interface AgentStatus {
  session_id: string;
  project: string;
  provider: string;
  model: string | null;
  prompt: string;
  status: "starting" | "running" | "completed" | "failed" | "cancelled";
  created_at: number;
  finished_at: number | null;
  returncode: number | null;
  result: string;
  error: string | null;
  events: number;
}
export type AgentEventType =
  | "session_started" | "message" | "tool_call" | "tool_result" | "run_started" | "run_finished" | "spec_changed"
  | "usage" | "stderr" | "raw" | "session_finished";
/** SSE `data` of an agent session; see docs/architecture/api.md "Agent events". */
export interface AgentEvent {
  type: AgentEventType | string;
  seq?: number;
  time?: number;
  session_id?: string;
  role?: "assistant" | "user" | "system" | "thinking" | "error" | "output";
  text?: string;
  /** tool_call / tool_result */
  id?: string;
  name?: string;
  server?: string | null;
  input?: unknown;
  ok?: boolean;
  content?: string;
  /** run_started / run_finished (runs the agent started through MCP) */
  tool_call_id?: string;
  tool?: string;
  stages?: string[];
  scenarios?: string[];
  counts?: Partial<Record<Severity, number>>;
  /** spec_changed / session_finished */
  previous_sha256?: string;
  sha256?: string;
  diff?: string;
  cumulative_diff?: string;
  spec_changed?: boolean;
  status?: string;
  returncode?: number | null;
  result?: string;
  error?: string | null;
  line?: string;
  provider?: string;
  model?: string | null;
  cwd?: string;
  command?: string[] | string;
  input_tokens?: number;
  output_tokens?: number;
  cost_usd?: number;
  [key: string]: unknown;
}

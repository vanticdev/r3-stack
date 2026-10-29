// Typed contract of the R3 HTTP API (`r3 serve`, services/orchestrator/r3_orchestrator/api/).
// Mirrors .r3-runs/ui-brief.md. Artifact payloads are passed through from build/ files, so
// their types below follow the stage hand-off formats in docs/architecture/r3-pipeline.md.
// Fields the UI can live without are optional: the client tolerates older or partial builds.

export type Vec3 = [number, number, number];
/** World pose: position (m) + quaternion (w, x, y, z), as MuJoCo xpos/xquat. */
export type Pose7 = [number, number, number, number, number, number, number];
export type Severity = "error" | "warning" | "info";
export type StageName =
  | "assembly" | "model" | "cad" | "pcb" | "wiring" | "electrical" | "software" | "sim" | "report";

export const STAGES: StageName[] = ["assembly", "model", "cad", "pcb", "wiring", "electrical", "software", "sim", "report"];

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
}

/** `build/<stage>/result.json` */
export interface StageResult {
  stage: StageName | string;
  ok: boolean;
  started_at?: number;
  duration_s?: number;
  diagnostics: Diagnostic[];
  artifacts: ArtifactRef[];
  assumptions: Assumption[];
  metrics: Record<string, unknown>;
  inputs?: Record<string, string | null>;
}

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
export interface DoctorPayload {
  ok: boolean;
  environment: { python: string; platform: string; display: boolean; worker_image: boolean; ci: boolean };
  tools: Record<string, DoctorTool>;
  stages: StageReadiness[];
  /** Agent CLIs (`claude`, `codex`) detected by the API, when it reports them. */
  agents?: Record<string, { available: boolean; version?: string | null; detail?: string }>;
}

// ------------------------------------------------------------------ projects
export type RunStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface ProjectSummary {
  id: string;
  name: string;
  path: string;
  description?: string;
  last_run?: { status: RunStatus | "ok" | "failed" | "none"; finished_at?: number | null; run_id?: string | null } | null;
  ok?: boolean | null;
  counts?: Partial<Record<Severity, number>>;
}

export interface CreateProjectRequest {
  name: string;
  template: string;
  github?: { private: boolean };
  confirm?: boolean;
}
export interface CreateProjectResponse {
  id: string;
  path: string;
  files?: string[];
  github_url?: string | null;
}

export type EvidenceStatus = "verified" | "estimated" | "failed" | "not_run" | "stale";

export interface Evidence {
  stage: string;
  check: string;
  value: number | string | boolean | null;
  limit?: number | string | boolean | null;
  unit?: string;
  artifact?: string | null;
  fingerprint?: string | null;
}

/** One row of the release gate (`r3_orchestrator/release.py`). */
export interface RequirementEvidence {
  id: string;
  kind: "requirement" | "expectation";
  label: string;
  scenario?: string | null;
  requirement: number | string | boolean | null;
  unit?: string;
  status: EvidenceStatus;
  evidence: Evidence | null;
  note?: string;
}

export interface ReleaseGate {
  fabrication_ready: boolean;
  current_fingerprint: string;
  counts: Partial<Record<EvidenceStatus, number>>;
  items: RequirementEvidence[];
}

export interface StageStatus {
  stage: string;
  status: "ok" | "failed" | "missing" | "stale" | "running";
  ok: boolean | null;
  stale: boolean | null;
  counts: Partial<Record<Severity, number>>;
  duration_s?: number | null;
}

export interface ProjectDetail {
  id: string;
  name: string;
  path: string;
  spec: {
    name: string;
    description?: string;
    version?: string;
    requirements: Record<string, unknown>;
    components: number;
    harnesses: number;
    scenarios: string[];
  };
  fingerprint: string;
  lock?: Record<string, unknown> | null;
  stages: StageStatus[];
  results?: Record<string, StageResult>;
  report?: { ok: boolean; counts: Partial<Record<Severity, number>>; generated_at?: number } | null;
  release: ReleaseGate;
  links?: { onshape?: string | null; github?: string | null };
}

export interface SpecDocument {
  yaml: string;
  sha256: string;
  spec: RobotSpec;
}

export interface SpecPatchResponse {
  ok: boolean;
  sha256: string;
  previous_sha256?: string;
  changed?: boolean;
  graph_diagnostics?: Diagnostic[];
}

// ------------------------------------------------------------------ robot.yaml (JSON form)
/** Raw robot.yaml values may be quantities ("20 mm") or SI numbers. */
export type Quantity = number | string;
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
  splices?: Record<string, { position: Vec3 }>;
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
export interface SpecBinding {
  interface: string;
  computer: string;
  pins: string[];
  device?: string;
  channel?: number;
  expects?: string[];
  [key: string]: unknown;
}
export interface SpecScenario {
  id: string;
  description?: string;
  duration: number;
  expect?: Record<string, unknown>;
  [key: string]: unknown;
}
export interface RobotSpec {
  schema?: string;
  robot: { name: string; description?: string; version?: string };
  requirements?: Record<string, unknown>;
  components: Record<string, { type: string; mount?: string; pose?: { xyz?: Vec3; rpy?: Vec3 } }>;
  mechanical?: {
    joints?: SpecJoint[];
    transmissions?: { id: string; type: string; actuator?: string; joints: string[]; ratio?: number; params?: Record<string, number> }[];
  };
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
export interface CatalogComponent {
  id: string;
  kind: string;
  name: string;
  description?: string;
  manufacturer?: string;
  part_number?: string;
  mechanical?: {
    shape?: string;
    /** SI numbers from the API, or raw YAML quantities ("46.5 mm") from the catalog files. */
    size?: (number | string)[];
    mass?: number | string;
    material?: string;
    density?: number;
    color?: string;
    moving?: boolean;
    provenance?: { status?: string; source?: string; notes?: string };
  };
  pins?: Record<string, Record<string, unknown>>;
  connectors?: Record<string, { type: string; position?: Vec3; direction?: Vec3; pins?: Record<string, string> }>;
  interfaces?: Record<string, { kind: string; origin?: Vec3; z?: Vec3; x?: Vec3; params?: Record<string, unknown> }>;
  [key: string]: unknown;
}
export interface Catalog {
  components: Record<string, CatalogComponent>;
  connectors: Record<string, Record<string, unknown>>;
  wires: Record<string, Record<string, unknown>>;
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
  pins?: Record<string, { role: string; passthrough: boolean; connector_pins: string[] }>;
  rails?: { source: string; nominal: number; min: number; max: number; sinks: string[] }[];
  interference?: { a: string; b: string; center: Vec3; extent: Vec3; volume: number }[];
  mass?: { total: number; limit?: number; by_component?: Record<string, { mass: number; source: string }> };
  diagnostics?: Diagnostic[];
}
/** `build/model/assembly.json` (assembly stage; absent for specs without `assembly:`). */
export interface AssemblyJson {
  root?: string;
  joins?: { id: string; a: string; b: string; kind: string; joint?: string | null; frame?: { origin: Vec3; z: Vec3; x?: Vec3 } }[];
  interfaces?: Record<string, { component: string; kind: string; origin: Vec3; z: Vec3; x?: Vec3 }>;
  [key: string]: unknown;
}
export interface ModelResponse {
  graph: ModelGraph;
  assembly: AssemblyJson | null;
  /** Compiled poses (robot frame) per component. */
  poses: Record<string, { xyz: Vec3; rpy: Vec3 }>;
  joints: SpecJoint[];
  transmissions?: NonNullable<RobotSpec["mechanical"]>["transmissions"];
}

// ------------------------------------------------------------------ cad
export interface CadPart {
  part_name?: string;
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
  cad_model: {
    source?: { provider?: string; url?: string; document_id?: string; [key: string]: unknown };
    parts: Record<string, CadPart>;
    contacts?: { contacts?: [string, string, number, number][] } | [string, string, number, number][];
  } | null;
  /** component id -> URL of its STL (robot frame, metres). */
  meshes: Record<string, string>;
  onshape_url?: string | null;
  views?: { name: string; url: string }[];
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
  /** Optional cable timeline (same shape as playback cables) when the API provides it. */
  timeline?: { t: number[]; cables: PlaybackCable[] };
}
export interface WiringResponse {
  routes: RoutesJson | null;
  routing_detail: RoutingDetail | null;
  bom: Record<string, string>[];
  clearances: RoutesJson["clearances"];
  slack_sim: SlackSim | null;
  images?: { name: string; url: string }[];
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
export interface OperatingPointsJson {
  schema?: string;
  operating_points: Record<string, OperatingPoint>;
  worst_case?: { wires?: Record<string, { continuous_current: number; continuous_point: string; ampacity: number; burst_current?: number; burst_point?: string; burst_ratio?: number; awg: number; harness: string }> };
  runtime?: { runtime: number; requirement: number; capacity_ah?: number; cutoff_voltage?: number; curve?: { soc: number; time: number; average_current: number; cruise_voltage: number; full_throttle_voltage: number }[] };
  thermal?: Record<string, Record<string, unknown>>;
  brownout?: Record<string, unknown>;
  fuses?: Record<string, Record<string, unknown>>;
  [key: string]: unknown;
}
export interface CurrentRating {
  id: string;
  kind: "wire" | "connector";
  harness?: string;
  current: number;
  rating: number;
  point: string;
  burst_current?: number;
  burst_rating?: number;
  burst_point?: string;
}
export interface PowerTree {
  nodes: { id: string; label: string; kind: string }[];
  edges: { from: string; to: string; rail: string; nominal?: number }[];
}
export interface ElectricalResponse {
  operating_points: OperatingPointsJson | null;
  wires: CurrentRating[];
  connectors: CurrentRating[];
  power_tree: PowerTree;
  rails: NonNullable<ModelGraph["rails"]>;
  images?: { name: string; url: string }[];
}

// ------------------------------------------------------------------ pcb
export interface PcbBoard {
  board: string;
  component: string;
  outline?: { width: number; height: number };
  nets?: Record<string, string[]>;
  connectors: Record<string, Record<string, string>>;
  erc?: { errors: number | null; warnings: number | null; source?: string };
  drc?: { errors: number | null; warnings?: number | null; source?: string };
  validation?: Record<string, unknown>;
  renders: Record<string, string>;
  gerber_url?: string | null;
  bom: Record<string, string>[];
  /** Connector pin -> board net vs the catalog's pin -> net map. */
  pin_map: { connector: string; pin: string; board_net: string | null; catalog_net: string | null; match: boolean }[];
  diagnostics?: Diagnostic[];
}
export interface PcbResponse {
  boards: PcbBoard[];
}

// ------------------------------------------------------------------ software
export interface BindingChain {
  handle: string;
  interface: string;
  computer: string;
  device?: string;
  channel?: number | null;
  pins: { pin: string; logical?: string | null; net: string | null; devices: string[] }[];
  expects: string[];
  ok: boolean;
}
export interface SoftwareResponse {
  bindings: BindingChain[];
  modules: { name: string; language?: string; runs_on?: string; entrypoint?: string; path?: string; files?: string[] }[];
  firmware_logs: { t: number; level: string; message: string; fields?: Record<string, unknown> }[];
  logs_source?: string | null;
  software_log?: string | null;
}

// ------------------------------------------------------------------ simulation
export interface ScenarioCheck {
  key: string;
  expected: unknown;
  measured: unknown;
  pass: boolean;
  detail?: string;
}
export interface ScenarioSummary {
  scenario: string;
  description?: string;
  passed: boolean;
  duration: number;
  checks: ScenarioCheck[];
  metrics: Record<string, number | string | boolean | null>;
  firmware_logs?: SoftwareResponse["firmware_logs"];
  diagnostics?: Diagnostic[];
  inputs?: Record<string, string | null>;
  [key: string]: unknown;
}
export interface ScenarioListItem {
  id: string;
  description?: string;
  duration: number;
  expect?: Record<string, unknown>;
  last: { passed: boolean; checks: ScenarioCheck[]; metrics?: Record<string, unknown>; stale?: boolean } | null;
}
export interface PlaybackCable {
  id: string;
  harness?: string;
  wires?: string[];
  colors?: string[];
  od?: number;
  /** Per frame, the cable body positions in world (m). */
  points: Vec3[][];
}
export interface PlaybackRecording {
  schema?: string;
  scenario: string;
  hz: number;
  duration: number;
  series: Record<string, number[]> & { t: number[] };
  bodies: { names: string[]; nominal: Record<string, Pose7>; t: number[]; poses: Record<string, Pose7[]> };
  cables: PlaybackCable[];
  firmware_logs?: SoftwareResponse["firmware_logs"];
  events?: unknown[];
}
export interface ScenarioResponse {
  summary: ScenarioSummary;
  recording: PlaybackRecording | null;
  renders: { name: string; url: string }[];
  clip_url?: string | null;
}

// ------------------------------------------------------------------ runs & events
export interface RunRequest {
  stages?: string[];
  scenario?: string;
}
export interface RunInfo {
  run_id: string;
  project: string;
  status: RunStatus;
  stages?: string[];
  scenario?: string | null;
  created_at?: number;
  started_at?: number | null;
  finished_at?: number | null;
  current_stage?: string | null;
}
export type RunEventType = "stage_started" | "log" | "progress" | "diagnostic" | "stage_finished" | "run_finished";
export interface RunEvent {
  type: RunEventType | string;
  run_id?: string;
  stage?: string;
  message?: string;
  level?: string;
  fraction?: number;
  ok?: boolean;
  status?: RunStatus;
  diagnostic?: Diagnostic;
  t?: number;
  [key: string]: unknown;
}

export interface DeployRequest {
  target: "sim" | "fleet";
  confirm?: boolean;
}
export interface DeployResponse {
  ok: boolean;
  target: string;
  run_id?: string | null;
  message?: string;
  [key: string]: unknown;
}

export type AgentProvider = "claude" | "codex";
export interface AgentRequest {
  prompt: string;
  provider: AgentProvider;
  model?: string;
}
export type AgentEventType =
  | "session_started" | "message" | "tool_call" | "tool_result" | "run_started" | "run_finished"
  | "spec_changed" | "error" | "session_finished";
export interface AgentEvent {
  type: AgentEventType | string;
  session_id?: string;
  role?: "assistant" | "user" | "system";
  text?: string;
  tool?: string;
  input?: unknown;
  output?: unknown;
  is_error?: boolean;
  run_id?: string;
  sha256?: string;
  status?: string;
  t?: number;
  [key: string]: unknown;
}

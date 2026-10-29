// build/connectors/connectors.json (r3_connectors stage): connector frames, exact pins, mated pairs and
// pin-to-pin wire display paths, all in the robot frame (SI). Served as a build artifact, so it needs no
// dedicated API route; the hook returns null when the stage has not run.
import { useQuery } from "@tanstack/react-query";

import type { Vec3 } from "./types.ts";
import { api } from "./client.ts";
import { keys } from "./hooks.ts";

export interface FrameJson { origin: Vec3; x: Vec3; y: Vec3; z: Vec3 }

export interface ConnectorTypeJson {
  family: string;
  gender: string;
  mates_with: string[];
  source: "kicad" | "vendor" | "kicad_board" | "proxy";
  model_path: string;
  license: string;
  footprint: string;
  pin_shape: "square_post" | "socket" | "blade" | "crimp" | "bullet" | "solder_cup" | "coax";
  pin_size: number;
  color: string;
  contact_color: string;
  envelope: number[];
  glb: string | null;
  pins: Record<string, { position: Vec3; axis: Vec3; tip: number; pad: Vec3 | null; wire_entry: Vec3 | null }>;
}

export interface ComponentPinJson {
  signal: string;
  bindings: string[];
  net: string[];
  contact?: Vec3;
  axis?: Vec3;
  tip?: Vec3;
  pad?: Vec3;
}

export interface HarnessPinJson {
  wire?: string;
  color?: string;
  signal?: string;
  awg?: number;
  to?: string;
  contact?: Vec3;
  wire_entry?: Vec3;
  wire_axis?: Vec3;
}

export interface ConnectorJson {
  owner: "component" | "harness";
  component?: string;
  harness?: string;
  name: string;
  type: string;
  frame: FrameJson;
  embedded?: boolean;
  source?: string;
  mates?: string | null;
  placement?: "component" | "pigtail" | "floating";
  glb: string | null;
  pins: Record<string, ComponentPinJson & HarnessPinJson>;
}

export interface MatedPairJson {
  a: string;
  b: string;
  kind: "harness-component" | "direct" | "floating";
  types: [string, string];
  axial_gap: number;
  lateral_offset: number;
  tilt_deg: number;
  clocking_deg: number;
  max_pin_lateral_error: number;
  contact_overlap: number;
  center: Vec3;
}

export interface WireEndJson { connector: string; pin: string; kind: string; position: Vec3; direction: Vec3; contact: Vec3; error?: number }

export interface ConnectorsJson {
  units: string;
  types: Record<string, ConnectorTypeJson>;
  components: Record<string, { type: string; kind: string; name: string; frame: FrameJson; glb: string | null; source?: string; license?: string; url?: string }>;
  connectors: Record<string, ConnectorJson>;
  mated_pairs: MatedPairJson[];
  wires: Record<string, { harness: string; ends: (WireEndJson | null)[]; fanout?: [number, number]; display_path?: Vec3[]; od?: number }>;
  checks: Record<string, unknown>;
}

async function fetchConnectors(pid: string, signal?: AbortSignal): Promise<ConnectorsJson | null> {
  const response = await fetch(api.artifactUrl(pid, "connectors/connectors.json"), { signal, headers: { Accept: "application/json" } });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`connectors.json: HTTP ${response.status}`);
  return (await response.json()) as ConnectorsJson;
}

export const useConnectors = (pid: string) =>
  useQuery({ queryKey: keys.section(pid, "connectors"), queryFn: ({ signal }) => fetchConnectors(pid, signal), retry: false });

/** URL of a GLB written by the connectors stage (paths in connectors.json are relative to build/connectors/). */
export const connectorAssetUrl = (pid: string, relative: string) => api.artifactUrl(pid, `connectors/${relative}`);

/** Harness id of a wire/connector reference, for "show the mated pairs of the selected harness". */
export function harnessOf(data: ConnectorsJson, selection: { kind: "component" | "wire"; id: string } | null): string | null {
  if (!selection) return null;
  if (selection.kind === "wire") return data.wires[selection.id]?.harness ?? null;
  return null;
}

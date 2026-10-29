// Harness route hints (robot.yaml `electrical.harnesses[].route.via / tie_points`) <-> editable 3D handles.
import type { RobotSpec, Vec3 } from "../api/types.ts";
import { resamplePolyline } from "./transforms.ts";
import { vector } from "./units.ts";

/** Authored hints in SI, or `null` when the harness has none. */
export function authoredHints(spec: RobotSpec, harness: string): { via: Vec3[]; ties: Vec3[] } | null {
  const route = spec.electrical?.harnesses?.find((h) => h.id === harness)?.route;
  const via = (route?.via ?? []).map(vector);
  const ties = (route?.tie_points ?? []).map(vector);
  return via.length || ties.length ? { via, ties } : null;
}

/** Initial handles: authored hints, else interior points sampled along the routed bundle path. */
export function initialHandles(spec: RobotSpec, harness: string, bundlePath: readonly Vec3[] | undefined,
                               routedTies: readonly Vec3[] | undefined, count = 4): { via: Vec3[]; ties: Vec3[] } {
  const authored = authoredHints(spec, harness);
  if (authored) return { via: authored.via, ties: authored.ties.length ? authored.ties : [...(routedTies ?? [])] };
  const samples = bundlePath && bundlePath.length > 1 ? resamplePolyline(bundlePath, count + 2).slice(1, -1) : [];
  return { via: samples, ties: [...(routedTies ?? [])] };
}

const mm = (v: number) => `${(Math.round(v * 10000) / 10).toFixed(1)} mm`;

/** JSON merge patch that sets one harness's route hints. Harnesses are a YAML list, which a merge
 * patch replaces whole, so the patch carries the full list with only this harness's route changed. */
export function routePatch(spec: RobotSpec, harness: string, via: readonly Vec3[], ties: readonly Vec3[]): Record<string, unknown> {
  const harnesses = spec.electrical?.harnesses ?? [];
  if (!harnesses.some((h) => h.id === harness)) throw new Error(`harness ${harness} is not in robot.yaml`);
  return {
    electrical: {
      harnesses: harnesses.map((h) => h.id !== harness ? h : {
        ...h,
        route: { ...(h.route ?? {}), via: via.map((p) => p.map(mm)), tie_points: ties.map((p) => p.map(mm)) },
      }),
    },
  };
}

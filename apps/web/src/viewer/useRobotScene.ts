import { useMemo } from "react";

import { connectorAssetUrl, useConnectors, type ConnectorsJson } from "../api/connectors.ts";
import { useCad, useDiagnostics, useModel, useSpec, useWiring } from "../api/hooks.ts";
import type { AssemblyJson, CadPart, Diagnostic, GraphComponent, GraphEndpoint, RoutesJson, SpecJoint, Vec3 } from "../api/types.ts";
import { buildScene, diagnosticsBySubject, withConnectorDisplayPaths, type SceneComponent, type SceneWire } from "../lib/scene.ts";
import { componentCenter, jointChains, mountTree, type MountTree } from "../lib/transforms.ts";
import { quantity } from "../lib/units.ts";

export interface RobotData {
  components: SceneComponent[];
  /** CAD-only parts (fasteners) drawn with their owner component. */
  hardware: { id: string; owner: string; url: string; kind: string }[];
  wires: SceneWire[];
  graphComponents: Record<string, GraphComponent>;
  meshes: Record<string, string>;
  cadParts: Record<string, CadPart>;
  joints: SpecJoint[];
  /** Ackermann linkage parameters (SI) from robot.yaml `mechanical.transmissions`, when declared. */
  steering: { joints: string[]; wheelbase: number; track: number; maxSteer: number } | null;
  tree: MountTree;
  chains: Record<string, SpecJoint[]>;
  centers: Record<string, Vec3>;
  endpoints: Record<string, GraphEndpoint>;
  contacts: [string, string][];
  interference: { a: string; b: string; center: Vec3; extent: Vec3 }[];
  routes: RoutesJson | null;
  diagnostics: Diagnostic[];
  bySubject: Record<string, Diagnostic[]>;
  assembly: AssemblyJson | null;
  massSource: Record<string, string>;
  /** build/connectors/connectors.json (exact connector frames, pins, mated pairs), when the stage has run. */
  connectors?: ConnectorsJson | null;
  /** URL of a build/connectors/ asset (GLB models). */
  connectorAsset?: (relative: string) => string;
}

/** Everything the 3D view needs, from /model, /cad, /wiring and /diagnostics. */
export function useRobotScene(pid: string) {
  const model = useModel(pid);
  const cad = useCad(pid);
  const wiring = useWiring(pid);
  const diagnostics = useDiagnostics(pid);
  const spec = useSpec(pid);
  const connectors = useConnectors(pid);

  const data = useMemo<RobotData | null>(() => {
    const graph = model.data?.graph;
    if (!model.data || !graph || cad.data?.authority?.ready !== true) return null;
    const diags = diagnostics.data?.diagnostics ?? [];
    const routes = wiring.data?.routes ?? null;
    const scene = buildScene({ graph, routes, routingDetail: wiring.data?.routing_detail ?? null, diagnostics: diags });
    const tree = mountTree(graph.components);
    const rawContacts = cad.data?.cad_model?.contacts;
    const contactList = Array.isArray(rawContacts) ? rawContacts : rawContacts?.contacts ?? [];
    const centers: Record<string, Vec3> = {};
    const cadParts = cad.data?.cad_model?.parts ?? {};
    for (const [id, c] of Object.entries(graph.components)) {
      const part = cadParts[id];
      centers[id] = part?.bbox_min && part.bbox_max ? componentCenter({ xyz: c.xyz, aabb_min: part.bbox_min, aabb_max: part.bbox_max }) : componentCenter(c);
    }
    const ackermann = spec.data?.spec.mechanical?.transmissions?.find((t) => t.type === "ackermann_steering");
    const steering = ackermann ? {
      joints: ackermann.joints, wheelbase: quantity(ackermann.params?.wheelbase ?? 0), track: quantity(ackermann.params?.kingpin_track ?? 0),
      maxSteer: quantity(ackermann.params?.max_steer ?? 0.45),
    } : null;
    // Moving joints: authored ones, plus those the assembly compiler derives from revolute/slider joins.
    // Older/mock APIs omit derived_joints even when the compiled artifact contains them.
    const derived = model.data.derived_joints?.length ? model.data.derived_joints : model.data.assembly?.joints ?? [];
    const joints = [...model.data.joints, ...derived.filter((d) => !model.data!.joints.some((j) => j.id === d.id))];
    const massSource: Record<string, string> = {};
    for (const [id, m] of Object.entries(graph.mass?.by_component ?? {})) massSource[id] = m.source;
    // Pin-to-pin display paths: conductors of a bundle side by side, each ending on its pin.
    const pinWires = connectors.data?.wires ?? {};
    const wires = withConnectorDisplayPaths(scene.wires, pinWires);
    return {
      components: scene.components, wires, graphComponents: graph.components,
      hardware: Object.entries(cadParts).flatMap(([id, part]) => {
        const url = cad.data?.meshes[id];
        const owner = part.owner && part.owner in graph.components ? part.owner : id;
        return url && owner && !(id in graph.components) ? [{ id, owner, url, kind: part.kind ?? "hardware" }] : [];
      }),
      meshes: cad.data?.meshes ?? {}, cadParts, joints, steering,
      tree, chains: jointChains(tree, joints), centers,
      endpoints: graph.endpoints ?? {}, contacts: contactList.map((c) => [c[0], c[1]] as [string, string]),
      interference: scene.interference, routes, diagnostics: diags, bySubject: diagnosticsBySubject(diags),
      assembly: model.data.assembly, massSource,
      connectors: connectors.data ?? null, connectorAsset: (relative: string) => connectorAssetUrl(pid, relative),
    };
  }, [model.data, cad.data, wiring.data, diagnostics.data, spec.data, connectors.data, pid]);

  return {
    data,
    isPending: model.isPending || (cad.isPending && !cad.isError),
    error: model.error ?? cad.error ?? (cad.data && cad.data.authority?.ready !== true
      ? new Error(cad.data.authority?.message ?? "No pulled CAD. Model and mate in Onshape, then run r3 cad pull.")
      : model.data && !model.data.graph && !cad.isPending ? new Error("build/model/graph.json is missing: run the model stage") : null),
    wiring: wiring.data ?? null,
    cad: cad.data ?? null,
  };
}

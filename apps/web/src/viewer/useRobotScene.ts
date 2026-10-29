import { useMemo } from "react";

import { useCad, useDiagnostics, useModel, useWiring } from "../api/hooks.ts";
import type { CadPart, Diagnostic, GraphComponent, GraphEndpoint, ModelResponse, RoutesJson, SpecJoint, Vec3 } from "../api/types.ts";
import { buildScene, diagnosticsBySubject, type SceneComponent, type SceneWire } from "../lib/scene.ts";
import { componentCenter, jointChains, mountTree, type MountTree } from "../lib/transforms.ts";

export interface RobotData {
  components: SceneComponent[];
  wires: SceneWire[];
  graphComponents: Record<string, GraphComponent>;
  meshes: Record<string, string>;
  cadParts: Record<string, CadPart>;
  joints: SpecJoint[];
  transmissions: ModelResponse["transmissions"];
  tree: MountTree;
  chains: Record<string, SpecJoint[]>;
  centers: Record<string, Vec3>;
  endpoints: Record<string, GraphEndpoint>;
  contacts: [string, string][];
  interference: { a: string; b: string; center: Vec3; extent: Vec3 }[];
  routes: RoutesJson | null;
  diagnostics: Diagnostic[];
  bySubject: Record<string, Diagnostic[]>;
  assembly: ModelResponse["assembly"];
  massSource: Record<string, string>;
}

/** Everything the 3D view needs, from /model, /cad, /wiring and /diagnostics. */
export function useRobotScene(pid: string) {
  const model = useModel(pid);
  const cad = useCad(pid);
  const wiring = useWiring(pid);
  const diagnostics = useDiagnostics(pid);

  const data = useMemo<RobotData | null>(() => {
    if (!model.data) return null;
    const graph = model.data.graph;
    const diags = diagnostics.data ?? [];
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
    const massSource: Record<string, string> = {};
    for (const [id, m] of Object.entries(graph.mass?.by_component ?? {})) massSource[id] = m.source;
    return {
      components: scene.components, wires: scene.wires, graphComponents: graph.components,
      meshes: cad.data?.meshes ?? {}, cadParts, joints: model.data.joints ?? [], transmissions: model.data.transmissions ?? [],
      tree, chains: jointChains(tree, model.data.joints ?? []), centers,
      endpoints: graph.endpoints ?? {}, contacts: contactList.map((c) => [c[0], c[1]] as [string, string]),
      interference: scene.interference, routes, diagnostics: diags, bySubject: diagnosticsBySubject(diags),
      assembly: model.data.assembly, massSource,
    };
  }, [model.data, cad.data, wiring.data, diagnostics.data]);

  return {
    data,
    isPending: model.isPending || (cad.isPending && !cad.isError),
    error: model.error,
    wiring: wiring.data ?? null,
    cad: cad.data ?? null,
  };
}

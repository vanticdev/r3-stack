import { useMemo } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams, useSearchParams } from "react-router-dom";

import { useProject } from "../api/hooks.ts";
import { api } from "../api/client.ts";
import { NativeMeshPreview } from "../viewer/NativeMeshPreview.tsx";
import { Icon } from "../components/icons.tsx";
import { Empty, ErrorBox } from "../components/ui.tsx";
import { timeAgo } from "../lib/units.ts";
import { useProjectRuns } from "../state/runs.tsx";
import { Inspector } from "../viewer/Inspector.tsx";
import { RobotViewer, type Selection } from "../viewer/RobotViewer.tsx";
import { useRobotScene } from "../viewer/useRobotScene.ts";

/** ?sel=component:esc selects; ?focus=a,b highlights diagnostic subjects; ?int=3 selects an interference region. */
export function useSelectionParams() {
  const [params, setParams] = useSearchParams();
  const sel = params.get("sel");
  const selection = useMemo<Selection | null>(() => {
    if (!sel) return null;
    const [kind, ...id] = sel.split(":");
    return kind === "component" || kind === "wire" ? { kind, id: id.join(":") } : null;
  }, [sel]);
  const focusParam = params.get("focus");
  const focus = useMemo(() => (focusParam ? focusParam.split(",").filter(Boolean) : []), [focusParam]);
  const intParam = params.get("int");
  const interference = intParam !== null && /^\d+$/.test(intParam) ? Number(intParam) : null;
  const update = (changes: Record<string, string | null>) => {
    setParams((p) => {
      const next = new URLSearchParams(p);
      for (const [k, v] of Object.entries(changes)) (v ? next.set(k, v) : next.delete(k));
      return next;
    }, { replace: true });
  };
  return {
    selection, focus, interference,
    select: (s: Selection | null) => update({ sel: s ? `${s.kind}:${s.id}` : null, int: null }),
    setFocus: (subjects: string[]) => update({ focus: subjects.length ? subjects.join(",") : null }),
    selectInterference: (index: number | null, parts: string[] = []) =>
      update({ int: index === null ? null : String(index), sel: null, focus: parts.length ? parts.join(",") : null }),
  };
}

/** Whether the displayed model matches the current inputs. */
function ModelRevision({ pid }: { pid: string }) {
  const project = useProject(pid);
  const { start, active } = useProjectRuns(pid);
  const rows = (project.data?.stages ?? []).filter((s) => ["assembly", "model", "cad"].includes(s.stage) && s.status !== "missing");
  if (!rows.length) return null;
  const outdated = rows.some((s) => s.stale);
  const built = Math.max(...rows.map((s) => s.started_at ?? 0));
  return outdated ? (
    <span className="viewer-badge outdated" data-testid="model-revision">
      <Icon name="clock" size={12} />Model out of date · inputs changed
      <button className="small" disabled={!!active} onClick={() => void start({ stages: rows.filter((s) => s.stale).map((s) => s.stage) })}>Rebuild</button>
    </span>
  ) : <span className="viewer-badge" data-testid="model-revision"><Icon name="check" size={12} />Model current{built ? ` · built ${timeAgo(built)}` : ""}</span>;
}

export function ThreeDPage() {
  const { pid = "" } = useParams();
  const { data, isPending, error, cad } = useRobotScene(pid);
  const client = useQueryClient();
  const preview = useMutation({ mutationFn: () => api.refreshCadPreview(pid),
    onSuccess: () => client.invalidateQueries({ queryKey: ["project", pid] }) });
  const { selection, focus, interference, select, setFocus, selectInterference } = useSelectionParams();
  if (error) return <div style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}>
    <div style={{ padding: 16 }}><ErrorBox error={error} />
      <button disabled={preview.isPending} onClick={() => void preview.mutate()}>
        {preview.isPending ? "Reading loaded Onshape geometry…" : cad?.preview ? "Refresh Onshape preview" : "Load Onshape preview"}
      </button>
      {(preview.error || cad?.preview_error) && <ErrorBox error={preview.error ?? new Error(cad?.preview_error ?? "")} />}
    </div>
    {cad?.preview && <NativeMeshPreview data={cad.preview} />}
  </div>;
  if (isPending || !data) return <div className="skeleton" style={{ margin: 20, flex: 1 }} />;
  if (!data.components.length) return <Empty title="No compiled model">Run the 3D model stage.</Empty>;
  return (
    <div className="workspace">
      <RobotViewer data={data} selected={selection} onSelect={select} highlight={focus}
                   selectedInterference={interference}
                   onInterference={(i) => { const o = data.interference[i]!; selectInterference(i, [o.a, o.b]); }}
                   toolbarExtra={<>
                     {focus.length > 0 && <div className="group"><button onClick={() => { setFocus([]); selectInterference(null); }}>Clear highlight</button></div>}
                     {cad?.authority?.code === 'cad_mesh_prototype'
                       ? <span className="viewer-badge outdated">Onshape mesh prototype · qualification pending</span>
                       : <ModelRevision pid={pid} />}
                   </>} />
      <Inspector data={data} selection={selection} interference={interference} onSelect={select}
                 onClear={() => { select(null); if (interference !== null) selectInterference(null); }} />
    </div>
  );
}

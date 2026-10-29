import { useMemo } from "react";
import { useParams, useSearchParams } from "react-router-dom";

import { Empty, ErrorBox } from "../components/ui.tsx";
import { Inspector } from "../viewer/Inspector.tsx";
import { RobotViewer, type Selection } from "../viewer/RobotViewer.tsx";
import { useRobotScene } from "../viewer/useRobotScene.ts";

/** ?sel=component:esc selects; ?focus=a,b highlights diagnostic subjects. */
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
  const update = (changes: Record<string, string | null>) => {
    setParams((p) => {
      const next = new URLSearchParams(p);
      for (const [k, v] of Object.entries(changes)) (v ? next.set(k, v) : next.delete(k));
      return next;
    }, { replace: true });
  };
  return {
    selection, focus,
    select: (s: Selection | null) => update({ sel: s ? `${s.kind}:${s.id}` : null }),
    setFocus: (subjects: string[]) => update({ focus: subjects.length ? subjects.join(",") : null }),
  };
}

export function ThreeDPage() {
  const { pid = "" } = useParams();
  const { data, isPending, error } = useRobotScene(pid);
  const { selection, focus, select, setFocus } = useSelectionParams();
  if (error) return <div style={{ padding: 20 }}><ErrorBox error={error} /></div>;
  if (isPending || !data) return <div className="skeleton" style={{ margin: 20, flex: 1 }} />;
  if (!data.components.length) return <Empty title="No compiled model">Run the model stage.</Empty>;
  return (
    <div className="workspace">
      <RobotViewer data={data} selected={selection} onSelect={select} highlight={focus} initialOptions={{ joints: false }}
                   toolbarExtra={focus.length ? <div className="group"><button onClick={() => setFocus([])}>Clear highlight</button></div> : null} />
      <Inspector data={data} selection={selection} onClear={() => select(null)} />
    </div>
  );
}

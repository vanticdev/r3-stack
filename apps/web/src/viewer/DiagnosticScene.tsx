import { ErrorBox } from "../components/ui.tsx";
import { RobotViewer, type Marker } from "./RobotViewer.tsx";
import { useRobotScene } from "./useRobotScene.ts";

export default function DiagnosticScene({ pid, highlight, markers, status }: {
  pid: string; highlight: string[]; markers: Marker[]; status?: string;
}) {
  const { data, error } = useRobotScene(pid);
  if (error) return <ErrorBox error={error} />;
  return data ? <RobotViewer data={data} highlight={highlight} markers={markers} status={status} showJointPanel={false} />
    : <div className="skeleton" style={{ margin: 20, flex: 1 }} />;
}

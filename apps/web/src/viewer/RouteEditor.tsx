// Drag harness route waypoints (robot.yaml `route.via`) and tie points (`route.tie_points`) in 3D.
import { Line, TransformControls } from "@react-three/drei";
import { useState } from "react";
import type { Mesh } from "three";

import type { Vec3 } from "../api/types.ts";

export interface RouteEditState {
  harness: string;
  /** Route waypoints (via), robot frame, metres. */
  via: Vec3[];
  ties: Vec3[];
  /** Fixed bundle end points (connector exits), drawn for context. */
  ends: [Vec3, Vec3] | null;
  onChange: (via: Vec3[], ties: Vec3[]) => void;
}

type HandleRef = { kind: "via" | "tie"; index: number };

export function RouteEditor({ editor }: { editor: RouteEditState }) {
  const [active, setActive] = useState<HandleRef | null>(null);
  const [object, setObject] = useState<Mesh | null>(null);
  const preview: Vec3[] = editor.ends ? [editor.ends[0], ...editor.via, editor.ends[1]] : editor.via;

  const move = () => {
    if (!active || !object) return;
    const p: Vec3 = [object.position.x, object.position.y, object.position.z];
    if (active.kind === "via") editor.onChange(editor.via.map((v, i) => (i === active.index ? p : v)), editor.ties);
    else editor.onChange(editor.via, editor.ties.map((v, i) => (i === active.index ? p : v)));
  };

  const handle = (kind: HandleRef["kind"], point: Vec3, index: number) => {
    const isActive = active?.kind === kind && active.index === index;
    return (
      <mesh key={`${kind}${index}`} position={point} name={`${kind}-${index}`}
            ref={(m) => { if (isActive && m && m !== object) setObject(m); }}
            onClick={(e) => { e.stopPropagation(); setActive({ kind, index }); }}>
        {kind === "via" ? <sphereGeometry args={[0.0042, 18, 14]} /> : <boxGeometry args={[0.006, 0.006, 0.006]} />}
        <meshStandardMaterial color={isActive ? "#ffffff" : kind === "via" ? "#f97316" : "#1baf7a"} emissive={kind === "via" ? "#f97316" : "#1baf7a"}
                              emissiveIntensity={0.5} depthTest={false} transparent />
      </mesh>
    );
  };

  return (
    <group>
      {preview.length > 1 && <Line points={preview} color="#f97316" lineWidth={2} dashed dashSize={0.006} gapSize={0.004} depthTest={false} />}
      {editor.ends?.map((p, i) => (
        <mesh key={`end${i}`} position={p}><sphereGeometry args={[0.003, 12, 10]} /><meshBasicMaterial color="#8f8e86" depthTest={false} transparent /></mesh>
      ))}
      {editor.via.map((p, i) => handle("via", p, i))}
      {editor.ties.map((p, i) => handle("tie", p, i))}
      {active && object && <TransformControls object={object} mode="translate" size={0.55} onObjectChange={move} />}
    </group>
  );
}

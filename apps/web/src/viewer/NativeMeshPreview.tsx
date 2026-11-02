import { Bounds, GizmoHelper, GizmoViewport, OrbitControls } from "@react-three/drei";
import { Canvas, useLoader } from "@react-three/fiber";
import { Component, Suspense, useEffect, useMemo, type ReactNode } from "react";
import { DoubleSide, Matrix4, Mesh, MeshStandardMaterial } from "three";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";

import type { CadResponse } from "../api/types.ts";

type Preview = NonNullable<CadResponse["preview"]>;

function NativePart({ part }: { part: Preview["parts"][number] }) {
  const source = useLoader(OBJLoader, part.mesh_url);
  const material = useMemo(() => new MeshStandardMaterial({ color: "#6b9ebf", roughness: 0.65, side: DoubleSide }), []);
  useEffect(() => () => material.dispose(), [material]);
  const object = useMemo(() => {
    const clone = source.clone(true);
    clone.traverse((node) => { if (node instanceof Mesh) node.material = material; });
    return clone;
  }, [source, material]);
  const pose = useMemo(() => new Matrix4().fromArray(part.pose.flat()).transpose(), [part.pose]);
  return <group matrix={pose} matrixAutoUpdate={false}><primitive object={object} /></group>;
}

class PreviewBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
  override state = { error: null as string | null };
  static getDerivedStateFromError(error: Error) { return { error: error.message }; }
  override render() {
    return this.state.error ? <p role="alert">Native mesh preview could not load: {this.state.error}</p> : this.props.children;
  }
}

/** Display-only native meshes never supply data to the wiring or simulation pages. */
export function NativeMeshPreview({ data }: { data: Preview }) {
  return <section style={{ display: "flex", flexDirection: "column", minHeight: 440, flex: 1 }} data-testid="native-mesh-preview">
    <div style={{ padding: "10px 16px", display: "flex", gap: 12, flexWrap: "wrap" }}>
      <strong>Onshape inspection preview</strong>
      <span>{data.parts.length} of {data.total} occurrences loaded</span>
      <span>Captured {new Date(data.captured_at).toLocaleTimeString()}</span>
      <a href={data.url} target="_blank" rel="noreferrer">Open in Onshape</a>
    </div>
    <p style={{ padding: "0 16px", margin: "0 0 8px", color: "var(--muted-foreground)" }}>
      Current native render meshes and placements. This assembly may be unfinished; fit and fabrication qualification remain pending.
      {data.unavailable.length > 0 && ` ${data.unavailable.length} occurrences have no loaded geometry.`}
    </p>
    <div style={{ position: "relative", flex: 1, minHeight: 380 }}>
      <PreviewBoundary key={data.microversion}>
        <Canvas camera={{ position: [0.6, -0.6, 0.45], near: 0.001, far: 100 }} onCreated={({ camera }) => camera.up.set(0, 0, 1)}>
          <ambientLight intensity={1.1} /><directionalLight position={[2, -3, 5]} intensity={2} />
          <OrbitControls makeDefault />
          <Suspense fallback={null}>
            <Bounds fit clip observe margin={1.25}>
              {data.parts.map((part) => <NativePart key={part.part_index} part={part} />)}
            </Bounds>
          </Suspense>
          <GizmoHelper alignment="bottom-right" margin={[60, 60]}><GizmoViewport /></GizmoHelper>
        </Canvas>
      </PreviewBoundary>
    </div>
  </section>;
}

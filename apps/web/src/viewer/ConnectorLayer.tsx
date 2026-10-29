// Connectors and pins from build/connectors: detailed component models (official Raspberry Pi 5 STEP,
// the KiCad HAT board), connector bodies (KiCad library models or datasheet proxies) posed at their
// exact frames, every contact as a pickable pin (bound pins highlighted, hover = pin number, signal,
// net, binding), and, for the selected harness, its mated connector pairs with their alignment.
import { Html } from "@react-three/drei";
import { useLoader, type ThreeEvent } from "@react-three/fiber";
import { Component, Suspense, useMemo, useState, type ReactNode } from "react";
import { BoxGeometry, Color, Matrix4, Mesh, MeshStandardMaterial, Object3D, Quaternion, SphereGeometry, Vector3 } from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

import type { ConnectorJson, ConnectorsJson, FrameJson, MatedPairJson } from "../api/connectors.ts";
import type { Vec3 } from "../api/types.ts";
import { wireColor } from "../lib/scene.ts";

export const BUS_COLORS: Record<string, string> = { pwm: "#ff7a00", uart: "#00a6d6", i2c: "#b03cd6", gpio: "#2bb24c", spi: "#d6b300" };
const PIN1 = "#ffffff";
const IDENTITY = new Matrix4();

export function frameMatrix(frame: FrameJson): Matrix4 {
  const m = new Matrix4().makeBasis(new Vector3(...frame.x), new Vector3(...frame.y), new Vector3(...frame.z));
  m.setPosition(...frame.origin);
  return m;
}

class Boundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  override state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  override render() { return this.state.failed ? null : this.props.children; }
}

function Glb({ url, matrix, ghost = false, emissive = null }: { url: string; matrix: Matrix4; ghost?: boolean; emissive?: string | null }) {
  const gltf = useLoader(GLTFLoader, url);
  const scene = useMemo(() => {
    const root = gltf.scene.clone(true);
    root.traverse((o: Object3D) => {
      const mesh = o as Mesh;
      if (!mesh.isMesh) return;
      const source = mesh.material as MeshStandardMaterial;
      const material = source.clone();
      material.transparent = ghost;
      material.opacity = ghost ? 0.25 : 1;
      material.depthWrite = !ghost;
      if (emissive) { material.emissive = new Color(emissive); material.emissiveIntensity = 0.35; }
      mesh.material = material;
    });
    return root;
  }, [gltf, ghost, emissive]);
  return <group matrixAutoUpdate={false} matrix={matrix}><primitive object={scene} /></group>;
}

function Model(props: { url: string; matrix: Matrix4; ghost?: boolean; emissive?: string | null }) {
  return <Boundary><Suspense fallback={null}><Glb {...props} /></Suspense></Boundary>;
}

const POST = new BoxGeometry(1, 1, 1);
const DOT = new SphereGeometry(1, 14, 10);

interface PinHover { ref: string; number: string; position: Vector3; lines: string[] }

function pinTransform(from: Vec3, to: Vec3, size: number): Matrix4 {
  const a = new Vector3(...from), b = new Vector3(...to);
  const length = Math.max(a.distanceTo(b), size);
  const q = new Quaternion().setFromUnitVectors(new Vector3(0, 0, 1), b.clone().sub(a).normalize());
  return new Matrix4().compose(a.clone().add(b).multiplyScalar(0.5), q, new Vector3(size, size, length));
}

function busOf(binding: string): string {
  return binding.split("(").pop()?.replace(")", "").trim() ?? "";
}

/** Contacts of one connector: posts (headers) or contact dots, coloured by use, with hover details. */
function Pins({ refName, c, data, base, onHover }: {
  refName: string; c: ConnectorJson; data: ConnectorsJson; base: Matrix4; onHover: (h: PinHover | null) => void;
}) {
  const type = data.types[c.type];
  const items = useMemo(() => Object.entries(c.pins).flatMap(([number, p]) => {
    const size = Math.max(type?.pin_size ?? 0.0006, 0.0004);
    if (c.owner === "component" && p.contact) {
      const bound = p.bindings?.length ? BUS_COLORS[busOf(p.bindings[0]!)] ?? "#f97316" : null;
      const tip = p.tip && type?.pin_shape === "square_post" ? p.tip : p.contact;
      const lines = [`${refName} pin ${number}${p.signal ? ` · ${p.signal}` : ""}`,
                     ...(p.net?.length ? [`net → ${p.net.slice(0, 4).join(", ")}${p.net.length > 4 ? " …" : ""}`] : []),
                     ...(p.bindings ?? []).map((b) => `software: ${b}`)];
      const color = bound ?? (number === "1" ? PIN1 : p.signal ? "#d4a640" : "#8b8b8b");
      // Posts slightly fatter than the model's so a bound pin reads clearly; plain pins stay as pick targets.
      const matrix = type?.pin_shape === "square_post" ? pinTransform(p.contact, tip, size * (bound ? 1.25 : 1.02))
        : new Matrix4().compose(new Vector3(...p.contact), new Quaternion(), new Vector3(size * 0.7, size * 0.7, size * 0.7));
      return [{ number, matrix, color, visible: !!bound || number === "1", lines, at: new Vector3(...tip) }];
    }
    if (c.owner === "harness" && (p.wire_entry || p.contact)) {
      const at = p.wire_entry ?? p.contact!;
      const lines = [`${refName} pin ${number}${p.signal ? ` · ${p.signal}` : ""}`, ...(p.wire ? [`${p.wire} · ${p.awg} AWG ${p.color}`, `to ${p.to}`] : ["no wire"])];
      const r = Math.max(size * 0.6, 0.0004);
      return [{ number, matrix: new Matrix4().compose(new Vector3(...at), new Quaternion(), new Vector3(r, r, r)),
                color: p.color ? `#${wireColor(p.color).toString(16).padStart(6, "0")}` : "#8b8b8b", visible: !!p.wire, lines,
                at: new Vector3(...at) }];
    }
    return [];
  }), [c, type, refName]);
  return (
    <group matrixAutoUpdate={false} matrix={base}>
      {items.map((item) => (
        <mesh key={item.number} geometry={c.owner === "component" && type?.pin_shape === "square_post" ? POST : DOT}
              matrixAutoUpdate={false} matrix={item.matrix} renderOrder={2}
              onPointerOver={(e: ThreeEvent<PointerEvent>) => { e.stopPropagation(); onHover({ ref: refName, number: item.number, position: item.at.clone().applyMatrix4(base), lines: item.lines }); }}
              onPointerOut={() => onHover(null)}>
          <meshStandardMaterial color={item.color} metalness={0.5} roughness={0.35} transparent={!item.visible} opacity={item.visible ? 1 : 0}
                                depthWrite={item.visible} emissive={item.visible && item.color !== PIN1 ? item.color : "#000000"} emissiveIntensity={0.25} />
        </mesh>
      ))}
    </group>
  );
}

function PairMarker({ pair }: { pair: MatedPairJson }) {
  return (
    <Html position={pair.center} center style={{ pointerEvents: "none" }}>
      <div className="viewer-label" title={`${pair.kind} mate`}>
        {pair.a} ↔ {pair.b} · pins Δ {(pair.max_pin_lateral_error * 1e3).toFixed(3)} mm · faces {(pair.axial_gap * 1e3).toFixed(2)} mm
      </div>
    </Html>
  );
}

export function ConnectorLayer({ data, assetUrl, matrices, harness, showBodies, showPins, ghost }: {
  data: ConnectorsJson;
  assetUrl: (relative: string) => string;
  /** Explode / joint transforms of components (identity at rest), keyed by component id. */
  matrices: Record<string, Matrix4>;
  /** Selected harness: its mated pairs are labelled and its connector bodies highlighted. */
  harness: string | null;
  showBodies: boolean;
  showPins: boolean;
  ghost: (componentId: string) => boolean;
}) {
  const [hover, setHover] = useState<PinHover | null>(null);
  const owned = (c: ConnectorJson) => (c.owner === "component" ? matrices[c.component!] ?? IDENTITY : IDENTITY);
  const pairs = useMemo(() => (harness ? data.mated_pairs.filter((p) => [p.a, p.b].some((r) => r.startsWith(`${harness}.`))) : []),
                        [data, harness]);
  const inPair = new Set(pairs.flatMap((p) => [p.a, p.b]));
  return (
    <group>
      {showBodies && Object.entries(data.components).filter(([, c]) => c.glb).map(([id, c]) => (
        <group key={`cm:${id}`} matrixAutoUpdate={false} matrix={matrices[id] ?? IDENTITY}>
          <Model url={assetUrl(c.glb!)} matrix={frameMatrix(c.frame)} ghost={ghost(id)} />
        </group>
      ))}
      {showBodies && Object.entries(data.connectors).map(([ref, c]) => {
        // Bodies already inside a component model (the Pi header, the HAT's KiCad parts) are not drawn twice.
        const embedded = c.owner === "component" && c.embedded && data.components[c.component!]?.glb;
        const glb = data.types[c.type]?.glb;
        if (embedded || !glb) return null;
        return (
          <group key={`cb:${ref}`} matrixAutoUpdate={false} matrix={owned(c)}>
            <Model url={assetUrl(glb)} matrix={frameMatrix(c.frame)} emissive={inPair.has(ref) ? "#f97316" : null}
                   ghost={c.owner === "component" && ghost(c.component!)} />
          </group>
        );
      })}
      {showPins && Object.entries(data.connectors).map(([ref, c]) => (
        <Pins key={`p:${ref}`} refName={ref} c={c} data={data} base={owned(c)} onHover={setHover} />
      ))}
      {pairs.map((p) => <PairMarker key={`${p.a}|${p.b}`} pair={p} />)}
      {hover && (
        <Html position={hover.position} style={{ pointerEvents: "none", transform: "translate(12px, -50%)" }}>
          <div className="viewer-label" data-testid="pin-tooltip">
            {hover.lines.map((line, i) => <div key={i} className={i ? "small" : ""}><strong>{i ? "" : ""}</strong>{line}</div>)}
          </div>
        </Html>
      )}
    </group>
  );
}

/** Components drawn from detailed models; their envelopes/STLs are hidden while bodies are shown. */
export function detailedComponents(data: ConnectorsJson | null): Set<string> {
  return new Set(Object.entries(data?.components ?? {}).filter(([, c]) => c.glb).map(([id]) => id));
}

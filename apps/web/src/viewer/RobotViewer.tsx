// The 3D robot view: CAD STL meshes (catalog envelopes as fallback) posed from the compiled
// assembly, harness tubes in wire colours, interface/join/joint overlays, joint sliders,
// exploded view, click-to-inspect, diagnostic highlighting, simulation playback and route editing.
import { Edges, GizmoHelper, GizmoViewport, Grid, Html, Line, OrbitControls } from "@react-three/drei";
import { Canvas, useFrame, useLoader, useThree, type ThreeEvent } from "@react-three/fiber";
import { Component, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  BoxGeometry, BufferGeometry, CatmullRomCurve3, Color, ConeGeometry, CylinderGeometry, DoubleSide, Group, LineCurve3, Matrix4,
  Mesh, MeshStandardMaterial, Object3D, Quaternion, TubeGeometry, Vector3,
} from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";

import type { PlaybackRecording, Vec3 } from "../api/types.ts";
import { resolveSubjects, type SceneComponent, type SceneWire } from "../lib/scene.ts";
import {
  ackermann, bodyDelta, bounds, chainMatrix, clampJoint, componentBody, explodeOffsets, frameAt, interpolatePoints,
  interpolatePose, poseMatrix, restPoses,
} from "../lib/transforms.ts";
import { humanize } from "../lib/units.ts";
import { Icon } from "../components/icons.tsx";
import { Popover } from "../components/ui.tsx";
import { cssVar, useThemeVersion } from "../state/prefs.ts";
import { ConnectorLayer, detailedComponents } from "./ConnectorLayer.tsx";
import { RouteEditor, type RouteEditState } from "./RouteEditor.tsx";
import { interfaceFrame } from "./interfaceFrame.ts";
import type { RobotData } from "./useRobotScene.ts";

Object3D.DEFAULT_UP.set(0, 0, 1); // robot frame: +z up

export interface Selection {
  kind: "component" | "wire";
  id: string;
}

export interface ViewOptions {
  wires: boolean;
  /** CAD fasteners (screws, nuts) */
  hardware: boolean;
  /** Envelope interference regions from the model stage */
  interference: boolean;
  interfaces: boolean;
  joins: boolean;
  joints: boolean;
  xray: boolean;
  issues: boolean;
  /** Detailed component models, connector bodies and individual pins (build/connectors). */
  connectors: boolean;
  explode: number;
}

export interface PlaybackState {
  recording: PlaybackRecording;
  t: number;
  follow: boolean;
}

/** A point of interest drawn on top of the model (minimum-clearance point, diagnostic location). */
export interface Marker {
  position: Vec3;
  label?: string;
  color?: string;
}

/** Layers of the "Show" menu. Features that add overlays (e.g. connector pins) append an entry here
 * and a matching boolean to ViewOptions. `available` hides layers the data cannot draw. */
export const SHOW_LAYERS: { key: keyof ViewOptions; label: string; available?: (data: RobotData, playback: boolean) => boolean; count?: (data: RobotData) => number }[] = [
  { key: "wires", label: "Wires", available: (_d, playback) => !playback },
  { key: "hardware", label: "Fasteners", available: (d) => d.hardware.length > 0, count: (d) => d.hardware.length },
  { key: "interfaces", label: "Connector frames" },
  { key: "joins", label: "Mounts and contacts" },
  { key: "joints", label: "Joint axes", available: (_d, playback) => !playback },
  { key: "issues", label: "Error highlights" },
  { key: "connectors", label: "Connectors and pins", available: (d, playback) => !!d.connectors && !playback },
  { key: "interference", label: "Interferences", available: (d, playback) => d.interference.length > 0 && !playback, count: (d) => d.interference.length },
];

const DEFAULT_OPTIONS: ViewOptions = { wires: true, hardware: true, interference: false, interfaces: false, joins: false, joints: false, xray: false, issues: false, connectors: true, explode: 0 };
const IDENTITY = new Matrix4();
const HIGHLIGHT = new Color("#f97316");
const SELECTED = new Color("#3987e5");
const ERROR = new Color("#d03b3b");

type PartState = "normal" | "highlight" | "selected" | "ghost";

// ------------------------------------------------------------------ geometry helpers

function envelopeGeometry(c: SceneComponent): BufferGeometry {
  const [a = 0.01, b = 0.01, cz = 0.01] = c.size;
  if (c.shape === "cylinder") {
    const geometry = new CylinderGeometry(a / 2, a / 2, b, 40); // axis along local +y
    if (c.axis === "x") geometry.rotateZ(-Math.PI / 2);
    if (c.axis === "z") { geometry.rotateX(Math.PI / 2); geometry.translate(0, 0, b / 2); } // rises from the mount face
    return geometry;
  }
  const geometry = new BoxGeometry(a, b, cz);
  geometry.translate(0, 0, cz / 2); // component origin is the bottom (mounting) face
  return geometry;
}

function tubeGeometry(points: readonly Vec3[], radius: number, radial = 8): TubeGeometry {
  const vectors = points.map((p) => new Vector3(p[0], p[1], p[2]));
  const curve = vectors.length > 2 ? new CatmullRomCurve3(vectors, false, "centripetal") : new LineCurve3(vectors[0]!, vectors[1] ?? vectors[0]!);
  return new TubeGeometry(curve, Math.min(600, Math.max(8, vectors.length * 2)), radius, radial, false);
}

function useMaterial(color: string | number, state: PartState, opts: { metal?: boolean; error?: boolean; xray?: boolean } = {}) {
  const material = useMemo(() => new MeshStandardMaterial({ side: DoubleSide }), []);
  useEffect(() => () => material.dispose(), [material]);
  const ghost = state === "ghost" || (opts.xray && state === "normal");
  material.color.set(color);
  material.metalness = opts.metal ? 0.55 : 0.05;
  material.roughness = opts.metal ? 0.38 : 0.62;
  material.transparent = !!ghost;
  material.opacity = ghost ? (state === "ghost" ? 0.14 : 0.32) : 1;
  material.depthWrite = !ghost;
  material.emissive.copy(state === "highlight" ? HIGHLIGHT : state === "selected" ? SELECTED : opts.error ? ERROR : new Color(0));
  material.emissiveIntensity = state === "highlight" ? 0.55 : state === "selected" ? 0.35 : opts.error ? 0.35 : 0;
  material.needsUpdate = true;
  return material;
}

class MeshBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  override state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  override render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

// ------------------------------------------------------------------ parts and wires

interface Outline { color: string; width: number; opacity: number; onTop?: boolean }

function StlMesh({ url, material, outline, onClick, onHover }: {
  url: string; material: MeshStandardMaterial; outline: Outline | null;
  onClick: (e: ThreeEvent<MouseEvent>) => void; onHover: (on: boolean) => void;
}) {
  const geometry = useLoader(STLLoader, url);
  return (
    <mesh geometry={geometry} material={material} onClick={onClick}
          onPointerOver={(e) => { e.stopPropagation(); onHover(true); }} onPointerOut={() => onHover(false)}>
      {outline && <Edges threshold={28} color={outline.color} lineWidth={outline.width} transparent opacity={outline.opacity} depthTest={!outline.onTop} renderOrder={outline.onTop ? 10 : 0} />}
    </mesh>
  );
}

function Envelope({ c, material, outline, onClick, onHover }: {
  c: SceneComponent; material: MeshStandardMaterial; outline: Outline | null;
  onClick: (e: ThreeEvent<MouseEvent>) => void; onHover: (on: boolean) => void;
}) {
  const geometry = useMemo(() => envelopeGeometry(c), [c]);
  const matrix = useMemo(() => poseMatrix(c.xyz, c.rpy), [c]);
  return (
    <group matrixAutoUpdate={false} matrix={matrix}>
      <mesh geometry={geometry} material={material} onClick={onClick}
            onPointerOver={(e) => { e.stopPropagation(); onHover(true); }} onPointerOut={() => onHover(false)}>
        {outline && <Edges threshold={20} color={outline.color} lineWidth={outline.width} transparent opacity={outline.opacity} depthTest={!outline.onTop} renderOrder={outline.onTop ? 10 : 0} />}
      </mesh>
    </group>
  );
}

function Part({ c, url, matrix, state, xray, error, edge, register, onSelect, onHover }: {
  c: SceneComponent; url: string | undefined; matrix: Matrix4; state: PartState; xray: boolean; error: boolean; edge: string;
  register: (id: string, group: Group | null) => void; onSelect?: (s: Selection) => void; onHover: (id: string | null) => void;
}) {
  const material = useMaterial(c.color, state, { metal: /alumin|steel/i.test(c.name) && c.kind === "structure", error, xray });
  const outline: Outline | null = state === "selected" ? { color: "#3987e5", width: 2.2, opacity: 1, onTop: true }
    : state === "highlight" ? { color: "#f97316", width: 2.2, opacity: 1, onTop: true }
    : state === "normal" && !xray ? { color: edge, width: 1, opacity: 0.55 } : null;
  const click = (e: ThreeEvent<MouseEvent>) => { e.stopPropagation(); onSelect?.({ kind: "component", id: c.id }); };
  const hover = (on: boolean) => onHover(on ? c.id : null);
  const envelope = <Envelope c={c} material={material} outline={outline} onClick={click} onHover={hover} />;
  return (
    <group ref={(g) => register(c.id, g)} matrixAutoUpdate={false} matrix={matrix} name={c.id}>
      {url ? (
        <MeshBoundary fallback={envelope}>
          <Suspense fallback={envelope}>
            <StlMesh url={url} material={material} outline={outline} onClick={click} onHover={hover} />
          </Suspense>
        </MeshBoundary>
      ) : envelope}
    </group>
  );
}

/** A fastener or other CAD-only part, drawn in its owner's frame. */
function HardwarePart({ id, owner, url, matrix, state, register, onSelect }: {
  id: string; owner: string; url: string; matrix: Matrix4; state: PartState;
  register: (id: string, group: Group | null) => void; onSelect?: (s: Selection) => void;
}) {
  const material = useMaterial("#a7adb4", state, { metal: true });
  const click = (e: ThreeEvent<MouseEvent>) => { e.stopPropagation(); onSelect?.({ kind: "component", id: owner }); };
  return (
    <group ref={(g) => register(id, g)} matrixAutoUpdate={false} matrix={matrix} name={id}>
      <MeshBoundary fallback={null}>
        <Suspense fallback={null}>
          <StlMesh url={url} material={material} outline={null} onClick={click} onHover={() => undefined} />
        </Suspense>
      </MeshBoundary>
    </group>
  );
}

function WireTube({ w, state, onSelect, onHover }: {
  w: SceneWire; state: PartState; onSelect?: (s: Selection) => void; onHover: (id: string | null) => void;
}) {
  // True conductor diameter in every state: thickening focused wires made the conductors of
  // one lead overlap again. Focus is a light glow that keeps the insulation colours legible.
  const geometry = useMemo(() => tubeGeometry(w.path, w.radius), [w]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  const material = useMaterial(w.color, state);
  if (state === "highlight" || state === "selected") material.emissiveIntensity = 0.18;
  material.transparent = material.transparent || !w.routed;
  if (!w.routed && state !== "ghost") material.opacity = 0.55;
  return (
    <mesh geometry={geometry} material={material}
          onClick={(e) => { e.stopPropagation(); onSelect?.({ kind: "wire", id: w.id }); }}
          onPointerOver={(e) => { e.stopPropagation(); onHover(w.id); }} onPointerOut={() => onHover(null)} />
  );
}

// ------------------------------------------------------------------ overlays

const CONE = new ConeGeometry(0.0022, 0.007, 12).translate(0, 0.0035, 0);

function InterfacesOverlay({ data, matrices }: { data: RobotData; matrices: Record<string, Matrix4> }) {
  const [hover, setHover] = useState<string | null>(null);
  const items = useMemo(() => Object.entries(data.endpoints).map(([id, e]) => {
    const owner = id.split(".")[0]!;
    const m = matrices[owner] ?? IDENTITY;
    const position = new Vector3(...e.position).applyMatrix4(m);
    const direction = new Vector3(...e.direction).transformDirection(m);
    const quaternion = new Quaternion().setFromUnitVectors(new Vector3(0, 1, 0), direction.lengthSq() > 0 ? direction : new Vector3(0, 0, 1));
    return { id, type: e.type, component: e.owner_kind === "component", position, quaternion };
  }), [data.endpoints, matrices]);
  const assembly = Object.entries(data.assembly?.interfaces ?? {}).flatMap(([id, value]) => {
    const frame = interfaceFrame(value);
    return frame ? [{ id, ...frame }] : [];
  });
  return (
    <group>
      {items.map((it) => (
        <mesh key={it.id} geometry={CONE} position={it.position} quaternion={it.quaternion}
              onPointerOver={(e) => { e.stopPropagation(); setHover(it.id); }} onPointerOut={() => setHover(null)}>
          <meshBasicMaterial color={it.component ? "#3987e5" : "#199e70"} depthTest={false} transparent opacity={0.95} />
        </mesh>
      ))}
      {assembly.map(({ id, ...f }) => (
        <Line key={id} points={[f.origin, [f.origin[0] + f.z[0] * 0.012, f.origin[1] + f.z[1] * 0.012, f.origin[2] + f.z[2] * 0.012]]}
              color="#9085e9" lineWidth={2} depthTest={false} />
      ))}
      {hover && (() => {
        const it = items.find((x) => x.id === hover)!;
        return <Html position={it.position} center style={{ pointerEvents: "none" }}><div className="viewer-label">{it.id} · {it.type}</div></Html>;
      })()}
    </group>
  );
}

function JoinsOverlay({ data, matrices }: { data: RobotData; matrices: Record<string, Matrix4> }) {
  const segments = useMemo(() => {
    const center = (id: string) => new Vector3(...(data.centers[id] ?? [0, 0, 0])).applyMatrix4(matrices[id] ?? IDENTITY);
    const mount = Object.entries(data.tree).filter(([, parent]) => parent).map(([id, parent]) => ({ key: `m:${id}`, a: center(parent!), b: center(id), mount: true }));
    const mountKeys = new Set(Object.entries(data.tree).map(([id, p]) => [id, p].sort().join("|")));
    const contacts = data.contacts.filter(([a, b]) => !mountKeys.has([a, b].sort().join("|")) && data.centers[a] && data.centers[b])
      .map(([a, b]) => ({ key: `c:${a}|${b}`, a: center(a), b: center(b), mount: false }));
    return [...mount, ...contacts];
  }, [data, matrices]);
  return (
    <group>
      {segments.map((s) => (
        <Line key={s.key} points={[s.a, s.b]} color={s.mount ? "#c98500" : "#9085e9"} lineWidth={s.mount ? 1.6 : 1}
              dashed={!s.mount} dashSize={0.004} gapSize={0.003} depthTest={false} transparent opacity={0.9} />
      ))}
      {segments.filter((s) => s.mount).map((s) => (
        <mesh key={`${s.key}:dot`} position={s.b}>
          <sphereGeometry args={[0.0018, 10, 8]} />
          <meshBasicMaterial color="#c98500" depthTest={false} transparent />
        </mesh>
      ))}
    </group>
  );
}

function JointsOverlay({ data, positions }: { data: RobotData; positions: Record<string, number> }) {
  return (
    <group>
      {data.joints.map((j) => {
        const parentChain = data.chains[j.parent] ?? [];
        const m = chainMatrix(parentChain, positions);
        const o = new Vector3(...j.origin).applyMatrix4(m);
        const axis = new Vector3(...j.axis).normalize().transformDirection(m).multiplyScalar(0.035);
        return (
          <group key={j.id}>
            <Line points={[o.clone().sub(axis), o.clone().add(axis)]} color="#d55181" lineWidth={2.5} depthTest={false} />
            <mesh position={o}><sphereGeometry args={[0.0028, 12, 10]} /><meshBasicMaterial color="#d55181" depthTest={false} transparent /></mesh>
            <Html position={o.clone().add(axis)} style={{ pointerEvents: "none" }}><div className="viewer-label">{j.id}</div></Html>
          </group>
        );
      })}
    </group>
  );
}

/** Interference regions as outlined boxes (edges plus a faint fill); clicking one reports the part pair. */
function InterferenceBox({ center, extent, selected, onClick }: { center: Vec3; extent: Vec3; selected: boolean; onClick?: () => void }) {
  const size = extent.map((v) => Math.max(v, 0.0008)) as Vec3;
  return (
    <mesh position={center} onClick={(e) => { e.stopPropagation(); onClick?.(); }}>
      <boxGeometry args={size} />
      <meshBasicMaterial color="#d03b3b" transparent opacity={selected ? 0.28 : 0.1} depthWrite={false} />
      <Edges color="#d03b3b" lineWidth={selected ? 2.4 : 1.4} depthTest={false} renderOrder={11} />
    </mesh>
  );
}

function MarkerPoint({ marker }: { marker: Marker }) {
  return (
    <group position={marker.position}>
      <mesh renderOrder={12}>
        <sphereGeometry args={[0.0032, 16, 12]} />
        <meshBasicMaterial color={marker.color ?? "#d03b3b"} depthTest={false} transparent />
      </mesh>
      <mesh renderOrder={12}>
        <ringGeometry args={[0.005, 0.0062, 32]} />
        <meshBasicMaterial color={marker.color ?? "#d03b3b"} depthTest={false} transparent side={DoubleSide} />
      </mesh>
      {marker.label && <Html center position={[0, 0, 0.012]} style={{ pointerEvents: "none" }}><div className="viewer-label">{marker.label}</div></Html>}
    </group>
  );
}

// ------------------------------------------------------------------ camera, playback

function CameraRig({ focus, fitKey, data }: { focus: Vec3[]; fitKey: string; data: RobotData }) {
  const { camera, controls, invalidate } = useThree();
  useEffect(() => {
    const orbit = controls as unknown as { target: Vector3; update(): void } | null;
    if (!orbit) return;
    const all = Object.values(data.cadParts).flatMap((p) => (p.bbox_min && p.bbox_max ? [p.bbox_min, p.bbox_max] : []));
    const box = bounds(focus.length ? focus : all.length ? all : Object.values(data.centers));
    if (!box) return;
    const center = new Vector3((box.min[0] + box.max[0]) / 2, (box.min[1] + box.max[1]) / 2, (box.min[2] + box.max[2]) / 2);
    const radius = Math.max(0.025, new Vector3(...box.max).distanceTo(new Vector3(...box.min)) / 2);
    const direction = camera.position.clone().sub(orbit.target);
    if (direction.lengthSq() < 1e-9) direction.set(-0.55, -0.7, 0.5);
    direction.normalize();
    orbit.target.copy(center);
    camera.position.copy(center.clone().add(direction.multiplyScalar(radius * (focus.length ? 3.4 : 3.1))));
    orbit.update();
    invalidate();
  }, [fitKey, controls]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

/** Imperatively poses part groups and cables from the recording at time `t` (no React re-render per frame).
 * A part authored in the robot frame is placed by its body's motion since rest: pose(t) · rest⁻¹. */
function PlaybackDriver({ playback, data, groups, cableMeshes }: {
  playback: PlaybackState; data: RobotData; groups: React.MutableRefObject<Map<string, Group>>;
  cableMeshes: React.MutableRefObject<Map<string, Mesh>>;
}) {
  const { camera, controls, invalidate } = useThree();
  const last = useRef<Vector3 | null>(null);
  const { recording, t, follow } = playback;
  const frames = recording.frames!;
  const root = frames.component_bodies?.chassis ?? (frames.bodies.chassis ? "chassis" : Object.keys(frames.bodies)[0] ?? null);
  const rest = useMemo(() => (root ? restPoses(frames, root) : {}), [frames, root]);
  const assignment = useMemo(() => {
    const bodies = new Set(Object.keys(frames.bodies));
    const of = (id: string) => frames.component_bodies?.[id] ?? componentBody(data.tree, id, bodies, root);
    return Object.fromEntries([...data.components.map((c) => [c.id, of(c.id)]), ...data.hardware.map((h) => [h.id, of(h.owner)])]);
  }, [data, frames, root]);
  useEffect(() => invalidate(), [t, invalidate]);
  useFrame(() => {
    const cursor = frameAt(frames.t, t);
    const deltas: Record<string, Matrix4> = {};
    for (const [name, poses] of Object.entries(frames.bodies)) {
      const nominal = rest[name];
      if (!poses.length || !nominal) continue;
      deltas[name] = bodyDelta(nominal, interpolatePose(poses[cursor.i0]!, poses[cursor.i1]!, cursor.alpha));
    }
    for (const [id, group] of groups.current) {
      const body = assignment[id];
      group.matrix.copy((body && deltas[body]) || IDENTITY);
      group.matrixWorldNeedsUpdate = true;
    }
    for (const [id, cable] of Object.entries(frames.cables ?? {})) {
      const mesh = cableMeshes.current.get(id);
      const a = cable.points[cursor.i0];
      const b = cable.points[cursor.i1] ?? a;
      if (!mesh || !a || a.length < 2) continue;
      mesh.geometry.dispose();
      mesh.geometry = tubeGeometry(interpolatePoints(a, b!, cursor.alpha), CABLE_RADIUS, 6);
    }
    const chassis = root ? deltas[root] : null;
    const orbit = controls as unknown as { target: Vector3; update(): void } | null;
    if (chassis && orbit) {
      const p = new Vector3().setFromMatrixPosition(chassis);
      if (follow && last.current) {
        const d = p.clone().sub(last.current);
        camera.position.add(d);
        orbit.target.add(d);
        orbit.update();
      }
      last.current = p;
    }
  });
  return null;
}

/** Cable polylines carry no diameter in the recording; draw them at a typical bundle size. */
const CABLE_RADIUS = 0.0014;

function rgbaColor(rgba: number[] | null | undefined): Color {
  const [r, g, b] = rgba ?? [];
  return r !== undefined && g !== undefined && b !== undefined ? new Color(r, g, b) : new Color("#999999");
}

function PlaybackCables({ recording, meshes }: { recording: PlaybackRecording; meshes: React.MutableRefObject<Map<string, Mesh>> }) {
  return (
    <group>
      {Object.entries(recording.frames?.cables ?? {}).map(([id, cable]) => (
        <mesh key={id} ref={(m) => { if (m) meshes.current.set(id, m); else meshes.current.delete(id); }}>
          <meshStandardMaterial color={rgbaColor(cable.rgba)} roughness={0.5} />
        </mesh>
      ))}
    </group>
  );
}

function Trajectory({ recording }: { recording: PlaybackRecording }) {
  const points = useMemo(() => {
    const { x = [], y = [] } = recording.series;
    const out: Vec3[] = [];
    const step = Math.max(1, Math.floor(x.length / 600));
    for (let i = 0; i < x.length; i += step) {
      const px = x[i], py = y[i];
      if (typeof px === "number" && typeof py === "number") out.push([px, py, 0.0008]);
    }
    return out;
  }, [recording]);
  return points.length > 1 ? <Line points={points} color="#f97316" lineWidth={2} transparent opacity={0.8} /> : null;
}

// ------------------------------------------------------------------ the viewer

export function RobotViewer({ data, highlight = [], selected = null, onSelect, playback: playbackProp = null, editor = null,
                              initialOptions, showJointPanel = true, toolbarExtra, toolbarStart, status, markers = [], fitRequest = 0,
                              onInterference, selectedInterference = null }: {
  data: RobotData;
  highlight?: string[];
  selected?: Selection | null;
  onSelect?: (s: Selection | null) => void;
  playback?: PlaybackState | null;
  editor?: RouteEditState | null;
  initialOptions?: Partial<ViewOptions>;
  showJointPanel?: boolean;
  /** extra controls after the view controls */
  toolbarExtra?: ReactNode;
  /** page-specific primary controls before the Show menu (e.g. Fit harness / Edit route) */
  toolbarStart?: ReactNode;
  status?: ReactNode;
  /** points drawn on top of the model */
  markers?: Marker[];
  /** increment to re-fit the camera to the current highlight */
  fitRequest?: number;
  /** clicking an interference region reports its index into `data.interference` */
  onInterference?: (index: number) => void;
  selectedInterference?: number | null;
}) {
  const playback = playbackProp?.recording.frames && !playbackProp.recording.frames.error ? playbackProp : null;
  const themeVersion = useThemeVersion();
  const [options, setOptions] = useState<ViewOptions>({ ...DEFAULT_OPTIONS, ...initialOptions });
  const [positions, setPositions] = useState<Record<string, number>>({});
  const [steer, setSteer] = useState(0);
  const [hover, setHover] = useState<string | null>(null);
  const [fitNonce, setFitNonce] = useState(0);
  const [moveJoints, setMoveJoints] = useState(false);
  const groups = useRef(new Map<string, Group>());
  const cableMeshes = useRef(new Map<string, Mesh>());
  const toggle = (key: keyof ViewOptions) => setOptions((o) => ({ ...o, [key]: !o[key] }));

  const focus = useMemo(() => resolveSubjects(highlight, data), [highlight, data]);
  const focusKey = [...focus.components, ...focus.wires].sort().join(",");
  const errorParts = useMemo(() => resolveSubjects(data.diagnostics.filter((d) => d.severity === "error").flatMap((d) => d.subjects), data).components,
                             [data]);
  const anyFocus = focus.components.size + focus.wires.size > 0;

  const explode = useMemo(() => explodeOffsets(
    Object.fromEntries(data.components.map((c) => [c.id, { mount: c.mount, xyz: data.centers[c.id] ?? c.xyz }])), options.explode * 0.9,
  ), [data, options.explode]);
  const matrices = useMemo(() => {
    const out: Record<string, Matrix4> = {};
    for (const c of data.components) {
      const offset = explode[c.id] ?? [0, 0, 0];
      out[c.id] = new Matrix4().makeTranslation(offset[0], offset[1], offset[2]).multiply(chainMatrix(data.chains[c.id] ?? [], positions));
    }
    return out;
  }, [data, explode, positions]);

  const focusPoints = useMemo(() => {
    const pts: Vec3[] = [];
    for (const id of focus.components) {
      const part = data.cadParts[id];
      const g = data.graphComponents[id];
      const box = part?.bbox_min && part.bbox_max ? [part.bbox_min, part.bbox_max] : g?.aabb_min && g.aabb_max ? [g.aabb_min, g.aabb_max] : [];
      pts.push(...box);
    }
    for (const w of data.wires) if (focus.wires.has(w.id)) pts.push(...w.path);
    return pts;
  }, [focus, data]);

  const steerJoints = useMemo(() => {
    const steering = data.steering;
    if (!steering) return null;
    const joints = steering.joints.map((id) => data.joints.find((j) => j.id === id)).filter((j) => !!j);
    const left = joints.find((j) => j.origin[1] > 0);
    const right = joints.find((j) => j.origin[1] <= 0);
    const max = Number.isFinite(steering.maxSteer) ? steering.maxSteer : left?.limits?.[1] ?? 0.45;
    return left && right ? { left, right, wheelbase: steering.wheelbase, track: steering.track, max } : null;
  }, [data]);

  const setSteerAngle = (delta: number) => {
    setSteer(delta);
    if (!steerJoints) return;
    const { left, right } = ackermann(delta, steerJoints.wheelbase, steerJoints.track);
    setPositions((p) => ({ ...p, [steerJoints.left.id]: clampJoint(steerJoints.left, left), [steerJoints.right.id]: clampJoint(steerJoints.right, right) }));
  };

  const stateOf = (id: string, isWire = false): PartState => {
    if (selected && selected.id === id) return "selected";
    if (anyFocus) return (isWire ? focus.wires.has(id) : focus.components.has(id)) ? "highlight" : "ghost";
    return "normal";
  };
  const register = (id: string, group: Group | null) => { if (group) groups.current.set(id, group); else groups.current.delete(id); };
  const background = useMemo(() => cssVar("--viewer-bg") || "#161615", [themeVersion]); // eslint-disable-line react-hooks/exhaustive-deps
  const dark = themeVersion !== "light";
  const hovered = hover ? data.components.find((c) => c.id === hover) ?? data.wires.find((w) => w.id === hover) : null;
  const meshCount = data.components.filter((c) => data.meshes[c.id]).length;
  const showConnectors = !!data.connectors && options.connectors && !playback;
  const detailed = useMemo(() => (showConnectors ? detailedComponents(data.connectors ?? null) : new Set<string>()), [showConnectors, data.connectors]);
  const selectedHarness = selected?.kind === "wire" ? data.connectors?.wires[selected.id]?.harness ?? null : null;
  const layers = SHOW_LAYERS.filter((l) => l.available?.(data, !!playback) ?? true);

  return (
    <div className="viewer" data-testid="robot-viewer">
      <div className="viewer-toolbar">
        {toolbarStart}
        <div className="group">
          <Popover label="Show layers" trigger={() => <span className="menu-trigger">Show<Icon name="chevron" size={12} /></span>}>
            <div className="layer-menu" role="group" aria-label="Layers">
              {layers.map((l) => (
                <label key={l.key} className="check">
                  <input type="checkbox" checked={!!options[l.key]} onChange={() => toggle(l.key)} />
                  {l.label}{l.count ? <span className="muted"> · {l.count(data)}</span> : null}
                </label>
              ))}
            </div>
          </Popover>
          {showJointPanel && !playback && data.joints.length > 0 && (
            <button className={moveJoints ? "on" : ""} aria-pressed={moveJoints} onClick={() => setMoveJoints((m) => !m)}>Move joints</button>
          )}
        </div>
        <div className="group" role="toolbar" aria-label="View">
          <button className={options.xray ? "on" : ""} aria-pressed={options.xray} onClick={() => toggle("xray")} title="See-through parts">X-ray</button>
          {!playback && (
            <label>Explode
              <input type="range" min={0} max={1} step={0.01} value={options.explode} aria-label="Exploded view"
                     onChange={(e) => setOptions((o) => ({ ...o, explode: Number(e.target.value) }))} style={{ width: 80 }} />
            </label>
          )}
          <button onClick={() => setFitNonce((n) => n + 1)} title="Fit the view to the model or the highlight">Fit</button>
        </div>
        {toolbarExtra}
      </div>

      {showJointPanel && moveJoints && !playback && (
        <div className="joint-panel" aria-label="Joint positions">
          <div className="row"><strong className="small">Joints</strong><span className="spacer" style={{ flex: 1 }} />
            <button className="small ghost" onClick={() => { setPositions({}); setSteer(0); }}>Reset</button></div>
          {steerJoints && (
            <div className="joint">
              <span>Steering (Ackermann)</span><span className="num mono">{(steer * 180 / Math.PI).toFixed(1)}°</span>
              <input type="range" min={-steerJoints.max} max={steerJoints.max} step={0.005} value={steer} aria-label="Steering"
                     onChange={(e) => setSteerAngle(Number(e.target.value))} />
            </div>
          )}
          {data.joints.map((j) => {
            const [lo, hi] = j.type === "continuous" || !j.limits ? [-Math.PI, Math.PI] : j.limits;
            const q = positions[j.id] ?? 0;
            return (
              <div className="joint" key={j.id}>
                <span className="ellipsis" title={`${j.type} · ${j.parent} → ${j.child}`}>{j.id}</span>
                <span className="num mono">{j.type === "prismatic" ? `${(q * 1000).toFixed(1)} mm` : `${(q * 180 / Math.PI).toFixed(1)}°`}</span>
                <input type="range" min={lo} max={hi} step={0.005} value={q} aria-label={j.id}
                       onChange={(e) => setPositions((p) => ({ ...p, [j.id]: Number(e.target.value) }))} />
              </div>
            );
          })}
          {!data.joints.length && <span className="muted small">No joints in the compiled model.</span>}
        </div>
      )}

      <Canvas frameloop="demand" dpr={[1, 2]} gl={{ antialias: true, preserveDrawingBuffer: true }}
              camera={{ fov: 36, near: 0.002, far: 400, position: [-0.3, -0.42, 0.32] }}
              onPointerMissed={() => onSelect?.(null)} style={{ cursor: hover ? "pointer" : "default" }}>
        <color attach="background" args={[background]} />
        <hemisphereLight args={[dark ? "#dfe6ee" : "#ffffff", dark ? "#3a3833" : "#9b9a92", dark ? 1.2 : 1.5]} />
        <directionalLight position={[0.6, -0.9, 1.4]} intensity={dark ? 1.9 : 2.1} />
        <directionalLight position={[-0.8, 0.6, 0.5]} intensity={0.55} />
        <Grid rotation={[Math.PI / 2, 0, 0]} position={[0, 0, -0.0005]} cellSize={0.01} sectionSize={0.1} infiniteGrid fadeDistance={4}
              fadeStrength={1.4} cellColor={dark ? "#2e2e2a" : "#d6d5cf"} sectionColor={dark ? "#45453f" : "#b5b4ad"} cellThickness={0.6} sectionThickness={1} />

        {data.components.filter((c) => !detailed.has(c.id)).map((c) => (
          <Part key={c.id} c={c} url={data.meshes[c.id]} matrix={playback ? IDENTITY : matrices[c.id] ?? IDENTITY} state={stateOf(c.id)}
                xray={options.xray} error={options.issues && !anyFocus && errorParts.has(c.id)} edge={dark ? "#77766f" : "#3a3a36"} register={register} onSelect={onSelect} onHover={setHover} />
        ))}
        {options.hardware && data.hardware.map((h) => (
          <HardwarePart key={h.id} id={h.id} owner={h.owner} url={h.url} matrix={playback ? IDENTITY : matrices[h.owner] ?? IDENTITY}
                        state={anyFocus || selected ? (stateOf(h.owner) === "ghost" ? "ghost" : "normal") : "normal"}
                        register={register} onSelect={onSelect} />
        ))}
        {!playback && options.wires && options.explode < 0.02 && data.wires.map((w) => (
          <WireTube key={w.id} w={w} state={stateOf(w.id, true)} onSelect={onSelect} onHover={setHover} />
        ))}
        {!playback && options.interference && data.interference.map((o, i) => (
          !anyFocus || focus.components.has(o.a) || focus.components.has(o.b) || selectedInterference === i
            ? <InterferenceBox key={`int${i}`} center={o.center} extent={o.extent} selected={selectedInterference === i} onClick={onInterference ? () => onInterference(i) : undefined} />
            : null
        ))}
        {showConnectors && data.connectors && data.connectorAsset && (
          <ConnectorLayer data={data.connectors} assetUrl={data.connectorAsset} matrices={matrices} harness={selectedHarness}
                          showBodies showPins={options.explode < 0.02} ghost={(id) => stateOf(id) === "ghost" || (options.xray && stateOf(id) === "normal")} />
        )}
        {!playback && markers.map((m, i) => <MarkerPoint key={`mk${i}`} marker={m} />)}
        {options.interfaces && <InterfacesOverlay data={data} matrices={playback ? {} : matrices} />}
        {options.joins && <JoinsOverlay data={data} matrices={playback ? {} : matrices} />}
        {!playback && options.joints && <JointsOverlay data={data} positions={positions} />}
        {editor && !playback && <RouteEditor editor={editor} />}
        {playback && (
          <>
            <PlaybackCables recording={playback.recording} meshes={cableMeshes} />
            <Trajectory recording={playback.recording} />
            <PlaybackDriver playback={playback} data={data} groups={groups} cableMeshes={cableMeshes} />
          </>
        )}
        {[...focus.components].slice(0, 6).map((id) => {
          const center = data.centers[id];
          if (!center || playback) return null;
          const p = new Vector3(...center).applyMatrix4(matrices[id] ?? IDENTITY);
          return <Html key={`lbl:${id}`} position={p} center style={{ pointerEvents: "none" }}><div className="viewer-label">{id}</div></Html>;
        })}

        <OrbitControls makeDefault target={[0.13, 0, 0.05]} enableDamping={false} minDistance={0.02} maxDistance={60} />
        <CameraRig focus={focusPoints} fitKey={`${focusKey}#${fitNonce}#${fitRequest}`} data={data} />
        <GizmoHelper alignment="bottom-right" margin={[54, 54]}>
          <GizmoViewport axisColors={["#e34948", "#1baf7a", "#2a78d6"]} labelColor={dark ? "#0b0b0b" : "#ffffff"} />
        </GizmoHelper>
      </Canvas>

      <div className="viewer-legend">
        {hovered ? (
          <span><strong>{hovered.id}</strong> · {"name" in hovered ? hovered.name : `${(hovered as SceneWire).harness} · ${(hovered as SceneWire).awg} AWG ${humanize((hovered as SceneWire).colorName)}`}</span>
        ) : anyFocus ? (
          <span><span className="swatch" style={{ background: "#f97316" }} /> Highlighted: {[focus.components.size ? `${focus.components.size} part${focus.components.size === 1 ? "" : "s"}` : "", focus.wires.size ? `${focus.wires.size} wire${focus.wires.size === 1 ? "" : "s"}` : ""].filter(Boolean).join(", ")}</span>
        ) : (
          <span>{meshCount === data.components.length ? "All parts from CAD" : `${meshCount} of ${data.components.length} parts from CAD, the rest as catalog envelopes`} · click a part to inspect</span>
        )}
        {options.issues && !anyFocus && <span><span className="swatch" style={{ background: "#d03b3b" }} /> part with an error</span>}
        {options.interference && !playback && data.interference.length > 0 && <span><span className="swatch outline" style={{ borderColor: "#d03b3b" }} /> interference region ({data.interference.length})</span>}
        {markers.length > 0 && !playback && <span><span className="swatch round" style={{ background: markers[0]!.color ?? "#d03b3b" }} /> {markers[0]!.label ?? "marked point"}</span>}
        {options.interfaces && <span><span className="swatch" style={{ background: "#3987e5" }} /> component connector <span className="swatch" style={{ background: "#199e70" }} /> harness connector</span>}
        {options.joins && <span><span className="swatch" style={{ background: "#c98500" }} /> mount <span className="swatch" style={{ background: "#9085e9" }} /> CAD contact</span>}
        {options.explode >= 0.02 && options.wires && !playback && <span className="muted">wires hidden while exploded</span>}
      </div>
      {status && <div className="viewer-status">{status}</div>}
    </div>
  );
}

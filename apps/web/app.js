// R3 dashboard: three.js view of component envelopes and wire routes plus diagnostics.
// Serve the repository root statically (python -m http.server) and open
// /apps/web/index.html?build=/examples/robots/rc-car/build/ , or use "Open files…".
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { buildScene, joinUrl, loadBuild, sortedDiagnostics, validateSummary } from "./loader.js";

const MM = 1000; // scene units are millimetres; robot frame is metres
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pill = (text, cls = text) => `<span class="pill ${esc(cls)}">${esc(text)}</span>`;

// ------------------------------------------------------------------ three.js
const view = $("view");
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setClearColor(0xf0f2f5);
view.prepend(renderer.domElement);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(40, 1, 1, 20000);
camera.up.set(0, 0, 1); // robot frame: +z up
camera.position.set(-350, -420, 380);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(130, 0, 50);
scene.add(new THREE.HemisphereLight(0xffffff, 0x667788, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 1.4);
sun.position.set(300, -400, 800);
scene.add(sun);
const grid = new THREE.GridHelper(1000, 40, 0xbbbbbb, 0xdddddd);
grid.rotation.x = Math.PI / 2; // GridHelper lies in XZ; robot ground plane is XY
scene.add(grid);
scene.add(new THREE.AxesHelper(60));
let content = new THREE.Group();
scene.add(content);
const pickable = [];

function resize() {
  const { clientWidth: w, clientHeight: h } = view;
  renderer.setSize(w, h);
  camera.aspect = w / Math.max(h, 1);
  camera.updateProjectionMatrix();
}
window.addEventListener("resize", resize);
resize();
renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); });

const SEVERITY_COLOR = { error: 0xd62728, warning: 0xe0a800 };

function componentMesh(c) {
  let geometry;
  if (c.shape === "cylinder") {
    const [diameter, length] = c.size.map((v) => v * MM);
    geometry = new THREE.CylinderGeometry(diameter / 2, diameter / 2, length, 40); // axis along local +y
    if (c.axis === "x") geometry.rotateZ(-Math.PI / 2);
    if (c.axis === "z") { geometry.rotateX(Math.PI / 2); geometry.translate(0, 0, length / 2); } // rises from the mount face
  } else {
    const [x, y, z] = c.size.map((v) => v * MM);
    geometry = new THREE.BoxGeometry(x, y, z);
    geometry.translate(0, 0, z / 2); // component origin is the bottom (mounting) face
  }
  const material = new THREE.MeshStandardMaterial({ color: new THREE.Color(c.color), transparent: true,
    opacity: c.kind === "structure" ? 0.45 : 0.85, roughness: 0.7, metalness: 0.1 });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.position.set(...c.xyz.map((v) => v * MM));
  mesh.rotation.set(c.rpy[0], c.rpy[1], c.rpy[2], "ZYX"); // R = Rz * Ry * Rx, as in robot.yaml
  const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geometry, 25),
    new THREE.LineBasicMaterial({ color: SEVERITY_COLOR[c.severity] ?? 0x333333, linewidth: c.severity ? 2 : 1 }));
  mesh.add(edges);
  mesh.userData = { kind: "component", item: c };
  return mesh;
}

function wireMesh(w) {
  const points = w.path.map((p) => new THREE.Vector3(...p.map((v) => v * MM)));
  const curve = points.length > 2 ? new THREE.CatmullRomCurve3(points, false, "centripetal") : new THREE.LineCurve3(points[0], points[1]);
  const radius = Math.max(0.5, 2.0 * Math.pow(0.89, w.awg - 12)); // ~outer radius of silicone wire, mm
  const geometry = new THREE.TubeGeometry(curve, Math.max(8, points.length * 6), radius, 8, false);
  const color = SEVERITY_COLOR[w.severity] ?? w.color;
  const material = new THREE.MeshStandardMaterial({ color, roughness: 0.5, transparent: !w.routed, opacity: w.routed ? 1 : 0.55 });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.userData = { kind: "wire", item: w };
  return mesh;
}

function interferenceMesh(o) {
  const box = new THREE.Mesh(new THREE.BoxGeometry(...o.extent.map((v) => Math.max(v * MM, 0.5))),
    new THREE.MeshBasicMaterial({ color: 0xd62728, transparent: true, opacity: 0.6, depthTest: false }));
  box.position.set(...o.center.map((v) => v * MM));
  box.userData = { kind: "interference", item: o };
  return box;
}

function renderScene(model) {
  scene.remove(content);
  content = new THREE.Group();
  pickable.length = 0;
  for (const c of model.components) { const m = componentMesh(c); content.add(m); pickable.push(m); }
  for (const w of model.wires) { const m = wireMesh(w); content.add(m); pickable.push(m); }
  for (const o of model.interference) content.add(interferenceMesh(o));
  scene.add(content);
  $("wire-note").textContent = model.wires.length
    ? (model.routed ? `${model.wires.length} routed wires (build/wiring/routes.json)` : `${model.wires.length} wires as straight lines (no routes.json yet)`)
    : "no wire data";
  const bounds = new THREE.Box3().setFromObject(content);
  if (!bounds.isEmpty()) controls.target.copy(bounds.getCenter(new THREE.Vector3()));
}

// ------------------------------------------------------------------ picking
const raycaster = new THREE.Raycaster();
renderer.domElement.addEventListener("click", (event) => {
  const rect = renderer.domElement.getBoundingClientRect();
  const pointer = new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
  raycaster.setFromCamera(pointer, camera);
  const hit = raycaster.intersectObjects(pickable, false)[0];
  showPicked(hit ? hit.object.userData : null);
});

function showPicked(data) {
  const box = $("picked");
  if (!data) { box.style.display = "none"; return; }
  const item = data.item;
  const lines = data.kind === "component"
    ? [`<b>${esc(item.id)}</b> <span class="muted">${esc(item.type)} (${esc(item.kind)})</span>`, esc(item.name),
       `mass ${(item.mass * 1000).toFixed(1)} g · pose [${item.xyz.map((v) => (v * MM).toFixed(1)).join(", ")}] mm`]
    : [`<b>${esc(item.id)}</b> <span class="muted">${esc(item.harness)} · AWG ${esc(item.awg)} · ${esc(item.signal || "")}</span>`,
       item.routed ? `routed length ${item.length != null ? (item.length * MM).toFixed(0) + " mm" : "n/a"}` : "not routed yet (straight line)"];
  for (const d of item.diagnostics) lines.push(`${pill(d.severity)} <code>${esc(d.code)}</code> ${esc(d.message)}`);
  box.innerHTML = lines.join("<br>");
  box.style.display = "block";
}

function highlight(subjects) {
  for (const mesh of pickable) {
    const on = subjects.includes(mesh.userData.item.id);
    mesh.material.emissive?.setHex(on ? 0x772222 : 0x000000);
  }
}

// ------------------------------------------------------------------ panels
let current = null;

function renderPanels(data) {
  const { summary } = data;
  const robot = summary.robot;
  $("title").textContent = `R3 · ${robot.name}`;
  $("meta").textContent = `v${robot.version} · robot.yaml ${summary.inputs?.robot_yaml_sha256 ?? ""}`;
  const c = summary.counts;
  $("overall").innerHTML = `${pill(summary.ok ? "passed" : "failed")} ${c.error} errors · ${c.warning} warnings · ${c.info} info`;
  $("stages").innerHTML = "<tr><th>Stage</th><th>Status</th><th>E</th><th>W</th></tr>" + summary.stages.map((s) =>
    `<tr><td>${esc(s.stage)}</td><td>${pill(s.status)}${s.stale ? " " + pill("stale", "warning") : ""}</td>` +
    `<td>${s.counts?.error ?? ""}</td><td>${s.counts?.warning ?? ""}</td></tr>`).join("");
  const stages = [...new Set(summary.diagnostics.map((d) => d.stage))];
  $("f-stage").innerHTML = '<option value="">all stages</option>' + stages.map((s) => `<option>${esc(s)}</option>`).join("");
  renderDiagnostics();
  $("scenarios").innerHTML = (summary.scenarios || []).length
    ? "<tr><th>Scenario</th><th>Result</th></tr>" + summary.scenarios.map((s) =>
      `<tr><td>${esc(s.id)}</td><td>${pill(s.passed ? "passed" : s.passed === false ? "failed" : "missing")}</td></tr>`).join("")
    : '<tr><td class="muted">No scenario results yet (r3 sim run).</td></tr>';
  const images = summary.artifacts?.images || [];
  $("images").innerHTML = data.buildUrl
    ? images.map((p) => `<a href="${esc(joinUrl(data.buildUrl, p))}" target="_blank"><img src="${esc(joinUrl(data.buildUrl, p))}" alt="${esc(p)}" title="${esc(p)}"></a>`).join("")
    : images.map((p) => `<div class="muted">${esc(p)}</div>`).join("");
}

function renderDiagnostics() {
  if (!current) return;
  const list = sortedDiagnostics(current.summary.diagnostics, { severity: $("f-severity").value, stage: $("f-stage").value, text: $("f-text").value });
  $("diagnostics").innerHTML = "<tr><th></th><th>Stage</th><th>Code / message</th></tr>" + list.map((d, i) =>
    `<tr class="diag" data-index="${i}"><td>${pill(d.severity)}</td><td>${esc(d.stage)}</td>` +
    `<td><code>${esc(d.code)}</code> <span class="muted">${esc((d.subjects || []).join(", "))}</span><br>${esc(d.message)}` +
    `${d.hint ? `<br><span class="muted">${esc(d.hint.split("\n")[0])}</span>` : ""}</td></tr>`).join("");
  for (const row of $("diagnostics").querySelectorAll("tr.diag")) {
    row.addEventListener("click", () => highlight(list[Number(row.dataset.index)].subjects || []));
  }
}
for (const id of ["f-severity", "f-stage", "f-text"]) $(id).addEventListener("input", renderDiagnostics);

function show(data) {
  current = data;
  $("status").textContent = "";
  renderPanels(data);
  renderScene(buildScene(data));
}

async function loadFromUrl(url) {
  try {
    $("status").textContent = "loading…";
    show(await loadBuild(url));
    history.replaceState(null, "", `?build=${encodeURIComponent(url)}`);
  } catch (error) {
    $("status").textContent = error.message;
  }
}

$("source").addEventListener("submit", (event) => { event.preventDefault(); loadFromUrl($("build-url").value.trim()); });
$("files").addEventListener("change", async (event) => {
  // Offline mode: pick summary.json (+ optional graph.json, routes.json) from disk.
  const files = {};
  for (const file of event.target.files) files[file.name] = JSON.parse(await file.text());
  try {
    show({ summary: validateSummary(files["summary.json"]), graph: files["graph.json"] || null, routes: files["routes.json"] || null, buildUrl: "" });
  } catch (error) {
    $("status").textContent = error.message;
  }
});

const initial = new URLSearchParams(location.search).get("build") || "../../examples/robots/rc-car/build/";
$("build-url").value = initial;
loadFromUrl(initial);

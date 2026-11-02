import { chromium } from "@playwright/test";
import fs from "node:fs";
const label = process.argv[2] ?? "measurement";
const api = process.env.R3_MEASURE_API ?? "http://127.0.0.1:8351";
const base = process.env.R3_MEASURE_URL ?? "http://127.0.0.1:5201";
const output = process.env.R3_MEASURE_OUTPUT ?? "/tmp/r3-viewer-perf";
fs.mkdirSync(output, { recursive: true });
const projects = await (await fetch(`${api}/api/projects`)).json();
const pid =
  process.env.R3_MEASURE_PROJECT ?? (projects.projects ?? projects)[0].id;
const cad = await (await fetch(`${api}/api/projects/${pid}/cad`)).json();
const expected = Object.keys(cad.meshes).length;
if (!expected) throw new Error("Benchmark requires pulled meshes");
const browser = await chromium.launch({
  channel: "chrome",
  headless: true,
  args: [
    `--use-angle=${process.env.R3_E2E_ANGLE ?? (process.platform === "darwin" ? "metal" : "swiftshader")}`,
    "--ignore-gpu-blocklist",
  ],
});
const context = await browser.newContext({
  viewport: { width: 1600, height: 1000 },
});
await context.addInitScript(() => {
  localStorage.setItem("r3.viewer.measure", "1");
  window.longTasks = [];
  new PerformanceObserver((l) =>
    window.longTasks.push(
      ...l
        .getEntries()
        .map((e) => ({ start: e.startTime, duration: e.duration })),
    ),
  ).observe({ type: "longtask", buffered: true });
});
const results = [];
try {
  for (const route of (process.env.R3_MEASURE_ROUTES ?? "3d,wiring,sim").split(
    ",",
  )) {
    const page = await context.newPage();
    const transfers = [];
    page.on("requestfinished", async (req) => {
      if (req.url().includes("/artifacts/"))
        transfers.push({ url: req.url(), ...(await req.sizes()) });
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    const start = Date.now();
    const cdp = process.env.R3_MEASURE_PROFILE
      ? await context.newCDPSession(page)
      : null;
    if (cdp) {
      await cdp.send("Profiler.enable");
      await cdp.send("Profiler.start");
    }
    await page.goto(`${base}/#/p/${pid}/${route}`);
    try {
      await page.waitForFunction(() => !!window.__r3Viewer, null, {
        timeout: 180000,
      });
    } catch {
      results.push({
        route,
        error: "no viewer",
        text: (await page.locator("body").innerText()).slice(-1800),
        errors,
      });
      await page.close();
      continue;
    }
    const ready = Date.now() - start;
    const times = await page.evaluate(async (expected) => {
      const start = performance.now();
      let first = null;
      return await new Promise((resolve, reject) => {
        function poll() {
          if (performance.now() - start > 180000) {
            reject(new Error(`Only loaded ${expected} meshes after timeout`));
            return;
          }
          let n = 0;
          window.__r3Viewer.state.scene.traverse((o) => {
            if (o.isMesh && o.geometry?.type === "BufferGeometry") n++;
          });
          if (n && first === null) first = performance.now() - start;
          if (n >= expected)
            resolve({ first, all: performance.now() - start, n });
          else requestAnimationFrame(poll);
        }
        poll();
      });
    }, expected);
    const first = ready + times.first,
      all = ready + times.all;
    await page.waitForTimeout(2000);
    if (cdp) {
      fs.writeFileSync(
        `${output}/${label}-${route}-profile.json`,
        JSON.stringify(await cdp.send("Profiler.stop")),
      );
    }
    const loaded = await page.evaluate(() => {
      const { state: s, counters } = window.__r3Viewer;
      let meshes = 0,
        triangles = 0;
      s.scene.traverse((o) => {
        if (o.isMesh) {
          meshes++;
          triangles +=
            (o.geometry.index?.count ??
              o.geometry.attributes.position?.count ??
              0) / 3;
        }
      });
      const gl = s.gl.getContext(),
        ext = gl.getExtension("WEBGL_debug_renderer_info");
      return {
        meshes,
        triangles,
        counters: { ...counters },
        heap: performance.memory?.usedJSHeapSize,
        gpu: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : "unknown",
        resources: performance
          .getEntriesByType("resource")
          .filter((e) => e.name.includes("/artifacts/"))
          .map((e) => ({
            url: e.name,
            bytes: e.transferSize,
            end: e.responseEnd,
          })),
        longTasks: window.longTasks,
      };
    });
    if (
      route === "sim" &&
      (await page.getByRole("button", { name: "Play", exact: true }).count())
    )
      await page.getByRole("button", { name: "Play", exact: true }).click();
    await page.evaluate(() => {
      window.longTasks = [];
      window.samples = [];
      window.calls = [];
      const s = window.__r3Viewer.state;
      let prev = performance.now();
      window.running = true;
      function tick(t) {
        if (!window.running) return;
        window.samples.push(t - prev);
        prev = t;
        const offset = s.camera.position
          .clone()
          .sub(s.controls.target)
          .applyAxisAngle(s.camera.up, 0.002);
        s.camera.position.copy(s.controls.target).add(offset);
        s.controls.update();
        s.invalidate();
        window.calls.push({ ...s.gl.info.render });
        requestAnimationFrame(tick);
      }
      requestAnimationFrame(tick);
    });
    const box = await page.locator("canvas").first().boundingBox();
    for (let i = 0; i < 80; i++)
      await page.mouse.move(
        box.x + box.width * (0.3 + (i % 30) / 100),
        box.y + box.height * 0.5,
      );
    await page.waitForTimeout(3000);
    const interaction = await page.evaluate(() => {
      window.running = false;
      const a = window.samples.slice(5).sort((a, b) => a - b);
      return {
        fps: 1000 / (a.reduce((a, b) => a + b, 0) / a.length),
        p95: a[Math.floor(a.length * 0.95)],
        longTasks: window.longTasks,
        counters: { ...window.__r3Viewer.counters },
        calls: window.calls.reduce((a, b) => (a.calls > b.calls ? a : b), {
          calls: 0,
        }),
      };
    });
    let selection = null;
    if (route === "3d") {
      await page.evaluate(() => {
        window.longTasks = [];
        window.selectionBefore = { ...window.__r3Viewer.counters };
      });
      await page.locator('[data-testid="parts-list"] button').first().click();
      await page.waitForTimeout(1000);
      selection = await page.evaluate(() => ({
        before: window.selectionBefore,
        after: { ...window.__r3Viewer.counters },
        longTasks: window.longTasks,
      }));
    }
    results.push({
      route,
      ready,
      first,
      all,
      ...loaded,
      interaction,
      selection,
      errors,
      transfers,
    });
    await page.close();
    fs.writeFileSync(
      `${output}/${label}.json`,
      JSON.stringify(results, null, 2),
    );
  }
} finally {
  await browser.close();
}
console.log(
  JSON.stringify(
    results.map(({ resources, longTasks, ...r }) => ({
      ...r,
      meshBytes: resources?.reduce((a, b) => a + b.bytes, 0),
      loadLongTaskMax: Math.max(0, ...(longTasks ?? []).map((t) => t.duration)),
    })),
    null,
    2,
  ),
);

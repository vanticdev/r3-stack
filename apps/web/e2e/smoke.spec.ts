// Loads every page against the API (mock layer by default), asserts there are no console errors,
// and saves review screenshots to apps/web/test-results/screenshots/.
import { mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, request, test, type Page } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
const shots = path.resolve(here, "..", "test-results", "screenshots");
mkdirSync(shots, { recursive: true });
const pid = process.env.R3_E2E_PROJECT ?? "rc-car";

// Against the mock layer every response carries X-R3-Mock: 1. Against a real `r3 serve`, the tests that
// start pipeline runs or agent sessions are skipped: they would execute stages and spend the user's
// Claude/Codex subscription.
let mock = true;
test.beforeAll(async ({ baseURL }) => {
  const context = await request.newContext({ baseURL });
  const response = await context.get("/api/projects");
  mock = response.headers()["x-r3-mock"] === "1";
  await context.dispose();
});

// Driver chatter from software WebGL (SwiftShader) is not an application error.
const IGNORED = [/GPU stall due to ReadPixels/, /GL Driver Message/, /THREE\.Clock: This module has been deprecated/];

function watchConsole(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error" && !IGNORED.some((r) => r.test(m.text()))) errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("requestfailed", (r) => {
    // ERR_ABORTED is the app cancelling a superseded query (TanStack Query aborts on refetch) or closing an SSE stream.
    const reason = r.failure()?.errorText ?? "";
    if (!/\/events$/.test(r.url()) && !/ERR_ABORTED/.test(reason)) errors.push(`request failed: ${r.url()} ${reason}`);
  });
  page.on("response", (r) => {
    if (r.status() >= 500) errors.push(`HTTP ${r.status()} ${r.url()}`);
  });
  return errors;
}

async function settle(page: Page, extra = 600) {
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(extra);
}

async function shot(page: Page, name: string) {
  await page.screenshot({ path: path.join(shots, `${name}.png`) });
}

async function viewerReady(page: Page) {
  await expect(page.getByTestId("robot-viewer").locator("canvas").first()).toBeVisible();
  await settle(page, 1800); // STL meshes stream in, then one demand-rendered frame
}

const PAGES: { name: string; route: string; ready: (page: Page) => Promise<void> }[] = [
  { name: "01-projects", route: "/", ready: async (p) => { await expect(p.getByTestId("project-card").first()).toBeVisible(); } },
  { name: "02-overview", route: `/p/${pid}/overview`, ready: async (p) => { await expect(p.getByTestId("evidence-matrix")).toBeVisible(); } },
  { name: "03-3d", route: `/p/${pid}/3d`, ready: viewerReady },
  { name: "04-wiring", route: `/p/${pid}/wiring`, ready: async (p) => { await expect(p.getByTestId("wire-table")).toBeVisible(); await viewerReady(p); } },
  { name: "05-electrical", route: `/p/${pid}/electrical`, ready: async (p) => { await expect(p.getByTestId("operating-points")).toBeVisible(); } },
  { name: "06-pcb", route: `/p/${pid}/pcb`, ready: async (p) => { await expect(p.getByRole("heading", { name: /PCB/ }).first()).toBeVisible(); } },
  { name: "07-software", route: `/p/${pid}/software`, ready: async (p) => { await expect(p.getByTestId("binding-map")).toBeVisible(); } },
  { name: "08-simulation", route: `/p/${pid}/sim`, ready: async (p) => {
    await expect(p.getByTestId("scenario-card").first()).toBeVisible();
    await expect(p.getByTestId("sim-charts").or(p.getByText(/has not been run/))).toBeVisible({ timeout: 60_000 });
    await settle(p, 1500);
  } },
  { name: "09-diagnostics", route: `/p/${pid}/diagnostics`, ready: async (p) => { await expect(p.getByTestId("diagnostic").first()).toBeVisible(); await viewerReady(p); } },
  { name: "10-agent", route: `/p/${pid}/agent`, ready: async (p) => { await expect(p.getByTestId("transcript")).toBeVisible(); } },
  { name: "11-deploy", route: `/p/${pid}/deploy`, ready: async (p) => { await expect(p.getByRole("heading", { name: "Deploy" })).toBeVisible(); } },
  { name: "12-settings", route: `/p/${pid}/settings`, ready: async (p) => { await expect(p.getByTestId("doctor-stages")).toBeVisible(); } },
];

test.describe("every page loads without console errors", () => {
  for (const entry of PAGES) {
    test(entry.name, async ({ page }) => {
      const errors = watchConsole(page);
      await page.goto(`/#${entry.route}`);
      await entry.ready(page);
      await settle(page);
      await shot(page, entry.name);
      expect(errors, errors.join("\n")).toEqual([]);
    });
  }
});

test.describe("interactions", () => {
  test("3D: click a part to inspect, overlays, joints and exploded view", async ({ page }) => {
    const errors = watchConsole(page);
    await page.goto(`/#/p/${pid}/3d?sel=component:battery`);
    await viewerReady(page);
    await expect(page.getByTestId("inspector")).toContainText("battery");
    await page.getByRole("button", { name: "Joints", exact: true }).click();
    await page.getByRole("button", { name: "Interfaces", exact: true }).click();
    await page.getByRole("button", { name: "Joins", exact: true }).click();
    const steering = page.getByLabel("Steering", { exact: true });
    if (await steering.count()) await steering.fill("0.35");
    await settle(page, 800);
    await shot(page, "13-3d-inspect-joints-overlays");
    await page.getByRole("button", { name: "Joints", exact: true }).click();
    await page.getByRole("button", { name: "Interfaces", exact: true }).click();
    await page.getByLabel("Exploded view").fill("0.8");
    await settle(page, 800);
    await shot(page, "14-3d-exploded");
    // Clicking the canvas centre picks whatever part is under it and opens the inspector.
    await page.goto(`/#/p/${pid}/3d`);
    await viewerReady(page);
    const box = (await page.getByTestId("robot-viewer").locator("canvas").first().boundingBox())!;
    await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.55);
    await expect(page).toHaveURL(/sel=component%3A|sel=component:|sel=wire/);
    expect(errors, errors.join("\n")).toEqual([]);
  });

  test("diagnostics: clicking a diagnostic highlights its subjects in 3D", async ({ page }) => {
    const errors = watchConsole(page);
    await page.goto(`/#/p/${pid}/diagnostics`);
    await viewerReady(page);
    await page.getByLabel("Search diagnostics").fill("clearance");
    await page.getByTestId("diagnostic").first().click();
    await expect(page.getByText(/subject\(s\):/)).toBeVisible();
    await settle(page, 1200);
    await shot(page, "15-diagnostics-highlight");
    expect(errors, errors.join("\n")).toEqual([]);
  });

  test("wiring: select a harness and edit its route waypoints", async ({ page }) => {
    const errors = watchConsole(page);
    await page.goto(`/#/p/${pid}/wiring`);
    await viewerReady(page);
    await page.getByRole("row", { name: /esc_motor/ }).first().click();
    await page.getByRole("button", { name: "Edit route" }).click();
    await expect(page.getByRole("button", { name: /Save/ })).toBeVisible();
    await settle(page, 1200);
    await shot(page, "16-wiring-route-edit");
    await page.getByRole("radio", { name: "Slack & vibration" }).click();
    const slack = page.getByTestId("timeline").or(page.getByText(/No cable recordings/));
    await expect(slack).toBeVisible({ timeout: 60_000 });
    await settle(page, 1500);
    await slack.scrollIntoViewIfNeeded();
    await shot(page, "17-wiring-slack-playback");
    expect(errors, errors.join("\n")).toEqual([]);
  });

  test("simulation: scrub the synchronized timeline", async ({ page }) => {
    const errors = watchConsole(page);
    await page.goto(`/#/p/${pid}/sim`);
    await expect(page.getByTestId("sim-charts")).toBeVisible({ timeout: 60_000 });
    const scrub = page.getByLabel("Scrub");
    const max = Number(await scrub.getAttribute("max"));
    await scrub.fill(String(max * 0.55));
    await settle(page, 1500);
    await shot(page, "18-simulation-scrubbed");
    await page.getByTestId("sim-charts").scrollIntoViewIfNeeded();
    await shot(page, "19-simulation-charts");
    expect(errors, errors.join("\n")).toEqual([]);
  });

  test("light theme and fleet deploy confirmation", async ({ page }) => {
    const errors = watchConsole(page);
    await page.goto(`/#/p/${pid}/overview`);
    await expect(page.getByTestId("evidence-matrix")).toBeVisible();
    await page.getByRole("button", { name: "Toggle theme" }).click();
    await settle(page);
    await shot(page, "22-overview-light");
    await page.goto(`/#/p/${pid}/3d`);
    await viewerReady(page);
    await shot(page, "23-3d-light");
    await page.goto(`/#/p/${pid}/deploy`);
    await page.getByRole("button", { name: /Fleet/ }).click();
    await page.getByRole("button", { name: /Deploy to fleet/ }).click();
    const confirm = page.getByRole("dialog").getByRole("button", { name: "Deploy to fleet" });
    await expect(confirm).toBeDisabled();
    await shot(page, "24-deploy-fleet-confirm");
    await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
    expect(errors, errors.join("\n")).toEqual([]);
  });

  // Last: the mock agent applies an (in-memory) spec patch, which marks results stale.
  test("pipeline run and agent session stream live", async ({ page }) => {
    test.skip(!mock, "starts a pipeline run and an agent session: mock API only");
    const errors = watchConsole(page);
    await page.goto(`/#/p/${pid}/overview`);
    await expect(page.getByTestId("evidence-matrix")).toBeVisible();
    await page.getByRole("button", { name: "Run pipeline" }).click();
    await expect(page.getByLabel("Run log").first()).toContainText(/finished|replaying/, { timeout: 30_000 });
    await shot(page, "20-overview-run");
    await page.goto(`/#/p/${pid}/agent`);
    await page.getByLabel("Prompt").fill("Make the battery lead shorter");
    await page.getByRole("button", { name: "Send" }).click();
    await expect(page.getByTestId("transcript")).toContainText(/Session (completed|finished)|session_finished|completed/, { timeout: 60_000 });
    await settle(page, 800);
    await shot(page, "21-agent-session");
    expect(errors, errors.join("\n")).toEqual([]);
  });
});

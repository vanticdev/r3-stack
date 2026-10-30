// Loads every page against the API (mock layer by default, or a running `r3 serve` via R3_E2E_BASE_URL),
// asserts there are no console errors, and saves review screenshots of every screen at 1600 px and
// 390 px in both themes to apps/web/test-results/screenshots/ (or R3_E2E_SCREENSHOTS).
import { mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, request, test, type Page } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
const shots = path.resolve(here, "..", process.env.R3_E2E_SCREENSHOTS ?? "test-results/screenshots");
mkdirSync(shots, { recursive: true });
const pid = process.env.R3_E2E_PROJECT ?? "robot";

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
const backend = () => (mock ? "mock" : "live");

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

/** Full-content capture: the app scrolls inside .content, so let the document grow for the screenshot. */
async function fullShot(page: Page, name: string, flush: boolean) {
  const style = flush ? null : await page.addStyleTag({ content: `
    html, body, #root { height: auto !important; }
    .shell { height: auto !important; min-height: 100vh; }
    .main, .content:not(.flush) { overflow: visible !important; }` });
  await page.waitForTimeout(250);
  await page.screenshot({ path: path.join(shots, `${name}.png`), fullPage: !flush });
  await style?.evaluate((el) => el.remove());
}

async function viewerReady(page: Page) {
  await expect(page.getByTestId("robot-viewer").locator("canvas").first()).toBeVisible();
  await settle(page, 1800); // STL meshes stream in, then one demand-rendered frame
}

const PAGES: { name: string; route: string; flush?: boolean; ready: (page: Page) => Promise<void> }[] = [
  { name: "01-projects", route: "/", ready: async (p) => { await expect(p.getByTestId("project-card").first()).toBeVisible(); } },
  { name: "02-overview", route: `/p/${pid}/overview`, ready: async (p) => { await expect(p.getByTestId("evidence-matrix")).toBeVisible(); } },
  { name: "03-3d", route: `/p/${pid}/3d`, flush: true, ready: viewerReady },
  { name: "04-wiring", route: `/p/${pid}/wiring`, ready: async (p) => { await expect(p.getByTestId("wire-table")).toBeVisible(); await viewerReady(p); } },
  { name: "05-electrical", route: `/p/${pid}/electrical`, ready: async (p) => { await expect(p.getByTestId("operating-points")).toBeVisible(); } },
  { name: "06-pcb", route: `/p/${pid}/pcb`, ready: async (p) => { await expect(p.getByTestId("pcb-checks")).toBeVisible(); } },
  { name: "07-software", route: `/p/${pid}/software`, ready: async (p) => { await expect(p.getByTestId("binding-map")).toBeVisible(); } },
  { name: "08-simulation", route: `/p/${pid}/sim`, ready: async (p) => {
    await expect(p.getByTestId("scenario-card").first()).toBeVisible();
    await expect(p.getByTestId("sim-checks").or(p.getByText(/has not been run/))).toBeVisible({ timeout: 60_000 });
    await settle(p, 1500);
  } },
  { name: "09-diagnostics", route: `/p/${pid}/diagnostics`, flush: true, ready: async (p) => { await expect(p.getByTestId("diagnostic").first()).toBeVisible(); await settle(p, 1500); } },
  { name: "10-agent", route: `/p/${pid}/agent`, ready: async (p) => { await expect(p.getByLabel("Prompt")).toBeVisible(); } },
  { name: "11-deploy", route: `/p/${pid}/deploy`, ready: async (p) => { await expect(p.getByTestId("deploy-readiness")).toBeVisible(); } },
  { name: "12-environment", route: "/environment", ready: async (p) => { await expect(p.getByTestId("doctor-stages")).toBeVisible(); } },
];

const VARIANTS = [
  { width: 1600, height: 1000, theme: "dark" },
  { width: 1600, height: 1000, theme: "light" },
  { width: 390, height: 844, theme: "dark" },
  { width: 390, height: 844, theme: "light" },
] as const;

test.describe("every page loads without console errors, at 1600 and 390 px in both themes", () => {
  for (const entry of PAGES) {
    test(entry.name, async ({ page }) => {
      const errors = watchConsole(page);
      await page.goto(`/#${entry.route}`);
      for (const v of VARIANTS) {
        await page.evaluate((theme) => localStorage.setItem("r3.theme", theme), v.theme);
        await page.setViewportSize({ width: v.width, height: v.height });
        await page.reload();
        await entry.ready(page);
        await settle(page);
        await fullShot(page, `${entry.name}-${v.width}-${v.theme}`, !!entry.flush);
      }
      expect(errors, errors.join("\n")).toEqual([]);
    });
  }
});

test.describe("interactions", () => {
  test("shell: status summary expands into stage rows; narrow screens use a drawer", async ({ page }) => {
    const errors = watchConsole(page);
    await page.goto(`/#/p/${pid}/overview`);
    await expect(page.getByTestId("evidence-matrix")).toBeVisible();
    await page.getByRole("button", { name: "Project status" }).click();
    await expect(page.getByRole("dialog", { name: "Project status" }).getByTestId("stage-list")).toBeVisible();
    await expect(page.getByRole("dialog", { name: "Project status" })).toContainText("Revision details");
    await shot(page, `30-status-summary-${backend()}`);
    await page.keyboard.press("Escape");
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: "Open navigation" }).click();
    await expect(page.getByRole("complementary", { name: "Navigation" }).getByRole("link", { name: "Wiring" })).toBeInViewport();
    await shot(page, `31-mobile-drawer-${backend()}`);
    await page.getByRole("complementary", { name: "Navigation" }).getByRole("link", { name: "Wiring" }).click();
    await expect(page).toHaveURL(/\/wiring/);
    await expect(page.getByRole("complementary", { name: "Navigation" }).getByRole("link", { name: "Wiring" })).not.toBeInViewport();
    expect(errors, errors.join("\n")).toEqual([]);
  });

  test("projects: create dialog with templates", async ({ page }) => {
    const errors = watchConsole(page);
    await page.goto("/#/");
    await page.getByRole("button", { name: "New project" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("radio", { name: /robot/ })).toBeChecked();
    await dialog.getByLabel("Project name").fill("my-rover");
    await dialog.getByText("Also create a GitHub repository").click();
    await expect(dialog).toContainText("Owner");
    await shot(page, `32-projects-create-${backend()}`);
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();
    expect(errors, errors.join("\n")).toEqual([]);
  });

  test("3D: parts list, Show menu layers, move joints, exploded view, click to inspect", async ({ page }) => {
    const errors = watchConsole(page);
    await page.goto(`/#/p/${pid}/3d`);
    await viewerReady(page);
    await expect(page.getByTestId("parts-list")).toBeVisible();
    await page.getByRole("radio", { name: /Errors/ }).click();
    await page.getByTestId("parts-list").getByRole("button").first().click();
    await expect(page.getByTestId("inspector")).toContainText("Diagnostics");
    await page.goto(`/#/p/${pid}/3d?sel=component:battery`);
    await viewerReady(page);
    await expect(page.getByTestId("inspector")).toContainText("battery");
    await page.getByRole("button", { name: "Show layers" }).click();
    for (const layer of ["Connector frames", "Mounts and contacts", "Joint axes"]) await page.getByRole("checkbox", { name: layer }).check();
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Move joints" }).click();
    const steering = page.getByLabel("Steering", { exact: true });
    if (await steering.count()) await steering.fill("0.35");
    await settle(page, 800);
    await shot(page, `13-3d-inspect-joints-overlays-${backend()}`);
    await page.getByRole("button", { name: "Move joints" }).click();
    await page.getByLabel("Exploded view").fill("0.8");
    await settle(page, 800);
    await shot(page, `14-3d-exploded-${backend()}`);
    // Clicking the canvas centre picks whatever part is under it and opens the inspector.
    await page.goto(`/#/p/${pid}/3d`);
    await viewerReady(page);
    const box = (await page.getByTestId("robot-viewer").locator("canvas").first().boundingBox())!;
    await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.55);
    await expect(page).toHaveURL(/sel=component%3A|sel=component:|sel=wire/);
    expect(errors, errors.join("\n")).toEqual([]);
  });

  test("diagnostics: selecting a finding shows its fix and highlights it in 3D", async ({ page }) => {
    const errors = watchConsole(page);
    await page.goto(`/#/p/${pid}/diagnostics`);
    await viewerReady(page);
    await page.getByLabel("Search diagnostics").fill("clearance");
    await page.getByTestId("diagnostic").first().getByRole("button").first().click();
    await expect(page.getByRole("link", { name: "Open in 3D model" })).toBeVisible();
    await expect(page.getByTestId("diagnostic").first()).toContainText(/Fix:|no suggested fix/);
    await settle(page, 1200);
    await shot(page, `15-diagnostics-highlight-${backend()}`);
    expect(errors, errors.join("\n")).toEqual([]);
  });

  test("wiring: locate the worst failure, select a harness and edit its route", async ({ page }) => {
    const errors = watchConsole(page);
    await page.goto(`/#/p/${pid}/wiring`);
    await viewerReady(page);
    const locate = page.getByRole("button", { name: "Locate interference" });
    if (await locate.count()) { await locate.click(); await settle(page, 800); }
    const harness = page.getByTestId("harness-list").getByRole("button", { name: /^ESC motor 1 wire/ });
    if ((await harness.getAttribute("aria-pressed")) !== "true") await harness.click(); // Locate may already have selected it
    await expect(harness).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: "Edit route" }).click();
    await expect(page.getByRole("button", { name: /Save/ })).toBeVisible();
    await expect(page.getByLabel("Route points")).toBeVisible();
    await settle(page, 1200);
    await shot(page, `16-wiring-route-edit-${backend()}`);
    await page.getByRole("radio", { name: "Cable motion" }).click();
    const motion = page.getByTestId("timeline").or(page.getByText(/No cable recordings/));
    await expect(motion).toBeVisible({ timeout: 60_000 });
    await settle(page, 1500);
    await motion.scrollIntoViewIfNeeded();
    await shot(page, `17-wiring-cable-motion-${backend()}`);
    expect(errors, errors.join("\n")).toEqual([]);
  });

  test("simulation: a check's timestamp seeks the shared playhead", async ({ page }) => {
    const errors = watchConsole(page);
    await page.goto(`/#/p/${pid}/sim`);
    await expect(page.getByTestId("sim-charts")).toBeVisible({ timeout: 60_000 });
    const scrub = page.getByLabel("Scrub");
    const seek = page.getByTestId("sim-checks").getByRole("button", { name: /t = / }).first();
    if (await seek.count()) {
      await seek.click();
      await expect.poll(async () => Number(await scrub.inputValue())).toBeGreaterThan(0);
    }
    const max = Number(await scrub.getAttribute("max"));
    await scrub.fill(String(max * 0.55));
    await settle(page, 1500);
    await shot(page, `18-simulation-scrubbed-${backend()}`);
    await page.getByTestId("sim-charts").scrollIntoViewIfNeeded();
    await shot(page, `19-simulation-charts-${backend()}`);
    expect(errors, errors.join("\n")).toEqual([]);
  });

  test("theme toggle and the physical-robot deploy confirmation", async ({ page }) => {
    const errors = watchConsole(page);
    await page.goto(`/#/p/${pid}/overview`);
    await expect(page.getByTestId("evidence-matrix")).toBeVisible();
    await page.getByRole("button", { name: "Toggle theme" }).click();
    await settle(page);
    await shot(page, `22-overview-toggled-theme-${backend()}`);
    await page.goto(`/#/p/${pid}/deploy`);
    await page.getByRole("radio", { name: "Physical robots" }).check();
    const override = page.getByRole("checkbox", { name: /Deploy anyway/ });
    if (await override.count()) await override.check();
    await page.getByRole("button", { name: "Deploy to physical robots" }).click();
    const confirm = page.getByRole("dialog").getByRole("button", { name: "Deploy to robots" });
    await expect(confirm).toBeDisabled();
    await expect(page.getByRole("dialog")).toContainText("robot.yaml");
    await shot(page, `24-deploy-fleet-confirm-${backend()}`);
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
    await expect(page.getByTestId("agent-outcome")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole("button", { name: "Reply" })).toBeVisible();
    await settle(page, 800);
    await shot(page, "21-agent-session");
    expect(errors, errors.join("\n")).toEqual([]);
  });
});

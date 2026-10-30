/** Headless evidence from real recordings with identical viewer dimensions/camera. */
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

const shots = path.resolve("test-results/screenshots");
mkdirSync(shots, { recursive: true });
const pid = process.env.R3_E2E_PROJECT ?? "rc-car";

async function ready(page: Page) {
  const viewer = page.getByTestId("robot-viewer");
  await expect(viewer.locator("canvas")).toBeVisible();
  // Identical dimensions give both pages the same camera angle and scale.
  await viewer.evaluate((el) => { el.style.width = "1000px"; el.style.height = "620px"; if (el.parentElement?.style.height) el.parentElement.style.height = "620px"; });
  await page.waitForLoadState("networkidle");
  await viewer.getByRole("button", { name: "Fit", exact: true }).click();
  await page.waitForTimeout(1800);
  return viewer;
}

test("recorded conductors and vendor models at rest and peak steering", async ({ page, request }) => {
  test.setTimeout(180_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const response = await request.get(`/api/projects/${pid}/sim/steering_sweep?hz=30&cables=true`, { timeout: 120_000 });
  expect(response.ok()).toBe(true);
  const recording = (await response.json()).recording;
  expect(recording.frames.error).toBeUndefined();
  const cables = Object.values(recording.frames.cables) as { wire_id: string; od: number; color: string; points: number[][][] }[];
  expect(cables.length).toBeGreaterThan(30);
  expect(cables.every((c) => c.wire_id && c.od > 0 && c.color && c.points[0]!.length > 2)).toBe(true);
  const steer = recording.series.steer as number[];
  const peak = steer.reduce((best, value, index) => Math.abs(value) > Math.abs(steer[best]!) ? index : best, 0);
  const peakTime = recording.series.t[peak] as number;
  writeFileSync(path.join(shots, "sim-cables-evidence.json"), JSON.stringify({ peakTime, peakSteer: steer[peak], conductors: cables.length }, null, 2));

  await page.goto(`/#/p/${pid}/3d`);
  let viewer = await ready(page);
  await viewer.screenshot({ path: path.join(shots, "sim-cables-model.png") });
  await page.screenshot({ path: path.join(shots, "sim-cables-model-page.png") });

  await page.goto(`/#/p/${pid}/sim?scenario=steering_sweep`);
  viewer = await ready(page);
  await viewer.getByRole("button", { name: "Show layers" }).click();
  await expect(viewer.getByRole("checkbox", { name: "Connectors and pins" })).toBeChecked();
  await viewer.getByRole("button", { name: "Show layers" }).click();
  await expect(page.getByLabel("Scrub")).toHaveValue("0");
  await viewer.screenshot({ path: path.join(shots, "sim-cables-t0.png") });
  await page.screenshot({ path: path.join(shots, "sim-cables-t0-page.png") });
  await page.getByLabel("Scrub").fill(String(peakTime));
  await page.waitForTimeout(1000);
  await viewer.screenshot({ path: path.join(shots, "sim-cables-peak-steering.png") });
  await page.screenshot({ path: path.join(shots, "sim-cables-peak-steering-page.png") });
  expect(errors).toEqual([]);
});

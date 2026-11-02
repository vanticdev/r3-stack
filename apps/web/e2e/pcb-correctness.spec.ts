import { expect, test } from "@playwright/test";

// Deliberately isolated contract fixture: no robot files, paid agents or native
// KiCad execution. This verifies presentation, not PCB physical correctness.
test("PCB views keep copper separate and never certify historical evidence", async ({ page }) => {
  let current = true;
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const stage = { stage: "pcb", status: current ? "ok" : "failed", ok: current, stale: false, counts: {}, started_at: 1 };
    let payload: unknown = {};
    if (path.endsWith("/diagnostics")) payload = { diagnostics: [], counts: {} };
    else if (path.endsWith("/pcb")) payload = {
      result: { diagnostics: [] }, boards: [{
        board: "coupon", component: "controller", evidence_current: current, spec: {},
        board_json: { board: "coupon", component: "controller", outline: { width: .038, height: .028 } },
        erc: { errors: 0 }, drc: { errors: 0, unconnected: 0 },
        connectors: { J1: { "1": "GND", "2": "UNEXPECTED" } }, catalog_connectors: { J1: { "1": "GND" } },
        bom: [], files: [], missing: [], gerbers_url: null,
        renders: ["copper_top.svg", "render_top.png"].map((name) => ({ name, path: `pcb/coupon/${name}`, url: `/fixtures/${name}` })),
      }],
    };
    else if (path.endsWith("/runs")) payload = [];
    else if (path.endsWith("/pcb-fixture")) payload = {
      id: "pcb-fixture", robot: { name: "PCB fixture", version: "1" }, stages: [stage], fingerprint: {},
      git: {}, links: {}, requirements: {}, scenarios: [], counts: {},
      release: { fabrication_ready: false, counts: {}, blocking: [], items: [] },
    };
    await route.fulfill({ json: payload });
  });
  await page.route("**/fixtures/*", (route) => route.fulfill({ contentType: "image/svg+xml",
    body: '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="80"><rect width="100" height="80" fill="green"/></svg>' }));
  await page.goto("/#/p/pcb-fixture/pcb");
  const image = page.getByRole("img");
  await expect(image).toHaveAttribute("src", "/fixtures/render_top.png");
  await expect(page.getByText("1 pin differ of 2")).toBeVisible();
  await page.getByRole("radio", { name: "Copper", exact: true }).click();
  await expect(image).toHaveAttribute("src", "/fixtures/copper_top.svg");
  current = false;
  await page.reload();
  await expect(page.getByText("Checks are unverified for the current build.", { exact: false })).toBeVisible();
  await expect(page.getByTestId("pcb-checks")).not.toContainText("Passed");
  expect(errors).toEqual([]);
});

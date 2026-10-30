import { expect, test } from "@playwright/test";

for (const code of ["cad_pull_missing", "cad_pull_modified", "cad_pull_invalid"]) {
  test(`robot bodies stay hidden when ${code}`, async ({ page }) => {
    const message = "No pulled CAD. Model and mate in Onshape, then run r3 cad pull.";
    await page.route("**/api/projects/authority-coupon/cad", (route) => route.fulfill({ json: {
      authority: { ready: false, code, message, subjects: ["coupon"] },
      cad_model: null, meshes: {}, onshape: {}, views: [], missing: ["cad_model"],
    } }));
    // A legacy graph and local model remain present: neither may make a robot scene.
    await page.route("**/api/projects/authority-coupon/model", (route) => route.fulfill({ json: {
      graph: { components: { coupon: { id: "coupon", xyz: [0, 0, 0], size: [.04, .03, .01] } } },
      components: {}, joints: [], images: [], results: {}, missing: [],
    } }));
    await page.route("**/api/projects/authority-coupon", (route) => route.fulfill({ json: {
      id: "authority-coupon", name: "Synthetic coupon", robot: { name: "Synthetic coupon" }, stages: [],
      summary: { components: 1 }, fingerprint: {}, results: {}, report: null,
      release: { fabrication_ready: false, counts: {}, blocking: [], items: [] }, components: 1, diagnostics: { error: 0, warning: 0, info: 0 }, links: {},
    } }));
    await page.goto("/#/p/authority-coupon/3d");
    await expect(page.getByText(message, { exact: true })).toBeVisible();
    await expect(page.locator("canvas")).toHaveCount(0);
    await expect(page.getByTestId("robot-viewer")).toHaveCount(0);
  });
}

/// <reference lib="dom" />
import { expect, test } from "@playwright/test";

test("loaded Onshape OBJ renders while the qualified model stays unavailable", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  let captured = false;
  let fetched = 0;
  const preview = {
    url: "https://cad.onshape.com/documents/" + "a".repeat(24) + "/w/" + "b".repeat(24) + "/e/" + "c".repeat(24),
    microversion: "synthetic-revision", captured_at: "2026-01-01T00:00:00Z", qualification: "display_mesh",
    total: 2, unavailable: [{ name: "Unloaded fixture", part_index: 1 }],
    parts: [{ name: "Synthetic fixture", part_index: 0,
      mesh_url: "/api/projects/mesh-inspection/artifacts/cad/viewer-mesh/fixture.obj",
      pose: [[1,0,0,.15],[0,1,0,-.2],[0,0,1,.03],[0,0,0,1]] }],
  };
  const cad = () => ({ authority: { ready: false, code: "cad_pull_missing", message: "Full qualified pull is pending" },
    cad_model: null, meshes: {}, onshape: {}, views: [], missing: ["cad_model"], preview: captured ? preview : null });
  await page.route("**/api/projects/mesh-inspection/**", async (route) => {
    const section = new URL(route.request().url()).pathname.split("/mesh-inspection/")[1];
    if (section === "cad/preview") { captured = true; return route.fulfill({ json: cad() }); }
    if (section === "artifacts/cad/viewer-mesh/fixture.obj") {
      fetched++;
      return route.fulfill({ contentType: "model/obj", body: "o body_0\nv 0 0 0\nv .03 0 0\nv 0 .02 .01\nf 1 2 3\n" });
    }
    const responses: Record<string, unknown> = {
      cad: cad(), model: { graph: null, components: {}, joints: [], images: [], missing: ["graph"] },
      spec: { spec: { robot: { name: "Synthetic fixture" }, components: {}, mechanical: {} }, sha256: "fixture", yaml: "" },
      wiring: { routes: null, routing_detail: null, missing: [] }, diagnostics: { diagnostics: [] },
    };
    return route.fulfill({ status: responses[section ?? ""] ? 200 : 404, json: responses[section ?? ""] ?? {} });
  });
  await page.route("**/api/projects/mesh-inspection", (route) => route.fulfill({ json: {
    id: "mesh-inspection", name: "Synthetic fixture", robot: { name: "Synthetic fixture" }, stages: [], summary: { components: 1 },
    fingerprint: {}, results: {}, report: null, release: { fabrication_ready: false, counts: {}, blocking: [], items: [] },
    components: 1, diagnostics: { error: 0, warning: 0, info: 0 }, links: {},
  } }));
  await page.goto("/#/p/mesh-inspection/3d");
  await expect(page.locator("canvas")).toHaveCount(0);
  await page.getByRole("button", { name: "Load Onshape preview", exact: true }).click();
  await expect(page.getByTestId("native-mesh-preview").locator("canvas")).toBeVisible();
  await expect.poll(() => fetched).toBeGreaterThan(0);
  await expect(page.getByText("1 of 2 occurrences loaded")).toBeVisible();
  await expect(page.getByText("Full qualified pull is pending", { exact: true })).toBeVisible();
  await expect(page.getByTestId("robot-viewer")).toHaveCount(0);
  // A mounted canvas and a fetched file are insufficient: verify actual mesh
  // pixels in the middle of the viewport, excluding the corner orientation gizmo.
  await expect.poll(async () => {
    const png = await page.getByTestId("native-mesh-preview").locator("canvas").screenshot();
    return page.evaluate(async (bytes) => {
      const image = await createImageBitmap(new Blob([new Uint8Array(bytes)], { type: "image/png" }));
      const canvas = document.createElement("canvas"); canvas.width = image.width; canvas.height = image.height;
      const context = canvas.getContext("2d")!; context.drawImage(image, 0, 0);
      const pixels = context.getImageData(0, 0, image.width, image.height).data;
      let different = 0;
      for (let y = Math.floor(image.height * .2); y < image.height * .8; y++) {
        for (let x = Math.floor(image.width * .25); x < image.width * .75; x++) {
          const i = (y * image.width + x) * 4;
          if (pixels[i] !== pixels[0] || pixels[i + 1] !== pixels[1] || pixels[i + 2] !== pixels[2]) different++;
        }
      }
      image.close(); return different;
    }, Array.from(png));
  }).toBeGreaterThan(200);
  await page.screenshot({ path: "test-results/screenshots/native-mesh-preview.png", fullPage: true });
  expect(errors).toEqual([]);
});

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

// A synthetic, closed 40 x 30 x 10 mm native STL. The graph also contains an
// obsolete catalog box: a missing mesh must leave that body hidden.
test("valid pulled STL renders while a stale catalog body stays hidden", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  let fetched = 0;
  const vertices = [[-.02,-.015,0],[.02,-.015,0],[.02,.015,0],[-.02,.015,0],
                    [-.02,-.015,.01],[.02,-.015,.01],[.02,.015,.01],[-.02,.015,.01]];
  const faces = [[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],
                 [1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]];
  const stl = "solid coupon\n" + faces.map((f) => "facet normal 0 0 0\nouter loop\n" +
    f.map((i) => `vertex ${vertices[i]!.join(" ")}\n`).join("") + "endloop\nendfacet\n").join("") + "endsolid coupon\n";
  await page.route("**/api/projects/pulled-coupon/**", async (route) => {
    const section = new URL(route.request().url()).pathname.split("/pulled-coupon/")[1];
    if (section === "artifacts/cad/pulled-meshes/coupon.stl") {
      fetched++;
      return route.fulfill({ contentType: "model/stl", body: stl });
    }
    const component = { id: "coupon", name: "Coupon", kind: "structure", type: "synthetic", shape: "mesh",
      size: [.04,.03,.01], xyz: [0,0,0], rpy: [0,0,0], mass: .02, mount: "world", color: "#999999" };
    const responses: Record<string, unknown> = {
      cad: { authority: { ready: true, code: "cad_pulled", message: "Pinned pulled CAD" },
        cad_model: { source: { provider: "pulled" }, parts: { coupon: { bbox_min: [-.02,-.015,0], bbox_max: [.02,.015,.01] } } },
        meshes: { coupon: "/api/projects/pulled-coupon/artifacts/cad/pulled-meshes/coupon.stl" }, onshape: {}, views: [], missing: [] },
      model: { graph: { components: { coupon: component, stale: { ...component, id: "stale", name: "Stale box", shape: "box" } }, wires: {}, endpoints: {} },
        components: {}, joints: [], derived_joints: [], assembly: null, images: [], results: {}, missing: [] },
      spec: { spec: { robot: { name: "Synthetic coupon" }, components: {}, mechanical: {} }, sha256: "fixture", yaml: "" },
      wiring: { routes: null, routing_detail: null, missing: [] }, diagnostics: { diagnostics: [] },
    };
    return route.fulfill({ status: responses[section ?? ""] ? 200 : 404, json: responses[section ?? ""] ?? {} });
  });
  await page.goto("/#/p/pulled-coupon/3d");
  await expect(page.getByTestId("robot-viewer").locator("canvas")).toBeVisible();
  await expect.poll(() => fetched).toBeGreaterThan(0);
  await expect(page.getByText("1 of 2 parts have pulled meshes; missing parts are hidden", { exact: false })).toBeVisible();
  expect(errors).toEqual([]);
});

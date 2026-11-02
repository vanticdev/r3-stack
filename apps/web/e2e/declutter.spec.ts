import { expect, test } from "@playwright/test";

// Run against a populated, isolated r3 serve project. Never starts a robot operation.
const pid = process.env.R3_E2E_PROJECT;
test.skip(!pid, "Set R3_E2E_PROJECT and R3_E2E_BASE_URL to a populated isolated project");
const route = (page: string) => `/#/p/${encodeURIComponent(pid!)}/${page}`;

test("calm defaults keep evidence and navigation reachable in both themes", async ({ page }) => {
  test.setTimeout(180_000);
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  for (const theme of ["light", "dark"]) {
    await page.goto(route("overview"));
    await page.evaluate(t => localStorage.setItem("r3.theme", t), theme);
    await page.reload();
    await expect(page.getByTestId("release-gate")).toBeVisible();
    await expect(page.getByTestId("evidence-matrix")).toBeHidden();
    await page.getByText("Requirements", { exact: true }).click();
    await expect(page.getByTestId("evidence-matrix")).toBeVisible();
    await page.getByText("Build & test", { exact: true }).click();
    await expect(page.getByRole("link", { name: "Wiring", exact: true })).toBeVisible();
    await page.goto(route("software"));
    await expect(page.getByTestId("binding-map")).toBeHidden();
    await page.getByText("Where each software binding goes", { exact: true }).click();
    await expect(page.getByTestId("binding-map")).toBeVisible();
    await page.goto(route("diagnostics"));
    await expect(page.getByLabel("Severity filter")).toHaveValue("error");
    await expect(page.getByTestId("diagnostic").first()).toBeHidden();
    await page.locator(".type-head").first().click();
    await expect(page.getByTestId("diagnostic").first()).toBeVisible();
    await page.getByTestId("diagnostic").first().getByRole("button").first().click();
    await expect(page.locator(".diag-detail")).toBeVisible();
    await page.getByLabel("Severity filter").selectOption("warning");
    await expect(page.locator(".type-head").first()).toBeVisible();
    await page.goto("/#/models");
    await expect(page.getByLabel("Name", { exact: true })).toBeHidden();
    await page.getByText("Add a model", { exact: true }).click();
    await expect(page.getByLabel("Name", { exact: true })).toBeVisible();
  }
  expect(errors).toEqual([]);
});

test("model inspector opens on request and the model remains primary", async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto(route("3d"));
  await expect(page.getByTestId("robot-viewer").locator("canvas")).toBeVisible({ timeout: 120_000 });
  await expect(page.getByLabel("Inspector", { exact: true })).toBeHidden();
  await page.getByRole("button", { name: "Inspect model" }).click();
  await expect(page.getByLabel("Inspector", { exact: true })).toBeVisible();
  await expect(page.getByTestId("parts-list")).toBeVisible();
  await page.getByRole("button", { name: "Inspect model" }).click();
  await expect(page.getByLabel("Inspector", { exact: true })).toBeHidden();
});

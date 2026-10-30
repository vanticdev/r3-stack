import { expect, test } from "@playwright/test";

test("empty project roots show a usable empty state", async ({ page, request }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const response = await request.get("/api/projects");
  expect(response.ok()).toBeTruthy();
  expect((await response.json()).projects).toEqual([]);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Projects", exact: true })).toBeVisible();
  await expect(page.getByText("No projects yet", { exact: true })).toBeVisible();
  await expect(page.getByTestId("project-card")).toHaveCount(0);
  await page.getByRole("button", { name: "New project", exact: true }).first().click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("radio", { name: /Empty project/ })).toBeChecked();
  await expect(dialog.getByRole("radio")).toHaveCount(1);
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  expect(errors).toEqual([]);
});

import { expect, test } from "@playwright/test";

test("agent changes show the owning part and domain files", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/projects/*/agent", async (route) => {
    await route.fulfill({ json: route.request().method() === "POST" ? { session_id: "split-description" } : [] });
  });
  await page.route("**/api/agent/split-description/events", async (route) => {
    const diff = "--- a/parts/link.yaml\n+++ b/parts/link.yaml\n@@ -1 +1 @@\n-label: Before\n+label: After\n"
      + "--- a/software/system.yaml\n+++ b/software/system.yaml\n@@ -1 +1 @@\n-mode: before\n+mode: after\n";
    const events = [
      { type: "session_started", provider: "claude" },
      { type: "spec_changed", sha256: "updated", cumulative_diff: diff },
      { type: "session_finished", status: "completed", cumulative_diff: diff },
    ];
    await route.fulfill({ contentType: "text/event-stream", body: events.map((event, seq) => `data: ${JSON.stringify({ ...event, seq })}\n\n`).join("") });
  });
  await page.goto("/#/p/synthetic/agent");
  await page.getByLabel("Prompt").fill("Update this mechanism");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.getByRole("tab", { name: /Changes|Latest changes/ }).click();
  await expect(page.getByText("parts/link.yaml", { exact: true })).toBeVisible();
  await expect(page.getByText("software/system.yaml", { exact: true })).toBeVisible();
  await expect(page.getByTestId("spec-diff")).toHaveCount(2);
  await expect(page.getByTestId("spec-diff").first()).toContainText("label: After");
  expect(errors).toEqual([]);
});

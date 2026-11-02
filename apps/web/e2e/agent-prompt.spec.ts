import { expect, test } from "@playwright/test";

test("a new agent task preserves the exact authored prompt", async ({ page }) => {
  let submitted: unknown = null;
  await page.route("**/api/projects/*/agent", async (route) => {
    if (route.request().method() === "POST") {
      submitted = route.request().postDataJSON();
      await route.fulfill({ json: { session_id: "prompt-fixture" } });
    } else {
      await route.fulfill({ json: [] });
    }
  });
  await page.route("**/api/agent/prompt-fixture/events", async (route) => {
    await route.fulfill({ contentType: "text/event-stream", body: 'data: {"type":"session_finished","status":"completed","result":"Done"}\n\n' });
  });
  await page.goto("/#/p/robot/agent");
  const prompt = "  Inspect this unrelated fixture.\n\nPreserve whitespace.\n";
  await page.getByLabel("Prompt").fill(prompt);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(() => submitted).toMatchObject({ prompt });
});

test("reply continues the selected provider conversation with exact follow-up text", async ({ page }) => {
  const requests: Record<string, unknown>[] = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/projects/*/agent", async (route) => {
    if (route.request().method() === "POST") {
      requests.push(route.request().postDataJSON() as Record<string, unknown>);
      await route.fulfill({ json: { session_id: requests.length === 1 ? "initial-execution" : "continued-execution" } });
    } else await route.fulfill({ json: [] });
  });
  await page.route("**/api/agent/*/events", async (route) => {
    await route.fulfill({ contentType: "text/event-stream", body: 'data: {"type":"session_finished","status":"completed","result":"Original context stays in the provider"}\n\n' });
  });
  await page.goto("/#/p/synthetic/agent");
  await page.getByRole("radio", { name: "Codex", exact: true }).click();
  await page.getByText("Options", { exact: true }).click();
  await page.getByLabel("Model", { exact: true }).fill("synthetic-model");
  await page.getByLabel("Prompt").fill("Remember the original task");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByTestId("agent-outcome")).toBeVisible();
  await page.getByRole("radio", { name: "Claude Code", exact: true }).click();
  if (!await page.getByLabel("Model", { exact: true }).isVisible()) await page.getByText("Options", { exact: true }).click();
  await page.getByLabel("Model", { exact: true }).fill("different-new-task-model");
  const followup = "  Retry the failed operation.\nKeep the existing context.\n";
  await page.getByLabel("Prompt").fill(followup);
  await page.getByRole("button", { name: "Reply", exact: true }).click();
  await expect.poll(() => requests.length).toBe(2);
  expect(requests[1]).toEqual({ prompt: followup, provider: "codex", model: "synthetic-model", resume_session_id: "initial-execution" });
  expect(errors).toEqual([]);
});

test("reply to a reopened execution shows its latest recorded changes", async ({ page }) => {
  const parent = "a".repeat(12);
  const child = "b".repeat(12);
  const submitted: Record<string, unknown>[] = [];
  await page.route("**/api/projects/*/agent", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: [{ session_id: parent, project: "synthetic", provider: "codex", model: "synthetic-model",
        prompt: "Retained task\n", status: "completed", created_at: 1, finished_at: 2, result: "Checkpoint", error: null, returncode: 0, events: 1 }] });
    } else {
      submitted.push(route.request().postDataJSON() as Record<string, unknown>);
      await route.fulfill({ json: { session_id: child } });
    }
  });
  await page.route("**/api/agent/*/events", async (route) => {
    const diff = "--- a/parts/link.yaml\n+++ b/parts/link.yaml\n@@ -1 +1 @@\n-label: Before\n+label: After retry\n";
    const events = route.request().url().includes(child)
      ? [{ type: "spec_changed", cumulative_diff: diff }, { type: "session_finished", status: "completed" }]
      : [{ type: "session_finished", status: "completed", result: "Checkpoint" }];
    await route.fulfill({ contentType: "text/event-stream", body: events.map(e => `data: ${JSON.stringify(e)}\n\n`).join("") });
  });
  await page.goto("/#/p/synthetic/agent");
  const retained = page.getByLabel("Agent history").getByRole("button", { name: /Retained task/ });
  await expect(retained).toBeVisible();
  await retained.click();
  await page.getByLabel("Prompt").fill("Retry the operation");
  await page.getByRole("button", { name: "Reply", exact: true }).click();
  await expect.poll(() => submitted.length).toBe(1);
  expect(submitted[0]).toEqual({ prompt: "Retry the operation", provider: "codex", model: "synthetic-model", resume_session_id: parent });
  await expect(page.getByText("parts/link.yaml", { exact: true })).toBeVisible();
  await expect(page.getByTestId("spec-diff")).toContainText("label: After retry");
});

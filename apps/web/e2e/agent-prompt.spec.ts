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

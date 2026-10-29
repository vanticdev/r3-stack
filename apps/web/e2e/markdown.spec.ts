import { mkdirSync } from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

const transcript = [
  "## Wiring review",
  "The **battery lead** is ready for the next check. Use `robot.yaml` to inspect it.",
  "| Part | Check | Result | Next step |\n|---|---|---|---|\n| Battery lead | Clearance | **8 mm** | Run wiring |\n| Motor lead | Bend radius | Passed | Keep route |",
  "- [x] Inspect the route\n- [ ] Run simulation",
  "> Keep the lead clear of moving parts.",
  '```python\nprint("Ready for simulation")\nvalidate_route(harness="battery_lead", min_clearance_mm=8, moving_parts=["front_wheel", "rear_wheel"], allow_estimates=False)\n```',
  "[Review guide](https://example.com/guide)",
].join("\n\n");

for (const width of [1600, 390]) for (const theme of ["light", "dark"]) {
  test(`Agent Markdown at ${width}px in ${theme}`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await page.addInitScript((value) => localStorage.setItem("r3.theme", value), theme);
    await page.route("**/api/projects/*/agent", async (route) => {
      await route.fulfill({ json: route.request().method() === "POST" ? { session_id: "markdown-fixture" } : [] });
    });
    await page.route("**/api/agent/markdown-fixture/events", async (route) => {
      const events = [
        { type: "session_started", provider: "claude" },
        { type: "message", role: "assistant", text: transcript },
        { type: "tool_call", id: "inspect", name: "inspect_route", input: { harness: "battery_lead" } },
        { type: "tool_result", id: "inspect", ok: true, content: "### Tool findings\n\n- **Clearance** verified\n\n`8 mm`" },
        { type: "session_finished", status: "completed" },
      ];
      await route.fulfill({ contentType: "text/event-stream", body: events.map((event, seq) => `data: ${JSON.stringify({ ...event, seq })}\n\n`).join("") });
    });
    await page.goto("/#/p/rc-car/agent");
    await page.getByLabel("Prompt").fill("Review the wiring");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    const chat = page.getByTestId("transcript");
    await expect(chat.locator("h2")).toHaveText("Wiring review");
    await expect(chat.locator("table")).toBeVisible();
    await expect(chat.locator("tbody tr")).toHaveCount(2);
    await expect(chat).not.toContainText("|---|");
    await expect(chat.locator("pre code")).toContainText('print("Ready for simulation")');
    await expect(chat.getByRole("link", { name: "Review guide" })).toHaveAttribute("rel", "noopener noreferrer");
    await chat.locator(".tool summary").click();
    await expect(chat.locator(".tool h3")).toHaveText("Tool findings");
    await expect(chat.locator(".tool pre")).toContainText('"harness": "battery_lead"');
    // Long prose/code wrap; only wide tables scroll within their own container.
    expect(await chat.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
    const tableRegion = chat.getByRole("region", { name: "Markdown table" });
    expect(await tableRegion.evaluate((el) => el.ownerDocument.defaultView!.getComputedStyle(el).overflowX)).toBe("auto");
    if (width === 390) expect(await tableRegion.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true);
    await chat.locator("h2").scrollIntoViewIfNeeded();
    const shots = path.resolve(process.env.R3_E2E_SCREENSHOTS ?? "test-results/screenshots");
    mkdirSync(shots, { recursive: true });
    // The app scrolls inside .content; expand that scroller for a complete review capture.
    await page.addStyleTag({ content: `
      html, body, #root { height: auto !important; }
      .shell { height: auto !important; min-height: 100vh; }
      .main, .content { overflow: visible !important; }
      .composer.docked { position: static; }` });
    await page.screenshot({ path: path.join(shots, `agent-markdown-${width}-${theme}.png`), fullPage: true });
    expect(errors).toEqual([]);
  });
}

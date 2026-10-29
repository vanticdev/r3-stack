// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Markdown } from "./Markdown.tsx";

let host: HTMLDivElement;
let root: Root;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.restoreAllMocks();
});
function render(text: string, inline = false) {
  act(() => root.render(<Markdown inline={inline}>{text}</Markdown>));
}

describe("Markdown", () => {
  it("renders headings, GFM tables, lists, task lists, strikethrough and quotes", () => {
    render("## Checks\n\n| Part | Result |\n|---|---|\n| motor | **pass** |\n\n- first\n- second\n\n1. ordered\n\n- [x] done\n- [ ] pending\n\n~~old~~\n\n> Keep clearance.");
    expect(host.querySelector("h2")?.textContent).toBe("Checks");
    expect(host.querySelectorAll("table tbody tr")).toHaveLength(1);
    expect(host.querySelector("th")?.textContent).toBe("Part");
    expect(host.querySelector("td strong")?.textContent).toBe("pass");
    expect(host.querySelector("ul li")?.textContent).toBe("first");
    expect(host.querySelector("ol li")?.textContent).toBe("ordered");
    const tasks = host.querySelectorAll<HTMLInputElement>('input[type="checkbox"]');
    expect(tasks).toHaveLength(2);
    expect(tasks[0]?.checked).toBe(true);
    expect(tasks[0]?.disabled).toBe(true);
    expect(tasks[1]?.checked).toBe(false);
    expect(host.querySelector("del")?.textContent).toBe("old");
    expect(host.querySelector("blockquote")?.textContent).toContain("Keep clearance.");
    expect(host.textContent).not.toContain("|---|");
  });

  it("renders inline and fenced code and copies the exact code as text", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    render('Use `robot.yaml`.\n\n```python\nprint("<script>")\n```');
    expect(host.querySelector("p code")?.textContent).toBe("robot.yaml");
    expect(host.querySelector("pre code")?.className).toBe("language-python");
    expect(host.querySelector("pre code")?.textContent).toBe('print("<script>")\n');
    expect(host.querySelector("script")).toBeNull();
    await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Copy code"]')!.click());
    expect(writeText).toHaveBeenCalledWith('print("<script>")\n');
    expect(host.querySelector("button")?.textContent).toBe("Copied");
  });

  it("opens safe explicit links and GFM autolinks with opener protection", () => {
    render("[Docs](https://example.com/docs) and https://example.org and [local](/guide)");
    const links = host.querySelectorAll("a");
    expect(links).toHaveLength(3);
    for (const link of links) {
      expect(link.target).toBe("_blank");
      expect(link.rel).toBe("noopener noreferrer");
    }
    expect(links[0]?.getAttribute("href")).toBe("https://example.com/docs");
  });

  it("neutralizes scripts, raw HTML, event attributes and unsafe URL schemes", () => {
    render('<script>window.pwned = true</script>\n\n<img src=x onerror="alert(1)">\n\n<iframe src="https://evil.test"></iframe>\n\n<a href="javascript:alert(1)">raw link</a>\n\n[bad](javascript:alert%281%29) [entity](java&#x73;cript:alert%281%29) [data](data:text/html,evil)\n\n![tracker](https://evil.test/track.png)');
    expect(host.querySelector("script, img, iframe")).toBeNull();
    expect(host.querySelector("[onerror], [onclick]")).toBeNull();
    expect(host.querySelector("a")).toBeNull();
    expect(host.textContent).not.toContain("window.pwned");
    expect(host.textContent).toContain("bad");
    expect(host.textContent).toContain("[Image: tracker]");
  });

  it("extends unfinished fences without replacing completed content or the code block", () => {
    const start = "## Stable heading\n\n- first\n\n```python\nprint(";
    render(start);
    const heading = host.querySelector("h2");
    const pre = host.querySelector("pre");
    const button = host.querySelector("button");
    expect(pre?.textContent).toBe("print(\n");
    render(start + '"ready")\n```');
    expect(host.querySelector("h2")).toBe(heading);
    expect(host.querySelector("pre")).toBe(pre);
    expect(host.querySelector("button")).toBe(button);
    expect(pre?.textContent).toBe('print("ready")\n');
  });

  it("handles partial emphasis, links and table rows as input arrives", () => {
    for (const partial of ["", "##", "**checking", "[docs](https://example", "| Part | Result |\n|---", "| Part | Result |\n|---|---|\n| motor | p"]) {
      expect(() => render(partial)).not.toThrow();
    }
    render("| Part | Result |\n|---|---|\n| motor | pass |");
    expect(host.querySelector("table td")?.textContent).toBe("motor");
  });

  it("memoizes unchanged messages while later messages update", () => {
    const mutation = vi.fn();
    render("## Previous message");
    const observer = new MutationObserver(mutation);
    observer.observe(host, { subtree: true, childList: true, characterData: true });
    render("## Previous message");
    expect(observer.takeRecords()).toHaveLength(0);
    observer.disconnect();
  });

  it("keeps compact labels valid inside interactive controls", () => {
    render("**Fix** `motor` [docs](https://example.com)", true);
    expect(host.querySelector("strong")?.textContent).toBe("Fix");
    expect(host.querySelector("code")?.textContent).toBe("motor");
    expect(host.querySelector("p, div.markdown, a, button")).toBeNull();
    expect(host.textContent).toContain("docs");
  });
});

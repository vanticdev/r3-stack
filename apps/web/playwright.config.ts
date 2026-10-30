import { existsSync } from "node:fs";

import { chromium, defineConfig } from "@playwright/test";

// R3_E2E_BASE_URL points the smoke test at a running server (e.g. `r3 serve`); otherwise it builds the
// app and serves it with `vite preview`, whose /api answers from the mock layer (build/ artifacts).
const external = process.env.R3_E2E_BASE_URL;
const port = Number(process.env.R3_E2E_PORT ?? 4317);
// Use native headless Metal on macOS; Linux/server runs use software rendering.
const angle = process.env.R3_E2E_ANGLE ?? (process.platform === "darwin" ? "metal" : "swiftshader");
// Prefer Playwright's own Chromium; fall back to an installed Google Chrome when it was not downloaded.
const bundled = (() => { try { return existsSync(chromium.executablePath()); } catch { return false; } })();

export default defineConfig({
  testDir: "e2e",
  outputDir: "test-results/playwright",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: external ?? `http://127.0.0.1:${port}`,
    viewport: { width: 1600, height: 1000 },
    headless: true,
    colorScheme: "dark",
    // Modern headless Chromium uses the full browser compositor for WebGL screenshots.
    // https://playwright.dev/docs/browsers#chromium-new-headless-mode
    channel: bundled ? "chromium" : "chrome",
    launchOptions: { args: [`--use-angle=${angle}`, "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] },
  },
  webServer: external ? undefined : {
    command: `npm run build && npx vite preview --host 127.0.0.1 --port ${port} --strictPort`,
    url: `http://127.0.0.1:${port}/api/doctor`,
    reuseExistingServer: false, // a fresh mock server per run: spec patches live in its memory
    timeout: 240_000,
    env: { R3_API: process.env.R3_API ?? "mock" },
  },
});

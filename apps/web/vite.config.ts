/// <reference types="vitest/config" />
import path from "node:path";
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

import { backendMiddleware } from "./mock/backend.ts";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "..", "..");

/** /api -> `r3 serve` (R3_API_URL, default http://127.0.0.1:8321) or the mock layer when it is down. */
function r3Api(): Plugin {
  const options = () => ({
    repoRoot,
    target: process.env.R3_API_URL ?? "http://127.0.0.1:8321",
    mode: (process.env.R3_API ?? "auto") as "auto" | "mock" | "proxy",
    cacheDir: path.join(here, ".mock-cache"),
  });
  return {
    name: "r3-api",
    configureServer(server) {
      server.middlewares.use(backendMiddleware({ ...options(), log: (m) => server.config.logger.info(m) }));
    },
    configurePreviewServer(server) {
      server.middlewares.use(backendMiddleware({ ...options(), log: (m) => server.config.logger.info(m) }));
    },
  };
}

export default defineConfig({
  plugins: [react(), r3Api()],
  resolve: { dedupe: ["react", "react-dom", "three"] },
  // Pre-bundle everything up front so the dev server never re-optimizes mid-session (two React copies).
  optimizeDeps: {
    include: ["react", "react-dom", "react-dom/client", "react-router-dom", "@tanstack/react-query", "three", "@react-three/fiber",
              "@react-three/drei", "uplot", "three/examples/jsm/loaders/STLLoader.js"],
  },
  server: { port: 5173, strictPort: false },
  preview: { port: 4173 },
  build: {
    outDir: "dist",
    chunkSizeWarningLimit: 1600,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules/three/") || id.includes("@react-three") || id.includes("three-stdlib")) return "three";
          if (id.includes("node_modules/uplot")) return "charts";
          return undefined;
        },
      },
    },
  },
  test: {
    include: ["src/**/*.test.ts"],
    environment: "node",
  },
});

// /api routing for the Vite dev and preview servers: forward to `r3 serve` when it is reachable,
// otherwise answer from the mock layer (build/ artifacts). R3_API=mock|proxy forces either side.
import http from "node:http";
import net from "node:net";
import path from "node:path";

import type { Connect } from "vite";

import { createMockApi } from "./api.ts";

export interface BackendOptions {
  repoRoot: string;
  target: string; // e.g. http://127.0.0.1:8000
  mode: "auto" | "mock" | "proxy";
  cacheDir: string;
  log?: (message: string) => void;
}

function reachable(target: URL, timeoutMs = 300): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = net.connect({ host: target.hostname, port: Number(target.port || 80) });
    const done = (ok: boolean) => { socket.destroy(); resolve(ok); };
    socket.setTimeout(timeoutMs, () => done(false));
    socket.once("connect", () => done(true));
    socket.once("error", () => done(false));
  });
}

export function backendMiddleware(options: BackendOptions): Connect.NextHandleFunction {
  const target = new URL(options.target);
  const mock = createMockApi({ repoRoot: options.repoRoot, cacheDir: path.resolve(options.cacheDir) });
  let checked = 0;
  let live = false;
  let announced: boolean | null = null;

  async function useProxy(write = false): Promise<boolean> {
    if (options.mode !== "auto") return options.mode === "proxy";
    // A stale negative read probe must not route a recovered backend's writes to the read-only mock.
    if (Date.now() - checked > 2000 || (write && !live)) {
      live = await reachable(target);
      checked = Date.now();
      if (announced !== live) {
        options.log?.(live ? `r3 API: proxying /api to ${target.origin}` : `r3 API: ${target.origin} unreachable, serving the mock API from build/ artifacts`);
        announced = live;
      }
    }
    return live;
  }

  return (req, res, next) => {
    if (!req.url?.startsWith("/api")) return next();
    // Hold the body until we know where it goes; otherwise it may end before it can be piped upstream.
    req.pause();
    const write = !["GET", "HEAD", "OPTIONS"].includes(req.method ?? "GET");
    void useProxy(write).then((proxy) => {
      if (!proxy) { req.resume(); return mock(req, res, next); }
      const upstream = http.request(
        { host: target.hostname, port: target.port || 80, method: req.method, path: req.url, headers: { ...req.headers, host: target.host } },
        (response) => {
          res.writeHead(response.statusCode ?? 502, response.headers);
          response.pipe(res); // streams SSE unbuffered
        },
      );
      upstream.on("error", (error) => {
        checked = 0;
        if (res.headersSent) return res.end();
        res.writeHead(502, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ code: "backend_unreachable", message: `r3 serve at ${target.origin}: ${error.message}`, data: {} }));
      });
      // The client went away (e.g. an SSE stream was closed): stop the upstream request too.
      res.on("close", () => { if (!res.writableFinished) upstream.destroy(); });
      req.pipe(upstream);
    });
  };
}

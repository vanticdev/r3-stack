import { afterEach, expect, test, vi } from "vitest";
import http, { type Server } from "node:http";
import { once } from "node:events";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { backendMiddleware } from "../../mock/backend.ts";

const servers: Server[] = [];
const folders: string[] = [];
async function listen(server: Server, port = 0): Promise<number> {
  servers.push(server);
  server.listen(port, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Expected TCP address");
  return address.port;
}
async function bridge(target: number, mode: "auto" | "mock" = "auto"): Promise<string> {
  const folder = await mkdtemp(path.join(tmpdir(), "r3-backend-recovery-"));
  folders.push(folder);
  const middleware = backendMiddleware({ repoRoot: folder, target: `http://127.0.0.1:${target}`,
    mode, cacheDir: path.join(folder, "cache") });
  return `http://127.0.0.1:${await listen(http.createServer((req, res) => middleware(req, res, () => {
    res.writeHead(404); res.end();
  })))}`;
}
afterEach(async () => {
  vi.restoreAllMocks();
  for (const server of servers.splice(0)) {
    server.closeAllConnections();
    if (server.listening) await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  }
  for (const folder of folders.splice(0)) await rm(folder, { recursive: true, force: true });
});

test("a write rechecks a recovered backend before the negative read cache expires", async () => {
  vi.spyOn(Date, "now").mockReturnValue(10000);
  const reservation = http.createServer();
  const target = await listen(reservation);
  await new Promise<void>((resolve) => reservation.close(() => resolve()));
  const url = await bridge(target);
  const initial = await fetch(url + "/api/projects");
  expect(initial.headers.get("x-r3-mock")).toBe("1");
  await initial.arrayBuffer();
  const requests: string[] = [];
  await listen(http.createServer(async (req, res) => {
    requests.push(req.method ?? "");
    const chunks: Buffer[] = [];
    for await (const chunk of req) chunks.push(chunk as Buffer);
    res.writeHead(201, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ id: "synthetic-fixture", received: JSON.parse(Buffer.concat(chunks).toString("utf8")) }));
  }), target);
  // Reads may retain their cache; an authored write must not be sent to the mock.
  const cached = await fetch(url + "/api/projects");
  expect(cached.headers.get("x-r3-mock")).toBe("1");
  await cached.arrayBuffer();
  const body = { name: "synthetic-fixture", payload: "αβγ".repeat(4000) };
  const created = await fetch(url + "/api/projects", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body), signal: AbortSignal.timeout(2000) });
  expect(created.status).toBe(201);
  expect(created.headers.get("x-r3-mock")).toBeNull();
  expect(await created.json()).toEqual({ id: "synthetic-fixture", received: body });
  expect(requests).toEqual(["POST"]);
});

test("explicit mock mode stays read-only even with a reachable backend", async () => {
  const requests: string[] = [];
  const target = await listen(http.createServer((req, res) => { requests.push(req.method ?? ""); res.end("{}"); }));
  const url = await bridge(target, "mock");
  const response = await fetch(url + "/api/projects", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: "synthetic-fixture" }), signal: AbortSignal.timeout(2000) });
  expect(response.status).toBe(503);
  expect((await response.json()).code).toBe("mock_read_only");
  expect(requests).toEqual([]);
});


test("an upstream write failure returns once without replaying it", async () => {
  let requests = 0;
  const target = await listen(http.createServer((req) => { requests++; req.resume(); req.socket.destroy(); }));
  const url = await bridge(target);
  const response = await fetch(url + "/api/projects", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: "synthetic-fixture" }), signal: AbortSignal.timeout(2000) });
  expect(response.status).toBe(502);
  expect((await response.json()).code).toBe("backend_unreachable");
  expect(requests).toBe(1);
});

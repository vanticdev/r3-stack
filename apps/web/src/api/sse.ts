// Server-sent events over fetch (instead of EventSource) so every event name reaches one
// handler, errors are observable, and the stream can be aborted.

export interface SseMessage {
  event: string;
  data: string;
  id?: string;
}

/** Incremental text/event-stream parser (WHATWG HTML §9.2.6). */
export class SseParser {
  private buffer = "";
  private event = "";
  private data: string[] = [];
  private id: string | undefined;

  push(chunk: string): SseMessage[] {
    this.buffer += chunk;
    const out: SseMessage[] = [];
    let newline: number;
    while ((newline = this.buffer.search(/\r\n|\r|\n/)) >= 0) {
      const line = this.buffer.slice(0, newline);
      const width = this.buffer.startsWith("\r\n", newline) ? 2 : 1;
      this.buffer = this.buffer.slice(newline + width);
      if (line === "") {
        if (this.data.length) out.push({ event: this.event || "message", data: this.data.join("\n"), ...(this.id ? { id: this.id } : {}) });
        this.event = "";
        this.data = [];
        continue;
      }
      if (line.startsWith(":")) continue; // comment / keep-alive
      const colon = line.indexOf(":");
      const field = colon < 0 ? line : line.slice(0, colon);
      let value = colon < 0 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") this.event = value;
      else if (field === "data") this.data.push(value);
      else if (field === "id") this.id = value;
    }
    return out;
  }
}

/** Parses an SSE message into a typed event: `{type: <event name>, ...JSON data}`. */
export function toEvent<T extends { type: string }>(message: SseMessage): T {
  let payload: Record<string, unknown> = {};
  try {
    const parsed: unknown = JSON.parse(message.data);
    payload = parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : { value: parsed };
  } catch {
    payload = { message: message.data };
  }
  const type = message.event !== "message" ? message.event : typeof payload.type === "string" ? payload.type : "message";
  return { ...payload, type } as unknown as T;
}

export interface StreamHandle {
  close(): void;
  done: Promise<void>;
}

/** Streams events from `url` until the server closes the stream or `close()` is called. */
export function streamEvents<T extends { type: string }>(url: string, onEvent: (event: T) => void,
                                                        onError?: (error: Error) => void): StreamHandle {
  const controller = new AbortController();
  const done = (async () => {
    try {
      const response = await fetch(url, { headers: { Accept: "text/event-stream" }, signal: controller.signal });
      if (!response.ok || !response.body) throw new Error(`event stream ${url}: HTTP ${response.status}`);
      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      const parser = new SseParser();
      for (;;) {
        const { value, done: finished } = await reader.read();
        if (finished) break;
        for (const message of parser.push(value)) onEvent(toEvent<T>(message));
      }
    } catch (error) {
      if ((error as Error).name !== "AbortError") onError?.(error as Error);
    }
  })();
  return { close: () => controller.abort(), done };
}

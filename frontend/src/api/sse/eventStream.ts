import { API_BASE } from "../client";
import { ApiError } from "../errors";
import { SseParser, type SseFrame } from "./parser";

export type StreamStatus = "connecting" | "open" | "reconnecting" | "disconnected" | "closed";
export type StreamFrame = SseFrame & { seq: number | null };

export type StreamOptions = {
  /** API path, e.g. `/tasks/{id}/events` (API_CONTRACTS §5). */
  path: string;
  /** Resume after this sequence number (`?after_seq=` and `Last-Event-ID`). */
  lastSeq?: number | null;
  onFrame: (frame: StreamFrame) => void;
  onStatus?: (status: StreamStatus, error?: ApiError) => void;
  onProtocolError?: (message: string) => void;
  /** Silence longer than this means a dead connection: the server sends `: ping` every 15 s. */
  heartbeatTimeoutMs?: number;
  initialBackoffMs?: number;
  maxBackoffMs?: number;
  /** Consecutive failures before the status turns from reconnecting to disconnected (retries go on). */
  disconnectedAfter?: number;
  jitter?: () => number;
  fetchImpl?: typeof fetch;
};

export type EventStream = { close(): void; lastSeq(): number | null };

const FATAL = new Set([401, 403, 404]);

/**
 * A resumable server-sent event stream over fetch. Not EventSource: a manual reconnect needs the
 * Last-Event-ID header, and the `: ping` heartbeat is only visible to a reader of the raw stream
 * (spec WP-1.4a, conflict #62). Frames with an id are delivered once, in seq order; frames without
 * one (ephemeral, e.g. agent.delta) are always delivered.
 */
export function openEventStream(options: StreamOptions): EventStream {
  const heartbeatMs = options.heartbeatTimeoutMs ?? 45_000;
  const initialBackoff = options.initialBackoffMs ?? 1_000;
  const maxBackoff = options.maxBackoffMs ?? 30_000;
  const disconnectedAfter = options.disconnectedAfter ?? 3;
  const jitter = options.jitter ?? Math.random;
  const fetchImpl = options.fetchImpl ?? fetch.bind(globalThis);

  let lastSeq = options.lastSeq ?? null;
  let failures = 0;
  let closed = false;
  let status: StreamStatus | null = null;
  let controller: AbortController | null = null;
  let reader: ReadableStreamDefaultReader<string> | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;

  const report = (next: StreamStatus, error?: ApiError) => {
    if (closed && next !== "closed") return;
    status = next;
    options.onStatus?.(next, error);
  };

  const scheduleRetry = (error: ApiError) => {
    if (closed) return;
    failures += 1;
    report(failures >= disconnectedAfter ? "disconnected" : "reconnecting", error);
    const base = Math.min(maxBackoff, initialBackoff * 2 ** (failures - 1));
    retryTimer = setTimeout(() => void connect(), base + base * 0.2 * jitter());
  };

  const deliver = (frame: SseFrame) => {
    if (frame.id === null) {
      options.onFrame({ ...frame, seq: null });
      return;
    }
    if (!/^\d+$/.test(frame.id)) {
      options.onProtocolError?.(`event id is not a sequence number: ${frame.id.slice(0, 40)}`);
      return;
    }
    const seq = Number(frame.id);
    if (lastSeq !== null && seq <= lastSeq) return; // already delivered (replay after a reconnect)
    lastSeq = seq;
    options.onFrame({ ...frame, seq });
  };

  async function connect(): Promise<void> {
    if (closed) return;
    report(status === null ? "connecting" : status === "disconnected" ? "disconnected" : "reconnecting");
    const attempt = new AbortController();
    controller = attempt;
    const url = new URL(`${API_BASE}${options.path}`, window.location.origin);
    const headers = new Headers({ Accept: "text/event-stream" });
    if (lastSeq !== null) {
      url.searchParams.set("after_seq", String(lastSeq));
      headers.set("Last-Event-ID", String(lastSeq));
    }

    let watchdog: ReturnType<typeof setTimeout> | null = null;
    let silent = false;
    const arm = () => {
      if (watchdog) clearTimeout(watchdog);
      watchdog = setTimeout(() => {
        silent = true;
        attempt.abort();
        // Cancel the reader too: not every fetch errors an already-open body on abort.
        reader?.cancel().catch(() => {});
      }, heartbeatMs);
    };

    try {
      arm();
      const response = await fetchImpl(url, {
        headers,
        credentials: "same-origin",
        cache: "no-store",
        signal: attempt.signal,
      });
      if (FATAL.has(response.status)) {
        if (watchdog) clearTimeout(watchdog);
        report(
          "disconnected",
          new ApiError({ kind: "http", status: response.status, message: "event stream refused" }),
        );
        closed = true;
        return;
      }
      const type = response.headers.get("Content-Type") ?? "";
      if (!response.ok || !type.startsWith("text/event-stream") || !response.body) {
        throw new ApiError({ kind: "bad_response", status: response.status, message: "not an event stream" });
      }
      failures = 0;
      report("open");
      const parser = new SseParser();
      reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        arm(); // any bytes, heartbeat comments included, prove the connection is alive
        for (const frame of parser.push(value)) deliver(frame);
      }
      if (watchdog) clearTimeout(watchdog);
      if (!closed) {
        scheduleRetry(
          silent
            ? new ApiError({ kind: "timeout", message: "no heartbeat from the event stream" })
            : new ApiError({ kind: "network", message: "event stream ended" }),
        );
      }
    } catch (cause) {
      if (watchdog) clearTimeout(watchdog);
      if (closed) return;
      const error =
        cause instanceof ApiError
          ? cause
          : new ApiError({
              kind: silent ? "timeout" : "network",
              message: "event stream interrupted",
              cause,
            });
      scheduleRetry(error);
    }
  }

  void connect();
  return {
    close() {
      if (closed) return;
      closed = true;
      if (retryTimer) clearTimeout(retryTimer);
      controller?.abort();
      reader?.cancel().catch(() => {});
      report("closed");
    },
    lastSeq: () => lastSeq,
  };
}

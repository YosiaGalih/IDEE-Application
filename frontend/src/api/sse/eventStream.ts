import { apiUrl } from "../client";
import { ApiError } from "../errors";
import { SseParser, type SseFrame } from "./parser";

/**
 * connecting → open; on a drop reconnecting, then disconnected after `disconnectedAfter` failures (retries go
 * on); failed = stopped for good (a status reconnecting cannot fix: call retry()); closed = closed by the caller.
 */
export type StreamStatus = "connecting" | "open" | "reconnecting" | "disconnected" | "failed" | "closed";
export type StreamFrame = SseFrame & { seq: number | null };

export type StreamOptions = {
  /** API path, e.g. `/tasks/{id}/events` (API_CONTRACTS §5). */
  path: string;
  /** Resume after this sequence number (`?after_seq=` and `Last-Event-ID`). */
  lastSeq?: number | null;
  onFrame: (frame: StreamFrame) => void;
  onStatus?: (status: StreamStatus, error: ApiError | null) => void;
  onProtocolError?: (message: string) => void;
  /** Silence longer than this means a dead connection: the server sends `: ping` every 15 s. */
  heartbeatTimeoutMs?: number;
  initialBackoffMs?: number;
  maxBackoffMs?: number;
  /** Consecutive failures before the status turns from reconnecting to disconnected (retries go on). */
  disconnectedAfter?: number;
  /** A connection that stayed open this long was healthy, even if it only carried heartbeats. */
  healthyAfterMs?: number;
  jitter?: () => number;
  fetchImpl?: typeof fetch;
};

export type EventStream = {
  close(): void;
  /** Starts again after `failed` (e.g. after signing in again); no effect otherwise. */
  retry(): void;
  lastSeq(): number | null;
};

/** Answers no reconnect can change: stop and say why. */
const FATAL = new Set([400, 401, 403, 404, 410, 422]);

async function fatalError(response: Response): Promise<ApiError> {
  let code: string | null = null;
  let requestId = response.headers.get("X-Request-ID");
  try {
    const body: unknown = await response.json();
    if (typeof body === "object" && body !== null) {
      const { error, request_id } = body as { error?: { code?: unknown }; request_id?: unknown };
      if (typeof error?.code === "string") code = error.code;
      if (typeof request_id === "string") requestId = request_id;
    }
  } catch {
    // not the envelope: the status alone decides
  }
  return new ApiError({
    kind: "http",
    status: response.status,
    code,
    requestId,
    message: "event stream refused",
  });
}

/**
 * A resumable server-sent event stream over fetch. Not EventSource: a manual reconnect needs the
 * Last-Event-ID header, and the `: ping` heartbeat is only visible to a reader of the raw stream
 * (spec WP-1.4a, conflict #62).
 *
 * Persistent frames (with an id) are delivered once and strictly in order: the server numbers them
 * `tasks.event_seq + 1` inside one transaction, so seq has no holes. A frame below the resume point is a
 * replayed duplicate and is skipped; a frame above `lastSeq + 1` means a lost or reordered live message, so
 * nothing past the gap is delivered and the stream reconnects from `lastSeq` (the server replays from the
 * database). Frames without an id (ephemeral, e.g. agent.delta) are always delivered.
 */
export function openEventStream(options: StreamOptions): EventStream {
  const heartbeatMs = options.heartbeatTimeoutMs ?? 45_000;
  const initialBackoff = options.initialBackoffMs ?? 1_000;
  const maxBackoff = options.maxBackoffMs ?? 30_000;
  const disconnectedAfter = options.disconnectedAfter ?? 3;
  const healthyAfterMs = options.healthyAfterMs ?? 15_000;
  const jitter = options.jitter ?? Math.random;
  const fetchImpl = options.fetchImpl ?? fetch.bind(globalThis);

  let lastSeq = options.lastSeq ?? null;
  let failures = 0;
  let closed = false;
  let stopped = false;
  let status: StreamStatus | null = null;
  let controller: AbortController | null = null;
  let reader: ReadableStreamDefaultReader<string> | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;

  const report = (next: StreamStatus, error: ApiError | null = null) => {
    if (closed && next !== "closed") return;
    status = next;
    try {
      options.onStatus?.(next, error);
    } catch (cause) {
      options.onProtocolError?.(`status handler threw: ${String(cause)}`);
    }
  };

  const protocolError = (message: string) => {
    try {
      options.onProtocolError?.(message);
    } catch {
      // a broken handler must not break the stream
    }
  };

  const scheduleRetry = (error: ApiError) => {
    if (closed || stopped) return;
    failures += 1;
    report(failures >= disconnectedAfter ? "disconnected" : "reconnecting", error);
    const base = Math.min(maxBackoff, initialBackoff * 2 ** (failures - 1));
    retryTimer = setTimeout(() => void run(), base + base * 0.2 * jitter());
  };

  const stop = (error: ApiError) => {
    stopped = true;
    report("failed", error);
  };

  /** Delivers one frame: "new" when it reached onFrame, "skip" when it did not, "gap" to drop the connection. */
  const deliver = (frame: SseFrame): "new" | "skip" | "gap" => {
    let seq: number | null = null;
    if (frame.id !== null) {
      if (!/^\d+$/.test(frame.id)) {
        protocolError(`event id is not a sequence number: ${frame.id.slice(0, 40)}`);
        return "skip";
      }
      seq = Number(frame.id);
      if (lastSeq !== null && seq <= lastSeq) return "skip"; // replayed after a reconnect
      if (lastSeq !== null && seq > lastSeq + 1) {
        protocolError(`seq gap: expected ${lastSeq + 1}, got ${seq}; resuming from ${lastSeq}`);
        return "gap";
      }
      lastSeq = seq;
    }
    try {
      options.onFrame({ ...frame, seq });
    } catch (cause) {
      protocolError(`frame handler threw for seq ${seq ?? "-"}: ${String(cause)}`);
    }
    return "new";
  };

  async function connect(): Promise<void> {
    if (closed || stopped) return;
    report(status === null ? "connecting" : status === "disconnected" ? "disconnected" : "reconnecting");
    const attempt = new AbortController();
    controller = attempt;
    const url = apiUrl(options.path);
    const headers = new Headers({ Accept: "text/event-stream" });
    if (lastSeq !== null) {
      url.searchParams.set("after_seq", String(lastSeq));
      headers.set("Last-Event-ID", String(lastSeq));
    }

    let watchdog: ReturnType<typeof setTimeout> | null = null;
    let silent = false;
    let gap = false;
    let openedAt: number | null = null;
    /** A connection that stayed up a while was healthy even with heartbeats only (an idle wait). */
    const wasHealthy = () => openedAt !== null && Date.now() - openedAt >= healthyAfterMs;
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
        stop(await fatalError(response));
        return;
      }
      const type = response.headers.get("Content-Type") ?? "";
      if (!response.ok || !type.startsWith("text/event-stream") || !response.body) {
        response.body?.cancel().catch(() => {});
        throw new ApiError({ kind: "bad_response", status: response.status, message: "not an event stream" });
      }
      report("open");
      openedAt = Date.now();
      const parser = new SseParser();
      reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        arm(); // any bytes, heartbeat comments included, prove the connection is alive
        for (const frame of parser.push(value)) {
          const outcome = deliver(frame);
          if (outcome === "gap") {
            gap = true;
            break;
          }
          // A new frame proves the stream works; an accepted handshake or a replayed duplicate does not.
          if (outcome === "new") failures = 0;
        }
        if (gap) {
          attempt.abort();
          reader.cancel().catch(() => {});
          break;
        }
      }
      if (watchdog) clearTimeout(watchdog);
      if (closed) return;
      if (!gap && wasHealthy()) failures = 0;
      if (gap)
        scheduleRetry(new ApiError({ kind: "bad_response", message: "events missing from the stream" }));
      else if (silent)
        scheduleRetry(new ApiError({ kind: "timeout", message: "no heartbeat from the event stream" }));
      else scheduleRetry(new ApiError({ kind: "network", message: "event stream ended" }));
    } catch (cause) {
      if (watchdog) clearTimeout(watchdog);
      if (closed) return;
      if (wasHealthy()) failures = 0;
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

  const run = () =>
    connect().catch((cause: unknown) => {
      // a bug, not the network: say so instead of leaving a stale status
      stop(new ApiError({ kind: "bad_response", message: `event stream failed: ${String(cause)}`, cause }));
    });

  void run();
  return {
    close() {
      if (closed) return;
      closed = true;
      if (retryTimer) clearTimeout(retryTimer);
      controller?.abort();
      reader?.cancel().catch(() => {});
      report("closed");
    },
    retry() {
      if (closed || !stopped) return;
      stopped = false;
      failures = 0;
      void run();
    },
    lastSeq: () => lastSeq,
  };
}

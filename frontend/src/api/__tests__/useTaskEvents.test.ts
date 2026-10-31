import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { openEventStream, type StreamStatus } from "@/api/sse/eventStream";
import { useTaskEvents } from "@/api/hooks/useTaskEvents";

const TASK = "6f1d2c3b-4a5e-4f60-8a7b-9c0d1e2f3a4b";
const OTHER = "00000000-0000-0000-0000-000000000002"; // not RFC 4122: fixtures may use such ids

/** A fake SSE endpoint: each fetch gets its own stream the test writes to. */
function fakeEndpoint(statuses: number[] = [], { abortErrorsBody = true } = {}) {
  const requests: { url: URL; headers: Headers }[] = [];
  const streams: ReadableStreamDefaultController<Uint8Array>[] = [];
  const encoder = new TextEncoder();
  const fetchImpl = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : String(input));
    requests.push({ url, headers: new Headers(init?.headers) });
    const status = statuses.shift() ?? 200;
    if (status === 0) return Promise.reject(new TypeError("network down"));
    if (status !== 200) {
      const body = {
        request_id: "r-1",
        status: "failed",
        data: null,
        error: {
          code: status === 401 ? "UNAUTHENTICATED" : status === 403 ? "CAPABILITY_DISABLED" : "BAD_REQUEST",
          message: "x",
        },
      };
      return Promise.resolve(
        new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
      );
    }
    let controller: ReadableStreamDefaultController<Uint8Array> | null = null;
    const body = new ReadableStream<Uint8Array>({
      start: (c) => {
        controller = c;
        streams.push(c);
      },
    });
    // Like a browser: aborting the request errors a body that is still open.
    init?.signal?.addEventListener("abort", () => {
      if (!abortErrorsBody) return;
      try {
        controller?.error(new DOMException("aborted", "AbortError"));
      } catch {
        // already closed
      }
    });
    return Promise.resolve(
      new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );
  });
  return {
    fetchImpl: fetchImpl as unknown as typeof fetch,
    requests,
    send: (text: string, index = streams.length - 1) => streams[index]?.enqueue(encoder.encode(text)),
    end: (index = streams.length - 1) => streams[index]?.close(),
    connections: () => streams.length,
  };
}

const frame = (seq: number | null, type = "task.log", extra = "") =>
  `${seq === null ? "" : `id: ${seq}\n`}event: ${type}\ndata: {"task_id":"${TASK}","seq":${seq ?? "null"},"type":"${type}","ts":"2026-10-31T01:00:00Z","payload":{${extra}}}\n\n`;

const fast = { initialBackoffMs: 5, maxBackoffMs: 20, heartbeatTimeoutMs: 1000, jitter: () => 0 };
const streams: { close(): void }[] = [];
afterEach(() => streams.splice(0).forEach((s) => s.close()));

function open(
  endpoint: ReturnType<typeof fakeEndpoint>,
  extra: Partial<Parameters<typeof openEventStream>[0]> = {},
) {
  const seqs: (number | null)[] = [];
  const statuses: StreamStatus[] = [];
  const stream = openEventStream({
    path: `/tasks/${TASK}/events`,
    fetchImpl: endpoint.fetchImpl,
    onFrame: (f) => seqs.push(f.seq),
    onStatus: (s) => statuses.push(s),
    ...fast,
    ...extra,
  });
  streams.push(stream);
  return { seqs, statuses, stream };
}

describe("openEventStream", () => {
  it("delivers frames in order and reports connecting then open", async () => {
    const endpoint = fakeEndpoint();
    const { seqs, statuses, stream } = open(endpoint);
    await waitFor(() => expect(endpoint.connections()).toBe(1));
    endpoint.send(frame(1) + frame(2));
    await waitFor(() => expect(seqs).toEqual([1, 2]));
    expect(statuses.slice(0, 2)).toEqual(["connecting", "open"]);
    expect(stream.lastSeq()).toBe(2);
    expect(endpoint.requests[0]?.headers.get("Accept")).toBe("text/event-stream");
  });

  it("resumes after the last seq with after_seq and Last-Event-ID, and drops replayed events", async () => {
    const endpoint = fakeEndpoint();
    const { seqs, statuses } = open(endpoint, { lastSeq: 4 });
    await waitFor(() => expect(endpoint.connections()).toBe(1));
    expect(endpoint.requests[0]?.url.searchParams.get("after_seq")).toBe("4");
    endpoint.send(frame(4) + frame(5));
    endpoint.end();
    await waitFor(() => expect(endpoint.connections()).toBe(2));
    expect(statuses).toContain("reconnecting");
    expect(endpoint.requests[1]?.url.searchParams.get("after_seq")).toBe("5");
    expect(endpoint.requests[1]?.headers.get("Last-Event-ID")).toBe("5");
    endpoint.send(frame(5) + frame(6)); // the server replays from its side too
    await waitFor(() => expect(seqs).toEqual([5, 6]));
  });

  it("passes ephemeral events (no id) without moving the resume point", async () => {
    const endpoint = fakeEndpoint();
    const { seqs, stream } = open(endpoint);
    await waitFor(() => expect(endpoint.connections()).toBe(1));
    endpoint.send(frame(1) + frame(null, "agent.delta") + frame(null, "agent.delta"));
    await waitFor(() => expect(seqs).toEqual([1, null, null]));
    expect(stream.lastSeq()).toBe(1);
  });

  it("reconnects when the stream goes silent, but heartbeats keep it open", async () => {
    const endpoint = fakeEndpoint();
    open(endpoint, { heartbeatTimeoutMs: 60 });
    await waitFor(() => expect(endpoint.connections()).toBe(1));
    for (let i = 0; i < 4; i++) {
      await new Promise((r) => setTimeout(r, 30));
      endpoint.send(": ping\n\n", 0);
    }
    expect(endpoint.connections()).toBe(1);
    await waitFor(() => expect(endpoint.connections()).toBe(2), { timeout: 1000 });
  });

  it("still reconnects a silent stream when the fetch does not end the body on abort", async () => {
    const endpoint = fakeEndpoint([], { abortErrorsBody: false });
    const { statuses } = open(endpoint, { heartbeatTimeoutMs: 40 });
    await waitFor(() => expect(endpoint.connections()).toBe(2), { timeout: 1000 });
    expect(statuses).toContain("reconnecting");
  });

  it("stops for good on 400, 401, 403, 404, 410 and 422: reconnecting cannot fix them", async () => {
    for (const code of [400, 401, 403, 404, 410, 422]) {
      const endpoint = fakeEndpoint([code]);
      const { statuses } = open(endpoint);
      await waitFor(() => expect(statuses).toContain("failed"));
      await new Promise((r) => setTimeout(r, 40));
      expect(endpoint.requests).toHaveLength(1);
      expect(statuses).not.toContain("reconnecting");
    }
  });

  it("keeps the error code of a refused stream and can retry after it", async () => {
    const endpoint = fakeEndpoint([403]);
    const errors: (string | null)[] = [];
    const { statuses, stream } = open(endpoint, {
      onStatus: (s, error) => {
        statuses.push(s);
        errors.push(error?.code ?? null);
      },
    });
    await waitFor(() => expect(statuses).toContain("failed"));
    expect(errors.at(-1)).toBe("CAPABILITY_DISABLED");
    stream.retry();
    await waitFor(() => expect(statuses.at(-1)).toBe("open"));
    expect(endpoint.requests).toHaveLength(2);
  });

  it("never delivers past a gap in seq: it reconnects from the last seq and gets the missing events", async () => {
    const endpoint = fakeEndpoint();
    const errors: string[] = [];
    const { seqs } = open(endpoint, { onProtocolError: (m) => errors.push(m) });
    await waitFor(() => expect(endpoint.connections()).toBe(1));
    endpoint.send(frame(1) + frame(3)); // the live message for 2 was lost
    await waitFor(() => expect(endpoint.connections()).toBe(2));
    expect(seqs).toEqual([1]);
    expect(errors[0]).toMatch(/seq gap: expected 2, got 3/);
    expect(endpoint.requests[1]?.url.searchParams.get("after_seq")).toBe("1");
    endpoint.send(frame(2) + frame(3)); // the replay from the database
    await waitFor(() => expect(seqs).toEqual([1, 2, 3]));
  });

  it("backs off with growing delays", async () => {
    const times: number[] = [];
    const fetchImpl = vi.fn(() => {
      times.push(performance.now());
      return Promise.reject(new TypeError("down"));
    });
    const stream = openEventStream({
      path: `/tasks/${TASK}/events`,
      fetchImpl: fetchImpl,
      onFrame: () => {},
      initialBackoffMs: 20,
      maxBackoffMs: 1000,
      jitter: () => 0,
    });
    streams.push(stream);
    await waitFor(() => expect(times.length).toBeGreaterThanOrEqual(4), { timeout: 2000 });
    const gaps = times.slice(1).map((t, i) => t - (times[i] ?? 0));
    expect(gaps[1]).toBeGreaterThan((gaps[0] ?? 0) * 1.5);
    expect(gaps[2]).toBeGreaterThan((gaps[1] ?? 0) * 1.5);
  });

  it("does not count an accepted handshake that delivers nothing as a success", async () => {
    const endpoint = fakeEndpoint();
    const { statuses } = open(endpoint, { disconnectedAfter: 3 });
    for (let i = 1; i <= 3; i++) {
      await waitFor(() => expect(endpoint.connections()).toBe(i), { timeout: 1000 });
      endpoint.end(); // 200, then closed before any frame
    }
    await waitFor(() => expect(statuses).toContain("disconnected"));
  });

  it("does not escalate drops of healthy idle connections, nor reset on replayed duplicates", async () => {
    const endpoint = fakeEndpoint();
    const { statuses } = open(endpoint, { disconnectedAfter: 2, healthyAfterMs: 30 });
    for (let i = 1; i <= 3; i++) {
      await waitFor(() => expect(endpoint.connections()).toBe(i), { timeout: 1000 });
      endpoint.send(": ping\n\n");
      await new Promise((r) => setTimeout(r, 40)); // stayed up longer than healthyAfterMs
      endpoint.end();
    }
    await waitFor(() => expect(endpoint.connections()).toBe(4), { timeout: 1000 });
    expect(statuses).not.toContain("disconnected");
  });

  it("counts a connection that only replays duplicates as a failure", async () => {
    const endpoint = fakeEndpoint();
    const { statuses } = open(endpoint, { lastSeq: 5, disconnectedAfter: 2, healthyAfterMs: 10_000 });
    for (let i = 1; i <= 2; i++) {
      await waitFor(() => expect(endpoint.connections()).toBe(i), { timeout: 1000 });
      endpoint.send(frame(4) + frame(5)); // nothing new
      endpoint.end();
    }
    await waitFor(() => expect(statuses).toContain("disconnected"));
  });

  it("refuses a path that leaves the API base and reports it as failed", async () => {
    const endpoint = fakeEndpoint();
    const statuses: StreamStatus[] = [];
    const stream = openEventStream({
      path: "/tasks/%2e%2e/admin",
      fetchImpl: endpoint.fetchImpl,
      onFrame: () => {},
      onStatus: (s) => statuses.push(s),
    });
    streams.push(stream);
    await waitFor(() => expect(statuses).toContain("failed"));
    expect(endpoint.requests).toHaveLength(0);
  });

  it("keeps going when a frame handler throws", async () => {
    const endpoint = fakeEndpoint();
    const errors: string[] = [];
    const seen: (number | null)[] = [];
    const stream = openEventStream({
      path: `/tasks/${TASK}/events`,
      fetchImpl: endpoint.fetchImpl,
      ...fast,
      onFrame: (f) => {
        seen.push(f.seq);
        if (f.seq === 1) throw new Error("boom");
      },
      onProtocolError: (m) => errors.push(m),
    });
    streams.push(stream);
    await waitFor(() => expect(endpoint.connections()).toBe(1));
    endpoint.send(frame(1) + frame(2));
    await waitFor(() => expect(seen).toEqual([1, 2]));
    expect(errors[0]).toMatch(/handler threw/);
    expect(endpoint.connections()).toBe(1);
  });

  it("retries failures with backoff, reports disconnected after three, and recovers", async () => {
    const endpoint = fakeEndpoint([0, 500, 502, 200]);
    const { statuses } = open(endpoint, { disconnectedAfter: 3 });
    await waitFor(() => expect(endpoint.connections()).toBe(1), { timeout: 1000 });
    expect(endpoint.requests).toHaveLength(4);
    expect(statuses.indexOf("disconnected")).toBeGreaterThan(statuses.indexOf("reconnecting"));
    await waitFor(() => expect(statuses.at(-1)).toBe("open"));
  });

  it("treats a 200 that is not an event stream as a failure", async () => {
    const endpoint = fakeEndpoint();
    const fetchImpl = vi.fn(() =>
      Promise.resolve(new Response("<html/>", { status: 200, headers: { "Content-Type": "text/html" } })),
    );
    const { statuses } = open(endpoint, { fetchImpl: fetchImpl as unknown as typeof fetch });
    await waitFor(() => expect(statuses).toContain("reconnecting"));
    expect(statuses).not.toContain("open");
  });

  it("drops a frame whose id is not a sequence number and reports it", async () => {
    const endpoint = fakeEndpoint();
    const errors: string[] = [];
    const { seqs } = open(endpoint, { onProtocolError: (m) => errors.push(m) });
    await waitFor(() => expect(endpoint.connections()).toBe(1));
    endpoint.send("id: abc\ndata: {}\n\n" + frame(1));
    await waitFor(() => expect(seqs).toEqual([1]));
    expect(errors).toHaveLength(1);
  });

  it("stops reconnecting and reporting after close()", async () => {
    const endpoint = fakeEndpoint();
    const { statuses, stream } = open(endpoint);
    await waitFor(() => expect(endpoint.connections()).toBe(1));
    stream.close();
    const count = statuses.length;
    await new Promise((r) => setTimeout(r, 50));
    expect(endpoint.connections()).toBe(1);
    expect(statuses).toHaveLength(count);
  });
});

describe("useTaskEvents", () => {
  const event = (seq: number | null, task = TASK, type = "task.log") =>
    `${seq === null ? "" : `id: ${seq}\n`}data: {"task_id":"${task}","seq":${seq ?? "null"},"type":"${type}","ts":"t","payload":{}}\n\n`;

  it("collects validated events and records every invalid frame with its reason", async () => {
    const endpoint = fakeEndpoint();
    const { result, unmount } = renderHook(() =>
      useTaskEvents(TASK, { fetchImpl: endpoint.fetchImpl, ...fast }),
    );
    await waitFor(() => expect(result.current.status).toBe("open"));
    act(() => {
      endpoint.send(
        event(1) +
          "id: 2\ndata: {not json\n\n" +
          event(3, OTHER) + // another task's event on this stream: never shown here
          'id: 4\ndata: {"task_id":"' +
          TASK +
          '","seq":9,"type":"x","ts":"t","payload":{}}\n\n' +
          event(5),
      );
    });
    await waitFor(() => expect(result.current.events.map((e) => e.seq)).toEqual([1, 5]));
    expect(result.current.invalidFrames).toBe(3);
    expect(result.current.lastInvalid.map((f) => f.seq)).toEqual([2, 3, 4]);
    expect(result.current.lastInvalid.map((f) => f.reason)).toEqual([
      "data is not JSON",
      "event of another task",
      "seq in data (9) differs from the event id",
    ]);
    expect(result.current.lastSeq).toBe(5);
    unmount();
    await new Promise((r) => setTimeout(r, 30));
    expect(endpoint.connections()).toBe(1);
  });

  it("starts from nothing when the task changes, never showing the previous task's events", async () => {
    const endpoint = fakeEndpoint();
    const { result, rerender } = renderHook(
      ({ id }) => useTaskEvents(id, { fetchImpl: endpoint.fetchImpl, ...fast }),
      {
        initialProps: { id: TASK },
      },
    );
    await waitFor(() => expect(result.current.status).toBe("open"));
    act(() => endpoint.send(event(1) + event(2)));
    await waitFor(() => expect(result.current.events).toHaveLength(2));
    rerender({ id: OTHER });
    expect(result.current.events).toEqual([]);
    expect(result.current.lastSeq).toBeNull();
    await waitFor(() => expect(endpoint.connections()).toBe(2));
    expect(endpoint.requests[1]?.url.pathname).toContain(OTHER);
    expect(endpoint.requests[1]?.url.searchParams.get("after_seq")).toBeNull();
    act(() => endpoint.send(event(1, OTHER)));
    await waitFor(() => expect(result.current.events.map((e) => e.task_id)).toEqual([OTHER]));
  });

  it("passes ephemeral events to onEvent without storing them, and keeps an event whose handler throws", async () => {
    const endpoint = fakeEndpoint();
    const seen: (number | null)[] = [];
    const onEvent = (e: { seq: number | null }) => {
      seen.push(e.seq);
      if (e.seq === 1) throw new Error("view bug");
    };
    const { result } = renderHook(() =>
      useTaskEvents(TASK, { fetchImpl: endpoint.fetchImpl, onEvent, ...fast }),
    );
    await waitFor(() => expect(result.current.status).toBe("open"));
    act(() => endpoint.send(event(1) + event(null, TASK, "agent.delta") + event(2)));
    await waitFor(() => expect(seen).toEqual([1, null, 2]));
    expect(result.current.events.map((e) => e.seq)).toEqual([1, 2]);
    expect(result.current.lastInvalid.map((f) => f.reason)[0]).toMatch(/onEvent threw/);
  });

  it("exposes the screen state of a refused stream and reconnects on request", async () => {
    const endpoint = fakeEndpoint([401]);
    const { result } = renderHook(() => useTaskEvents(TASK, { fetchImpl: endpoint.fetchImpl, ...fast }));
    await waitFor(() => expect(result.current.status).toBe("failed"));
    expect(result.current.uxState).toBe("unauthenticated");
    expect(result.current.error?.status).toBe(401);
    act(() => result.current.reconnect());
    await waitFor(() => expect(result.current.status).toBe("open"));
    expect(result.current.uxState).toBeNull();
    expect(result.current.error).toBeNull();
  });

  it("does nothing without a task id", () => {
    const endpoint = fakeEndpoint();
    const { result } = renderHook(() => useTaskEvents(null, { fetchImpl: endpoint.fetchImpl }));
    expect(result.current.status).toBe("closed");
    expect(endpoint.requests).toHaveLength(0);
  });
});

import { act, renderHook, waitFor } from "@testing-library/react";
import { useLayoutEffect, useState } from "react";
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

const END = (status: string | null) =>
  `event: stream.end\ndata: {"task_id":"${TASK}","status":${status === null ? "null" : `"${status}"`}}\n\n`;

/** The server's stand-in for a stored event it cannot send as it is (API §5.1 `event.invalid`). */
const PLACEHOLDER = (seq: number) =>
  `id: ${seq}\nevent: event.invalid\ndata: {"task_id":"${TASK}","seq":${seq},"type":"event.invalid","ts":"2026-10-31T01:00:00Z","payload":{}}\n\n`;

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

  it("stops on stream.end without reconnecting", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const endpoint = fakeEndpoint();
      const { seqs, statuses } = open(endpoint);
      await waitFor(() => expect(endpoint.connections()).toBe(1));
      endpoint.send(frame(1) + frame(2) + END("completed"));
      endpoint.end();
      await waitFor(() => expect(statuses).toContain("ended"));
      await vi.advanceTimersByTimeAsync(120_000);
      expect(endpoint.fetchImpl).toHaveBeenCalledTimes(1);
      expect(seqs).toEqual([1, 2]); // stream.end is not a task event
      expect(statuses.at(-1)).toBe("ended");
    } finally {
      vi.useRealTimers();
    }
  });

  it("delivers nothing after stream.end, even in the same chunk", async () => {
    const endpoint = fakeEndpoint();
    const { seqs, statuses } = open(endpoint);
    await waitFor(() => expect(endpoint.connections()).toBe(1));
    endpoint.send(frame(1) + frame(2) + END("completed") + frame(3) + frame(null, "agent.delta"));
    await waitFor(() => expect(statuses).toContain("ended"));
    await new Promise((r) => setTimeout(r, 30));
    expect(seqs).toEqual([1, 2]);
    expect(endpoint.connections()).toBe(1);
  });

  it("leaves no timer behind once ended", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const endpoint = fakeEndpoint();
      const { statuses } = open(endpoint);
      await waitFor(() => expect(endpoint.connections()).toBe(1));
      endpoint.send(frame(1) + END("cancelled"));
      await waitFor(() => expect(statuses).toContain("ended"));
      expect(vi.getTimerCount()).toBe(0); // no heartbeat watchdog, no retry
    } finally {
      vi.useRealTimers();
    }
  });

  it("retries 429 (too many streams) with backoff instead of failing", async () => {
    const endpoint = fakeEndpoint([429]);
    const { seqs, statuses } = open(endpoint);
    await waitFor(() => expect(endpoint.connections()).toBe(1)); // the second request got a stream
    expect(endpoint.requests).toHaveLength(2);
    expect(statuses).toContain("reconnecting");
    expect(statuses).not.toContain("failed");
    endpoint.send(frame(1));
    await waitFor(() => expect(seqs).toEqual([1]));
  });

  it("keeps lastSeq contiguous across an event.invalid placeholder", async () => {
    const endpoint = fakeEndpoint();
    const errors: string[] = [];
    const { seqs, stream } = open(endpoint, { onProtocolError: (m) => errors.push(m) });
    await waitFor(() => expect(endpoint.connections()).toBe(1));
    endpoint.send(frame(1) + PLACEHOLDER(2) + frame(3));
    await waitFor(() => expect(seqs).toEqual([1, 2, 3]));
    expect(stream.lastSeq()).toBe(3);
    expect(errors).toEqual([]); // no gap: no reconnect
    expect(endpoint.connections()).toBe(1);
  });

  it("reports connecting, not reconnecting, when retry() starts again after ended", async () => {
    const endpoint = fakeEndpoint();
    const { statuses, stream } = open(endpoint);
    await waitFor(() => expect(endpoint.connections()).toBe(1));
    endpoint.send(frame(1) + END("completed"));
    await waitFor(() => expect(statuses).toContain("ended"));
    const before = statuses.length;
    stream.retry();
    await waitFor(() => expect(statuses.at(-1)).toBe("open"));
    expect(statuses.slice(before)).toEqual(["connecting", "open"]);
  });

  it("retry after ended resumes from lastSeq", async () => {
    const endpoint = fakeEndpoint();
    const { statuses, stream } = open(endpoint);
    await waitFor(() => expect(endpoint.connections()).toBe(1));
    endpoint.send(frame(1) + frame(2) + END("failed"));
    await waitFor(() => expect(statuses).toContain("ended"));
    stream.retry();
    await waitFor(() => expect(endpoint.connections()).toBe(2));
    expect(endpoint.requests[1]?.headers.get("Last-Event-ID")).toBe("2");
    expect(endpoint.requests[1]?.url.searchParams.get("after_seq")).toBe("2");
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
    // The delays the stream asks for, not wall-clock gaps: those depend on how busy the machine is.
    const delays: number[] = [];
    const realSetTimeout = globalThis.setTimeout;
    const record = (handler: () => void, ms?: number) => {
      if (ms !== undefined && ms < 45_000) delays.push(ms); // 45 s is the heartbeat watchdog
      return realSetTimeout(handler, ms);
    };
    const spy = vi.spyOn(globalThis, "setTimeout").mockImplementation(record);
    const fetchImpl = vi.fn(() => Promise.reject(new TypeError("down")));
    const stream = openEventStream({
      path: `/tasks/${TASK}/events`,
      fetchImpl: fetchImpl,
      onFrame: () => {},
      initialBackoffMs: 20,
      maxBackoffMs: 1000,
      jitter: () => 0,
    });
    streams.push(stream);
    // Polls with the real timer, so the only setTimeout calls recorded are the stream's own.
    for (let i = 0; i < 200 && fetchImpl.mock.calls.length < 4; i++) {
      await new Promise((resolve) => realSetTimeout(resolve, 10));
    }
    spy.mockRestore();
    expect(delays.slice(0, 3)).toEqual([20, 40, 80]);
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

  it("keeps the last 500 events and counts dropped", async () => {
    const endpoint = fakeEndpoint();
    const { result } = renderHook(() => useTaskEvents(TASK, { fetchImpl: endpoint.fetchImpl, ...fast }));
    await waitFor(() => expect(result.current.status).toBe("open"));
    act(() => {
      let text = "";
      for (let i = 1; i <= 520; i++) text += event(i);
      endpoint.send(text);
    });
    await waitFor(() => expect(result.current.lastSeq).toBe(520));
    expect(result.current.events).toHaveLength(500);
    expect(result.current.events[0]?.seq).toBe(21);
    expect(result.current.dropped).toBe(20);
  });

  it("resets dropped when switching task", async () => {
    const endpoint = fakeEndpoint();
    const { result, rerender } = renderHook(
      ({ id }) => useTaskEvents(id, { fetchImpl: endpoint.fetchImpl, ...fast }),
      { initialProps: { id: TASK } },
    );
    await waitFor(() => expect(result.current.status).toBe("open"));
    act(() => {
      let text = "";
      for (let i = 1; i <= 505; i++) text += event(i);
      endpoint.send(text);
    });
    await waitFor(() => expect(result.current.dropped).toBe(5));
    rerender({ id: OTHER });
    expect(result.current.dropped).toBe(0);
  });

  it("ended is not an error state", async () => {
    const endpoint = fakeEndpoint();
    const { result } = renderHook(() => useTaskEvents(TASK, { fetchImpl: endpoint.fetchImpl, ...fast }));
    await waitFor(() => expect(result.current.status).toBe("open"));
    act(() => endpoint.send(event(1) + END(null)));
    await waitFor(() => expect(result.current.status).toBe("ended"));
    expect(result.current.uxState).toBeNull();
    expect(result.current.error).toBeNull();
    expect(result.current.invalidFrames).toBe(0);
    expect(endpoint.connections()).toBe(1);
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

  it("keeps the placeholder of an invalid stored event in place", async () => {
    const endpoint = fakeEndpoint();
    const { result } = renderHook(() => useTaskEvents(TASK, { fetchImpl: endpoint.fetchImpl, ...fast }));
    await waitFor(() => expect(result.current.status).toBe("open"));
    act(() => endpoint.send(event(1) + PLACEHOLDER(2) + event(3)));
    await waitFor(() => expect(result.current.lastSeq).toBe(3));
    expect(result.current.events.map((e) => [e.seq, e.type])).toEqual([
      [1, "task.log"],
      [2, "event.invalid"],
      [3, "task.log"],
    ]);
    expect(result.current.invalidFrames).toBe(0);
    expect(endpoint.connections()).toBe(1);
  });

  it("opens the next task's stream from that task's resume point, whatever the previous one still sends", async () => {
    const endpoint = fakeEndpoint();
    let switchTo: (id: string) => void = () => {};
    const { result } = renderHook(() => {
      const [id, setId] = useState(TASK);
      switchTo = setId;
      const state = useTaskEvents(id, { fetchImpl: endpoint.fetchImpl, ...fast });
      useLayoutEffect(() => {
        // The previous task's stream is still open until the passive effects run: it gets one more frame.
        if (id === OTHER) endpoint.send(event(6), 0);
      }, [id]);
      return state;
    });
    await waitFor(() => expect(result.current.status).toBe("open"));
    act(() => endpoint.send(event(1) + event(2) + event(3) + event(4) + event(5)));
    await waitFor(() => expect(result.current.lastSeq).toBe(5));
    // Outside act, as in a browser: React runs the passive effects (close the old stream, open the new one)
    // in a later task than the layout effects, so the old stream's frame lands in between.
    const reactGlobal = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean | undefined };
    const actEnvironment = reactGlobal.IS_REACT_ACT_ENVIRONMENT;
    reactGlobal.IS_REACT_ACT_ENVIRONMENT = false;
    try {
      switchTo(OTHER);
      await waitFor(() => expect(endpoint.connections()).toBe(2));
    } finally {
      reactGlobal.IS_REACT_ACT_ENVIRONMENT = actEnvironment;
    }
    expect(endpoint.requests[1]?.url.pathname).toContain(OTHER);
    expect(endpoint.requests[1]?.url.searchParams.get("after_seq")).toBeNull();
    act(() => endpoint.send(event(1, OTHER)));
    await waitFor(() => expect(result.current.lastSeq).toBe(1));
    act(() => result.current.reconnect());
    await waitFor(() => expect(endpoint.connections()).toBe(3));
    expect(endpoint.requests[2]?.url.searchParams.get("after_seq")).toBe("1"); // OTHER's own seq
  });

  it("starts the same task again from nothing after it went away (A, null, A)", async () => {
    const endpoint = fakeEndpoint();
    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useTaskEvents(id, { fetchImpl: endpoint.fetchImpl, ...fast }),
      { initialProps: { id: TASK as string | null } },
    );
    await waitFor(() => expect(result.current.status).toBe("open"));
    act(() => endpoint.send(event(1) + event(2) + event(3) + event(4) + event(5)));
    await waitFor(() => expect(result.current.lastSeq).toBe(5));
    rerender({ id: null });
    expect(result.current.status).toBe("closed");
    rerender({ id: TASK });
    expect(result.current.events).toEqual([]);
    await waitFor(() => expect(endpoint.connections()).toBe(2));
    // The state starts from nothing, so the stream must too: never resume where the hidden events end.
    expect(endpoint.requests[1]?.url.searchParams.get("after_seq")).toBeNull();
    expect(endpoint.requests[1]?.headers.get("Last-Event-ID")).toBeNull();
    act(() => endpoint.send(event(1) + event(2) + event(3) + event(4) + event(5)));
    await waitFor(() => expect(result.current.events.map((e) => e.seq)).toEqual([1, 2, 3, 4, 5]));
    expect(result.current.dropped).toBe(0);
  });

  it("resumes after ended on reconnect(): no duplicates, dropped consistent, connecting first", async () => {
    const endpoint = fakeEndpoint();
    const seen: string[] = [];
    const { result } = renderHook(() => {
      const state = useTaskEvents(TASK, { fetchImpl: endpoint.fetchImpl, ...fast });
      if (seen.at(-1) !== state.status) seen.push(state.status);
      return state;
    });
    await waitFor(() => expect(result.current.status).toBe("open"));
    act(() => {
      let text = "";
      for (let i = 1; i <= 505; i++) text += event(i);
      endpoint.send(text + END("completed"));
    });
    await waitFor(() => expect(result.current.status).toBe("ended"));
    expect([result.current.events.length, result.current.dropped]).toEqual([500, 5]);
    const before = seen.length;
    act(() => result.current.reconnect()); // e.g. the screen saw the task reopened
    await waitFor(() => expect(endpoint.connections()).toBe(2));
    expect(endpoint.requests[1]?.url.searchParams.get("after_seq")).toBe("505");
    act(() => endpoint.send(event(504) + event(505) + event(506) + event(507))); // the replay overlaps
    await waitFor(() => expect(result.current.lastSeq).toBe(507));
    const seqs = result.current.events.map((e) => e.seq);
    expect(new Set(seqs).size).toBe(seqs.length);
    expect([seqs.length, seqs[0], seqs.at(-1), result.current.dropped]).toEqual([500, 8, 507, 7]);
    expect(seen.slice(before)).toEqual(["connecting", "open"]);
    expect(result.current.uxState).toBeNull();
  });

  it("does nothing without a task id", () => {
    const endpoint = fakeEndpoint();
    const { result } = renderHook(() => useTaskEvents(null, { fetchImpl: endpoint.fetchImpl }));
    expect(result.current.status).toBe("closed");
    expect(endpoint.requests).toHaveLength(0);
  });
});

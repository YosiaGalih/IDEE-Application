import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { openEventStream, type StreamStatus } from "@/api/sse/eventStream";
import { useTaskEvents } from "@/api/hooks/useTaskEvents";

const TASK = "6f1d2c3b-4a5e-4f60-8a7b-9c0d1e2f3a4b";

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
    if (status !== 200) return Promise.resolve(new Response("{}", { status }));
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

  it("stops for good on 401, 403 and 404: reconnecting cannot fix them", async () => {
    for (const code of [401, 403, 404]) {
      const endpoint = fakeEndpoint([code]);
      const { statuses } = open(endpoint);
      await waitFor(() => expect(statuses).toContain("disconnected"));
      await new Promise((r) => setTimeout(r, 40));
      expect(endpoint.requests).toHaveLength(1);
    }
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
  it("collects validated task events, counts invalid ones, and closes on unmount", async () => {
    const endpoint = fakeEndpoint();
    const { result, unmount } = renderHook(() =>
      useTaskEvents(TASK, { fetchImpl: endpoint.fetchImpl, ...fast }),
    );
    await waitFor(() => expect(result.current.status).toBe("open"));
    act(() => {
      endpoint.send(frame(1, "task.status_changed") + "id: 2\ndata: {not json\n\n" + frame(3));
    });
    await waitFor(() => expect(result.current.events.map((e) => e.seq)).toEqual([1, 3]));
    expect(result.current.invalidFrames).toBe(1);
    expect(result.current.lastSeq).toBe(3);
    unmount();
    await new Promise((r) => setTimeout(r, 30));
    expect(endpoint.connections()).toBe(1);
  });

  it("does nothing without a task id", () => {
    const endpoint = fakeEndpoint();
    const { result } = renderHook(() => useTaskEvents(null, { fetchImpl: endpoint.fetchImpl }));
    expect(result.current.status).toBe("closed");
    expect(endpoint.requests).toHaveLength(0);
  });
});

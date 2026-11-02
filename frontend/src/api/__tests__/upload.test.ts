import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";
import { ApiError } from "../errors";
import { apiUpload } from "../upload";

const API = "/api/v1";
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  document.cookie = "kk_csrf=csrf-test; path=/";
});

const schema = z.object({ job_id: z.string() });
const file = () => new File([new Uint8Array(64 * 1024)], "proj.zip", { type: "application/zip" });

describe("apiUpload", () => {
  it("sends the file with the CSRF token and the key, reports progress, and returns the data", async () => {
    let seen: { csrf: string | null; key: string | null; hasFile: boolean } | null = null;
    server.use(
      http.post(`${API}/projects/p1/imports`, async ({ request }) => {
        const form = await request.formData();
        // jsdom's File does not survive the XHR interceptor's serialisation (it arrives as a 9-byte "blob"),
        // so only the field is checked here; the bytes are proven by the backend tests and the live check.
        seen = {
          csrf: request.headers.get("X-CSRF-Token"),
          key: request.headers.get("Idempotency-Key"),
          hasFile: form.has("file"),
        };
        return HttpResponse.json(
          { request_id: "r1", status: "accepted", data: { job_id: "j1" } },
          { status: 202 },
        );
      }),
    );
    const progress: number[] = [];
    const result = await apiUpload("/projects/p1/imports", {
      file: file(),
      schema,
      idempotencyKey: "key-1",
      onProgress: (sent, total) => progress.push(Math.round((sent / total) * 100)),
    });
    expect(result.data).toEqual({ job_id: "j1" });
    expect(result.status).toBe("accepted");
    expect(seen).toEqual({ csrf: "csrf-test", key: "key-1", hasFile: true });
    expect(progress.at(-1)).toBe(100);
  });

  it("turns the failure envelope into an http error with its details and the key", async () => {
    server.use(
      http.post(`${API}/projects/p1/imports`, () =>
        HttpResponse.json(
          {
            request_id: "r2",
            status: "failed",
            error: { code: "CONFLICT", message: "busy", details: [{ reason: "import_running" }] },
          },
          { status: 409 },
        ),
      ),
    );
    const failure = await apiUpload("/projects/p1/imports", {
      file: file(),
      schema,
      idempotencyKey: "key-2",
    }).catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({
      kind: "http",
      status: 409,
      code: "CONFLICT",
      details: [{ reason: "import_running" }],
      idempotencyKey: "key-2",
      requestId: "r2",
    });
  });

  it("reports no answer as a network error and a stopped upload as aborted", async () => {
    server.use(http.post(`${API}/projects/p1/imports`, () => HttpResponse.error()));
    await expect(
      apiUpload("/projects/p1/imports", { file: file(), schema, idempotencyKey: "k" }),
    ).rejects.toMatchObject({
      kind: "network",
    });
    const stop = new AbortController();
    stop.abort();
    await expect(
      apiUpload("/projects/p1/imports", { file: file(), schema, idempotencyKey: "k", signal: stop.signal }),
    ).rejects.toMatchObject({ kind: "aborted" });
  });
});

describe("apiUpload without MSW (a stand-in XMLHttpRequest)", () => {
  /** MSW's interceptor ignores xhr.abort(); these paths need a request that waits until told. */
  class HangingXhr {
    static last: HangingXhr | null = null;
    upload = { onprogress: null as ((event: ProgressEvent) => void) | null };
    status = 0;
    responseText = "";
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    onabort: (() => void) | null = null;
    aborted = false;
    open() {}
    setRequestHeader() {}
    getResponseHeader() {
      return null;
    }
    send() {
      HangingXhr.last = this;
    }
    abort() {
      this.aborted = true;
      this.onabort?.();
    }
  }

  it("stops a running upload on abort and reports it as aborted", async () => {
    vi.stubGlobal("XMLHttpRequest", HangingXhr);
    try {
      const stop = new AbortController();
      const pending = apiUpload("/projects/p1/imports", {
        file: file(),
        schema,
        idempotencyKey: "k",
        signal: stop.signal,
      });
      await vi.waitFor(() => expect(HangingXhr.last).not.toBeNull());
      stop.abort();
      await expect(pending).rejects.toMatchObject({ kind: "aborted", idempotencyKey: "k" });
      expect(HangingXhr.last?.aborted).toBe(true);
    } finally {
      vi.unstubAllGlobals();
      HangingXhr.last = null;
    }
  });

  it("reports an upload past its time limit as a timeout", async () => {
    vi.stubGlobal("XMLHttpRequest", HangingXhr);
    try {
      await expect(
        apiUpload("/projects/p1/imports", { file: file(), schema, idempotencyKey: "k", timeoutMs: 20 }),
      ).rejects.toMatchObject({ kind: "timeout" });
    } finally {
      vi.unstubAllGlobals();
      HangingXhr.last = null;
    }
  });

  it("reports a gateway's HTML 502 as bad_response with its status (outcome unknown)", async () => {
    class GatewayXhr extends HangingXhr {
      override send() {
        this.status = 502;
        this.responseText = "<html>502 Bad Gateway</html>";
        queueMicrotask(() => this.onload?.());
      }
    }
    vi.stubGlobal("XMLHttpRequest", GatewayXhr);
    try {
      await expect(
        apiUpload("/projects/p1/imports", { file: file(), schema, idempotencyKey: "k" }),
      ).rejects.toMatchObject({
        kind: "bad_response",
        status: 502,
      });
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

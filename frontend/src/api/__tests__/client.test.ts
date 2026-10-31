import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { z } from "zod";
import { apiRequest } from "@/api/client";
import { ApiError } from "@/api/errors";
import { uxStateForError } from "@/api/uxState";

const API = `${window.location.origin}/api/v1`;
const server = setupServer();
const seen: Request[] = [];

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => {
  server.resetHandlers();
  seen.length = 0;
});
afterAll(() => server.close());

function ok(data: unknown, init?: ResponseInit) {
  return HttpResponse.json({ request_id: "req-12345678", status: "completed", data, error: null }, init);
}
function failed(status: number, code: string) {
  return HttpResponse.json(
    { request_id: "req-12345678", status: "failed", data: null, error: { code, message: code, details: {} } },
    { status },
  );
}
function record(response: () => Response) {
  return ({ request }: { request: Request }) => {
    seen.push(request.clone());
    return response();
  };
}

const Thing = z.object({ id: z.string() });

describe("apiRequest", () => {
  it("returns validated data from the success envelope", async () => {
    server.use(
      http.get(
        `${API}/things/1`,
        record(() => ok({ id: "t1" })),
      ),
    );
    const result = await apiRequest("/things/1", { schema: Thing });
    expect(result.data).toEqual({ id: "t1" });
    expect(result.requestId).toBe("req-12345678");
    expect(seen[0]?.headers.get("X-CSRF-Token")).toBeNull();
    expect(seen[0]?.headers.get("Idempotency-Key")).toBeNull();
  });

  it("turns the error envelope into an ApiError with code and status", async () => {
    server.use(http.get(`${API}/things/1`, () => failed(403, "FORBIDDEN")));
    const error = await apiRequest("/things/1", { schema: Thing }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ kind: "http", status: 403, code: "FORBIDDEN", requestId: "req-12345678" });
  });

  it("rejects data that does not match the schema instead of passing it on", async () => {
    server.use(http.get(`${API}/things/1`, () => ok({ id: 42 })));
    await expect(apiRequest("/things/1", { schema: Thing })).rejects.toMatchObject({ kind: "bad_response" });
  });

  it("rejects a body that is not an envelope or not JSON", async () => {
    server.use(http.get(`${API}/a`, () => HttpResponse.json({ id: "x" })));
    server.use(http.get(`${API}/b`, () => new HttpResponse("<html>bad gateway</html>", { status: 502 })));
    await expect(apiRequest("/a", { schema: Thing })).rejects.toMatchObject({ kind: "bad_response" });
    await expect(apiRequest("/b", { schema: Thing })).rejects.toMatchObject({
      kind: "bad_response",
      status: 502,
    });
  });

  it("sends the current CSRF cookie and a fresh Idempotency-Key on every mutation", async () => {
    server.use(
      http.post(
        `${API}/things`,
        record(() => ok({ id: "t1" }, { status: 201 })),
      ),
    );
    document.cookie = "kk_csrf=first; path=/";
    await apiRequest("/things", { method: "POST", body: { name: "a" }, schema: Thing });
    document.cookie = "kk_csrf=second; path=/"; // login rotates the cookie: never cache it
    await apiRequest("/things", { method: "POST", body: { name: "a" }, schema: Thing });
    expect(seen.map((r) => r.headers.get("X-CSRF-Token"))).toEqual(["first", "second"]);
    const keys = seen.map((r) => r.headers.get("Idempotency-Key"));
    expect(keys[0]).toMatch(/^[A-Za-z0-9_.:-]{1,128}$/);
    expect(keys[0]).not.toEqual(keys[1]);
    expect(await seen[0]?.json()).toEqual({ name: "a" });
  });

  it("keeps a caller-supplied Idempotency-Key for a retry of the same action", async () => {
    server.use(
      http.post(
        `${API}/things`,
        record(() => ok({ id: "t1" })),
      ),
    );
    document.cookie = "kk_csrf=c; path=/";
    await apiRequest("/things", { method: "POST", schema: Thing, idempotencyKey: "retry-1" });
    expect(seen[0]?.headers.get("Idempotency-Key")).toBe("retry-1");
  });

  it("refuses a mutation without a CSRF cookie before any request is sent", async () => {
    server.use(
      http.delete(
        `${API}/things/1`,
        record(() => ok({ id: "t1" })),
      ),
    );
    await expect(apiRequest("/things/1", { method: "DELETE", schema: Thing })).rejects.toMatchObject({
      kind: "csrf_missing",
    });
    expect(seen).toHaveLength(0);
  });

  it("logs in without CSRF or Idempotency-Key, and auth mutations carry no Idempotency-Key", async () => {
    server.use(
      http.post(
        `${API}/auth/login`,
        record(() => ok({ id: "u1" })),
      ),
    );
    server.use(
      http.post(
        `${API}/auth/logout`,
        record(() => ok({ id: "u1" })),
      ),
    );
    await apiRequest("/auth/login", { method: "POST", body: { email: "a@b.c" }, schema: Thing });
    document.cookie = "kk_csrf=c; path=/";
    await apiRequest("/auth/logout", { method: "POST", schema: Thing });
    expect(seen[0]?.headers.get("X-CSRF-Token")).toBeNull();
    expect(seen.map((r) => r.headers.get("Idempotency-Key"))).toEqual([null, null]);
    expect(seen[1]?.headers.get("X-CSRF-Token")).toBe("c");
  });

  it("reports network failures and timeouts as their own kinds", async () => {
    server.use(http.get(`${API}/down`, () => HttpResponse.error()));
    server.use(http.get(`${API}/slow`, () => new Promise<never>(() => {})));
    await expect(apiRequest("/down", { schema: Thing })).rejects.toMatchObject({ kind: "network" });
    await expect(apiRequest("/slow", { schema: Thing, timeoutMs: 20 })).rejects.toMatchObject({
      kind: "timeout",
    });
  });

  it("refuses paths that leave the API base", async () => {
    await expect(apiRequest("https://evil.example/x", { schema: Thing })).rejects.toThrow();
    await expect(apiRequest("/a/../../x", { schema: Thing })).rejects.toThrow();
  });
});

describe("uxStateForError", () => {
  const cases: [ApiError, string][] = [
    [new ApiError({ kind: "http", status: 401, code: "UNAUTHENTICATED" }), "unauthenticated"],
    [new ApiError({ kind: "http", status: 401, code: "SESSION_EXPIRED" }), "unauthenticated"],
    [new ApiError({ kind: "http", status: 403, code: "FORBIDDEN" }), "permission_denied"],
    [new ApiError({ kind: "http", status: 403, code: "POLICY_DENIED" }), "permission_denied"],
    [new ApiError({ kind: "http", status: 403, code: "CAPABILITY_DISABLED" }), "capability_disabled"],
    [new ApiError({ kind: "http", status: 404, code: "NOT_FOUND" }), "not_found"],
    [new ApiError({ kind: "http", status: 429, code: "QUOTA_EXCEEDED" }), "quota_exceeded"],
    [new ApiError({ kind: "http", status: 429, code: "RATE_LIMITED" }), "rate_limited"],
    [new ApiError({ kind: "http", status: 503, code: "DEPENDENCY_UNAVAILABLE" }), "provider_degraded"],
    [new ApiError({ kind: "http", status: 500, code: "INTERNAL_ERROR" }), "error"],
    [new ApiError({ kind: "http", status: 403, code: "CSRF_FAILED" }), "error"],
    [new ApiError({ kind: "timeout" }), "timeout"],
    [new ApiError({ kind: "network" }), "disconnected"],
    [new ApiError({ kind: "bad_response", status: 200 }), "error"],
  ];
  it.each(cases)("%#: maps to %s", (error, state) => {
    expect(uxStateForError(error)).toBe(state);
  });
  it("treats anything that is not an ApiError as a generic error", () => {
    expect(uxStateForError(new Error("x"))).toBe("error");
  });
});

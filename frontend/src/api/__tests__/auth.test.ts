import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { fetchMe, login, logout } from "@/api/auth";
import { readCookie } from "@/api/cookies";
import { ApiError } from "@/api/errors";
import { createQueryClient, HANDLES_UNAUTHENTICATED, shouldRetry } from "@/api/queryClient";

const API = `${window.location.origin}/api/v1`;
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const user = {
  id: "0b6a3f1e-6a1f-4e0e-9a51-6c1c8d7f2a10",
  email: "tira@kodekantor.test",
  display_name: "Tira",
  status: "active",
  created_at: "2026-10-31T01:00:00+00:00",
  last_login_at: null,
};
const envelope = (data: unknown) =>
  HttpResponse.json({ request_id: "r-12345678", status: "completed", data });

describe("auth API", () => {
  it("reads /auth/me with roles and permissions", async () => {
    server.use(
      http.get(`${API}/auth/me`, () =>
        envelope({ user, roles: [{ role: "owner", scope: "global" }], permissions: ["project:read"] }),
      ),
    );
    const me = await fetchMe();
    expect(me.user.display_name).toBe("Tira");
    expect(me.permissions).toEqual(["project:read"]);
  });

  it("sends remember_me in the backend's field name and returns the user", async () => {
    let body: unknown;
    server.use(
      http.post(`${API}/auth/login`, async ({ request }) => {
        body = await request.json();
        return envelope({ user });
      }),
    );
    await expect(
      login({ email: "tira@kodekantor.test", password: "pw", rememberMe: true }),
    ).resolves.toMatchObject({ id: user.id });
    expect(body).toEqual({ email: "tira@kodekantor.test", password: "pw", remember_me: true });
  });

  it("refuses a user whose fields do not have the contract's types", async () => {
    server.use(
      http.get(`${API}/auth/me`, () =>
        envelope({ user: { ...user, id: "not-a-uuid" }, roles: [], permissions: [] }),
      ),
    );
    await expect(fetchMe()).rejects.toMatchObject({ kind: "bad_response" });
  });

  it("logs out with the CSRF cookie", async () => {
    let token: string | null = null;
    server.use(
      http.post(`${API}/auth/logout`, ({ request }) => {
        token = request.headers.get("X-CSRF-Token");
        return envelope({});
      }),
    );
    document.cookie = "kk_csrf=abc; path=/";
    await logout();
    expect(token).toBe("abc");
  });
});

describe("readCookie", () => {
  it("decodes the value and does not match a cookie that only ends with the name", () => {
    document.cookie = "xkk_csrf=wrong; path=/";
    document.cookie = "kk_csrf=a%20b; path=/";
    expect(readCookie("kk_csrf")).toBe("a b");
    expect(readCookie("missing")).toBeNull();
  });
});

describe("shouldRetry", () => {
  it("retries a gateway error page as well", () => {
    expect(shouldRetry(0, new ApiError({ kind: "bad_response", status: 502 }))).toBe(true);
  });

  it("retries no answer and server errors, never client errors, at most twice", () => {
    expect(shouldRetry(0, new ApiError({ kind: "network" }))).toBe(true);
    expect(shouldRetry(0, new ApiError({ kind: "http", status: 503, code: "DEPENDENCY_UNAVAILABLE" }))).toBe(
      true,
    );
    expect(shouldRetry(0, new ApiError({ kind: "http", status: 404, code: "NOT_FOUND" }))).toBe(false);
    expect(shouldRetry(0, new ApiError({ kind: "bad_response", status: 200 }))).toBe(false);
    expect(shouldRetry(2, new ApiError({ kind: "network" }))).toBe(false);
    expect(shouldRetry(0, new Error("x"))).toBe(false);
  });
});

describe("createQueryClient 401 handling", () => {
  const unauthenticated = new ApiError({ kind: "http", status: 401, code: "SESSION_EXPIRED" });

  it("reports a 401 from a query or a mutation once each", async () => {
    const onUnauthenticated = vi.fn();
    const client = createQueryClient({ onUnauthenticated });
    await client
      .fetchQuery({ queryKey: ["x"], queryFn: () => Promise.reject(unauthenticated) })
      .catch(() => {});
    await client
      .getMutationCache()
      .build(client, { mutationFn: () => Promise.reject(unauthenticated) })
      .execute(undefined)
      .catch(() => {});
    expect(onUnauthenticated).toHaveBeenCalledTimes(2);
  });

  it("leaves a 401 to queries and mutations marked HANDLES_UNAUTHENTICATED", async () => {
    const onUnauthenticated = vi.fn();
    const client = createQueryClient({ onUnauthenticated });
    await client
      .fetchQuery({
        queryKey: ["me"],
        queryFn: () => Promise.reject(unauthenticated),
        meta: HANDLES_UNAUTHENTICATED,
      })
      .catch(() => {});
    await client
      .getMutationCache()
      .build(client, { mutationFn: () => Promise.reject(unauthenticated), meta: HANDLES_UNAUTHENTICATED })
      .execute(undefined)
      .catch(() => {});
    expect(onUnauthenticated).not.toHaveBeenCalled();
  });

  it("ignores other failures", async () => {
    const onUnauthenticated = vi.fn();
    const client = createQueryClient({ onUnauthenticated });
    const forbidden = new ApiError({ kind: "http", status: 403, code: "FORBIDDEN" });
    await client.fetchQuery({ queryKey: ["y"], queryFn: () => Promise.reject(forbidden) }).catch(() => {});
    expect(onUnauthenticated).not.toHaveBeenCalled();
  });
});

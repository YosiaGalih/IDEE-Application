import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { fetchMe, login, logout } from "@/api/auth";
import { ApiError } from "@/api/errors";
import { shouldRetry } from "@/api/queryClient";

const API = `${window.location.origin}/api/v1`;
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
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
        envelope({ user, roles: [{ role: "owner", scope: "global" }], permissions: ["project.read"] }),
      ),
    );
    const me = await fetchMe();
    expect(me.user.display_name).toBe("Tira");
    expect(me.permissions).toEqual(["project.read"]);
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

  it("refuses a user object that carries more than the public fields' types allow", async () => {
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

describe("shouldRetry", () => {
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

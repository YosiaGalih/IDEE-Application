import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { useLogout } from "@/api/hooks/useLogout";
import { createQueryClient } from "@/api/queryClient";

const API = `${window.location.origin}/api/v1`;
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  document.cookie = "kk_csrf=c; path=/";
});

const failure = (status: number, code: string) =>
  HttpResponse.json({ request_id: "r", status: "failed", error: { code, message: code } }, { status });

function setup(reply: () => Response) {
  server.use(http.post(`${API}/auth/logout`, reply));
  const onUnauthenticated = vi.fn();
  const client = createQueryClient({ onUnauthenticated });
  client.setQueryData(["projects"], [{ id: "p1" }]);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const { result } = renderHook(() => useLogout(), { wrapper });
  return { client, result, onUnauthenticated };
}

describe("useLogout", () => {
  it("drops the cache once the session is gone", async () => {
    const { client, result } = setup(() =>
      HttpResponse.json({ request_id: "r", status: "completed", data: {} }),
    );
    result.current.mutate();
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(client.getQueryData(["projects"])).toBeUndefined();
  });

  it("drops the cache on a 401 too (no session left), and leaves the redirect to the caller", async () => {
    const { client, result, onUnauthenticated } = setup(() => failure(401, "SESSION_EXPIRED"));
    result.current.mutate();
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(client.getQueryData(["projects"])).toBeUndefined();
    expect(onUnauthenticated).not.toHaveBeenCalled();
  });

  it("keeps the cache when the logout failed: the session is still alive", async () => {
    const { client, result } = setup(() => failure(500, "INTERNAL_ERROR"));
    result.current.mutate();
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(client.getQueryData(["projects"])).toEqual([{ id: "p1" }]);
  });
});

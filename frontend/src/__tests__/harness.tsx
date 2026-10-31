import { QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { createMemoryRouter, RouterProvider, type InitialEntry, type RouteObject } from "react-router";
import { createQueryClient } from "@/api/queryClient";
import { routes } from "@/app/routes";
import { redirectToLogin } from "@/app/session";

/** Shared fixtures for screen tests: the real routes, the real query client and its 401 handler. */
export const API = `${window.location.origin}/api/v1`;

export const user = {
  id: "0b6a3f1e-6a1f-4e0e-9a51-6c1c8d7f2a10",
  email: "tira@kodekantor.test",
  display_name: "Tira Wulandari",
  status: "active",
  created_at: "2026-10-31T01:00:00+00:00",
  last_login_at: null,
};
export const me = { user, roles: [{ role: "owner", scope: "global" }], permissions: ["project:read"] };

export const ok = (data: unknown) =>
  HttpResponse.json({ request_id: "r-ok-0001", status: "completed", data });
export const fail = (status: number, code: string, message = "refused", headers?: Record<string, string>) =>
  HttpResponse.json(
    { request_id: "r-err-0001", status: "failed", error: { code, message } },
    { status, ...(headers ? { headers } : {}) },
  );

export const signedIn = () => http.get(`${API}/auth/me`, () => ok(me));
export const signedOut = () =>
  http.get(`${API}/auth/me`, () => fail(401, "UNAUTHENTICATED", "Authentication required"));

type Options = {
  /** Called before the app's own 401 handler: to see whether a 401 reached it at all. */
  onUnauthenticated?: () => void;
};

export function renderApp(entry: InitialEntry, routeList: RouteObject[] = routes, options: Options = {}) {
  const router = createMemoryRouter(routeList, { initialEntries: [entry] });
  const queryClient = createQueryClient({
    onUnauthenticated: () => {
      options.onUnauthenticated?.();
      redirectToLogin(router);
    },
  });
  // Same retry rules, no waiting between attempts.
  queryClient.setDefaultOptions({
    ...queryClient.getDefaultOptions(),
    queries: { ...queryClient.getDefaultOptions().queries, retryDelay: 0 },
  });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { router, queryClient, ...view };
}

/** A response that waits until the test lets it go (to see the submitting state). */
export function deferred() {
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { gate, release };
}

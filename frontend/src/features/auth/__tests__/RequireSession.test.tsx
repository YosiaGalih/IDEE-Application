import { useQuery } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http } from "msw";
import { setupServer } from "msw/node";
import type { RouteObject } from "react-router";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { API, deferred, fail, me, ok, renderApp, signedIn, signedOut, user } from "@/__tests__/harness";
import { apiRequest } from "@/api/client";
import { routes } from "@/app/routes";
import id from "@/i18n/locales/id.json";
import { z } from "zod";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  document.cookie = "kk_csrf=csrf-test; path=/";
});

const signedInAs = id.auth.menu.signedInAs.replace("{{name}}", user.display_name);

describe("RequireSession (the session guard)", () => {
  it("shows the loading state while it asks who is signed in", async () => {
    const { gate, release } = deferred();
    server.use(
      http.get(`${API}/auth/me`, async () => {
        await gate;
        return ok(me);
      }),
    );
    renderApp("/projects");
    expect(await screen.findByRole("status")).toHaveTextContent(id.states.loading.title);
    expect(screen.queryByRole("navigation")).toBeNull();
    release();
    expect(await screen.findByText(signedInAs)).toBeInTheDocument();
  });

  it("sends a signed-out visitor to /login with the page to come back to", async () => {
    server.use(signedOut());
    const { router } = renderApp("/projects?tab=tasks");
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(router.state.location.search).toBe(`?next=${encodeURIComponent("/projects?tab=tasks")}`);
    expect(screen.queryByRole("navigation")).toBeNull();
  });

  it("says the session ended when it expired (SESSION_EXPIRED), not on a first visit", async () => {
    server.use(http.get(`${API}/auth/me`, () => fail(401, "SESSION_EXPIRED", "Session expired")));
    const { router } = renderApp("/approvals");
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(router.state.location.search).toBe(`?next=${encodeURIComponent("/approvals")}`);
    expect(await screen.findByText(id.auth.login.sessionExpired)).toBeInTheDocument();
  });

  it("gives a first visit no session notice", async () => {
    server.use(signedOut());
    const { router } = renderApp("/approvals");
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    await screen.findByRole("button", { name: id.auth.login.submit });
    expect(screen.queryByText(id.auth.login.sessionExpired)).toBeNull();
  });

  it("shows the error with a retry on 500, never the app or a redirect, then recovers", async () => {
    let fail500 = true;
    const retry = deferred();
    server.use(
      http.get(`${API}/auth/me`, async () => {
        if (fail500) return fail(500, "INTERNAL_ERROR");
        await retry.gate;
        return ok(me);
      }),
    );
    const { router } = renderApp("/projects");
    expect(await screen.findByRole("alert")).toHaveTextContent(id.states.error.title);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(id.states.error.title);
    expect(screen.getByText(id.common.requestId.replace("{{id}}", "r-err-0001"))).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/projects");
    expect(screen.queryByRole("navigation")).toBeNull();
    fail500 = false;
    await userEvent.click(screen.getByRole("button", { name: id.states.error.action }));
    // While the retry runs: loading, not the stale error.
    expect(await screen.findByRole("status")).toHaveTextContent(id.states.loading.title);
    retry.release();
    expect(await screen.findByText(signedInAs)).toBeInTheDocument();
  });

  it("shows the disconnected state when the API cannot be reached", async () => {
    server.use(http.get(`${API}/auth/me`, () => Response.error()));
    renderApp("/");
    expect(await screen.findByRole("alert")).toHaveTextContent(id.states.disconnected.title);
  });

  it("renders the app shell with the signed-in user", async () => {
    server.use(signedIn());
    renderApp("/");
    expect(await screen.findByText(signedInAs)).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: id.app.mainNav })).toBeInTheDocument();
  });

  it.each(["/login", "/forgot-password", "/reset-password"])(
    "does not guard the public page %s",
    async (path) => {
      server.use(signedOut());
      const { router } = renderApp(path);
      expect(await screen.findByTestId("screen-title")).toBeInTheDocument();
      expect(router.state.location.pathname).toBe(path);
    },
  );
});

describe("the global 401 handler", () => {
  function Projects() {
    const query = useQuery({
      queryKey: ["projects"],
      queryFn: async () => (await apiRequest("/projects", { schema: z.array(z.unknown()) })).data,
    });
    return <p>{query.isError ? "failed" : "loading"}</p>;
  }
  const probe: RouteObject = { path: "/probe", element: <Projects /> };
  const withProjects: RouteObject[] = [
    { errorElement: routes[0]?.errorElement, children: [...(routes[0]?.children ?? []), probe] },
  ];

  it("sends any other 401 to /login with next, and forgets the cached session", async () => {
    server.use(
      signedIn(),
      http.get(`${API}/projects`, () => fail(401, "SESSION_EXPIRED")),
    );
    const { router, queryClient } = renderApp("/probe?x=1", withProjects);
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(router.state.location.search).toBe(`?next=${encodeURIComponent("/probe?x=1")}`);
    expect(queryClient.getQueryData(["auth", "me"])).toBeUndefined();
    expect(await screen.findByText(id.auth.login.sessionExpired)).toBeInTheDocument();
  });
});

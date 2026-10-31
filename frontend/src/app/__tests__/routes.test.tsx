import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { setupServer } from "msw/node";
import { createMemoryRouter, RouterProvider, type RouteObject } from "react-router";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { renderApp, signedIn, signedOut } from "@/__tests__/harness";
import { ApiError } from "@/api/errors";
import { routes } from "@/app/routes";
import en from "@/i18n/locales/en.json";
import id from "@/i18n/locales/id.json";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

/** Signed in: the session guard lets the app shell through. */
function renderAt(path: string) {
  server.use(signedIn());
  return renderApp(path);
}

describe("route map (ARCHITECTURE §3.1b)", () => {
  it("shows the error state with a reload, never React Router's stack trace, when a screen throws", () => {
    const Boom = () => {
      throw new Error("internal detail");
    };
    const withBoom: RouteObject[] = [
      { errorElement: routes[0]?.errorElement, children: [{ path: "/boom", element: <Boom /> }] },
    ];
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<RouterProvider router={createMemoryRouter(withBoom, { initialEntries: ["/boom"] })} />);
    quiet.mockRestore();
    expect(screen.getByRole("alert")).toHaveTextContent(id.states.error.title);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(id.states.error.title);
    expect(screen.getByRole("button", { name: id.states.error.action })).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("internal detail");
    expect(document.body).not.toHaveTextContent("Unexpected Application Error");
  });

  it("offers sign-in on an unauthenticated route error, coming back to the same page", async () => {
    const Expired = () => {
      throw new ApiError({ kind: "http", status: 401, code: "SESSION_EXPIRED", requestId: "r-401" });
    };
    const withExpired: RouteObject[] = [
      {
        errorElement: routes[0]?.errorElement,
        children: [...(routes[0]?.children ?? []), { path: "/expired", element: <Expired /> }],
      },
    ];
    server.use(signedOut());
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    const { router } = renderApp("/expired?x=1", withExpired);
    await userEvent.click(await screen.findByRole("button", { name: id.states.unauthenticated.action }));
    quiet.mockRestore();
    expect(router.state.location.pathname).toBe("/login");
    expect(router.state.location.search).toBe(`?next=${encodeURIComponent("/expired?x=1")}`);
  });

  it("names each screen in the document title and as the page heading", async () => {
    renderAt("/approvals");
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(id.screens.approvals);
    expect(document.title).toBe(`${id.screens.approvals} · ${id.app.name}`);
  });

  it("switches the language from the top bar and records it on <html lang>", async () => {
    renderAt("/");
    await userEvent.selectOptions(await screen.findByRole("combobox"), "en");
    expect(screen.getByTestId("screen-title")).toHaveTextContent(en.screens.dashboard);
    expect(document.documentElement.lang).toBe("en");
    await userEvent.selectOptions(screen.getByRole("combobox"), "id");
  });

  it.each([
    ["/", id.screens.dashboard],
    ["/projects/new", id.screens.newProject],
    ["/projects/p1/workspace", id.screens.workspace],
    ["/tasks/t1/acceptance", id.screens.acceptance],
    ["/settings/audit", id.screens.audit],
  ])("%s renders its screen inside the app shell", async (path, title) => {
    renderAt(path);
    expect(await screen.findByTestId("screen-title")).toHaveTextContent(title);
    expect(screen.getByRole("navigation", { name: id.app.mainNav })).toBeInTheDocument();
  });

  it("renders /login without the app shell and without asking for a session", async () => {
    server.use(signedOut());
    renderApp("/login");
    expect(await screen.findByTestId("screen-title")).toHaveTextContent(id.auth.login.title);
    expect(document.title).toBe(`${id.auth.login.title} · ${id.app.name}`);
    expect(screen.queryByRole("navigation")).toBeNull();
  });

  it("shows not found for an unknown path inside the shell", async () => {
    renderAt("/nope");
    expect(await screen.findByRole("alert")).toHaveTextContent(id.screens.notFound);
  });
});

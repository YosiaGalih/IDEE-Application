import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider, type RouteObject } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { routes } from "@/app/routes";
import en from "@/i18n/locales/en.json";
import id from "@/i18n/locales/id.json";

function renderAt(path: string) {
  render(<RouterProvider router={createMemoryRouter(routes, { initialEntries: [path] })} />);
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

  it("names each screen in the document title and as the page heading", () => {
    renderAt("/approvals");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(id.screens.approvals);
    expect(document.title).toBe(`${id.screens.approvals} · ${id.app.name}`);
  });

  it("switches the language from the top bar and records it on <html lang>", async () => {
    renderAt("/");
    await userEvent.selectOptions(screen.getByRole("combobox"), "en");
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
  ])("%s renders its screen inside the app shell", (path, title) => {
    renderAt(path);
    expect(screen.getByTestId("screen-title")).toHaveTextContent(title);
    expect(screen.getByRole("navigation", { name: id.app.mainNav })).toBeInTheDocument();
  });

  it("renders /login without the app shell", () => {
    renderAt("/login");
    expect(screen.getByTestId("screen-title")).toHaveTextContent(id.screens.login);
    expect(screen.queryByRole("navigation")).toBeNull();
  });

  it("shows not found for an unknown path", () => {
    renderAt("/nope");
    expect(screen.getByRole("alert")).toHaveTextContent(id.screens.notFound);
  });
});

import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";
import { routes } from "@/app/routes";
import id from "@/i18n/locales/id.json";

function renderAt(path: string) {
  render(<RouterProvider router={createMemoryRouter(routes, { initialEntries: [path] })} />);
}

describe("route map (ARCHITECTURE §3.1b)", () => {
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

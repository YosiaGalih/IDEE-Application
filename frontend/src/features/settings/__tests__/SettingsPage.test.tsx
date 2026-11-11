import { screen, within } from "@testing-library/react";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { pendingApprovals, renderApp, signedIn, signedInWith } from "@/__tests__/harness";
import id from "@/i18n/locales/id.json";

const server = setupServer(pendingApprovals());
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("SettingsPage (spec WP-3.1e2 Goal 2)", () => {
  it("lists only the settings screens the user may open", async () => {
    server.use(signedInWith(["project:read", "settings:providers", "audit:read"]));
    renderApp("/settings");
    expect(await screen.findByTestId("screen-title")).toHaveTextContent(id.screens.settings);
    const main = screen.getByRole("main");
    expect(
      within(main).getByRole("link", { name: new RegExp(id.settings.sections.providers.title) }),
    ).toHaveAttribute("href", "/settings/providers");
    expect(
      within(main).getByRole("link", { name: new RegExp(id.settings.sections.audit.title) }),
    ).toHaveAttribute("href", "/settings/audit");
    expect(
      within(main).queryByRole("link", { name: new RegExp(id.settings.sections.users.title) }),
    ).toBeNull();
  });

  it("says permission denied to someone with no settings permission", async () => {
    server.use(signedIn()); // project:read only
    renderApp("/settings");
    expect(await screen.findByText(id.states.permission_denied.title)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: new RegExp(id.settings.sections.providers.title) })).toBeNull();
  });
});

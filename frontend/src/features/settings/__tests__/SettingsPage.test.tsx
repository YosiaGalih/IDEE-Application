import { screen } from "@testing-library/react";
import { http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { API, ok, pendingApprovals, renderApp, signedIn, signedInWith } from "@/__tests__/harness";
import { provider, readiness } from "@/__tests__/providerFixtures";
import id from "@/i18n/locales/id.json";

const server = setupServer(pendingApprovals());
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("SettingsPage (API §11 #18: /settings opens the first settings screen the user may)", () => {
  it("opens Providers for a holder of settings:providers", async () => {
    server.use(
      signedInWith(["project:read", "audit:read", "settings:providers"]),
      http.get(`${API}/settings/providers`, () => ok([provider()])),
      http.get(`${API}/settings/providers/:id/readiness`, () => ok(readiness())),
    );
    const { router } = renderApp("/settings");
    expect(await screen.findByTestId("screen-title")).toHaveTextContent(id.screens.providers);
    expect(router.state.location.pathname).toBe("/settings/providers");
  });

  it("opens the first one allowed when Providers is not", async () => {
    server.use(signedInWith(["project:read", "audit:read"]));
    const { router } = renderApp("/settings");
    expect(await screen.findByTestId("screen-title")).toHaveTextContent(id.screens.audit);
    expect(router.state.location.pathname).toBe("/settings/audit");
  });

  it("says permission denied to someone with no settings permission", async () => {
    server.use(signedIn()); // project:read only
    renderApp("/settings");
    expect(await screen.findByText(id.states.permission_denied.title)).toBeInTheDocument();
    expect(screen.getByTestId("screen-title")).toHaveTextContent(id.screens.settings);
  });
});

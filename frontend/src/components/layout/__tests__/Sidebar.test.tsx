import { screen, waitFor, within } from "@testing-library/react";
import { http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { API, fail, okPage, pendingApprovals, renderApp, signedIn, signedInWith } from "@/__tests__/harness";
import { PENDING_POLL_MS } from "@/api/hooks/useApprovals";
import id from "@/i18n/locales/id.json";
import { IDLE_AFTER_MS } from "@/lib/userActivity";

const server = setupServer(pendingApprovals());
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

/** The approvals link in the main navigation, once the shell has rendered. */
const approvalsLink = async () => {
  const nav = await screen.findByRole("navigation", { name: id.app.mainNav });
  return within(nav).getByRole("link", { name: new RegExp(`^${id.nav.approvals}`) });
};

describe("Sidebar pending approvals badge", () => {
  it("shows how many approvals the user can decide now, with a spoken label", async () => {
    let query: URLSearchParams | null = null;
    server.use(
      signedIn(),
      http.get(`${API}/approvals`, ({ request }) => {
        query = new URL(request.url).searchParams;
        return okPage([], { page_size: 1, total: 3 });
      }),
    );
    renderApp("/settings/usage");
    const link = await approvalsLink();
    expect(await within(link).findByText("3")).toHaveAttribute("aria-hidden", "true");
    expect(link).toHaveAccessibleName(id.nav.approvals + id.nav.pendingCount_other.replace("{{count}}", "3"));
    expect(Object.fromEntries(query ?? [])).toMatchObject({ status: "pending", page_size: "1" });
  });

  it("shows no badge when nothing is pending", async () => {
    server.use(signedIn());
    renderApp("/settings/usage");
    const link = await approvalsLink();
    await screen.findByRole("heading", { level: 1 });
    expect(link).toHaveAccessibleName(id.nav.approvals);
  });

  it("shows no badge (never a false count) when the count cannot be read", async () => {
    let calls = 0;
    server.use(
      signedIn(),
      http.get(`${API}/approvals`, () => {
        calls += 1;
        return fail(500, "INTERNAL_ERROR");
      }),
    );
    renderApp("/settings/usage");
    const link = await approvalsLink();
    await vi.waitFor(() => expect(calls).toBeGreaterThan(0));
    expect(link).toHaveAccessibleName(id.nav.approvals);
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
  });
  it("polls only while the page is in use, and catches up when someone comes back (AG-13 M1)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let calls = 0;
      server.use(
        signedIn(),
        http.get(`${API}/approvals`, () => {
          calls += 1;
          return okPage([], { page_size: 1, total: 1 });
        }),
      );
      window.dispatchEvent(new KeyboardEvent("keydown"));
      renderApp("/settings/usage");
      await approvalsLink();
      await waitFor(() => expect(calls).toBe(1));
      await vi.advanceTimersByTimeAsync(PENDING_POLL_MS + 50);
      await waitFor(() => expect(calls).toBe(2)); // in use: it polls
      await vi.advanceTimersByTimeAsync(IDLE_AFTER_MS + PENDING_POLL_MS); // nobody touches the page
      const whenIdle = calls;
      await vi.advanceTimersByTimeAsync(PENDING_POLL_MS * 4);
      expect(calls).toBe(whenIdle); // no request keeps the session alive
      window.dispatchEvent(new KeyboardEvent("keydown"));
      await waitFor(() => expect(calls).toBe(whenIdle + 1)); // back: one catch-up read
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("Sidebar settings entry (spec WP-3.1e2 Goal 2)", () => {
  const nav = () => screen.findByRole("navigation", { name: id.app.mainNav });

  it("shows Settings, pointing at the landing page, to a holder of a settings permission", async () => {
    server.use(signedInWith(["project:read", "settings:providers"]));
    renderApp("/settings/usage"); // a placeholder: no other request
    const link = await within(await nav()).findByRole("link", { name: id.nav.settings });
    expect(link).toHaveAttribute("href", "/settings");
  });

  it("hides the whole settings section from everyone else", async () => {
    server.use(signedIn()); // project:read only
    renderApp("/settings/usage"); // a placeholder: no other request
    await within(await nav()).findByRole("link", { name: id.nav.dashboard });
    expect(within(await nav()).queryByRole("link", { name: id.nav.settings })).toBeNull();
    expect(within(await nav()).queryAllByText(id.nav.sectionSettings)).toHaveLength(0); // nor its heading
  });
});

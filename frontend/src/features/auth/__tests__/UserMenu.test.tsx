import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http } from "msw";
import { setupServer } from "msw/node";
import { HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { API, fail, me, ok, renderApp, user } from "@/__tests__/harness";
import id from "@/i18n/locales/id.json";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  document.cookie = "kk_csrf=csrf-test; path=/";
});

const signedInAs = id.auth.menu.signedInAs.replace("{{name}}", user.display_name);

/** Signed in until logout; the logout and change replies are set per test. */
function backend(replies: { logout?: () => Response; change?: () => Response } = {}) {
  const state = { session: true, logoutCsrf: null as string | null, changeBody: null as unknown };
  server.use(
    http.get(`${API}/auth/me`, () => (state.session ? ok(me) : fail(401, "UNAUTHENTICATED"))),
    http.post(`${API}/auth/logout`, ({ request }) => {
      state.logoutCsrf = request.headers.get("X-CSRF-Token");
      const reply = replies.logout?.() ?? ok({});
      if (reply.status === 200) state.session = false;
      return reply;
    }),
    http.post(`${API}/auth/password/change`, async ({ request }) => {
      state.changeBody = await request.json();
      return replies.change?.() ?? ok({});
    }),
  );
  return state;
}

describe("UserMenu", () => {
  it("names the signed-in user in the top bar", async () => {
    backend();
    renderApp("/");
    const menu = await screen.findByRole("group", { name: id.auth.menu.label });
    expect(within(menu).getByText(signedInAs)).toBeInTheDocument();
  });

  it("signs out with the CSRF token, empties the cache, and goes to /login", async () => {
    const state = backend();
    const { router, queryClient } = renderApp("/projects");
    await screen.findByText(signedInAs);
    queryClient.setQueryData(["projects"], [{ id: "p1" }]);
    await userEvent.click(screen.getByRole("button", { name: id.auth.menu.logout }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(router.state.location.search).toBe("");
    expect(state.logoutCsrf).toBe("csrf-test");
    expect(queryClient.getQueryData(["projects"])).toBeUndefined();
  });

  it.each([
    ["a server error", () => fail(500, "INTERNAL_ERROR")],
    ["a refused CSRF token", () => fail(403, "CSRF_FAILED")],
    ["a dropped connection", () => HttpResponse.error()],
  ])("stays signed in and says so when logout fails with %s, then retries", async (_case, reply) => {
    let failing = true;
    const state = backend({ logout: () => (failing ? reply() : ok({})) });
    const { router } = renderApp("/projects");
    await screen.findByText(signedInAs);
    await userEvent.click(screen.getByRole("button", { name: id.auth.menu.logout }));
    expect(await screen.findByRole("alert")).toHaveTextContent(id.auth.menu.logoutFailed);
    expect(router.state.location.pathname).toBe("/projects");
    expect(state.session).toBe(true);
    failing = false;
    await userEvent.click(screen.getByRole("button", { name: id.auth.menu.logout }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
  });

  it("goes to plain /login when the session was already gone (401 on logout)", async () => {
    backend({ logout: () => fail(401, "SESSION_EXPIRED") });
    const { router, queryClient } = renderApp("/projects");
    await screen.findByText(signedInAs);
    queryClient.setQueryData(["projects"], [{ id: "p1" }]);
    await userEvent.click(screen.getByRole("button", { name: id.auth.menu.logout }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(router.state.location.search).toBe("");
    expect(queryClient.getQueryData(["projects"])).toBeUndefined();
  });
});

describe("ChangePasswordDialog", () => {
  async function openAndFill(current = "kata-sandi-lama!", next = "kata-sandi-baru-123", confirm = next) {
    await userEvent.click(await screen.findByRole("button", { name: id.auth.change.open }));
    const dialog = await screen.findByRole("dialog", { name: id.auth.change.title });
    await userEvent.type(within(dialog).getByLabelText(id.auth.change.current), current);
    await userEvent.type(within(dialog).getByLabelText(id.auth.reset.newPassword), next);
    await userEvent.type(within(dialog).getByLabelText(id.auth.reset.confirmPassword), confirm);
    await userEvent.click(within(dialog).getByRole("button", { name: id.auth.change.submit }));
    return dialog;
  }

  it("checks length and confirmation locally before sending", async () => {
    const state = backend();
    renderApp("/");
    const dialog = await openAndFill("lama", "pendek");
    expect(within(dialog).getByLabelText(id.auth.reset.newPassword)).toHaveAccessibleDescription(
      id.auth.errors.passwordTooShort,
    );
    await userEvent.clear(within(dialog).getByLabelText(id.auth.reset.newPassword));
    await userEvent.type(within(dialog).getByLabelText(id.auth.reset.newPassword), "kata-sandi-baru-123");
    await userEvent.click(within(dialog).getByRole("button", { name: id.auth.change.submit }));
    expect(within(dialog).getByLabelText(id.auth.reset.confirmPassword)).toHaveAccessibleDescription(
      id.auth.errors.passwordMismatch,
    );
    expect(state.changeBody).toBeNull();
  });

  it("sends the backend's field names and says the password changed", async () => {
    const state = backend();
    renderApp("/");
    const dialog = await openAndFill();
    expect(await within(dialog).findByText(id.auth.change.done)).toBeInTheDocument();
    expect(state.changeBody).toEqual({
      current_password: "kata-sandi-lama!",
      new_password: "kata-sandi-baru-123",
    });
    await userEvent.click(within(dialog).getByRole("button", { name: id.auth.change.close }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("does not call a CSRF refusal a wrong password", async () => {
    backend({ change: () => fail(403, "CSRF_FAILED", "CSRF token missing or invalid") });
    renderApp("/");
    const dialog = await openAndFill();
    expect(await within(dialog).findByText(id.states.error.title)).toBeInTheDocument();
    expect(within(dialog).queryByText(id.auth.change.wrongCurrent)).toBeNull();
  });

  it("says the outcome is unknown when the answer never arrives", async () => {
    backend({ change: () => HttpResponse.error() });
    renderApp("/");
    const dialog = await openAndFill();
    expect(await within(dialog).findByText(id.auth.outcomeUnknown)).toBeInTheDocument();
  });

  it("keeps no password in the mutation cache after the dialog closes", async () => {
    backend();
    const { queryClient } = renderApp("/");
    const dialog = await openAndFill();
    await within(dialog).findByText(id.auth.change.done);
    await userEvent.click(within(dialog).getByRole("button", { name: id.auth.change.close }));
    await waitFor(() =>
      expect(
        JSON.stringify(
          queryClient
            .getMutationCache()
            .getAll()
            .map((m) => m.state.variables),
        ),
      ).not.toContain("kata-sandi"),
    );
  });

  it("says the current password is wrong on 403, and stays signed in", async () => {
    backend({ change: () => fail(403, "FORBIDDEN", "Current password is incorrect") });
    const { router } = renderApp("/projects");
    const dialog = await openAndFill();
    expect(await within(dialog).findByText(id.auth.change.wrongCurrent)).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/projects");
  });

  it("asks to sign in again on 409, and does it from the dialog", async () => {
    const state = backend({ change: () => fail(409, "CONFLICT") });
    const { router } = renderApp("/projects");
    const dialog = await openAndFill();
    expect(await within(dialog).findByText(id.auth.change.conflict)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: id.auth.change.signInAgain }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(state.session).toBe(false);
  });

  it("forgets what was typed when closed", async () => {
    backend({ change: () => fail(403, "FORBIDDEN") });
    renderApp("/");
    const dialog = await openAndFill();
    await within(dialog).findByText(id.auth.change.wrongCurrent);
    await userEvent.click(within(dialog).getByRole("button", { name: id.auth.change.cancel }));
    await userEvent.click(screen.getByRole("button", { name: id.auth.change.open }));
    const reopened = await screen.findByRole("dialog", { name: id.auth.change.title });
    expect(within(reopened).getByLabelText(id.auth.change.current)).toHaveValue("");
    expect(within(reopened).queryByText(id.auth.change.wrongCurrent)).toBeNull();
  });
});

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { API, fail, ok, renderApp, signedOut } from "@/__tests__/harness";
import id from "@/i18n/locales/id.json";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  document.cookie = "kk_csrf=csrf-test; path=/";
});

describe("ForgotPasswordPage", () => {
  function backend(reply: () => Response = () => ok({})) {
    const state = { bodies: [] as unknown[] };
    server.use(
      signedOut(),
      http.post(`${API}/auth/password/forgot`, async ({ request }) => {
        state.bodies.push(await request.json());
        return reply();
      }),
    );
    return state;
  }

  async function submit(email: string) {
    await userEvent.type(screen.getByLabelText(id.auth.email), email);
    await userEvent.click(screen.getByRole("button", { name: id.auth.forgot.submit }));
  }

  it("shows the same message whether or not the email is registered, and focuses it", async () => {
    const state = backend();
    renderApp("/forgot-password");
    await submit("siapa@kodekantor.test");
    expect(await screen.findByText(id.auth.forgot.sent)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("status")).toHaveFocus());
    expect(state.bodies).toEqual([{ email: "siapa@kodekantor.test" }]);
    expect(screen.queryByLabelText(id.auth.email)).toBeNull();
    expect(screen.getByRole("link", { name: id.auth.forgot.backToLogin })).toHaveAttribute("href", "/login");
  });

  it("checks the email locally first", async () => {
    const state = backend();
    renderApp("/forgot-password");
    await submit("tanpa-at");
    expect(screen.getByLabelText(id.auth.email)).toHaveAccessibleDescription(id.auth.errors.emailInvalid);
    expect(state.bodies).toEqual([]);
  });

  it("says to wait on 429 (limited per IP, so it reveals nothing) instead of claiming a link was sent", async () => {
    backend(() => fail(429, "RATE_LIMITED", "Too many", { "Retry-After": "12" }));
    renderApp("/forgot-password");
    await submit("siapa@kodekantor.test");
    expect(await screen.findByText(id.auth.rateLimited_other.replace("{{count}}", "12"))).toBeInTheDocument();
    expect(screen.queryByText(id.auth.forgot.sent)).toBeNull();
  });

  it("shows the disconnected state and keeps the form when the network is down", async () => {
    backend(() => HttpResponse.error());
    renderApp("/forgot-password");
    await submit("siapa@kodekantor.test");
    expect(await screen.findByText(id.states.disconnected.title)).toBeInTheDocument();
    expect(screen.queryByText(id.auth.forgot.sent)).toBeNull();
    expect(screen.getByLabelText(id.auth.email)).toBeInTheDocument();
  });
});

describe("ResetPasswordPage", () => {
  const TOKEN = "tok-3f9a1c7e5b2d";

  function backend(reply: () => Response = () => ok({})) {
    const state = { bodies: [] as unknown[] };
    server.use(
      signedOut(),
      http.post(`${API}/auth/password/reset`, async ({ request }) => {
        state.bodies.push(await request.json());
        return reply();
      }),
    );
    return state;
  }

  async function submit(password: string, confirm = password) {
    await userEvent.type(screen.getByLabelText(id.auth.reset.newPassword), password);
    await userEvent.type(screen.getByLabelText(id.auth.reset.confirmPassword), confirm);
    await userEvent.click(screen.getByRole("button", { name: id.auth.reset.submit }));
  }

  it("takes the token out of the URL and the history, but still sends it", async () => {
    const state = backend();
    const { router } = renderApp(`/reset-password/${TOKEN}`);
    await waitFor(() => expect(router.state.location.pathname).toBe("/reset-password"));
    expect(router.state.historyAction).toBe("REPLACE");
    await submit("kata-sandi-baru-123");
    await waitFor(() =>
      expect(state.bodies).toEqual([{ token: TOKEN, new_password: "kata-sandi-baru-123" }]),
    );
  });

  it("goes to /login with a notice after the reset, keeping no password in the mutation cache", async () => {
    backend();
    const { router, queryClient } = renderApp(`/reset-password/${TOKEN}`);
    await submit("kata-sandi-baru-123");
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(await screen.findByText(id.auth.login.resetDone)).toBeInTheDocument();
    await waitFor(() => expect(queryClient.getMutationCache().getAll()).toEqual([]));
  });

  it("counts characters as the backend does: 11 emoji are too short even though they are 22 UTF-16 units", async () => {
    const state = backend();
    renderApp(`/reset-password/${TOKEN}`);
    await submit("😀".repeat(11));
    expect(screen.getByLabelText(id.auth.reset.newPassword)).toHaveAccessibleDescription(
      id.auth.errors.passwordTooShort,
    );
    expect(state.bodies).toEqual([]);
  });

  it("refuses a confirmation that differs", async () => {
    const state = backend();
    renderApp(`/reset-password/${TOKEN}`);
    await submit("kata-sandi-baru-123", "kata-sandi-baru-124");
    expect(screen.getByLabelText(id.auth.reset.confirmPassword)).toHaveAccessibleDescription(
      id.auth.errors.passwordMismatch,
    );
    expect(state.bodies).toEqual([]);
  });

  it("says the outcome is unknown when the answer never arrives (the password may have changed)", async () => {
    backend(() => HttpResponse.error());
    renderApp(`/reset-password/${TOKEN}`);
    await submit("kata-sandi-baru-123");
    expect(await screen.findByText(id.auth.outcomeUnknown)).toBeInTheDocument();
    expect(screen.getByText(id.states.disconnected.title)).toBeInTheDocument();
  });

  it("keeps the form for a 400 that is not about the token", async () => {
    backend(() => fail(400, "PATH_VIOLATION"));
    renderApp(`/reset-password/${TOKEN}`);
    await submit("kata-sandi-baru-123");
    expect(await screen.findByText(id.states.error.title)).toBeInTheDocument();
    expect(screen.queryByText(id.auth.reset.invalidLink)).toBeNull();
    expect(screen.getByLabelText(id.auth.reset.newPassword)).toBeInTheDocument();
  });

  it("says the link is invalid or expired on 400, and offers a new one", async () => {
    backend(() => fail(400, "BAD_REQUEST", "Reset token is invalid or expired"));
    renderApp(`/reset-password/${TOKEN}`);
    await submit("kata-sandi-baru-123");
    expect(await screen.findByText(id.auth.reset.invalidLink)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: id.auth.reset.requestNew })).toHaveAttribute(
      "href",
      "/forgot-password",
    );
    expect(screen.queryByLabelText(id.auth.reset.newPassword)).toBeNull();
  });

  it("asks to open the emailed link when there is no token", () => {
    backend();
    renderApp("/reset-password");
    expect(screen.getByText(id.auth.reset.noToken)).toBeInTheDocument();
    expect(screen.queryByLabelText(id.auth.reset.newPassword)).toBeNull();
  });
});

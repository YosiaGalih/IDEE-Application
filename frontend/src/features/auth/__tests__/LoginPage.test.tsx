import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { API, deferred, fail, me, ok, renderApp, user } from "@/__tests__/harness";
import { meQueryKey } from "@/api/hooks/useMe";
import { routes } from "@/app/routes";
import id from "@/i18n/locales/id.json";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

/** /auth/me answers 401 until a login succeeds, like the backend. */
function backend(loginReply: () => Response | Promise<Response> = () => ok({ user })) {
  const state = { session: false, logins: 0, body: null as unknown };
  server.use(
    http.get(`${API}/auth/me`, () => (state.session ? ok(me) : fail(401, "UNAUTHENTICATED"))),
    http.post(`${API}/auth/login`, async ({ request }) => {
      state.logins += 1;
      state.body = await request.json();
      const reply = await loginReply();
      if (reply.status === 200) state.session = true;
      return reply;
    }),
  );
  return state;
}

async function fillAndSubmit(email = "tira@kodekantor.test", password = "kata-sandi-panjang") {
  await userEvent.type(screen.getByLabelText(id.auth.email), email);
  await userEvent.type(screen.getByLabelText(id.auth.password), password);
  await userEvent.click(screen.getByRole("button", { name: id.auth.login.submit }));
}

describe("LoginPage", () => {
  it("checks the fields before sending anything, and ties each error to its field", async () => {
    const state = backend();
    renderApp("/login");
    await userEvent.click(screen.getByRole("button", { name: id.auth.login.submit }));

    const email = screen.getByLabelText(id.auth.email);
    expect(email).toHaveAttribute("aria-invalid", "true");
    expect(email).toHaveAccessibleDescription(id.auth.errors.emailRequired);
    expect(screen.getByLabelText(id.auth.password)).toHaveAccessibleDescription(
      id.auth.errors.passwordRequired,
    );
    // The summary takes focus so a keyboard or screen reader user lands on what went wrong.
    expect(screen.getByRole("alert")).toHaveTextContent(id.auth.errorSummary);
    expect(screen.getByRole("alert")).toHaveFocus();

    await userEvent.type(email, "bukan-email");
    await userEvent.click(screen.getByRole("button", { name: id.auth.login.submit }));
    expect(email).toHaveAccessibleDescription(id.auth.errors.emailInvalid);
    expect(state.logins).toBe(0);
  });

  it("accepts any address with an @, as the backend does (an owner may be admin@localhost)", async () => {
    const state = backend();
    const { router } = renderApp("/login");
    await fillAndSubmit("admin@localhost");
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
    expect(state.body).toEqual({
      email: "admin@localhost",
      password: "kata-sandi-panjang",
      remember_me: false,
    });
  });

  it("sends remember_me when ticked", async () => {
    const state = backend();
    renderApp("/login");
    await userEvent.click(screen.getByLabelText(id.auth.rememberMe));
    await fillAndSubmit();
    await waitFor(() => expect(state.body).toMatchObject({ remember_me: true }));
  });

  it.each([
    ["wrong credentials", "Invalid email or password"],
    ["a locked account", "Account locked until 10:15"],
  ])("shows one generic message for %s, never the server's text", async (_case, serverText) => {
    backend(() => fail(401, "UNAUTHENTICATED", serverText));
    const onUnauthenticated = vi.fn();
    const { router } = renderApp("/login?next=/projects", routes, { onUnauthenticated });
    await fillAndSubmit();
    const alert = await screen.findByText(id.auth.login.invalid);
    expect(alert.closest("[role=alert]")).toHaveFocus();
    expect(document.body).not.toHaveTextContent(serverText);
    // A 401 here is not an expired session: the global handler never hears of it, next is kept.
    expect(onUnauthenticated).not.toHaveBeenCalled();
    expect(router.state.location.search).toBe("?next=/projects");
  });

  it("shows the wait from Retry-After on 429", async () => {
    backend(() => fail(429, "RATE_LIMITED", "Too many attempts", { "Retry-After": "37" }));
    renderApp("/login");
    await fillAndSubmit();
    expect(await screen.findByText(id.auth.rateLimited_other.replace("{{count}}", "37"))).toBeInTheDocument();
  });

  it("says try again later on 429 without Retry-After", async () => {
    backend(() => fail(429, "RATE_LIMITED"));
    renderApp("/login");
    await fillAndSubmit();
    expect(await screen.findByText(id.auth.rateLimitedNoTime)).toBeInTheDocument();
  });

  it("shows the degraded state with the request id on 503", async () => {
    backend(() => fail(503, "DEPENDENCY_UNAVAILABLE"));
    renderApp("/login");
    await fillAndSubmit();
    expect(await screen.findByText(id.states.provider_degraded.title)).toBeInTheDocument();
    expect(screen.getByText(id.common.requestId.replace("{{id}}", "r-err-0001"))).toBeInTheDocument();
  });

  it("shows the disconnected state when the network is down", async () => {
    backend(() => HttpResponse.error());
    renderApp("/login");
    await fillAndSubmit();
    expect(await screen.findByText(id.states.disconnected.title)).toBeInTheDocument();
  });

  it("disables the button while signing in, so one click is one attempt", async () => {
    const { gate, release } = deferred();
    const state = backend(async () => {
      await gate;
      return ok({ user });
    });
    const { router } = renderApp("/login");
    await fillAndSubmit();
    const button = await screen.findByRole("button", { name: id.auth.login.submitting });
    expect(button).toBeDisabled();
    await userEvent.type(screen.getByLabelText(id.auth.password), "{Enter}");
    release();
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
    expect(state.logins).toBe(1);
  });

  it("goes to a safe next after signing in, and shows the app with the signed-in user", async () => {
    backend();
    const { router } = renderApp("/login?next=%2Fprojects%3Ftab%3Dtasks");
    await fillAndSubmit();
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects"));
    expect(router.state.location.search).toBe("?tab=tasks");
    expect(
      await screen.findByText(id.auth.menu.signedInAs.replace("{{name}}", user.display_name)),
    ).toBeVisible();
  });

  it.each([
    "//evil.test",
    "https://evil.test/x",
    "/\\evil.test",
    "javascript:alert(1)",
    "/login",
    "/..//evil.test",
  ])("goes to / instead of next=%s", async (next) => {
    backend();
    const { router } = renderApp(`/login?next=${encodeURIComponent(next)}`);
    await fillAndSubmit();
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
  });

  it("drops the cached session when the login page opens, so Back cannot show the last user's data", async () => {
    const state = backend();
    state.session = true;
    const { router, queryClient } = renderApp("/projects");
    await screen.findByText(id.auth.menu.signedInAs.replace("{{name}}", user.display_name));
    queryClient.setQueryData(["projects"], [{ id: "p-old" }]);
    state.session = false;
    await router.navigate("/login");
    await screen.findByRole("button", { name: id.auth.login.submit });
    expect(queryClient.getQueryData(meQueryKey)).toBeUndefined();
    expect(queryClient.getQueryData(["projects"])).toBeUndefined();
  });

  it("drops queries cached before the login once it succeeds (another user may sign in)", async () => {
    backend();
    const { router, queryClient } = renderApp("/login");
    await screen.findByRole("button", { name: id.auth.login.submit });
    queryClient.setQueryData(["projects"], [{ id: "p-old" }]);
    await fillAndSubmit();
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
    expect(queryClient.getQueryData(["projects"])).toBeUndefined();
  });

  it("tells the user the password was reset, once: the notice leaves the history entry", async () => {
    backend();
    const { router } = renderApp({
      pathname: "/login",
      search: "?next=/x",
      state: { notice: "passwordReset" },
    });
    expect(screen.getByText(id.auth.login.resetDone)).toBeInTheDocument();
    await waitFor(() => expect(router.state.location.state).toBeNull());
    expect(router.state.location.search).toBe("?next=/x");
    expect(screen.getByText(id.auth.login.resetDone)).toBeInTheDocument();
  });

  it("ignores a notice it does not know", () => {
    backend();
    renderApp({ pathname: "/login", state: { notice: "<b>hi</b>" } });
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("says so when the server accepted the login but the browser kept no session", async () => {
    const state = backend();
    server.use(
      http.post(`${API}/auth/login`, () => {
        state.logins += 1;
        return ok({ user }); // 200, yet /auth/me stays 401: the cookie was not stored
      }),
    );
    const { router } = renderApp("/login?next=/projects");
    await fillAndSubmit();
    expect(await screen.findByText(id.auth.login.sessionNotStored)).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
    expect(screen.getByRole("alert")).toHaveFocus();
  });

  it("keeps no password in the mutation cache once the page is gone", async () => {
    backend();
    const { router, queryClient } = renderApp("/login");
    await fillAndSubmit();
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
    await waitFor(() => expect(queryClient.getMutationCache().getAll()).toEqual([]));
  });

  it("links to the forgot password page", async () => {
    backend();
    const { router } = renderApp("/login");
    await userEvent.click(screen.getByRole("link", { name: id.auth.login.forgot }));
    expect(router.state.location.pathname).toBe("/forgot-password");
  });
});

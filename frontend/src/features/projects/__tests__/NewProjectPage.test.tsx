import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { API, deferred, fail, ok, okPage, renderApp, signedIn } from "@/__tests__/harness";
import id from "@/i18n/locales/id.json";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  document.cookie = "kk_csrf=csrf-test; path=/";
});

const PROJECT_ID = "6f2c1d3e-0a4b-4c5d-8e9f-0a1b2c3d4e5f";
const TEMPLATES = [
  {
    id: "python-basic",
    name: "Python dasar",
    description: "Kalkulator + pytest",
    stack: "python",
    icon: "code",
  },
  { id: "node-basic", name: "Node dasar", description: "Modul + node:test", stack: "node", icon: "code" },
];

function created(overrides: Record<string, unknown> = {}) {
  return {
    id: PROJECT_ID,
    name: "Kalkulator",
    slug: "kalkulator",
    description: null,
    status: "active",
    stack: {},
    default_branch: "main",
    settings: {},
    owner_id: "0b6a3f1e-6a1f-4e0e-9a51-6c1c8d7f2a10",
    created_at: "2026-11-02T09:30:00+00:00",
    updated_at: "2026-11-02T09:30:00+00:00",
    repository: null,
    permissions: ["project:read", "project:write"],
    stats: { files: null, last_commit: "a".repeat(40), open_tasks: 0 },
    ...overrides,
  };
}

function backend(reply: () => Response | Promise<Response> = () => ok(created())) {
  const state = { bodies: [] as unknown[], keys: [] as (string | null)[] };
  server.use(
    signedIn(),
    http.get(`${API}/projects/templates`, () => ok(TEMPLATES)),
    http.get(`${API}/projects`, () => okPage([])),
    http.post(`${API}/projects`, async ({ request }) => {
      state.bodies.push(await request.json());
      state.keys.push(request.headers.get("Idempotency-Key"));
      const response = await reply();
      return response.status === 200
        ? HttpResponse.json((await response.json()) as Record<string, unknown>, { status: 201 })
        : response;
    }),
  );
  return state;
}

async function fillName(name: string) {
  await userEvent.type(await screen.findByLabelText(id.projects.new.name), name);
}

describe("NewProjectPage", () => {
  it("creates an empty project and opens its workspace", async () => {
    const state = backend();
    const { router } = renderApp("/projects/new");
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(id.projects.new.title);
    await fillName("  Kalkulator  ");
    await userEvent.type(screen.getByLabelText(id.projects.new.description), "Latihan");
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/projects/${PROJECT_ID}/workspace`));
    expect(state.bodies).toEqual([{ name: "Kalkulator", description: "Latihan", source: { type: "blank" } }]);
  });

  it("sends a template the user picked", async () => {
    const state = backend();
    renderApp("/projects/new");
    await fillName("Calc");
    await userEvent.click(screen.getByRole("radio", { name: new RegExp(id.projects.new.template) }));
    await userEvent.click(await screen.findByRole("radio", { name: /Python dasar/ }));
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    await waitFor(() =>
      expect(state.bodies).toEqual([
        { name: "Calc", description: null, source: { type: "template", template_id: "python-basic" } },
      ]),
    );
  });

  it("checks the fields before sending anything", async () => {
    const state = backend();
    renderApp("/projects/new");
    await screen.findByLabelText(id.projects.new.name);
    await userEvent.click(screen.getByRole("radio", { name: new RegExp(id.projects.new.template) }));
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    expect(screen.getByLabelText(id.projects.new.name)).toHaveAccessibleDescription(
      id.projects.errors.nameRequired,
    );
    expect(await screen.findByText(id.projects.errors.templateRequired)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveFocus();
    await fillName("x".repeat(121));
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    expect(screen.getByLabelText(id.projects.new.name)).toHaveAccessibleDescription(
      id.projects.errors.nameTooLong,
    );
    // A bidi override makes a name display as another one: refused, as the backend does.
    await userEvent.clear(screen.getByLabelText(id.projects.new.name));
    await fillName("Tira\u202eevil");
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    expect(screen.getByLabelText(id.projects.new.name)).toHaveAccessibleDescription(
      id.projects.errors.nameInvalid,
    );
    expect(state.bodies).toEqual([]);
  });

  it("puts a server validation error on its field", async () => {
    backend(() =>
      HttpResponse.json(
        {
          request_id: "r-422",
          status: "failed",
          error: {
            code: "VALIDATION_ERROR",
            message: "Input did not pass validation",
            details: [{ loc: ["body", "name"], msg: "must be 1-120 characters" }],
          },
        },
        { status: 422 },
      ),
    );
    renderApp("/projects/new");
    await fillName("Kalkulator");
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    await waitFor(() =>
      expect(screen.getByLabelText(id.projects.new.name)).toHaveAccessibleDescription(
        id.projects.errors.fieldRejected,
      ),
    );
  });

  it("disables the button while creating", async () => {
    const { gate, release } = deferred();
    backend(async () => {
      await gate;
      return ok(created());
    });
    renderApp("/projects/new");
    await fillName("Kalkulator");
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    expect(await screen.findByRole("button", { name: id.projects.new.submitting })).toBeDisabled();
    release();
  });

  it("says the outcome is unknown when the answer is lost, and retries with the same key", async () => {
    let lose = true;
    const state = backend(() => (lose ? HttpResponse.error() : ok(created())));
    const { router } = renderApp("/projects/new");
    await fillName("Kalkulator");
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    expect(await screen.findByText(id.projects.new.outcomeUnknown)).toBeInTheDocument();
    lose = false;
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/projects/${PROJECT_ID}/workspace`));
    // Same body after a lost answer: the server's idempotency store answers once, so no second project.
    expect(state.keys).toHaveLength(2);
    expect(state.keys[0]).toBe(state.keys[1]);
  });

  it("uses a new key once the request changes", async () => {
    let lose = true;
    const state = backend(() => (lose ? HttpResponse.error() : ok(created())));
    renderApp("/projects/new");
    await fillName("Kalkulator");
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    await screen.findByText(id.projects.new.outcomeUnknown);
    lose = false;
    await userEvent.type(screen.getByLabelText(id.projects.new.name), " 2");
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    await waitFor(() => expect(state.keys).toHaveLength(2));
    expect(state.keys[0]).not.toBe(state.keys[1]);
  });

  it("uses a new key after a failure that did not run (503 from a dependency)", async () => {
    let failing = true;
    const state = backend(() => (failing ? fail(503, "DEPENDENCY_UNAVAILABLE") : ok(created())));
    renderApp("/projects/new");
    await fillName("Kalkulator");
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    await screen.findByText(id.states.provider_degraded.title);
    failing = false;
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    await waitFor(() => expect(state.keys).toHaveLength(2));
    expect(state.keys[0]).not.toBe(state.keys[1]);
  });

  it.each([
    ["409 CONFLICT from the idempotency store", () => fail(409, "CONFLICT", "in progress")],
    ["a gateway 502 page", () => new HttpResponse("<html>502 Bad Gateway</html>", { status: 502 })],
  ])("keeps the key and says the outcome is unknown on %s", async (_case, reply) => {
    let first = true;
    const state = backend(() => {
      if (!first) return ok(created());
      first = false;
      return reply();
    });
    const { router } = renderApp("/projects/new");
    await fillName("Kalkulator");
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    expect(await screen.findByText(id.projects.new.outcomeUnknown)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/projects/${PROJECT_ID}/workspace`));
    expect(state.keys[0]).toBe(state.keys[1]);
  });

  it("uses a new key after a git failure (it ran and was rolled back)", async () => {
    let first = true;
    const state = backend(() => {
      if (!first) return ok(created());
      first = false;
      return fail(500, "GIT_ERROR");
    });
    renderApp("/projects/new");
    await fillName("Kalkulator");
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    await screen.findByText(id.states.error.title);
    expect(screen.queryByText(id.projects.new.outcomeUnknown)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    await waitFor(() => expect(state.keys).toHaveLength(2));
    expect(state.keys[0]).not.toBe(state.keys[1]);
  });

  it("marks the project list stale after a failed create, so the list shows what the server has", async () => {
    backend(() => HttpResponse.error());
    const { queryClient } = renderApp("/projects/new");
    queryClient.setQueryData(["projects", "list", { status: "active", q: "" }], []);
    await fillName("Kalkulator");
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    await screen.findByText(id.projects.new.outcomeUnknown);
    await waitFor(() =>
      expect(
        queryClient.getQueryState(["projects", "list", { status: "active", q: "" }])?.isInvalidated,
      ).toBe(true),
    );
  });

  it("counts characters as the backend does (code points), and says the server refused a field", async () => {
    const state = backend(() =>
      HttpResponse.json(
        {
          request_id: "r-422",
          status: "failed",
          error: {
            code: "VALIDATION_ERROR",
            message: "x",
            details: [{ loc: ["body", "description"], msg: "nul" }],
          },
        },
        { status: 422 },
      ),
    );
    renderApp("/projects/new");
    await fillName("😀".repeat(70)); // 140 UTF-16 units, 70 characters: allowed
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    await waitFor(() => expect(state.bodies).toHaveLength(1));
    expect(await screen.findByRole("alert")).toHaveTextContent(id.projects.new.serverFieldSummary);
    expect(screen.getByLabelText(id.projects.new.description)).toHaveAccessibleDescription(
      id.projects.errors.fieldRejected,
    );
  });

  it("says when there is no template to choose", async () => {
    backend();
    server.use(http.get(`${API}/projects/templates`, () => ok([])));
    renderApp("/projects/new");
    await userEvent.click(await screen.findByRole("radio", { name: new RegExp(id.projects.new.template) }));
    expect(await screen.findByText(id.projects.new.noTemplates)).toBeInTheDocument();
  });

  it("shows the degraded state with the request id on 503", async () => {
    backend(() => fail(503, "DEPENDENCY_UNAVAILABLE"));
    renderApp("/projects/new");
    await fillName("Kalkulator");
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    expect(await screen.findByText(id.states.provider_degraded.title)).toBeInTheDocument();
    expect(screen.getByText(id.common.requestId.replace("{{id}}", "r-err-0001"))).toBeInTheDocument();
  });

  it("says when the templates cannot be loaded, and loads them again", async () => {
    let failing = true;
    backend();
    server.use(
      http.get(`${API}/projects/templates`, () => (failing ? fail(500, "INTERNAL_ERROR") : ok(TEMPLATES))),
    );
    renderApp("/projects/new");
    await userEvent.click(await screen.findByRole("radio", { name: new RegExp(id.projects.new.template) }));
    expect(await screen.findByText(id.projects.new.templatesFailed)).toBeInTheDocument();
    failing = false;
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.retryTemplates }));
    expect(await screen.findByRole("radio", { name: /Node dasar/ })).toBeInTheDocument();
  });

  it("goes back to the list on cancel", async () => {
    backend();
    const { router } = renderApp("/projects/new");
    await userEvent.click(await screen.findByRole("link", { name: id.projects.new.cancel }));
    expect(router.state.location.pathname).toBe("/projects");
  });
});

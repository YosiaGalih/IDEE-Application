import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { API, deferred, fail, ok, okPage, pendingApprovals, renderApp, signedIn } from "@/__tests__/harness";
import id from "@/i18n/locales/id.json";

const server = setupServer(pendingApprovals());
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  document.cookie = "kk_csrf=csrf-test; path=/";
});

const OWNER_PERMS = ["project:read", "project:write", "project:delete", "workspace:read"];

function project(overrides: Record<string, unknown> = {}) {
  return {
    id: "6f2c1d3e-0a4b-4c5d-8e9f-0a1b2c3d4e5f",
    name: "Kalkulator",
    slug: "kalkulator",
    description: null,
    status: "active",
    stack: {
      languages: ["python"],
      frameworks: [],
      package_managers: ["pip"],
      test_profiles: [],
      entrypoints: [],
    },
    default_branch: "main",
    settings: {},
    owner_id: "0b6a3f1e-6a1f-4e0e-9a51-6c1c8d7f2a10",
    created_at: "2026-11-01T08:00:00+00:00",
    updated_at: "2026-11-02T09:30:00+00:00",
    repository: {
      provider: "template",
      remote_url: null,
      branch: "main",
      sync_state: "ready",
      last_indexed_at: null,
      error: null,
    },
    permissions: OWNER_PERMS,
    stats: null,
    ...overrides,
  };
}

/** The list endpoint: records each query and answers from `rows`. */
function backend(rows: () => unknown[] | Response) {
  const state = { queries: [] as URLSearchParams[], archived: [] as string[] };
  server.use(
    signedIn(),
    http.get(`${API}/projects`, ({ request }) => {
      state.queries.push(new URL(request.url).searchParams);
      const result = rows();
      return result instanceof Response ? result : okPage(result);
    }),
    http.post(`${API}/projects/:id/:action`, ({ params }) => {
      state.archived.push(`${String(params.action)}:${String(params.id)}`);
      return ok(project({ status: params.action === "archive" ? "archived" : "active" }));
    }),
  );
  return state;
}

describe("ProjectsPage", () => {
  it("shows loading, then the projects with name link, stack and status as text", async () => {
    const { gate, release } = deferred();
    backend(() => [project()]);
    server.use(
      http.get(`${API}/projects`, async () => {
        await gate;
        return okPage([project()]);
      }),
    );
    renderApp("/projects");
    expect(await screen.findByRole("status")).toHaveTextContent(id.states.loading.title);
    release();
    const table = await screen.findByRole("table", { name: id.projects.table.caption });
    const row = within(table).getAllByRole("row")[1];
    expect(row).toBeDefined();
    const link = within(row as HTMLElement).getByRole("link", { name: "Kalkulator" });
    expect(link).toHaveAttribute("href", `/projects/${project().id}/workspace`);
    expect(row).toHaveTextContent("python");
    expect(row).toHaveTextContent(id.projects.status.active);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(id.screens.projects);
  });

  it("asks for active projects by default, and sends the status filter and the search", async () => {
    const state = backend(() => []);
    renderApp("/projects");
    await screen.findByText(id.projects.empty.title);
    expect(state.queries.at(-1)?.get("status")).toBe("active");
    expect(state.queries.at(-1)?.has("q")).toBe(false);

    await userEvent.selectOptions(screen.getByLabelText(id.projects.statusFilter.label), "archived");
    await waitFor(() => expect(state.queries.at(-1)?.get("status")).toBe("archived"));

    await userEvent.type(screen.getByLabelText(id.projects.search.label), "kalk");
    await userEvent.click(screen.getByRole("button", { name: id.projects.search.submit }));
    await waitFor(() => expect(state.queries.at(-1)?.get("q")).toBe("kalk"));
    expect(await screen.findByText(id.projects.empty.filteredTitle)).toBeInTheDocument();
  });

  it("marks the old rows as busy while a new filter loads", async () => {
    const { gate, release } = deferred();
    let first = true;
    backend(() => [project()]);
    server.use(
      http.get(`${API}/projects`, async () => {
        if (!first) await gate;
        first = false;
        return okPage([project()]);
      }),
    );
    renderApp("/projects");
    const table = await screen.findByRole("table");
    expect(table.parentElement).toHaveAttribute("aria-busy", "false");
    await userEvent.selectOptions(screen.getByLabelText(id.projects.statusFilter.label), "all");
    await waitFor(() => expect(table.parentElement).toHaveAttribute("aria-busy", "true"));
    release();
    await waitFor(() =>
      expect(screen.getByRole("table").parentElement).toHaveAttribute("aria-busy", "false"),
    );
  });

  it("keeps the search box in step with the URL, and a status change applies what is typed", async () => {
    const state = backend(() => [project()]);
    const { router } = renderApp("/projects?q=kalk");
    const box = await screen.findByLabelText(id.projects.search.label);
    expect(box).toHaveValue("kalk");
    await userEvent.click(screen.getByRole("navigation").querySelector('a[href="/projects"]') as HTMLElement);
    await waitFor(() => expect(router.state.location.search).toBe(""));
    expect(box).toHaveValue("");
    await userEvent.type(box, "foo");
    await userEvent.selectOptions(screen.getByLabelText(id.projects.statusFilter.label), "all");
    await waitFor(() => expect(router.state.location.search).toBe("?status=all&q=foo"));
    expect(state.queries.at(-1)?.get("q")).toBe("foo");
  });

  it("shows loading, never 'no projects', while a new filter loads over an empty list", async () => {
    const { gate, release } = deferred();
    let calls = 0;
    backend(() => []);
    server.use(
      http.get(`${API}/projects`, async () => {
        calls += 1;
        if (calls === 1) return okPage([]);
        await gate;
        return okPage([project()]);
      }),
    );
    renderApp("/projects?status=archived");
    await screen.findByText(id.projects.empty.filteredTitle);
    await userEvent.selectOptions(screen.getByLabelText(id.projects.statusFilter.label), "active");
    expect(await screen.findByRole("status")).toHaveTextContent(id.states.loading.title);
    expect(screen.queryByText(id.projects.empty.title)).toBeNull();
    release();
    expect(await screen.findByRole("link", { name: "Kalkulator" })).toBeInTheDocument();
  });

  it("says a project was archived and moves focus to the heading", async () => {
    backend(() => [project()]);
    renderApp("/projects");
    await userEvent.click(
      await screen.findByRole("button", { name: id.projects.archiveOf.replace("{{name}}", "Kalkulator") }),
    );
    expect(
      await screen.findByText(id.projects.archived.replace("{{name}}", "Kalkulator")),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveFocus();
  });

  it("marks a project whose repository has a problem", async () => {
    backend(() => [
      project({
        repository: {
          provider: "local",
          remote_url: null,
          branch: "main",
          sync_state: "error",
          last_indexed_at: null,
          error: "repository_missing",
        },
      }),
    ]);
    renderApp("/projects");
    expect(await screen.findByText(id.projects.repositoryProblem)).toBeInTheDocument();
  });

  it("offers a first project when there is none", async () => {
    backend(() => []);
    const { router } = renderApp("/projects");
    expect(await screen.findByText(id.projects.empty.title)).toBeInTheDocument();
    const links = screen.getAllByRole("link", { name: id.projects.newProject });
    await userEvent.click(links.at(-1) as HTMLElement);
    expect(router.state.location.pathname).toBe("/projects/new");
  });

  it("shows the error with a retry and the request id", async () => {
    let failing = true;
    backend(() => (failing ? fail(500, "INTERNAL_ERROR") : [project()]));
    renderApp("/projects");
    expect(await screen.findByRole("alert")).toHaveTextContent(id.states.error.title);
    failing = false;
    await userEvent.click(screen.getByRole("button", { name: id.states.error.action }));
    expect(await screen.findByRole("link", { name: "Kalkulator" })).toBeInTheDocument();
  });

  it("archives only where the user may write, then reloads the list", async () => {
    const readOnly = project({
      id: "7a2c1d3e-0a4b-4c5d-8e9f-0a1b2c3d4e5f",
      name: "Shared",
      permissions: ["project:read"],
    });
    const state = backend(() => [project(), readOnly]);
    renderApp("/projects");
    await screen.findByRole("link", { name: "Kalkulator" });
    expect(
      screen.queryByRole("button", { name: id.projects.archiveOf.replace("{{name}}", "Shared") }),
    ).toBeNull();
    const before = state.queries.length;
    await userEvent.click(
      screen.getByRole("button", { name: id.projects.archiveOf.replace("{{name}}", "Kalkulator") }),
    );
    await waitFor(() => expect(state.archived).toEqual([`archive:${project().id}`]));
    await waitFor(() => expect(state.queries.length).toBeGreaterThan(before));
  });

  it("says why when archiving fails, and keeps the list", async () => {
    backend(() => [project()]);
    server.use(http.post(`${API}/projects/:id/archive`, () => fail(409, "INVALID_TRANSITION")));
    renderApp("/projects");
    await userEvent.click(
      await screen.findByRole("button", { name: id.projects.archiveOf.replace("{{name}}", "Kalkulator") }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(id.projects.archiveConflict);
    expect(screen.getByRole("link", { name: "Kalkulator" })).toBeInTheDocument();
  });

  it("shows an archived project with its status text and a restore action", async () => {
    const state = backend(() => [project({ status: "archived" })]);
    renderApp("/projects?status=archived");
    const row = (await screen.findByRole("link", { name: "Kalkulator" })).closest("tr");
    expect(row).toHaveTextContent(id.projects.status.archived);
    expect(screen.getByLabelText(id.projects.statusFilter.label)).toHaveValue("archived");
    await userEvent.click(
      screen.getByRole("button", { name: id.projects.unarchiveOf.replace("{{name}}", "Kalkulator") }),
    );
    await waitFor(() => expect(state.archived).toEqual([`unarchive:${project().id}`]));
  });

  it("refuses a list that does not match the contract instead of showing it", async () => {
    backend(() => [{ ...project(), id: "not-a-uuid" }]);
    renderApp("/projects");
    expect(await screen.findByRole("alert")).toHaveTextContent(id.states.error.title);
  });

  it("refuses a list without its page meta (a later page would look like the end)", async () => {
    backend(() => ok([project()]));
    renderApp("/projects");
    expect(await screen.findByRole("alert")).toHaveTextContent(id.states.error.title);
  });

  it("pages through a long list, and a new filter starts at its first page", async () => {
    const state = backend(() => []);
    server.use(
      http.get(`${API}/projects`, ({ request }) => {
        const params = new URL(request.url).searchParams;
        state.queries.push(params);
        const page = Number(params.get("page"));
        return okPage([project({ name: `P${page}` })], { page, total: 120 });
      }),
    );
    const { router } = renderApp("/projects");
    const pager = await screen.findByRole("navigation", { name: id.projects.pagination.label });
    expect(pager).toHaveTextContent(
      id.projects.pagination.status
        .replace("{{page}}", "1")
        .replace("{{pages}}", "3")
        .replace("{{total}}", "120"),
    );
    expect(state.queries.at(-1)?.get("page_size")).toBe("50");
    expect(within(pager).getByRole("button", { name: id.projects.pagination.previous })).toBeDisabled();
    await userEvent.click(within(pager).getByRole("button", { name: id.projects.pagination.next }));
    await waitFor(() => expect(router.state.location.search).toBe("?page=2"));
    expect(await screen.findByRole("link", { name: "P2" })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText(id.projects.statusFilter.label), "all");
    await waitFor(() => expect(router.state.location.search).toBe("?status=all"));
    expect(state.queries.at(-1)?.get("page")).toBe("1");
  });

  it("moves to the last page with rows when the page is past the end", async () => {
    const state = backend(() => []);
    server.use(
      http.get(`${API}/projects`, ({ request }) => {
        const params = new URL(request.url).searchParams;
        state.queries.push(params);
        const page = Number(params.get("page"));
        return okPage(page > 2 ? [] : [project()], { page, total: 60 });
      }),
    );
    const { router } = renderApp("/projects?page=5");
    expect(await screen.findByRole("link", { name: "Kalkulator" })).toBeInTheDocument();
    expect(router.state.location.search).toBe("?page=2");
    expect(screen.queryByText(id.projects.empty.title)).toBeNull();
  });

  it("is reachable from the sidebar", async () => {
    backend(() => [project()]);
    const { router } = renderApp("/");
    await userEvent.click(await screen.findByRole("link", { name: id.nav.projects }));
    expect(router.state.location.pathname).toBe("/projects");
    expect(await screen.findByRole("link", { name: "Kalkulator" })).toBeInTheDocument();
  });

  it("shows the degraded state on 503, after the retries for a 5xx", async () => {
    backend(() => fail(503, "DEPENDENCY_UNAVAILABLE"));
    renderApp("/projects");
    expect(await screen.findByRole("alert", undefined, { timeout: 4000 })).toHaveTextContent(
      id.states.provider_degraded.title,
    );
  });
});

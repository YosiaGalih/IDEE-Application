import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { dashboardData, dashboardMeta, project, task } from "@/__tests__/dashboardFixtures";
import { API, deferred, fail, renderApp, signedIn, user } from "@/__tests__/harness";
import id from "@/i18n/locales/id.json";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  document.cookie = "kk_csrf=csrf-test; path=/";
});

const answer = (data: unknown = dashboardData(), meta: unknown = dashboardMeta()) =>
  HttpResponse.json({ request_id: "r-dash-0001", status: "completed", data, meta });

/** `GET /dashboard`, counting the requests. */
function backend(respond: () => Response | Promise<Response> = () => answer()) {
  const state = { requests: 0 };
  server.use(
    signedIn(),
    http.get(`${API}/dashboard`, async () => {
      state.requests += 1;
      return respond();
    }),
  );
  return state;
}

const region = (name: string) => screen.getByRole("region", { name });

describe("DashboardPage", () => {
  it("shows metrics, recent work and the attention queue", async () => {
    backend();
    renderApp("/");
    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: id.dashboard.greeting.replace("{{name}}", user.display_name),
      }),
    ).toBeInTheDocument();
    const metrics = await screen.findByRole("region", { name: id.dashboard.metrics.title });
    expect(within(metrics).getByText(id.dashboard.metrics.completed_tasks).parentElement).toHaveTextContent(
      "3",
    );
    expect(within(metrics).getByText(id.dashboard.metrics.failed_tasks).parentElement).toHaveTextContent("1");
    const recent = region(id.dashboard.recentTasks.title);
    expect(within(recent).getByRole("link", { name: "Tambahkan fungsi multiply" })).toHaveAttribute(
      "href",
      `/tasks/${task().id}`,
    );
    expect(within(recent).getByText(id.status.failed)).toBeInTheDocument();
    const queue = region(id.dashboard.queue.title);
    expect(within(queue).getByText("tests failed")).toBeInTheDocument();
    expect(
      within(queue).getByRole("link", {
        name: id.dashboard.queue.openOf.replace("{{title}}", "Tambahkan fungsi multiply"),
      }),
    ).toHaveAttribute("href", `/tasks/${task().id}`);
    const projects = region(id.dashboard.recentProjects.title);
    expect(within(projects).getByRole("link", { name: "Kalkulator" })).toHaveAttribute(
      "href",
      `/projects/${project().id}/workspace`,
    );
  });

  it("never shows a number for what is not recorded", async () => {
    backend();
    renderApp("/");
    const agents = await screen.findByRole("region", { name: id.dashboard.agents.title });
    expect(within(agents).getByText(id.dashboard.agents.body)).toBeInTheDocument();
    expect(within(agents).queryByText(/\d/)).toBeNull();
    const usage = region(id.dashboard.usage.title);
    expect(within(usage).getAllByText(id.dashboard.unavailable)).toHaveLength(3);
    expect(within(usage).queryByText("0")).toBeNull();
  });

  it("shows how many more need attention", async () => {
    backend(() => answer(dashboardData(), dashboardMeta({ failed_queue_total: 14 })));
    renderApp("/");
    const queue = await screen.findByRole("region", { name: id.dashboard.queue.title });
    expect(within(queue).getByText(id.dashboard.queue.more.replace("{{count}}", "13"))).toBeInTheDocument();
  });

  it("filters recent work without asking the server again", async () => {
    const data = dashboardData();
    data.recent_tasks = [
      task(),
      task({ id: "2b3c4d5e-6f7a-4b8c-9d0e-1f2a3b4c5d6e", title: "Perbaiki login" }),
    ];
    data.recent_projects = [
      project(),
      project({ id: "7a3d2e4f-1b5c-4d6e-9f0a-1b2c3d4e5f60", name: "Toko", slug: "toko" }),
    ];
    const state = backend(() => answer(data));
    renderApp("/");
    const recent = await screen.findByRole("region", { name: id.dashboard.recentTasks.title });
    expect(within(recent).getAllByRole("link")).toHaveLength(2);
    await userEvent.type(screen.getByRole("searchbox", { name: id.dashboard.searchLabel }), "login");
    expect(
      within(recent)
        .getAllByRole("link")
        .map((link) => link.textContent),
    ).toEqual(["Perbaiki login"]);
    expect(
      within(region(id.dashboard.recentProjects.title)).getByText(id.dashboard.recentProjects.filteredEmpty),
    ).toBeInTheDocument();
    expect(
      within(region(id.dashboard.queue.title)).getByText(id.dashboard.queue.filteredEmpty),
    ).toBeInTheDocument();
    expect(state.requests).toBe(1);
  });

  it("offers a new project and explains why tasks cannot be created yet", async () => {
    backend();
    renderApp("/");
    expect(await screen.findByRole("link", { name: id.dashboard.newProject })).toHaveAttribute(
      "href",
      "/projects/new",
    );
    const newTask = screen.getByRole("button", { name: id.dashboard.newTask });
    expect(newTask).toBeDisabled();
    expect(newTask).toHaveAccessibleDescription(id.dashboard.newTaskUnavailable);
  });

  it("invites the first project when there is none", async () => {
    const data = dashboardData();
    data.metrics = {
      active_projects: 0,
      running_tasks: 0,
      completed_tasks: 0,
      failed_tasks: 0,
      waiting_approval: 0,
    };
    data.recent_projects = [];
    data.recent_tasks = [];
    data.failed_queue = [];
    backend(() => answer(data, dashboardMeta({ failed_queue_total: 0 })));
    renderApp("/");
    expect(await screen.findByText(id.dashboard.empty.title)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: id.dashboard.empty.action })).toHaveAttribute(
      "href",
      "/projects/new",
    );
  });

  it("shows a loading state until the answer arrives", async () => {
    const gate = deferred();
    backend(async () => {
      await gate.gate;
      return answer();
    });
    renderApp("/");
    expect(await screen.findByRole("status", { busy: true })).toBeInTheDocument();
    gate.release();
    expect(await screen.findByRole("region", { name: id.dashboard.metrics.title })).toBeInTheDocument();
  });

  it("shows the permission state when the server refuses", async () => {
    let calls = 0;
    backend(() => {
      calls += 1;
      return calls === 1 ? fail(403, "FORBIDDEN") : answer();
    });
    renderApp("/");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(id.states.permission_denied.title);
    expect(screen.queryByRole("region", { name: id.dashboard.metrics.title })).toBeNull();
  });

  it("shows the disconnected state with a retry that asks again", async () => {
    let calls = 0;
    backend(() => {
      calls += 1;
      return calls <= 4 ? HttpResponse.error() : answer();
    });
    renderApp("/");
    const retry = await screen.findByRole(
      "button",
      { name: id.states.disconnected.action },
      { timeout: 4000 },
    );
    await userEvent.click(retry);
    expect(await screen.findByRole("region", { name: id.dashboard.metrics.title })).toBeInTheDocument();
  });

  it("treats a malformed answer as an error, not an empty page", async () => {
    const data = dashboardData() as Record<string, unknown>;
    delete data.failed_queue;
    backend(() => answer(data));
    renderApp("/");
    expect(await screen.findByRole("alert", undefined, { timeout: 4000 })).toHaveTextContent(
      id.states.error.title,
    );
    await waitFor(() => expect(screen.queryByText(id.dashboard.empty.title)).toBeNull());
  });
});

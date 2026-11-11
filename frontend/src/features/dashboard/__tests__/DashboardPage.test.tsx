import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { agentRun, dashboardData, dashboardMeta, project, task } from "@/__tests__/dashboardFixtures";
import { API, deferred, fail, pendingApprovals, renderApp, signedIn, user } from "@/__tests__/harness";
import en from "@/i18n/locales/en.json";
import id from "@/i18n/locales/id.json";

const server = setupServer(pendingApprovals());
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
    // Each metric has its own fixture value: the exact number must sit under its own label.
    const shown = Object.fromEntries(
      within(metrics)
        .getAllByRole("listitem")
        .map((item): [string, string] => [
          item.firstElementChild?.firstElementChild?.textContent ?? "",
          item.textContent ?? "",
        ]),
    );
    expect(shown).toEqual({
      [id.dashboard.metrics.active_projects]: `${id.dashboard.metrics.active_projects}2`,
      [id.dashboard.metrics.running_tasks]: `${id.dashboard.metrics.running_tasks}4`,
      [id.dashboard.metrics.completed_tasks]: `${id.dashboard.metrics.completed_tasks}3`,
      [id.dashboard.metrics.failed_tasks]: `${id.dashboard.metrics.failed_tasks}1`,
      [id.dashboard.metrics.waiting_approval]: `${id.dashboard.metrics.waiting_approval}5`,
    });
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

  it("says not available for the parts the server lists, never a number", async () => {
    backend();
    renderApp("/");
    const agents = await screen.findByRole("region", { name: id.dashboard.agents.title });
    expect(within(agents).getByText(id.dashboard.agents.body)).toBeInTheDocument();
    expect(within(agents).getByText(id.dashboard.unavailable)).toBeInTheDocument();
    const usage = region(id.dashboard.usage.title);
    const values = within(usage).getAllByRole("definition");
    expect(values.map((value) => value.textContent)).toEqual(Array(3).fill(id.dashboard.unavailable));
  });

  it("shows recorded usage with how it was obtained, and tokens next to an unknown cost", async () => {
    const data = dashboardData();
    data.usage = {
      ...data.usage,
      input_tokens: 275306,
      output_tokens: 4143,
      estimated_cost: "0.5",
      cost_status: "estimated",
    };
    backend(() => answer(data, dashboardMeta({ unavailable: ["agent_activity"] })));
    renderApp("/");
    const usage = await screen.findByRole("region", { name: id.dashboard.usage.title });
    const [input, output, cost] = within(usage).getAllByRole("definition");
    expect(input).toHaveTextContent(`275.306${id.dashboard.costStatus.estimated}`);
    expect(output).toHaveTextContent(`4.143${id.dashboard.costStatus.estimated}`);
    expect(cost).toHaveTextContent(/0,50/);
    expect(cost).toHaveTextContent(id.dashboard.costStatus.estimated);
  });

  it("shows an unknown cost as not available, next to the counted tokens", async () => {
    const data = dashboardData();
    data.usage = {
      ...data.usage,
      input_tokens: 0,
      output_tokens: 12,
      estimated_cost: null,
      cost_status: "unavailable",
    };
    backend(() => answer(data, dashboardMeta({ unavailable: ["agent_activity"] })));
    renderApp("/");
    const usage = await screen.findByRole("region", { name: id.dashboard.usage.title });
    const [input, output, cost] = within(usage).getAllByRole("definition");
    expect(input).toHaveTextContent(/^0$/); // a counted 0 is a real 0
    expect(output).toHaveTextContent(/^12$/);
    expect(cost).toHaveTextContent(id.dashboard.unavailable);
  });

  it("shows a tiny cost as less than a cent, never as zero", async () => {
    const data = dashboardData();
    data.usage = {
      ...data.usage,
      input_tokens: 0,
      output_tokens: 1,
      estimated_cost: "0.0000005",
      cost_status: "estimated",
    };
    backend(() => answer(data, dashboardMeta({ unavailable: ["agent_activity"] })));
    renderApp("/");
    const usage = await screen.findByRole("region", { name: id.dashboard.usage.title });
    const cost = within(usage).getAllByRole("definition")[2];
    expect(cost).toHaveTextContent(`< US$0,01${id.dashboard.costStatus.estimated}`);
    expect(cost).not.toHaveTextContent("US$0,00");
  });

  it("names a partial total a partial estimate (API_CONTRACTS §11 #3)", async () => {
    const data = dashboardData();
    data.usage = {
      ...data.usage,
      input_tokens: 160,
      output_tokens: 25,
      estimated_cost: "0.5",
      cost_status: "partial",
    };
    backend(() => answer(data, dashboardMeta({ unavailable: ["agent_activity"] })));
    renderApp("/");
    const usage = await screen.findByRole("region", { name: id.dashboard.usage.title });
    const cost = within(usage).getAllByRole("definition")[2];
    expect(cost).toHaveTextContent("US$0,50estimasi sebagian");
    expect(en.dashboard.costStatus.partial).toBe("partial estimate");
  });

  it("rounds a cost up to the cent, never down", async () => {
    const data = dashboardData();
    data.usage = {
      ...data.usage,
      input_tokens: 1,
      output_tokens: 1,
      estimated_cost: "1.001",
      cost_status: "estimated",
    };
    backend(() => answer(data, dashboardMeta({ unavailable: ["agent_activity"] })));
    renderApp("/");
    const usage = await screen.findByRole("region", { name: id.dashboard.usage.title });
    const cost = within(usage).getAllByRole("definition")[2];
    expect(cost).toHaveTextContent(`US$1,01${id.dashboard.costStatus.estimated}`);
  });

  it("shows a counted zero cost as zero", async () => {
    const data = dashboardData();
    data.usage = {
      ...data.usage,
      input_tokens: 0,
      output_tokens: 0,
      estimated_cost: "0",
      cost_status: "estimated",
    };
    backend(() => answer(data, dashboardMeta({ unavailable: ["agent_activity"] })));
    renderApp("/");
    const usage = await screen.findByRole("region", { name: id.dashboard.usage.title });
    const cost = within(usage).getAllByRole("definition")[2];
    expect(cost).toHaveTextContent(`US$0,00${id.dashboard.costStatus.estimated}`);
    expect(cost).not.toHaveTextContent("<");
  });

  it("lists agent runs once they are recorded, and says when there are none", async () => {
    const data = dashboardData();
    data.agent_activity = [agentRun()];
    backend(() => answer(data, dashboardMeta({ unavailable: ["usage"] })));
    const { unmount } = renderApp("/");
    const agents = await screen.findByRole("region", { name: id.dashboard.agents.title });
    expect(within(agents).getByRole("link", { name: "General Code Implementation Agent" })).toHaveAttribute(
      "href",
      `/tasks/${agentRun().task_id}`,
    );
    expect(within(agents).queryByText(id.dashboard.unavailable)).toBeNull();
    unmount();
    data.agent_activity = [];
    renderApp("/");
    const empty = await screen.findByRole("region", { name: id.dashboard.agents.title });
    expect(within(empty).getByText(id.dashboard.agents.empty)).toBeInTheDocument();
  });

  it("shows a project whose repository has a problem as such, not as healthy", async () => {
    const data = dashboardData();
    const broken = project();
    data.recent_projects = [
      { ...broken, repository: { ...broken.repository, sync_state: "error", error: "repository_missing" } },
    ];
    backend(() => answer(data));
    renderApp("/");
    const projects = await screen.findByRole("region", { name: id.dashboard.recentProjects.title });
    expect(within(projects).getByText(id.dashboard.recentProjects.repositoryProblem)).toBeInTheDocument();
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
    // Case does not matter, and the slug counts too.
    const box = screen.getByRole("searchbox", { name: id.dashboard.searchLabel });
    await userEvent.clear(box);
    await userEvent.type(box, "TOKO");
    expect(
      within(region(id.dashboard.recentProjects.title)).getByRole("link", { name: "Toko" }),
    ).toBeInTheDocument();
    expect(state.requests).toBe(1);
  });

  it("never says nothing matches about queue items it did not load", async () => {
    backend(() => answer(dashboardData(), dashboardMeta({ failed_queue_total: 14 })));
    renderApp("/");
    await screen.findByRole("region", { name: id.dashboard.queue.title });
    await userEvent.type(screen.getByRole("searchbox", { name: id.dashboard.searchLabel }), "pembayaran");
    expect(
      within(region(id.dashboard.queue.title)).getByText(
        id.dashboard.queue.filteredEmptyPartial.replace("{{shown}}", "1").replace("{{more}}", "13"),
      ),
    ).toBeInTheDocument();
  });

  it("keeps the attention queue when the only project is archived", async () => {
    const data = dashboardData();
    data.metrics = {
      active_projects: 0,
      running_tasks: 0,
      completed_tasks: 0,
      failed_tasks: 1,
      waiting_approval: 0,
    };
    data.recent_projects = [];
    data.recent_tasks = [];
    backend(() => answer(data));
    renderApp("/");
    expect(await screen.findByRole("region", { name: id.dashboard.queue.title })).toBeInTheDocument();
    expect(screen.queryByText(id.dashboard.empty.title)).toBeNull();
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
    expect(within(alert).queryByRole("button")).toBeNull(); // retrying cannot grant the right
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

describe("DashboardPage with hostile content", () => {
  it("renders titles, names and reasons as text, never as markup (AG-13 I6)", async () => {
    const payload =
      '<img src=x onerror="document.body.dataset.pwned=1"><script>document.body.dataset.pwned=2</script>';
    const data = dashboardData();
    data.recent_tasks = [task({ title: payload, project_name: payload, status_reason: payload })];
    data.failed_queue = [task({ title: payload, project_name: payload, status_reason: payload })];
    const shown = project({ name: payload });
    data.recent_projects = [
      { ...shown, repository: { ...shown.repository, sync_state: "error", error: payload } },
    ];
    backend(() => answer(data));
    renderApp("/");
    const queue = await screen.findByRole("region", { name: id.dashboard.queue.title });
    expect(within(queue).getAllByText(payload).length).toBeGreaterThan(0);
    expect(document.querySelectorAll("main script")).toHaveLength(0);
    expect(document.querySelectorAll('main img[src="x"]')).toHaveLength(0);
    expect(document.body.dataset.pwned).toBeUndefined();
  });
});

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { agent, catalog, counts } from "@/__tests__/agentFixtures";
import { API, deferred, fail, renderApp, signedIn } from "@/__tests__/harness";
import type { Agent } from "@/api/agents";
import id from "@/i18n/locales/id.json";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const listAnswer = (agents: Agent[] = catalog(), meta?: unknown) =>
  HttpResponse.json({
    request_id: "r-agents-01",
    status: "completed",
    data: agents,
    meta: meta ?? { total: agents.length, counts: counts(agents) },
  });

/** Each GET /agents is answered by `respond`; `calls()` says how many arrived. */
function backend(respond: () => Response | Promise<Response> = () => listAnswer()) {
  let calls = 0;
  server.use(
    signedIn(),
    http.get(`${API}/agents`, () => {
      calls += 1;
      return respond();
    }),
  );
  return { calls: () => calls };
}

const card = (name: string) => screen.getByRole("link", { name: new RegExp(name) });
/** The h1 renders before the data; the "all" chip only once the catalog has loaded. */
const loaded = () => screen.findByRole("button", { name: `${id.agents.filters.all} (4)` });

describe("AgentsPage", () => {
  it("shows every group with its agents and the mode counts", async () => {
    backend();
    renderApp("/agents");
    await loaded();
    expect(screen.getByRole("heading", { level: 1, name: id.screens.agents })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 2, name: new RegExp(id.agents.groups.planning_intent) }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 2, name: new RegExp(id.agents.groups.git_collaboration) }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: `${id.agents.modes.llm} (1)` })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.getByRole("button", { name: `${id.agents.filters.all} (4)` })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(card("Requirement Analyst")).toHaveAttribute("href", "/agents/A02");
  });

  it("filters by mode chip, status and search", async () => {
    backend();
    renderApp("/agents");
    await loaded();
    await userEvent.click(screen.getByRole("button", { name: `${id.agents.modes.tool} (1)` }));
    expect(card("Git Operator")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Requirement Analyst/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: `${id.agents.filters.all} (4)` }));
    await userEvent.selectOptions(screen.getByLabelText(id.agents.filters.status), "planned");
    expect(card("Release Manager")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Git Operator/ })).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText(id.agents.filters.status), "");
    await userEvent.type(screen.getByLabelText(id.agents.filters.search), "git.commit");
    expect(card("Git Operator")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Intent Classifier/ })).not.toBeInTheDocument();
  });

  it("says why an agent is not ready, in words and with an icon", async () => {
    backend();
    renderApp("/agents");
    const analyst = await screen.findByRole("link", { name: /Requirement Analyst/ });
    expect(within(analyst).getByText(id.agents.status.not_ready)).toBeInTheDocument();
    expect(within(analyst).getByText(new RegExp(id.agents.reasons.runtime_unavailable))).toBeInTheDocument();
    expect(
      within(analyst).getByText(
        new RegExp(id.agents.reasons.tools_unavailable_other.replace("{{count}}", "2")),
      ),
    ).toBeInTheDocument();
    expect(within(analyst).getByTestId("status-icon")).toBeInTheDocument();
  });

  it("derives the approval statement from the data", async () => {
    backend();
    renderApp("/agents");
    expect(await screen.findByText(id.agents.approvalStatement)).toBeInTheDocument();
  });

  it("warns instead when an agent could grant approvals", async () => {
    const agents = catalog();
    agents[0] = agent({ permission_profile: { ...agents[0]!.permission_profile, approval_grant: "allow" } });
    backend(() => listAnswer(agents));
    renderApp("/agents");
    expect(
      await screen.findByText(id.agents.approvalWarning_one.replace("{{count}}", "1")),
    ).toBeInTheDocument();
    expect(screen.queryByText(id.agents.approvalStatement)).not.toBeInTheDocument();
  });

  it("shows the empty state when no agent matches", async () => {
    backend();
    renderApp("/agents");
    await loaded();
    await userEvent.type(screen.getByLabelText(id.agents.filters.search), "nothing-like-this");
    expect(screen.getByText(id.agents.noMatch.title)).toBeInTheDocument();
  });

  it("shows an error with the request id, and a retry that asks again", async () => {
    let healthy = false;
    const api = backend(() => (healthy ? listAnswer() : fail(500, "INTERNAL", "boom")));
    renderApp("/agents");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(id.states.error.title);
    expect(alert).toHaveTextContent(id.common.requestId.replace("{{id}}", "r-err-0001"));
    const failedCalls = api.calls();
    healthy = true;
    await userEvent.click(within(alert).getByRole("button", { name: id.states.error.action }));
    await loaded();
    expect(api.calls()).toBe(failedCalls + 1);
  });

  it("shows a loading state until the catalog arrives", async () => {
    const wait = deferred();
    backend(async () => {
      await wait.gate;
      return listAnswer();
    });
    renderApp("/agents");
    expect(await screen.findByRole("status", { busy: true })).toBeInTheDocument();
    wait.release();
    await loaded();
  });

  it("shows the permission state, without a retry, when the server refuses", async () => {
    backend(() => fail(403, "FORBIDDEN"));
    renderApp("/agents");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(id.states.permission_denied.title);
    expect(within(alert).queryByRole("button")).toBeNull();
  });

  it("states the totals, with the disabled agents", async () => {
    const agents = [
      ...catalog(),
      agent({
        agent_id: "B09",
        slug: "code_writer",
        name: "Code Writer",
        enabled: false,
        status: "disabled",
        status_reasons: [],
      }),
    ];
    backend(() => listAnswer(agents));
    renderApp("/agents");
    await screen.findByRole("button", { name: `${id.agents.filters.all} (5)` });
    expect(
      screen.getByText(
        id.agents.subtitle_other
          .replace("{{count}}", "5")
          .replace("{{ready}}", "0")
          .replace("{{notReady}}", "3")
          .replace("{{disabled}}", "1")
          .replace("{{planned}}", "1"),
      ),
    ).toBeInTheDocument();
  });

  it("makes no approval statement about an empty catalog", async () => {
    backend(() => listAnswer([]));
    renderApp("/agents");
    expect(await screen.findByRole("button", { name: `${id.agents.filters.all} (0)` })).toBeInTheDocument();
    expect(screen.queryByText(id.agents.approvalStatement)).toBeNull();
  });

  it("rejects a list whose total does not match", async () => {
    backend(() => listAnswer(catalog(), { total: 99, counts: counts() }));
    renderApp("/agents");
    expect(await screen.findByText(id.states.error.title)).toBeInTheDocument();
  });

  it("rejects counts that do not add up to the agents sent", async () => {
    const wrong = counts();
    wrong.mode.llm += 1;
    backend(() => listAnswer(catalog(), { total: 4, counts: wrong }));
    renderApp("/agents");
    expect(await screen.findByText(id.states.error.title)).toBeInTheDocument();
  });

  it("rejects an agent with an unknown status", async () => {
    backend(() =>
      HttpResponse.json({
        request_id: "r-x",
        status: "completed",
        data: [{ ...agent(), status: "almost" }],
        meta: { total: 1, counts: counts([agent()]) },
      }),
    );
    renderApp("/agents");
    expect(await screen.findByText(id.states.error.title)).toBeInTheDocument();
  });
});

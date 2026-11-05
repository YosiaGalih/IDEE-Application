import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { agent, catalog, counts } from "@/__tests__/agentFixtures";
import { API, fail, renderApp, signedIn } from "@/__tests__/harness";
import type { Agent } from "@/api/agents";
import id from "@/i18n/locales/id.json";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function backend(agents: Agent[] = catalog(), respond?: () => Response) {
  server.use(
    signedIn(),
    http.get(`${API}/agents`, () =>
      respond
        ? respond()
        : HttpResponse.json({
            request_id: "r-agents-01",
            status: "completed",
            data: agents,
            meta: { total: agents.length, counts: counts(agents) },
          }),
    ),
  );
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
    backend(agents);
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

  it("shows an error with the request id and a retry", async () => {
    backend(catalog(), () => fail(500, "INTERNAL", "boom", { "X-Request-ID": "req-agents-err" }));
    renderApp("/agents");
    expect(await screen.findByText(id.states.error.title)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: id.states.error.action })).toBeInTheDocument();
  });

  it("rejects a list whose total does not match", async () => {
    backend(catalog(), () =>
      HttpResponse.json({
        request_id: "r-x",
        status: "completed",
        data: catalog(),
        meta: { total: 99, counts: counts() },
      }),
    );
    renderApp("/agents");
    expect(await screen.findByText(id.states.error.title)).toBeInTheDocument();
  });

  it("rejects an agent with an unknown status", async () => {
    backend(catalog(), () =>
      HttpResponse.json({
        request_id: "r-x",
        status: "completed",
        data: [{ ...agent(), status: "almost" }],
        meta: { total: 1, counts: counts() },
      }),
    );
    renderApp("/agents");
    expect(await screen.findByText(id.states.error.title)).toBeInTheDocument();
  });
});

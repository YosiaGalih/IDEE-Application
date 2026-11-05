import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { agent, detail, run } from "@/__tests__/agentFixtures";
import { API, fail, renderApp, signedIn } from "@/__tests__/harness";
import type { AgentDetail } from "@/api/agents";
import id from "@/i18n/locales/id.json";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function backend(answer: AgentDetail | (() => Response) = detail()) {
  server.use(
    signedIn(),
    http.get(`${API}/agents/:agentId`, () =>
      typeof answer === "function"
        ? answer()
        : HttpResponse.json({ request_id: "r-a-01", status: "completed", data: answer }),
    ),
  );
}

describe("AgentDetailPage", () => {
  it("lists permissions, tools with unavailable marks, limits and contracts", async () => {
    backend();
    renderApp("/agents/A02");
    expect(await screen.findByRole("heading", { level: 1, name: /Requirement Analyst/ })).toBeInTheDocument();
    const permissions = screen.getByRole("table", { name: id.agents.detail.permissions });
    expect(
      within(permissions).getByRole("row", {
        name: new RegExp(`${id.agents.permissionFields.approval_grant}.*deny`),
      }),
    ).toBeInTheDocument();
    const tools = screen.getByRole("list", { name: id.agents.detail.tools });
    expect(within(tools).getAllByText(id.agents.detail.toolUnavailable)).toHaveLength(2);
    expect(within(tools).getByText("submit_result")).toBeInTheDocument();
    expect(screen.getByText("IntakeInput")).toBeInTheDocument();
    expect(screen.getByText("IntakeOutput")).toBeInTheDocument();
    expect(screen.getByText(id.agents.detail.seconds_other.replace("{{count}}", "300"))).toBeInTheDocument();
    expect(screen.getByText(new RegExp(id.agents.reasons.provider_readiness_unknown))).toBeInTheDocument();
  });

  it("says when there are no runs yet, and that runs are limited to the user's projects", async () => {
    backend();
    renderApp("/agents/A02");
    expect(await screen.findByText(id.agents.detail.noRuns)).toBeInTheDocument();
    expect(screen.getByText(id.agents.detail.runsScope)).toBeInTheDocument();
    expect(screen.getAllByText(id.agents.detail.notAvailable).length).toBeGreaterThan(0);
  });

  it("shows recent runs and the statistics", async () => {
    backend(
      detail({
        recent_runs: [
          run(),
          run({ id: "44444444-4444-4444-8444-444444444444", status: "failed", duration_ms: 2500 }),
        ],
        stats: { runs: 2, success_rate: 0.5, avg_duration_ms: 2000 },
      }),
    );
    renderApp("/agents/A02");
    const runs = await screen.findByRole("table", { name: id.agents.detail.runs });
    expect(within(runs).getByText(id.agents.runStatus.succeeded)).toBeInTheDocument();
    expect(within(runs).getByText(id.agents.runStatus.failed)).toBeInTheDocument();
    expect(screen.getByText("50%")).toBeInTheDocument();
  });

  it("names the runtime of a profile agent", async () => {
    backend(
      detail({
        agent: agent({
          agent_id: "A01",
          name: "Intent Classifier",
          mvp_mode: "profile",
          runtime_agent: "A02",
        }),
      }),
    );
    renderApp("/agents/A01");
    expect(
      await screen.findByText(id.agents.detail.runtimeAgent.replace("{{agent}}", "A02")),
    ).toBeInTheDocument();
  });

  it("shows not found for an unknown agent", async () => {
    backend(() => fail(404, "NOT_FOUND", "Not found"));
    renderApp("/agents/Z99");
    expect(await screen.findByText(id.states.not_found.title)).toBeInTheDocument();
  });

  it("rejects a run with an unknown status", async () => {
    backend(() =>
      HttpResponse.json({
        request_id: "r-x",
        status: "completed",
        data: { ...detail(), recent_runs: [{ ...run(), status: "teleported" }] },
      }),
    );
    renderApp("/agents/A02");
    expect(await screen.findByText(id.states.error.title)).toBeInTheDocument();
  });
});

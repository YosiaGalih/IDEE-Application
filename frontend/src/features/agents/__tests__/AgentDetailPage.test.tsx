import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { detail, run } from "@/__tests__/agentFixtures";
import { API, deferred, fail, pendingApprovals, renderApp, signedIn } from "@/__tests__/harness";
import type { AgentDetail } from "@/api/agents";
import id from "@/i18n/locales/id.json";

const server = setupServer(pendingApprovals());
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const detailAnswer = (data: AgentDetail = detail()) =>
  HttpResponse.json({ request_id: "r-a-01", status: "completed", data });

/** Each GET /agents/:id is answered by `respond`; `calls()` says how many arrived. */
function backend(respond: () => Response | Promise<Response> = () => detailAnswer()) {
  let calls = 0;
  server.use(
    signedIn(),
    http.get(`${API}/agents/:agentId`, () => {
      calls += 1;
      return respond();
    }),
  );
  return { calls: () => calls };
}

const loaded = () => screen.findByRole("heading", { level: 1, name: /Requirement Analyst/ });

describe("AgentDetailPage", () => {
  it("lists permissions, tools with unavailable marks, limits and contracts", async () => {
    backend();
    renderApp("/agents/A02");
    expect(await loaded()).toBeInTheDocument();
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
    backend(() =>
      detailAnswer(
        detail({
          recent_runs: [
            run(),
            run({ id: "44444444-4444-4444-8444-444444444444", status: "failed", duration_ms: 2500 }),
          ],
          stats: { runs: 2, success_rate: 0.5, avg_duration_ms: 2000 },
        }),
      ),
    );
    renderApp("/agents/A02");
    const runs = await screen.findByRole("table", { name: id.agents.detail.runs });
    expect(within(runs).getByText(id.agents.runStatus.succeeded)).toBeInTheDocument();
    expect(within(runs).getByText(id.agents.runStatus.failed)).toBeInTheDocument();
    expect(within(runs).getAllByTestId("run-status-icon")).toHaveLength(2); // text + icon, never text alone
    expect(screen.getByText("50%")).toBeInTheDocument();
  });

  it("marks unavailable tools with an icon as well as words", async () => {
    backend();
    renderApp("/agents/A02");
    await loaded();
    const tools = screen.getByRole("list", { name: id.agents.detail.tools });
    expect(within(tools).getAllByTestId("tool-unavailable-icon")).toHaveLength(2);
  });

  it("does not show a planned agent's contracts as if they existed", async () => {
    backend(() =>
      detailAnswer(
        detail({
          mvp_mode: "planned",
          status: "planned",
          contract_status: "planned",
          input_contract: "SecurityScanInput",
          output_contract: "SecurityScanOutput",
        }),
      ),
    );
    renderApp("/agents/A02");
    await loaded();
    expect(
      screen.getByText(id.agents.detail.contractPlanned.replace("{{name}}", "SecurityScanInput")),
    ).toBeInTheDocument();
    expect(screen.queryByText("SecurityScanInput", { selector: "code" })).toBeNull();
  });

  it("lists the tool quota when the registry sets one", async () => {
    backend(() =>
      detailAnswer(
        detail({ limits: { max_iterations: 8, timeout_seconds: 300, tool_quota: { "repo.read_file": 20 } } }),
      ),
    );
    renderApp("/agents/A02");
    await loaded();
    const quota = screen.getByLabelText(id.agents.detail.toolQuota);
    expect(within(quota).getByText("repo.read_file")).toBeInTheDocument();
    expect(
      within(quota).getByText(id.agents.detail.quotaValue_other.replace("{{count}}", "20")),
    ).toBeInTheDocument();
  });

  it("names the runtime of a profile agent", async () => {
    backend(() =>
      detailAnswer(
        detail({
          agent_id: "A01",
          name: "Intent Classifier",
          mvp_mode: "profile",
          runtime_agent: "A02",
        }),
      ),
    );
    renderApp("/agents/A01");
    expect(
      await screen.findByText(id.agents.detail.runtimeAgent.replace("{{agent}}", "A02")),
    ).toBeInTheDocument();
  });

  it("shows not found for an unknown agent, as the page heading", async () => {
    backend(() => fail(404, "NOT_FOUND", "Not found"));
    renderApp("/agents/Z99");
    expect(
      await screen.findByRole("heading", { level: 1, name: id.states.not_found.title }),
    ).toBeInTheDocument();
  });

  it("shows an error with the request id, and a retry that asks again", async () => {
    let healthy = false;
    const api = backend(() => (healthy ? detailAnswer() : fail(500, "INTERNAL", "boom")));
    renderApp("/agents/A02");
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByRole("heading", { level: 1, name: id.states.error.title })).toBeInTheDocument();
    expect(alert).toHaveTextContent(id.common.requestId.replace("{{id}}", "r-err-0001"));
    const failedCalls = api.calls();
    healthy = true;
    await userEvent.click(within(alert).getByRole("button", { name: id.states.error.action }));
    await loaded();
    expect(api.calls()).toBe(failedCalls + 1);
  });

  it("shows a loading state until the agent arrives", async () => {
    const wait = deferred();
    backend(async () => {
      await wait.gate;
      return detailAnswer();
    });
    renderApp("/agents/A02");
    expect(await screen.findByRole("status", { busy: true })).toBeInTheDocument();
    wait.release();
    await loaded();
  });

  it("shows the permission state, without a retry, when the server refuses", async () => {
    backend(() => fail(403, "FORBIDDEN"));
    renderApp("/agents/A02");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(id.states.permission_denied.title);
    expect(within(alert).queryByRole("button")).toBeNull();
  });

  it("names the run statistics", async () => {
    backend();
    renderApp("/agents/A02");
    await loaded();
    expect(screen.getByLabelText(id.agents.detail.stats)).toBeInTheDocument();
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

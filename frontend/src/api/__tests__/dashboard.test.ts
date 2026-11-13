import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { getDashboard } from "@/api/dashboard";
import { ApiError } from "@/api/errors";
import { agentRun, dashboardData, dashboardMeta } from "@/__tests__/dashboardFixtures";

const API = `${window.location.origin}/api/v1`;
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function answer(data: unknown, meta: unknown = dashboardMeta()) {
  server.use(
    http.get(`${API}/dashboard`, () =>
      HttpResponse.json({ request_id: "r-dash-0001", status: "completed", data, meta }),
    ),
  );
}

describe("getDashboard", () => {
  it("returns the validated dashboard with what is unavailable and the queue total", async () => {
    answer(dashboardData());
    const result = await getDashboard();
    expect(result.data.metrics.failed_tasks).toBe(1);
    expect(result.data.failed_queue[0]?.project_name).toBe("Kalkulator");
    expect([...result.unavailable]).toEqual(["agent_activity", "usage"]);
    expect(result.failedQueueTotal).toBe(1);
  });

  it("accepts recorded usage and agent runs once the server no longer lists them as unavailable", async () => {
    const data = dashboardData();
    data.usage = {
      ...data.usage,
      cost_status: "estimated",
      input_tokens: 10,
      output_tokens: 2,
      estimated_cost: "0.5",
    };
    data.agent_activity = [agentRun()];
    answer(data, dashboardMeta({ unavailable: [] }));
    const result = await getDashboard();
    expect(result.data.usage.cost_status).toBe("estimated");
    expect(result.data.usage.estimated_cost).toBe("0.5");
    expect(result.data.agent_activity[0]?.agent_name).toBe("General Code Implementation Agent");
    expect(result.unavailable.size).toBe(0);
  });

  it.each([
    [
      "agent runs for a part listed as unavailable",
      () => ({ ...dashboardData(), agent_activity: [agentRun()] }),
    ],
    [
      "usage values for a part listed as unavailable",
      () => {
        const data = dashboardData();
        return { ...data, usage: { ...data.usage, input_tokens: 0 } };
      },
    ],
    [
      "a cost status for usage listed as unavailable",
      () => {
        const data = dashboardData();
        return { ...data, usage: { ...data.usage, cost_status: "estimated" } };
      },
    ],
  ])("refuses an answer that contradicts itself: %s", async (_, make) => {
    answer(make());
    await expect(getDashboard()).rejects.toMatchObject({ kind: "bad_response" });
  });

  it.each([
    ["a cost sent as a number", 0.5],
    ["a cost in exponent form", "5E-7"],
    ["a negative cost", "-1"],
  ])("refuses %s: money is a plain decimal string (WP-3.1f1)", async (_, cost) => {
    const data = dashboardData();
    data.usage = { ...data.usage, cost_status: "estimated", input_tokens: 1, output_tokens: 1 };
    answer({ ...data, usage: { ...data.usage, estimated_cost: cost } }, dashboardMeta({ unavailable: [] }));
    await expect(getDashboard()).rejects.toMatchObject({ kind: "bad_response" });
  });

  it.each([
    ["an unavailable cost that carries an amount", "0", "unavailable"],
    ["an estimated cost without an amount", null, "estimated"],
    ["a partial cost without an amount", null, "partial"],
  ])("refuses %s: the cost must match its status (WP-3.1f1)", async (_, cost, status) => {
    const data = dashboardData();
    const usage = {
      ...data.usage,
      input_tokens: 1,
      output_tokens: 1,
      estimated_cost: cost,
      cost_status: status,
    };
    answer({ ...data, usage }, dashboardMeta({ unavailable: [] }));
    await expect(getDashboard()).rejects.toMatchObject({ kind: "bad_response" });
    const recent = {
      ...data.recent_tasks[0],
      usage: { ...usage, estimated_cost: cost, cost_status: status },
    };
    answer({ ...data, recent_tasks: [recent] });
    await expect(getDashboard()).rejects.toMatchObject({ kind: "bad_response" });
  });

  it("refuses a task cost sent as a number", async () => {
    const data = dashboardData();
    const recent = {
      ...data.recent_tasks[0],
      usage: { ...data.usage, cost_status: "estimated", estimated_cost: 0.5 },
    };
    answer({ ...data, recent_tasks: [recent] });
    await expect(getDashboard()).rejects.toMatchObject({ kind: "bad_response" });
  });

  it("refuses an agent run that is not an AgentRunSummary", async () => {
    answer({ ...dashboardData(), agent_activity: [{ id: "x" }] }, dashboardMeta({ unavailable: ["usage"] }));
    await expect(getDashboard()).rejects.toMatchObject({ kind: "bad_response" });
  });

  it("refuses a part name it does not know in meta.unavailable", async () => {
    answer(dashboardData(), dashboardMeta({ unavailable: ["agent_activity", "usage", "budget"] }));
    await expect(getDashboard()).rejects.toMatchObject({ kind: "bad_response" });
  });

  it("refuses an answer missing a metric: an error, never an empty dashboard", async () => {
    const data = dashboardData() as { metrics: Partial<ReturnType<typeof dashboardData>["metrics"]> };
    delete data.metrics.failed_tasks;
    answer(data);
    await expect(getDashboard()).rejects.toMatchObject({ kind: "bad_response" });
  });

  it("refuses an answer without the queue total", async () => {
    answer(dashboardData(), { unavailable: [] });
    const error = await getDashboard().catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).kind).toBe("bad_response");
  });
});

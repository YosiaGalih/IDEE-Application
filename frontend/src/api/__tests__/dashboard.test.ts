import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { getDashboard } from "@/api/dashboard";
import { ApiError } from "@/api/errors";
import { dashboardData, dashboardMeta } from "@/__tests__/dashboardFixtures";

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
    expect(result.unavailable).toEqual(["agent_activity", "usage"]);
    expect(result.failedQueueTotal).toBe(1);
  });

  it("accepts every cost status the contract names", async () => {
    const data = dashboardData();
    data.usage = { ...data.usage, cost_status: "estimated", input_tokens: 10, estimated_cost: 0.5 };
    answer(data);
    expect((await getDashboard()).data.usage.cost_status).toBe("estimated");
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

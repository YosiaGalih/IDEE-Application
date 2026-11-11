import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { JOB_ID, PROVIDER_ID, provider, readiness, report } from "@/__tests__/providerFixtures";
import { getReadiness, listProviders, patchProvider, runSmoke, SMOKE_TIMEOUT_MS } from "@/api/providers";

const API = `${window.location.origin}/api/v1`;
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  document.cookie = "kk_csrf=csrf-test; path=/";
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const ok = (data: unknown, meta: unknown = null, status = 200) =>
  HttpResponse.json(
    { request_id: "r-prov-0001", status: status === 202 ? "accepted" : "completed", data, meta },
    { status },
  );

describe("listProviders", () => {
  it("returns the rows in the §9 shape", async () => {
    server.use(http.get(`${API}/settings/providers`, () => ok([provider()], { total: 1 })));
    const rows = await listProviders();
    expect(rows[0]?.secret_hint).toBe("sk-ant-…a1b2");
    expect(rows[0]?.readiness_effective).toEqual({ state: "ready", reason: "passed" });
  });

  it("refuses an effective state it does not know: the screen colours by it", async () => {
    server.use(
      http.get(`${API}/settings/providers`, () =>
        ok([provider({ readiness_effective: { state: "green", reason: "passed" } })]),
      ),
    );
    await expect(listProviders()).rejects.toMatchObject({ kind: "bad_response" });
  });
});

describe("patchProvider", () => {
  it("sends only model_policy and enabled, with the CSRF token", async () => {
    let body: unknown = null;
    let csrf: string | null = null;
    server.use(
      http.patch(`${API}/settings/providers/${PROVIDER_ID}`, async ({ request }) => {
        body = await request.json();
        csrf = request.headers.get("X-CSRF-Token");
        return ok(provider({ model_policy: { fast: "claude-haiku-5-5" } }));
      }),
    );
    const row = await patchProvider(PROVIDER_ID, {
      model_policy: { fast: "claude-haiku-5-5", complex: null },
    });
    expect(body).toEqual({ model_policy: { fast: "claude-haiku-5-5", complex: null } });
    expect(csrf).toBe("csrf-test");
    expect(row.model_policy).toEqual({ fast: "claude-haiku-5-5" });
  });
});

describe("getReadiness", () => {
  it("returns the last report with the job ids and whether it counts now", async () => {
    server.use(http.get(`${API}/settings/providers/${PROVIDER_ID}/readiness`, () => ok(readiness())));
    const result = await getReadiness(PROVIDER_ID);
    expect(result.job_id).toBe(JOB_ID);
    expect(result.effective.state).toBe("ready");
    expect(result.checks).toHaveLength(9);
  });
});

describe("runSmoke", () => {
  it("returns the report and whether it counts now when the worker answered in time", async () => {
    server.use(
      http.post(`${API}/settings/providers/${PROVIDER_ID}/smoke-test`, () =>
        ok(report(), { job_id: JOB_ID, effective: { state: "ready", reason: "passed" } }),
      ),
    );
    const result = await runSmoke(PROVIDER_ID);
    expect(result).toMatchObject({ kind: "finished", jobId: JOB_ID, effective: { state: "ready" } });
    expect(result.kind === "finished" && result.report.ready).toBe(true);
  });

  it("says the job is still running on 202, never a result", async () => {
    server.use(
      http.post(`${API}/settings/providers/${PROVIDER_ID}/smoke-test`, () =>
        ok({ job_id: JOB_ID }, null, 202),
      ),
    );
    expect(await runSmoke(PROVIDER_ID)).toEqual({ kind: "running", jobId: JOB_ID });
  });

  it("waits at most 60 s on the client (the server waits SMOKE_WAIT_S below that)", () => {
    expect(SMOKE_TIMEOUT_MS).toBe(60_000);
  });

  it("gives up on the client limit with a timeout error", async () => {
    server.use(
      http.post(`${API}/settings/providers/${PROVIDER_ID}/smoke-test`, async () => {
        await new Promise((resolve) => setTimeout(resolve, 200));
        return ok(report(), { job_id: JOB_ID, effective: { state: "ready", reason: "passed" } });
      }),
    );
    await expect(runSmoke(PROVIDER_ID, { timeoutMs: 20 })).rejects.toMatchObject({ kind: "timeout" });
  });
});

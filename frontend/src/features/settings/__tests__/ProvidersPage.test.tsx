import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { API, fail, ok, pendingApprovals, renderApp, signedInWith } from "@/__tests__/harness";
import {
  JOB_ID,
  OTHER_JOB_ID,
  PROVIDER_ID,
  checks,
  provider,
  readiness,
  report,
} from "@/__tests__/providerFixtures";
import { SMOKE_POLL_MS } from "@/api/hooks/useProviders";
import id from "@/i18n/locales/id.json";

const server = setupServer(pendingApprovals());
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  document.cookie = "kk_csrf=csrf-test; path=/";
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const PROVIDERS = `${API}/settings/providers`;
const READINESS = `${PROVIDERS}/${PROVIDER_ID}/readiness`;
const SMOKE = `${PROVIDERS}/${PROVIDER_ID}/smoke-test`;
const admin = () => signedInWith(["project:read", "settings:providers"]);

function backend(rows = [provider()], last = readiness()) {
  server.use(
    admin(),
    http.get(PROVIDERS, () =>
      HttpResponse.json({ request_id: "r-1", status: "completed", data: rows, meta: { total: rows.length } }),
    ),
    http.get(READINESS, () => ok(last)),
  );
}

async function open() {
  renderApp("/settings/providers");
  expect(await screen.findByTestId("screen-title")).toHaveTextContent(id.screens.providers);
}

describe("ProvidersPage: what the worker reported", () => {
  it("shows the key status and hint, never a key field, and the data flow statement", async () => {
    backend();
    await open();
    expect(await screen.findByText(id.providers.key.configured)).toBeInTheDocument();
    expect(screen.getByText(id.providers.key.hint.replace("{{hint}}", "sk-ant-…a1b2"))).toBeInTheDocument();
    expect(screen.getByText(id.providers.key.managedInEnv)).toBeInTheDocument();
    expect(screen.queryByLabelText(/key/i, { selector: "input" })).toBeNull(); // no way to type a key
    expect(screen.getByText(id.providers.endpointDefault)).toBeInTheDocument();
    // F5: the data flow statement (SECURITY_MODEL §7) is on the screen.
    expect(screen.getByRole("heading", { name: id.providers.dataFlowTitle })).toBeInTheDocument();
    expect(screen.getByText(id.providers.dataFlow)).toBeInTheDocument();
  });

  it("colours readiness by whether the report counts now, not by the report's own result (F4)", async () => {
    backend(
      [
        provider({
          readiness_status: "unknown",
          readiness_effective: { state: "unknown", reason: "models_changed" },
        }),
      ],
      readiness({ report_ready: true, effective: { state: "unknown", reason: "models_changed" } }),
    );
    await open();
    const panel = await screen.findByRole("region", { name: id.providers.readiness.title });
    expect(within(panel).getByText(id.providers.readiness.state.unknown)).toBeInTheDocument();
    expect(within(panel).queryByText(id.providers.readiness.state.ready)).toBeNull();
    expect(within(panel).getByText(id.providers.readiness.reason.models_changed)).toBeInTheDocument();
    expect(within(panel).getByText(id.providers.readiness.reportResult.passed)).toBeInTheDocument();
  });

  it("warns plainly when the readiness gate is switched off", async () => {
    backend(
      [provider({ readiness_effective: { state: "ready", reason: "readiness_not_required" } })],
      readiness({ effective: { state: "ready", reason: "readiness_not_required" } }),
    );
    await open();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      id.providers.readiness.reason.readiness_not_required,
    );
  });

  it("lists every check by its own label and shows a detail as text, never as markup (F6)", async () => {
    const shown = checks("streaming_ok").map((check) =>
      check.key === "streaming_ok" ? { ...check, detail: "<img src=x onerror=alert(1)> no delta" } : check,
    );
    backend(
      [provider()],
      readiness({
        checks: shown,
        report_ready: false,
        effective: { state: "not_ready", reason: "report_failed" },
      }),
    );
    const { container } = renderApp("/settings/providers");
    const panel = await screen.findByRole("region", { name: id.providers.readiness.title });
    expect(within(panel).getByText(id.providers.checks.streaming_ok)).toBeInTheDocument();
    expect(within(panel).getByText("<img src=x onerror=alert(1)> no delta")).toBeInTheDocument();
    expect(container.querySelector("img[src='x']")).toBeNull();
    expect(within(panel).getAllByText(id.providers.readiness.status.passed)).toHaveLength(8);
  });

  it("says permission denied to someone the server refuses", async () => {
    server.use(
      admin(),
      http.get(PROVIDERS, () => fail(403, "FORBIDDEN", "Not allowed")),
    );
    renderApp("/settings/providers");
    expect(await screen.findByText(id.states.permission_denied.title)).toBeInTheDocument();
  });
});

describe("ProvidersPage: changing models and the provider switch", () => {
  it("sends every alias, an empty one as null so it falls back to the env (F8)", async () => {
    backend();
    let body: unknown = null;
    server.use(
      http.patch(`${PROVIDERS}/${PROVIDER_ID}`, async ({ request }) => {
        body = await request.json();
        return ok(provider({ model_policy: { fast: "claude-haiku-5-5" }, readiness_status: "unknown" }));
      }),
    );
    await open();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText(id.providers.models.alias.fast), "claude-haiku-5-5");
    await user.click(screen.getByRole("button", { name: id.providers.models.save }));
    await waitFor(() =>
      expect(body).toEqual({ model_policy: { default: null, fast: "claude-haiku-5-5", complex: null } }),
    );
    expect(await screen.findByText(id.providers.models.saved)).toBeInTheDocument();
  });

  it("refuses an id that is not a Claude model before sending anything", async () => {
    backend();
    const sent = vi.fn();
    server.use(
      http.patch(`${PROVIDERS}/${PROVIDER_ID}`, () => {
        sent();
        return ok(provider());
      }),
    );
    await open();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText(id.providers.models.alias.default), "gpt-4o");
    await user.click(screen.getByRole("button", { name: id.providers.models.save }));
    expect(await screen.findByText(id.providers.models.invalid)).toBeInTheDocument();
    expect(sent).not.toHaveBeenCalled();
  });

  it("disables the provider with enabled=false only", async () => {
    backend();
    let body: unknown = null;
    server.use(
      http.patch(`${PROVIDERS}/${PROVIDER_ID}`, async ({ request }) => {
        body = await request.json();
        return ok(
          provider({ enabled: false, readiness_effective: { state: "disabled", reason: "disabled" } }),
        );
      }),
    );
    await open();
    await userEvent.setup().click(await screen.findByRole("button", { name: id.providers.enabled.disable }));
    await waitFor(() => expect(body).toEqual({ enabled: false }));
  });
});

describe("ProvidersPage: smoke test", () => {
  it("shows the result when the worker answered in time, and reads readiness again", async () => {
    backend([provider()], readiness({ job_id: OTHER_JOB_ID }));
    let reads = 0;
    server.use(
      http.get(READINESS, () => {
        reads += 1;
        return ok(readiness({ job_id: reads > 1 ? JOB_ID : OTHER_JOB_ID }));
      }),
      http.post(SMOKE, () =>
        HttpResponse.json({
          request_id: "r-3",
          status: "completed",
          data: report(),
          meta: { job_id: JOB_ID, effective: { state: "ready", reason: "passed" } },
        }),
      ),
    );
    await open();
    await userEvent.setup().click(await screen.findByRole("button", { name: id.providers.smoke.run }));
    expect(await screen.findByText(id.providers.smoke.finished)).toBeInTheDocument();
    await waitFor(() => expect(reads).toBeGreaterThan(1));
  });

  it("on 202 says it is still running, then shows the result once the report of that job lands (F3)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let landed = false;
      backend();
      server.use(
        http.get(READINESS, () =>
          ok(
            landed
              ? readiness({ job_id: JOB_ID })
              : readiness({ job_id: OTHER_JOB_ID, running_job_id: JOB_ID }),
          ),
        ),
        http.post(SMOKE, () =>
          HttpResponse.json(
            { request_id: "r-2", status: "accepted", data: { job_id: JOB_ID } },
            { status: 202 },
          ),
        ),
      );
      await open();
      await userEvent
        .setup({ advanceTimers: vi.advanceTimersByTime })
        .click(await screen.findByRole("button", { name: id.providers.smoke.run }));
      expect(await screen.findByText(id.providers.smoke.stillRunning)).toBeInTheDocument();
      expect(screen.queryByText(id.providers.smoke.finished)).toBeNull();
      landed = true;
      await act(() => vi.advanceTimersByTimeAsync(SMOKE_POLL_MS + 50));
      expect(await screen.findByText(id.providers.smoke.finished)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops waiting when the job neither runs nor reported: it ended without a report (F7)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let reads = 0;
      backend();
      server.use(
        http.get(READINESS, () => {
          reads += 1;
          return ok(readiness({ job_id: OTHER_JOB_ID, running_job_id: reads > 2 ? null : JOB_ID }));
        }),
        http.post(SMOKE, () =>
          HttpResponse.json(
            { request_id: "r-2", status: "accepted", data: { job_id: JOB_ID } },
            { status: 202 },
          ),
        ),
      );
      await open();
      await userEvent
        .setup({ advanceTimers: vi.advanceTimersByTime })
        .click(await screen.findByRole("button", { name: id.providers.smoke.run }));
      await screen.findByText(id.providers.smoke.stillRunning);
      await act(() => vi.advanceTimersByTimeAsync(SMOKE_POLL_MS * 3));
      expect(await screen.findByText(id.providers.smoke.endedWithout)).toBeInTheDocument();
      const settled = reads;
      await act(() => vi.advanceTimersByTimeAsync(SMOKE_POLL_MS * 3));
      expect(reads).toBe(settled); // no more polling
    } finally {
      vi.useRealTimers();
    }
  });

  it.each([
    [409, "CONFLICT", id.providers.smoke.busy],
    [429, "RATE_LIMITED", id.providers.smoke.rateLimited],
  ])("explains a %s", async (status, code, text) => {
    backend();
    server.use(http.post(SMOKE, () => fail(status, code)));
    await open();
    await userEvent.setup().click(await screen.findByRole("button", { name: id.providers.smoke.run }));
    expect(await screen.findByText(text)).toBeInTheDocument();
  });
});

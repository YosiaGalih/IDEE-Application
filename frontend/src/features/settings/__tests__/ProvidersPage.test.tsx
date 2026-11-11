import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { API, deferred, fail, ok, pendingApprovals, renderApp, signedInWith } from "@/__tests__/harness";
import {
  JOB_ID,
  OTHER_JOB_ID,
  PROVIDER_ID,
  checks,
  provider,
  readiness,
  report,
} from "@/__tests__/providerFixtures";
import { SMOKE_POLL_MS, SMOKE_WAIT_LIMIT_MS } from "@/api/hooks/useProviders";
import { SMOKE_TIMEOUT_MS } from "@/api/providers";
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

let providerReads = 0;
function backend(rows = [provider()], last = readiness()) {
  server.use(
    admin(),
    http.get(PROVIDERS, () => {
      providerReads += 1;
      return HttpResponse.json({
        request_id: "r-1",
        status: "completed",
        data: rows,
        meta: { total: rows.length },
      });
    }),
    http.get(READINESS, () => ok(last)),
  );
}

/** Before the smoke request nothing holds the lock; after it, this job does (until `landed`). */
function lockAfterPost(state: { posted: boolean; landed: boolean }) {
  return http.get(READINESS, () => {
    if (state.landed) return ok(readiness({ job_id: JOB_ID }));
    return ok(readiness({ job_id: OTHER_JOB_ID, running_job_id: state.posted ? JOB_ID : null }));
  });
}
const accepted = (state: { posted: boolean }) =>
  http.post(SMOKE, () => {
    state.posted = true;
    return HttpResponse.json(
      { request_id: "r-2", status: "accepted", data: { job_id: JOB_ID } },
      { status: 202 },
    );
  });

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
    // Status as text + icon, never colour alone (frontend.md).
    const keyBadge = screen.getByText(id.providers.key.configured);
    expect(keyBadge.querySelector("[data-testid='status-icon']")).not.toBeNull();
    // F5: the data flow statement (SECURITY_MODEL §7) is on the screen.
    expect(screen.getByRole("heading", { name: id.providers.dataFlowTitle })).toBeInTheDocument();
    expect(screen.getByText(id.providers.dataFlow)).toBeInTheDocument();
    // A report that counts now is not marked outdated, and nothing says the data is stale.
    await screen.findByRole("region", { name: id.providers.readiness.title });
    expect(screen.queryByText(id.providers.readiness.outdated)).toBeNull();
    expect(screen.queryByText(id.providers.readiness.stale)).toBeNull();
    expect(screen.queryByText(id.providers.listStale)).toBeNull();
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

  it("shows loading, then the empty state when no provider exists", async () => {
    const held = deferred();
    server.use(
      admin(),
      http.get(PROVIDERS, async () => {
        await held.gate;
        return ok([]);
      }),
    );
    renderApp("/settings/providers");
    expect(await screen.findByText(id.states.loading.title)).toBeInTheDocument();
    held.release();
    expect(await screen.findByText(id.providers.empty)).toBeInTheDocument();
  });

  it("shows the error state with a retry when the list cannot be read", async () => {
    server.use(
      admin(),
      http.get(PROVIDERS, () => fail(500, "INTERNAL_ERROR")),
    );
    renderApp("/settings/providers");
    expect(await screen.findByText(id.states.error.title)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: id.states.error.action })).toBeInTheDocument();
  });

  it("keeps the provider on screen when only its readiness cannot be read", async () => {
    server.use(
      admin(),
      http.get(PROVIDERS, () => ok([provider()])),
      http.get(READINESS, () => fail(500, "INTERNAL_ERROR")),
    );
    await open();
    expect(await screen.findByText(id.providers.key.configured)).toBeInTheDocument();
    expect(await screen.findByText(id.states.error.title)).toBeInTheDocument();
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

  it("asks before disabling, then sends enabled=false only; cancelling sends nothing", async () => {
    backend();
    let body: unknown = null;
    let calls = 0;
    server.use(
      http.patch(`${PROVIDERS}/${PROVIDER_ID}`, async ({ request }) => {
        calls += 1;
        body = await request.json();
        return ok(
          provider({ enabled: false, readiness_effective: { state: "disabled", reason: "disabled" } }),
        );
      }),
    );
    await open();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: id.providers.enabled.disable }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: id.providers.enabled.cancel }));
    expect(calls).toBe(0);
    await user.click(screen.getByRole("button", { name: id.providers.enabled.disable }));
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: id.providers.enabled.confirm }),
    );
    await waitFor(() => expect(body).toEqual({ enabled: false }));
  });

  it("keeps saying the models were saved after the list comes back with them", async () => {
    let policy: Record<string, string> = {};
    server.use(
      admin(),
      http.get(PROVIDERS, () => ok([provider({ model_policy: policy })])),
      http.get(READINESS, () => ok(readiness())),
      http.patch(`${PROVIDERS}/${PROVIDER_ID}`, () => {
        policy = { fast: "claude-haiku-5-5" };
        return ok(provider({ model_policy: policy, readiness_status: "unknown" }));
      }),
    );
    await open();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText(id.providers.models.alias.fast), "claude-haiku-5-5");
    await user.click(screen.getByRole("button", { name: id.providers.models.save }));
    expect(await screen.findByText(id.providers.models.saved)).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByLabelText(id.providers.models.alias.fast)).toHaveValue("claude-haiku-5-5"),
    );
    expect(screen.getByText(id.providers.models.saved)).toBeInTheDocument(); // the list read did not wipe it
  });

  it("drops the saved message once the models are edited again (silent-failure L1)", async () => {
    backend();
    server.use(http.patch(`${PROVIDERS}/${PROVIDER_ID}`, () => ok(provider())));
    await open();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: id.providers.models.save }));
    expect(await screen.findByText(id.providers.models.saved)).toBeInTheDocument();
    await user.type(screen.getByLabelText(id.providers.models.alias.fast), "x");
    expect(screen.queryByText(id.providers.models.saved)).toBeNull();
  });

  it("shows what the server applied even when the list cannot be read again, and says it may be stale (silent-failure H1)", async () => {
    let listReads = 0;
    server.use(
      admin(),
      http.get(PROVIDERS, () => {
        listReads += 1;
        return listReads === 1 ? ok([provider()]) : fail(500, "INTERNAL_ERROR");
      }),
      http.get(READINESS, () => ok(readiness())),
      http.patch(`${PROVIDERS}/${PROVIDER_ID}`, () =>
        ok(provider({ enabled: false, readiness_effective: { state: "disabled", reason: "disabled" } })),
      ),
    );
    await open();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: id.providers.enabled.disable }));
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: id.providers.enabled.confirm }),
    );
    expect(await screen.findByText(id.providers.enabled.off)).toBeInTheDocument();
    expect(screen.queryByText(id.providers.enabled.on)).toBeNull();
    expect(await screen.findByText(id.providers.listStale)).toBeInTheDocument();
  });

  it("says when the report on screen could not be read again (silent-failure M1)", async () => {
    let reads = 0;
    backend();
    server.use(
      http.get(READINESS, () => {
        reads += 1;
        return reads === 1 ? ok(readiness()) : fail(500, "INTERNAL_ERROR");
      }),
      http.patch(`${PROVIDERS}/${PROVIDER_ID}`, () => ok(provider())),
    );
    await open();
    await screen.findByRole("region", { name: id.providers.readiness.title });
    expect(screen.queryByText(id.providers.readiness.stale)).toBeNull();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: id.providers.models.save })); // re-reads readiness
    expect(await screen.findByText(id.providers.readiness.stale)).toBeInTheDocument();
  });

  it("marks a report that no longer counts as outdated, and a current one not (silent-failure L4)", async () => {
    backend(
      [provider()],
      readiness({ readiness_status: "unknown", effective: { state: "unknown", reason: "models_changed" } }),
    );
    await open();
    expect(await screen.findByText(id.providers.readiness.outdated)).toBeInTheDocument();
  });
});

describe("ProvidersPage: smoke test", () => {
  const click = async (withTimers = false) =>
    (withTimers ? userEvent.setup({ advanceTimers: vi.advanceTimersByTime }) : userEvent.setup()).click(
      await screen.findByRole("button", { name: id.providers.smoke.run }),
    );

  async function withFakeTimers(body: () => Promise<void>) {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      await body();
    } finally {
      vi.useRealTimers();
    }
  }

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
    await click();
    expect(await screen.findByText(id.providers.smoke.finished)).toBeInTheDocument();
    await waitFor(() => expect(reads).toBeGreaterThan(1));
  });

  it("after a 200, claims finished only once readiness shows that job's report (silent-failure M1)", async () => {
    let afterPost = false;
    backend([provider()], readiness({ job_id: OTHER_JOB_ID }));
    server.use(
      http.get(READINESS, () =>
        afterPost ? fail(500, "INTERNAL_ERROR") : ok(readiness({ job_id: OTHER_JOB_ID })),
      ),
      http.post(SMOKE, () => {
        afterPost = true;
        return HttpResponse.json({
          request_id: "r-3",
          status: "completed",
          data: report(),
          meta: { job_id: JOB_ID, effective: { state: "ready", reason: "passed" } },
        });
      }),
    );
    await open();
    await click();
    expect(await screen.findByText(id.providers.smoke.loading)).toBeInTheDocument();
    expect(await screen.findByText(id.providers.smoke.readFailed)).toBeInTheDocument();
    expect(screen.queryByText(id.providers.smoke.finished)).toBeNull();
    // Its lock is released: another click now would start a second paid run.
    expect(screen.getByRole("button", { name: id.providers.smoke.run })).toBeDisabled();
  });

  it("on 202 says it is still running, then shows the result once the report of that job lands (F3)", async () => {
    await withFakeTimers(async () => {
      const state = { posted: false, landed: false };
      backend();
      server.use(lockAfterPost(state), accepted(state));
      await open();
      await click(true);
      expect(await screen.findByText(id.providers.smoke.stillRunning)).toBeInTheDocument();
      expect(screen.queryByText(id.providers.smoke.finished)).toBeNull();
      const listReads = providerReads;
      state.landed = true;
      await act(() => vi.advanceTimersByTimeAsync(SMOKE_POLL_MS + 50));
      expect(await screen.findByText(id.providers.smoke.finished)).toBeInTheDocument();
      // The card's own verdict comes from the list: read again once the report landed.
      await waitFor(() => expect(providerReads).toBeGreaterThan(listReads));
    });
  });

  it("stops waiting when the job neither runs nor reported: it ended without a report (F7)", async () => {
    await withFakeTimers(async () => {
      let reads = 0;
      let afterPost = 0;
      backend();
      server.use(
        http.get(READINESS, () => {
          reads += 1;
          if (afterPost > 0) afterPost += 1;
          return ok(
            readiness({
              job_id: OTHER_JOB_ID,
              running_job_id: afterPost > 0 && afterPost < 3 ? JOB_ID : null,
            }),
          );
        }),
        http.post(SMOKE, () => {
          afterPost = 1;
          return HttpResponse.json(
            { request_id: "r-2", status: "accepted", data: { job_id: JOB_ID } },
            { status: 202 },
          );
        }),
      );
      await open();
      await click(true);
      await act(() => vi.advanceTimersByTimeAsync(SMOKE_POLL_MS * 3));
      expect(await screen.findByText(id.providers.smoke.endedWithout)).toBeInTheDocument();
      const settled = reads;
      await act(() => vi.advanceTimersByTimeAsync(SMOKE_POLL_MS * 3));
      expect(reads).toBe(settled); // no more polling
    });
  });

  it("lets only reads made after the 202 decide: the cached one says nothing runs (F9)", async () => {
    let after202 = false;
    const held = deferred(); // the first read after the 202 waits: the cached read is all there is
    backend();
    server.use(
      http.get(READINESS, async () => {
        if (!after202) return ok(readiness({ job_id: OTHER_JOB_ID }));
        await held.gate;
        return ok(readiness({ job_id: OTHER_JOB_ID, running_job_id: JOB_ID }));
      }),
      http.post(SMOKE, () => {
        after202 = true;
        return HttpResponse.json(
          { request_id: "r-2", status: "accepted", data: { job_id: JOB_ID } },
          { status: 202 },
        );
      }),
    );
    await open();
    await screen.findByRole("region", { name: id.providers.readiness.title }); // cached: nothing running
    await click();
    expect(await screen.findByText(id.providers.smoke.stillRunning)).toBeInTheDocument();
    expect(screen.queryByText(id.providers.smoke.endedWithout)).toBeNull();
    held.release();
    await waitFor(() => expect(screen.getByText(id.providers.smoke.stillRunning)).toBeInTheDocument());
  });

  it("with no answer, keeps reading, and never claims a new report as its own (AG-13 L2, silent-failure H2)", async () => {
    await withFakeTimers(async () => {
      const state = { posted: false, landed: false };
      backend();
      server.use(
        lockAfterPost(state),
        http.post(SMOKE, () => {
          state.posted = true;
          return HttpResponse.error(); // no answer: whether the run started is unknown
        }),
      );
      await open();
      await click(true);
      expect(await screen.findByText(id.providers.smoke.noAnswer)).toBeInTheDocument();
      expect(await screen.findByText(id.providers.smoke.stillRunning)).toBeInTheDocument();
      state.landed = true; // a report lands, but nothing says whose run made it
      await act(() => vi.advanceTimersByTimeAsync(SMOKE_POLL_MS + 50));
      expect(await screen.findByText(id.providers.smoke.unconfirmed)).toBeInTheDocument();
      expect(screen.queryByText(id.providers.smoke.finished)).toBeNull();
    });
  });

  it("with no answer, nothing running and no new report, says the request may not have arrived", async () => {
    let posts = 0;
    backend([provider()], readiness({ job_id: OTHER_JOB_ID }));
    server.use(
      http.post(SMOKE, () => {
        posts += 1;
        return HttpResponse.error();
      }),
    );
    await open();
    await screen.findByRole("region", { name: id.providers.readiness.title });
    await click();
    expect(await screen.findByText(id.providers.smoke.noRun)).toBeInTheDocument();
    expect(screen.queryByText(id.providers.smoke.endedWithout)).toBeNull();
    await new Promise((resolve) => setTimeout(resolve, 1_100)); // past any retry delay
    expect(posts).toBe(1); // a lost answer is never sent again (AG-13 delta LOW-3)
  });

  it("treats the client's own 60 s limit as no answer, and sends nothing again (AG-13 delta LOW-3)", async () => {
    const limit = new AbortController();
    const original = AbortSignal.timeout.bind(AbortSignal);
    const spy = vi
      .spyOn(AbortSignal, "timeout")
      .mockImplementation((ms) => (ms === SMOKE_TIMEOUT_MS ? limit.signal : original(ms)));
    try {
      let posts = 0;
      const state = { posted: false, landed: false };
      backend();
      server.use(
        lockAfterPost(state),
        http.post(SMOKE, async () => {
          posts += 1;
          state.posted = true;
          await delay("infinite");
          return HttpResponse.json({});
        }),
      );
      await open();
      await click();
      await waitFor(() => expect(posts).toBe(1));
      limit.abort(new DOMException("The operation timed out.", "TimeoutError"));
      expect(await screen.findByText(id.providers.smoke.noAnswer)).toBeInTheDocument();
      expect(await screen.findByText(id.providers.smoke.stillRunning)).toBeInTheDocument();
      await new Promise((resolve) => setTimeout(resolve, 1_100));
      expect(posts).toBe(1);
    } finally {
      spy.mockRestore();
    }
  });

  it("treats a gateway error page as an unknown outcome and keeps reading (silent-failure M3)", async () => {
    const state = { posted: false, landed: false };
    backend();
    server.use(
      lockAfterPost(state),
      http.post(SMOKE, () => {
        state.posted = true;
        return new HttpResponse("<html>504 Gateway Time-out</html>", {
          status: 504,
          headers: { "Content-Type": "text/html" },
        });
      }),
    );
    await open();
    await click();
    expect(await screen.findByText(id.providers.smoke.noAnswer)).toBeInTheDocument();
    expect(await screen.findByText(id.providers.smoke.stillRunning)).toBeInTheDocument();
  });

  it("stops waiting after the limit and says so (no endless polling)", async () => {
    await withFakeTimers(async () => {
      let reads = 0;
      const state = { posted: false, landed: false };
      backend();
      server.use(
        http.get(READINESS, () => {
          reads += 1;
          return ok(readiness({ job_id: OTHER_JOB_ID, running_job_id: state.posted ? JOB_ID : null })); // a stuck lock
        }),
        accepted(state),
      );
      await open();
      await click(true);
      await screen.findByText(id.providers.smoke.stillRunning);
      await act(() => vi.advanceTimersByTimeAsync(SMOKE_WAIT_LIMIT_MS + SMOKE_POLL_MS * 2));
      expect(await screen.findByText(id.providers.smoke.stalled)).toBeInTheDocument();
      const settled = reads;
      await act(() => vi.advanceTimersByTimeAsync(SMOKE_POLL_MS * 3));
      expect(reads).toBe(settled);
    });
  });

  it("says when readiness cannot be read while waiting, and gives up after the limit (AG-13 L1)", async () => {
    await withFakeTimers(async () => {
      let reads = 0;
      const state = { posted: false, landed: false };
      backend();
      server.use(
        http.get(READINESS, () => {
          reads += 1;
          return state.posted ? fail(403, "FORBIDDEN") : ok(readiness({ job_id: OTHER_JOB_ID }));
        }),
        accepted(state),
      );
      await open();
      await click(true);
      await act(() => vi.advanceTimersByTimeAsync(SMOKE_POLL_MS * 2));
      expect(await screen.findByText(id.providers.smoke.readFailed)).toBeInTheDocument();
      await act(() => vi.advanceTimersByTimeAsync(SMOKE_WAIT_LIMIT_MS + SMOKE_POLL_MS * 2));
      // It cannot know whether a report came: it says the reads failed, not "no report" (silent-failure M2).
      expect(await screen.findByText(id.providers.smoke.unreadable)).toBeInTheDocument();
      expect(screen.queryByText(id.providers.smoke.stalled)).toBeNull();
      const settled = reads;
      await act(() => vi.advanceTimersByTimeAsync(SMOKE_POLL_MS * 3));
      expect(reads).toBe(settled);
      await userEvent
        .setup({ advanceTimers: vi.advanceTimersByTime })
        .click(screen.getByRole("button", { name: id.providers.smoke.recheck }));
      await waitFor(() => expect(reads).toBe(settled + 1));
    });
  });

  it("stops reading a run it did not start once reads keep failing past the limit (AG-13 delta LOW-1)", async () => {
    await withFakeTimers(async () => {
      let reads = 0;
      backend();
      server.use(
        http.get(READINESS, () => {
          reads += 1;
          return reads === 1
            ? ok(readiness({ job_id: OTHER_JOB_ID, running_job_id: JOB_ID }))
            : fail(503, "DEPENDENCY_UNAVAILABLE");
        }),
      );
      await open();
      expect(await screen.findByText(id.providers.smoke.stillRunning)).toBeInTheDocument();
      // A run we did not start is read every few seconds while it holds the lock; failing reads end that.
      await act(() => vi.advanceTimersByTimeAsync(SMOKE_WAIT_LIMIT_MS + SMOKE_POLL_MS * 3));
      expect(await screen.findByText(id.providers.smoke.unreadable)).toBeInTheDocument();
      const settled = reads;
      await act(() => vi.advanceTimersByTimeAsync(SMOKE_POLL_MS * 5));
      expect(reads).toBe(settled);
    });
  });

  it("keeps the button off until readiness has been read, so a new report can be told apart (AG-13 delta LOW-2)", async () => {
    const held = deferred();
    backend();
    server.use(
      http.get(READINESS, async () => {
        await held.gate;
        return ok(readiness());
      }),
    );
    await open();
    expect(await screen.findByRole("button", { name: id.providers.smoke.run })).toBeDisabled();
    held.release();
    await waitFor(() => expect(screen.getByRole("button", { name: id.providers.smoke.run })).toBeEnabled());
  });

  it("shows a run already going (another admin, the CLI, or before navigating away) and keeps the button off", async () => {
    backend([provider()], readiness({ job_id: OTHER_JOB_ID, running_job_id: JOB_ID }));
    await open();
    expect(await screen.findByText(id.providers.smoke.stillRunning)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: id.providers.smoke.run })).toBeDisabled();
  });

  it("sends one request however fast the clicks come, and never retries a failed one (AG-13 L3)", async () => {
    const held = deferred();
    let posts = 0;
    backend();
    server.use(
      http.post(SMOKE, async () => {
        posts += 1;
        await held.gate;
        return fail(503, "DEPENDENCY_UNAVAILABLE");
      }),
    );
    await open();
    const button = await screen.findByRole("button", { name: id.providers.smoke.run });
    await waitFor(() => expect(button).toBeEnabled());
    // In one act: no re-render between the clicks, so only the guard stops the second and third.
    act(() => {
      button.click();
      button.click();
      button.click();
    });
    await new Promise((resolve) => setTimeout(resolve, 100));
    held.release();
    await screen.findByText(id.states.provider_degraded.title);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(posts).toBe(1);
  });

  it.each([
    [409, "CONFLICT", id.providers.smoke.busy],
    [429, "RATE_LIMITED", id.providers.smoke.rateLimited],
  ])("explains a %s", async (status, code, text) => {
    backend();
    server.use(http.post(SMOKE, () => fail(status, code)));
    await open();
    await click();
    expect(await screen.findByText(text)).toBeInTheDocument();
  });
});

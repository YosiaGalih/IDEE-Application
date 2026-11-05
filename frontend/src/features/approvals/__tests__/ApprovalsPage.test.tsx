import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { approval } from "@/__tests__/approvalFixtures";
import { API, deferred, fail, okPage, pendingApprovals, renderApp, signedIn } from "@/__tests__/harness";
import type { Approval } from "@/api/approvals";
import id from "@/i18n/locales/id.json";

const server = setupServer(pendingApprovals());
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  document.cookie = "kk_csrf=csrf-test; path=/";
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const other = approval({
  id: "8a3b1c2d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
  project_id: "1f2e3d4c-5b6a-4978-8695-a4b3c2d1e0f9",
  project_name: "Toko Online",
  payload_hash: "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
});

type Backend = {
  pending?: () => Response | Promise<Response>;
  history?: (query: URLSearchParams) => Response | Promise<Response>;
  decide?: (body: unknown) => Response | Promise<Response>;
};

/** The approvals API as this screen sees it; counts what arrived. */
function backend({ pending, history, decide }: Backend = {}) {
  const state = { pendingCalls: 0, historyQueries: [] as URLSearchParams[], decisions: [] as unknown[] };
  server.use(
    signedIn(),
    http.get(`${API}/approvals`, ({ request }) => {
      const query = new URL(request.url).searchParams;
      if (query.get("status") === "pending" && query.get("page_size") !== "1") {
        state.pendingCalls += 1;
        return pending ? pending() : okPage([approval(), other], { page_size: 200 });
      }
      if (query.get("status") === "pending") return okPage([], { page_size: 1, total: 2 });
      state.historyQueries.push(query);
      return history ? history(query) : okPage([], { page_size: 20 });
    }),
    http.post(`${API}/approvals/:approvalId/decision`, async ({ request }) => {
      const body = await request.json();
      state.decisions.push(body);
      return decide
        ? decide(body)
        : HttpResponse.json({
            request_id: "r-dec-0001",
            status: "completed",
            data: { ...approval(), status: "consumed", handler_status: "succeeded", can_decide: false },
          });
    }),
  );
  return state;
}

const decided = (overrides: Partial<Approval>, base: Approval = approval()) =>
  HttpResponse.json({
    request_id: "r-dec-0002",
    status: "completed",
    data: { ...base, can_decide: false, ...overrides },
  });
const pendingSection = () =>
  screen.getByRole("region", { name: id.approvals.pendingHeading.replace("{{count}}", "2") });
const cardOf = async (project: string) => {
  const group = await screen.findByRole("group", { name: project });
  return group;
};
const openDecision = async (project: string, which: "approve" | "reject") => {
  const card = await cardOf(project);
  await userEvent.click(within(card).getByRole("button", { name: id.approvals.card[which] }));
  return screen.findByRole("dialog");
};

describe("ApprovalsPage", () => {
  it("states that an agent can never approve, and groups pending approvals by project", async () => {
    backend();
    renderApp("/approvals");
    expect(await screen.findByRole("heading", { level: 1, name: id.screens.approvals })).toBeInTheDocument();
    expect(screen.getByText(id.approvals.subtitle)).toBeInTheDocument();
    await cardOf("Kalkulator API");
    expect(pendingSection()).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Toko Online" })).toBeInTheDocument();
  });

  it("shows the summary, the payload as text, the short hash, the requester, the expiry and the risk", async () => {
    const tricky = approval({
      summary: "Delete project (workspace kept 7 days, then purged)",
      payload: { project_id: approval().project_id, name: "<b>bold</b>", previous_status: "active" },
    });
    backend({ pending: () => okPage([tricky], { page_size: 200 }) });
    const { container } = renderApp("/approvals");
    const card = await screen.findByRole("group", { name: "Kalkulator API" });
    // The summary in the screen's language, from the hash-bound payload (not the server's English text).
    expect(
      within(card).getByText(id.approvals.summaries.project_delete.replace("{{name}}", "<b>bold</b>")),
    ).toBeInTheDocument();
    expect(within(card).getByText(/"name": "<b>bold<\/b>"/)).toBeInTheDocument();
    expect(container.querySelector("b")).toBeNull(); // shown as text, never as markup
    expect(within(card).getByText("abcdef012345…")).toBeInTheDocument();
    expect(within(card).getByText(tricky.requested_by)).toBeInTheDocument();
    expect(within(card).getByText(/R3/)).toHaveTextContent(id.approvals.risk[3]);
    expect(within(card).getByText(id.approvals.card.bound)).toBeInTheDocument();
  });

  it("shows an action type it does not know as it is, with the server's summary", async () => {
    const unknown = approval({ action_type: "deploy_canary", summary: "Deploy build 42 to canary" });
    backend({ pending: () => okPage([unknown], { page_size: 200 }) });
    renderApp("/approvals");
    const card = await screen.findByRole("group", { name: "Kalkulator API" });
    expect(
      within(card).getByText(id.approvals.card.required.replace("{{action}}", "deploy_canary")),
    ).toBeInTheDocument();
    expect(within(card).getByText("Deploy build 42 to canary")).toBeInTheDocument();
  });

  it("approves with the hash of the approval shown and announces the deletion", async () => {
    const state = backend();
    renderApp("/approvals");
    const dialog = await openDecision("Kalkulator API", "approve");
    expect(within(dialog).getByText(approval().payload_hash)).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText(id.approvals.dialog.note), "sudah dicek");
    await userEvent.click(within(dialog).getByRole("button", { name: id.approvals.dialog.confirmApprove }));
    expect(
      await screen.findByText(id.approvals.result.deleted.replace("{{name}}", "Kalkulator API")),
    ).toBeInTheDocument();
    expect(state.decisions).toEqual([
      { decision: "approve", payload_hash: approval().payload_hash, note: "sudah dicek" },
    ]);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("says the deletion will be retried when the handler failed, never that it is done", async () => {
    backend({ decide: () => decided({ status: "approved", handler_status: "failed" }) });
    renderApp("/approvals");
    const dialog = await openDecision("Kalkulator API", "approve");
    await userEvent.click(within(dialog).getByRole("button", { name: id.approvals.dialog.confirmApprove }));
    expect(
      await screen.findByText(id.approvals.result.approvedRetrying.replace("{{name}}", "Kalkulator API")),
    ).toBeInTheDocument();
    expect(screen.queryByText(id.approvals.result.deleted.replace("{{name}}", "Kalkulator API"))).toBeNull();
  });

  it("rejects without a note", async () => {
    const state = backend({
      decide: () => decided({ status: "rejected", handler_status: "succeeded" }, other),
    });
    renderApp("/approvals");
    const dialog = await openDecision("Toko Online", "reject");
    expect(within(dialog).getByText(other.payload_hash)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: id.approvals.dialog.confirmReject }));
    await screen.findByText(id.approvals.result.rejected.replace("{{name}}", "Toko Online"));
    expect(state.decisions).toEqual([{ decision: "reject", payload_hash: other.payload_hash, note: null }]);
  });

  it("sends one decision when the confirm button is clicked twice", async () => {
    const { gate, release } = deferred();
    const state = backend({
      decide: async () => {
        await gate;
        return decided({ status: "rejected", handler_status: "succeeded" });
      },
    });
    renderApp("/approvals");
    const dialog = await openDecision("Kalkulator API", "reject");
    const confirm = within(dialog).getByRole("button", { name: id.approvals.dialog.confirmReject });
    await userEvent.dblClick(confirm);
    release();
    await screen.findByText(id.approvals.result.rejected.replace("{{name}}", "Kalkulator API"));
    expect(state.decisions).toHaveLength(1);
  });

  it.each([
    ["expired", id.approvals.errors.expired],
    ["superseded", id.approvals.errors.superseded],
    ["consumed", id.approvals.errors.decided],
  ])("explains a %s approval and reloads the list", async (reason, text) => {
    const state = backend({
      decide: () =>
        HttpResponse.json(
          {
            request_id: "r-err-0409",
            status: "failed",
            error: { code: "INVALID_TRANSITION", message: "not pending", details: [{ reason }] },
          },
          { status: 409 },
        ),
    });
    renderApp("/approvals");
    const dialog = await openDecision("Kalkulator API", "approve");
    await userEvent.click(within(dialog).getByRole("button", { name: id.approvals.dialog.confirmApprove }));
    expect(await within(dialog).findByText(text)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: id.approvals.dialog.confirmApprove })).toBeDisabled();
    await waitFor(() => expect(state.pendingCalls).toBeGreaterThanOrEqual(2));
  });

  it("explains that the action changed since it was opened (payload hash mismatch)", async () => {
    backend({ decide: () => fail(409, "APPROVAL_PAYLOAD_MISMATCH") });
    renderApp("/approvals");
    const dialog = await openDecision("Kalkulator API", "approve");
    await userEvent.click(within(dialog).getByRole("button", { name: id.approvals.dialog.confirmApprove }));
    expect(await within(dialog).findByText(id.approvals.errors.mismatch)).toBeInTheDocument();
  });

  it("says the outcome is unknown when no answer came back", async () => {
    backend({ decide: () => Response.error() });
    renderApp("/approvals");
    const dialog = await openDecision("Kalkulator API", "approve");
    await userEvent.click(within(dialog).getByRole("button", { name: id.approvals.dialog.confirmApprove }));
    expect(await within(dialog).findByText(id.approvals.errors.outcomeUnknown)).toBeInTheDocument();
  });

  it("says when nothing is waiting", async () => {
    backend({ pending: () => okPage([], { page_size: 200 }) });
    renderApp("/approvals");
    expect(await screen.findByText(id.approvals.pendingEmpty.title)).toBeInTheDocument();
  });

  it("shows the error with its request id and retries", async () => {
    let failing = true;
    backend({
      pending: () => (failing ? fail(500, "INTERNAL_ERROR") : okPage([approval()], { page_size: 200 })),
    });
    renderApp("/approvals");
    expect(await screen.findByText(id.states.error.title)).toBeInTheDocument();
    expect(screen.getByText(id.common.requestId.replace("{{id}}", "r-err-0001"))).toBeInTheDocument();
    failing = false;
    await userEvent.click(screen.getByRole("button", { name: id.states.error.action }));
    await cardOf("Kalkulator API");
  });

  it("says how many are not shown when more are pending than one page holds", async () => {
    backend({ pending: () => okPage([approval()], { page_size: 200, total: 201 }) });
    renderApp("/approvals");
    expect(
      await screen.findByText(
        id.approvals.pendingTruncated.replace("{{shown}}", "1").replace("{{total}}", "201"),
      ),
    ).toBeInTheDocument();
  });

  it("lists the history with a status filter and pages, including requests still pending", async () => {
    const rows = [
      approval({ status: "rejected", decision_note: "tidak jadi", decided_at: "2026-11-05T10:00:00+00:00" }),
      approval({ id: "9b8a7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d", can_decide: false }),
    ];
    const state = backend({
      history: (query) => okPage(query.get("page") === "2" ? [] : rows, { page_size: 20, total: 21 }),
    });
    renderApp("/approvals");
    const table = await screen.findByRole("table", { name: id.approvals.history.caption });
    expect(within(table).getByText(id.approvals.status.rejected)).toBeInTheDocument();
    expect(within(table).getByText(id.approvals.status.pending)).toBeInTheDocument();
    expect(within(table).getByText(/tidak jadi/)).toBeInTheDocument();
    expect(Object.fromEntries(state.historyQueries[0] ?? [])).toMatchObject({ status: "all", page: "1" });

    await userEvent.selectOptions(screen.getByLabelText(id.approvals.history.filterLabel), "rejected");
    await waitFor(() => expect(state.historyQueries.some((q) => q.get("status") === "rejected")).toBe(true));
    await userEvent.click(await screen.findByRole("button", { name: id.approvals.history.next }));
    await waitFor(() => expect(state.historyQueries.some((q) => q.get("page") === "2")).toBe(true));
  });
});

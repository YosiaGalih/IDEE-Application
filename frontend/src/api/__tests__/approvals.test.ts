import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { approval } from "@/__tests__/approvalFixtures";
import { decideApproval, listApprovals } from "@/api/approvals";
import { deleteProject } from "@/api/projects";

const API = `${window.location.origin}/api/v1`;
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  document.cookie = "kk_csrf=csrf-test; path=/";
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const page = (data: unknown[], meta: unknown = { page: 1, page_size: 20, total: data.length }) =>
  HttpResponse.json({ request_id: "r-appr-0001", status: "completed", data, meta });

describe("listApprovals", () => {
  it("sends the filter and returns the rows with their page meta", async () => {
    let query: URLSearchParams | null = null;
    server.use(
      http.get(`${API}/approvals`, ({ request }) => {
        query = new URL(request.url).searchParams;
        return page([approval()], { page: 2, page_size: 20, total: 21 });
      }),
    );
    const result = await listApprovals({ status: "all", page: 2, pageSize: 20 });
    expect(Object.fromEntries(query ?? [])).toEqual({ status: "all", page: "2", page_size: "20" });
    expect(result.items[0]?.project_name).toBe("Kalkulator API");
    expect(result.meta.total).toBe(21);
  });

  it("refuses a list without its page meta: a later page would look like the end", async () => {
    server.use(http.get(`${API}/approvals`, () => page([approval()], null)));
    await expect(listApprovals({ status: "pending", page: 1, pageSize: 20 })).rejects.toMatchObject({
      kind: "bad_response",
    });
  });

  it("refuses a payload hash that is not 64 lowercase hex characters", async () => {
    server.use(http.get(`${API}/approvals`, () => page([approval({ payload_hash: "A".repeat(64) })])));
    await expect(listApprovals({ status: "pending", page: 1, pageSize: 20 })).rejects.toMatchObject({
      kind: "bad_response",
    });
  });
});

describe("decideApproval", () => {
  it("sends exactly the decision, the shown hash and the note, with the CSRF token", async () => {
    let body: unknown = null;
    let csrf: string | null = null;
    const shown = approval();
    server.use(
      http.post(`${API}/approvals/${shown.id}/decision`, async ({ request }) => {
        body = await request.json();
        csrf = request.headers.get("X-CSRF-Token");
        return HttpResponse.json({
          request_id: "r-dec-0001",
          status: "completed",
          data: { ...shown, status: "consumed", handler_status: "succeeded", can_decide: false },
        });
      }),
    );
    const decided = await decideApproval(shown.id, {
      decision: "approve",
      payload_hash: shown.payload_hash,
      note: "ok",
    });
    expect(body).toEqual({ decision: "approve", payload_hash: shown.payload_hash, note: "ok" });
    expect(csrf).toBe("csrf-test");
    expect(decided.status).toBe("consumed");
  });
});

describe("deleteProject", () => {
  it("asks for the delete and returns the approval and the project from the 202", async () => {
    const pending = approval();
    let method: string | null = null;
    server.use(
      http.delete(`${API}/projects/${pending.project_id}`, ({ request }) => {
        method = request.method;
        return HttpResponse.json(
          {
            request_id: "r-del-0001",
            status: "accepted",
            data: {
              approval: pending,
              project: {
                id: pending.project_id,
                name: pending.project_name,
                slug: "kalkulator-api",
                description: null,
                status: "deleting_pending_approval",
                stack: {},
                default_branch: "main",
                settings: {},
                owner_id: "0b6a3f1e-6a1f-4e0e-9a51-6c1c8d7f2a10",
                created_at: "2026-11-05T09:00:00+00:00",
                updated_at: "2026-11-05T09:18:20+00:00",
                repository: null,
                permissions: ["project:read", "project:delete"],
                stats: null,
              },
            },
          },
          { status: 202 },
        );
      }),
    );
    const result = await deleteProject(pending.project_id);
    expect(method).toBe("DELETE");
    expect(result.approval.id).toBe(pending.id);
    expect(result.project.status).toBe("deleting_pending_approval");
  });
});

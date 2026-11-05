import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { ApiError } from "@/api/errors";
import { commitChanges, createBranch, getBranches, getGitDiff, getGitLog, isReservedBranch } from "@/api/git";

const API = `${window.location.origin}/api/v1`;
const PROJECT = "6f2c1d3e-0a4b-4c5d-8e9f-0a1b2c3d4e5f";
const BASE = `${API}/projects/${PROJECT}/git`;
const COMMIT = "c".repeat(40);
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  document.cookie = "kk_csrf=csrf-test; path=/";
});

const ok = (data: unknown, status = 200) =>
  HttpResponse.json({ request_id: "r-git-0001", status: "completed", data }, { status });

const diff = (lineType = "add") => ({
  base: COMMIT,
  head: null,
  files: [
    {
      path: "a b.py",
      old_path: null,
      status: "modified",
      additions: 1,
      deletions: 0,
      is_binary: false,
      hunks: [
        {
          header: "@@ -1,1 +1,2 @@",
          old_start: 1,
          old_lines: 1,
          new_start: 1,
          new_lines: 2,
          lines: [
            { type: "context", old_no: 1, new_no: 1, text: "x = 1" },
            { type: lineType, old_no: null, new_no: 2, text: "y = 2" },
          ],
        },
      ],
    },
  ],
  stats: { files: 1, additions: 1, deletions: 0 },
  truncated: false,
});

describe("git client", () => {
  it("reads a file's diff and the whole working tree", async () => {
    const asked: (string | null)[] = [];
    server.use(
      http.get(`${BASE}/diff`, ({ request }) => {
        asked.push(new URL(request.url).searchParams.get("path"));
        return ok(diff());
      }),
    );
    expect((await getGitDiff(PROJECT, "a b.py")).files[0]?.hunks[0]?.lines[1]).toEqual({
      type: "add",
      old_no: null,
      new_no: 2,
      text: "y = 2",
    });
    await getGitDiff(PROJECT, null);
    expect(asked).toEqual(["a b.py", null]);
  });

  it("refuses a diff whose shape is unknown, instead of showing it as empty", async () => {
    server.use(http.get(`${BASE}/diff`, () => ok(diff("weird"))));
    await expect(getGitDiff(PROJECT, null)).rejects.toMatchObject({ kind: "bad_response" });
    await expect(getGitDiff(PROJECT, null)).rejects.toBeInstanceOf(ApiError);
  });

  it("commits explicit paths with the given key", async () => {
    let seen: { body: unknown; key: string | null } | null = null;
    server.use(
      http.post(`${BASE}/commit`, async ({ request }) => {
        seen = { body: await request.json(), key: request.headers.get("Idempotency-Key") };
        return ok({ commit: COMMIT }, 201);
      }),
    );
    expect(await commitChanges(PROJECT, { message: "m", paths: ["a.py"] }, "k-1")).toEqual({
      commit: COMMIT,
    });
    expect(seen).toEqual({ body: { message: "m", paths: ["a.py"] }, key: "k-1" });
  });

  it("creates a branch from a ref, and from HEAD when none is given", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.post(`${BASE}/branch`, async ({ request }) => {
        const body = (await request.json()) as { name: string };
        bodies.push(body);
        return ok({ name: body.name, head: COMMIT }, 201);
      }),
    );
    expect(await createBranch(PROJECT, { name: "fitur", from: "main" }, "k-2")).toEqual({
      name: "fitur",
      head: COMMIT,
    });
    await createBranch(PROJECT, { name: "fitur2", from: null }, "k-3");
    expect(bodies).toEqual([{ name: "fitur", from: "main" }, { name: "fitur2" }]);
  });

  it("reads branches and the log with its limit", async () => {
    let limit: string | null = null;
    server.use(
      http.get(`${BASE}/branches`, () =>
        ok([
          { name: "main", head: COMMIT, is_task_branch: false },
          { name: "kk/task-1", head: COMMIT, is_task_branch: true },
        ]),
      ),
      http.get(`${BASE}/log`, ({ request }) => {
        limit = new URL(request.url).searchParams.get("limit");
        return ok([
          {
            commit: COMMIT,
            author: "Tira <t@example.test>",
            date: "2026-11-05T08:00:00+07:00",
            message: "awal",
          },
        ]);
      }),
    );
    expect((await getBranches(PROJECT)).map((branch) => branch.is_task_branch)).toEqual([false, true]);
    expect((await getGitLog(PROJECT, 50))[0]?.message).toBe("awal");
    expect(limit).toBe("50");
  });

  it("knows the branch names the platform keeps for itself", () => {
    for (const name of ["kk", "HEAD", "kk/x", "refs/heads/x"]) expect(isReservedBranch(name)).toBe(true);
    for (const name of ["fitur/kk", "main", "kkx"]) expect(isReservedBranch(name)).toBe(false);
  });
});

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { useState } from "react";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { API, fail, pendingApprovals, renderApp } from "@/__tests__/harness";
import id from "@/i18n/locales/id.json";
import { GitPanel } from "../GitPanel";

const text = id.workspace.gitPanel;
const PROJECT = "6f2c1d3e-0a4b-4c5d-8e9f-0a1b2c3d4e5f";
const BASE = `${API}/projects/${PROJECT}/git`;
const HEAD = "1234567".padEnd(40, "a");
const NEW = "89abcde".padEnd(40, "b");

const server = setupServer(pendingApprovals());
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  document.cookie = "kk_csrf=csrf-test; path=/";
});

const ok = (data: unknown, status = 200) =>
  HttpResponse.json({ request_id: "r-git", status: "completed", data }, { status });
const conflict = (reason: string) =>
  HttpResponse.json(
    {
      request_id: "r-409",
      status: "failed",
      error: { code: "CONFLICT", message: "x", details: [{ reason }] },
    },
    { status: 409 },
  );

type Change = { path: string; status: string };
type Options = { changes?: Change[]; commit?: () => Response; branch?: () => Response; logSize?: number };

function backend({ changes = [], commit, branch, logSize = 3 }: Options = {}) {
  const state = {
    changes,
    head: HEAD,
    branch: "main" as string | null,
    statusReads: 0,
    logLimits: [] as number[],
    commits: [] as { body: unknown; key: string | null }[],
    branches: [] as { body: unknown; key: string | null }[],
  };
  server.use(
    http.get(`${BASE}/status`, () => {
      state.statusReads += 1;
      return ok({
        branch: state.branch,
        head: state.head,
        clean: state.changes.length === 0,
        changes: state.changes,
      });
    }),
    http.get(`${BASE}/branches`, () =>
      ok([
        { name: "main", head: HEAD, is_task_branch: false },
        { name: "kk/task-1", head: HEAD, is_task_branch: true },
      ]),
    ),
    http.get(`${BASE}/log`, ({ request }) => {
      const limit = Number(new URL(request.url).searchParams.get("limit"));
      state.logLimits.push(limit);
      return ok(
        Array.from({ length: Math.min(limit, logSize) }, (_, index) => ({
          commit: String(index).padStart(40, "0"),
          author: "Tira <t@example.test>",
          date: "2026-11-05T08:00:00+07:00",
          message: `commit ${index}`,
        })),
      );
    }),
    http.post(`${BASE}/commit`, async ({ request }) => {
      state.commits.push({ body: await request.json(), key: request.headers.get("Idempotency-Key") });
      return commit ? commit() : ok({ commit: NEW }, 201);
    }),
    http.post(`${BASE}/branch`, async ({ request }) => {
      const body = (await request.json()) as { name: string };
      state.branches.push({ body, key: request.headers.get("Idempotency-Key") });
      return branch ? branch() : ok({ name: body.name, head: HEAD }, 201);
    }),
  );
  return state;
}

type PanelProps = { canRead?: boolean; canCommit?: boolean; canBranch?: boolean; unsavedPaths?: string[] };

function renderPanel({
  canRead = true,
  canCommit = true,
  canBranch = true,
  unsavedPaths = [],
}: PanelProps = {}) {
  return renderApp("/", [
    {
      path: "/",
      element: (
        <GitPanel
          projectId={PROJECT}
          canRead={canRead}
          canCommit={canCommit}
          canBranch={canBranch}
          unsavedPaths={unsavedPaths}
        />
      ),
    },
  ]);
}

const pick = (path: string) => screen.getByRole("checkbox", { name: text.pickOf.replace("{{path}}", path) });
const message = () => screen.getByLabelText(text.commit.message);
const commitButton = () => screen.getByRole("button", { name: text.commit.submit });

describe("GitPanel", () => {
  it("says the tree is clean, with the branch and head", async () => {
    backend();
    renderPanel();
    expect(await screen.findByText(text.clean)).toBeInTheDocument();
    expect(
      screen.getByText(text.head.replace("{{branch}}", "main").replace("{{head}}", "1234567")),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText(text.commit.message)).toBeNull();
  });

  it("commits only the files that are listed and ticked", async () => {
    const state = backend({
      changes: [
        { path: "a.py", status: "modified" },
        { path: "b.py", status: "untracked" },
      ],
    });
    const { queryClient } = renderPanel();
    await userEvent.click(
      await screen.findByRole("checkbox", { name: text.pickOf.replace("{{path}}", "b.py") }),
    );
    // The list is read again with a new file while the user writes the message.
    state.changes = [...state.changes, { path: "c.py", status: "added" }];
    await act(() => queryClient.refetchQueries({ queryKey: ["workspace", PROJECT, "git-status"] }));
    await screen.findByRole("checkbox", { name: text.pickOf.replace("{{path}}", "c.py") });
    expect(pick("b.py")).not.toBeChecked();
    // c.py appeared while the user was looking: it is named, not ticked.
    expect(pick("c.py")).not.toBeChecked();
    expect(
      screen.getByText(text.fresh.replace("{{count}}", "1").replace("{{paths}}", "c.py")),
    ).toBeInTheDocument();
    await userEvent.type(message(), "Perbaiki hitung");
    const readsBefore = state.statusReads;
    await userEvent.click(commitButton());
    expect(await screen.findByText(text.commit.done.replace("{{sha}}", "89abcde"))).toBeInTheDocument();
    expect(state.commits[0]?.body).toEqual({ message: "Perbaiki hitung", paths: ["a.py"] });
    await waitFor(() => expect(state.statusReads).toBeGreaterThan(readsBefore));
    await waitFor(() => expect(state.logLimits.length).toBeGreaterThan(1));
    expect(message()).toHaveValue("");
  });

  it("retries an unknown commit with the same key, and a changed one with a new key", async () => {
    let answer: "lost" | "ok" = "lost";
    const state = backend({
      changes: [{ path: "a.py", status: "modified" }],
      commit: () => (answer === "lost" ? HttpResponse.error() : ok({ commit: NEW }, 201)),
    });
    renderPanel();
    await userEvent.type(await screen.findByLabelText(text.commit.message), "satu");
    await userEvent.click(commitButton());
    expect(await screen.findByText(text.commit.unknownOutcome, { exact: false })).toBeInTheDocument();
    await userEvent.click(commitButton());
    await waitFor(() => expect(state.commits).toHaveLength(2));
    expect(state.commits[1]?.key).toBe(state.commits[0]?.key);
    await userEvent.type(message(), " lagi");
    answer = "ok";
    await userEvent.click(commitButton());
    await waitFor(() => expect(state.commits).toHaveLength(3));
    expect(state.commits[2]?.key).not.toBe(state.commits[0]?.key);
    expect(await screen.findByText(text.commit.done.replace("{{sha}}", "89abcde"))).toBeInTheDocument();
  });

  it("keeps the commit's answer when the commit took the last change", async () => {
    const state = backend({ changes: [{ path: "a.py", status: "modified" }] });
    renderPanel();
    await userEvent.type(await screen.findByLabelText(text.commit.message), "semua");
    state.changes = []; // what the server lists once the commit landed
    await userEvent.click(commitButton());
    expect(await screen.findByText(text.clean)).toBeInTheDocument();
    expect(screen.getByText(text.commit.done.replace("{{sha}}", "89abcde"))).toBeInTheDocument();
  });

  it("never reuses a kept key once the repository moved on", async () => {
    let lost = true;
    const state = backend({
      changes: [{ path: "a.py", status: "modified" }],
      commit: () => (lost ? HttpResponse.error() : ok({ commit: NEW }, 201)),
    });
    const { queryClient } = renderPanel();
    await userEvent.type(await screen.findByLabelText(text.commit.message), "Fix");
    await userEvent.click(commitButton());
    expect(await screen.findByText(text.commit.unknownOutcome, { exact: false })).toBeInTheDocument();
    // The lost commit had landed: HEAD moved. The same file and message later are a new commit.
    state.head = NEW;
    await act(() => queryClient.refetchQueries({ queryKey: ["workspace", PROJECT, "git-status"] }));
    await screen.findByText(text.head.replace("{{branch}}", "main").replace("{{head}}", "89abcde"));
    lost = false;
    await userEvent.click(commitButton());
    await waitFor(() => expect(state.commits).toHaveLength(2));
    expect(state.commits[1]?.key).not.toBe(state.commits[0]?.key);
  });

  it("refuses more files than one commit may take, before asking", async () => {
    const state = backend({
      changes: Array.from({ length: 501 }, (_, index) => ({ path: `f${index}.txt`, status: "untracked" })),
    });
    renderPanel();
    // 501 rows: role queries compute 500 accessible names in jsdom, so this test finds elements by text.
    fireEvent.change(await screen.findByLabelText(text.commit.message), { target: { value: "banyak" } });
    expect(screen.getByText(text.commit.tooMany)).toBeInTheDocument();
    expect(screen.getByText(text.commit.submit, { selector: "button" })).toBeDisabled();
    expect(state.commits).toHaveLength(0);
  }, 20_000);

  it("says the server refused the commit's input, with its request id", async () => {
    backend({
      changes: [{ path: "a.py", status: "modified" }],
      commit: () =>
        HttpResponse.json(
          {
            request_id: "r-400",
            status: "failed",
            error: { code: "BAD_REQUEST", message: "x", details: [{ reason: "path" }] },
          },
          { status: 400 },
        ),
    });
    renderPanel();
    await userEvent.type(await screen.findByLabelText(text.commit.message), "m");
    await userEvent.click(commitButton());
    const refused = await screen.findByText(text.commit.invalid, { exact: false });
    expect(refused).toHaveTextContent("r-400");
  });

  it("says when there is nothing to commit, never that it committed", async () => {
    backend({ changes: [{ path: "a.py", status: "modified" }], commit: () => conflict("nothing_to_commit") });
    renderPanel();
    await userEvent.type(await screen.findByLabelText(text.commit.message), "m");
    await userEvent.click(commitButton());
    expect(await screen.findByText(text.commit.nothing)).toBeInTheDocument();
    expect(screen.queryByText(/dibuat\./)).toBeNull();
  });

  it("allows a commit only with a message and at least one file", async () => {
    backend({ changes: [{ path: "a.py", status: "modified" }] });
    renderPanel();
    await userEvent.type(await screen.findByLabelText(text.commit.message), "   ");
    expect(commitButton()).toBeDisabled();
    await userEvent.type(message(), "ok");
    expect(commitButton()).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: text.clearAll }));
    expect(commitButton()).toBeDisabled();
    expect(screen.getByText(text.commit.picked.replace("{{count}}", "0"))).toBeInTheDocument();
  });

  it("warns that unsaved tabs are not in the commit", async () => {
    backend({ changes: [{ path: "a.py", status: "modified" }] });
    renderPanel({ unsavedPaths: ["a.py", "src/b.py"] });
    expect(
      await screen.findByText(
        text.commit.unsaved.replace("{{count}}", "2").replace("{{paths}}", "a.py, src/b.py"),
      ),
    ).toBeInTheDocument();
  });

  it("creates a branch and says the working tree stays where it is", async () => {
    let exists = false;
    const state = backend({
      branch: () => (exists ? conflict("branch_exists") : ok({ name: "fitur", head: HEAD }, 201)),
    });
    renderPanel();
    await userEvent.type(await screen.findByLabelText(text.branch.name), "fitur");
    await userEvent.selectOptions(screen.getByLabelText(text.branch.from), "main");
    await userEvent.click(screen.getByRole("button", { name: text.branch.submit }));
    expect(
      await screen.findByText(
        text.branch.done
          .replace("{{name}}", "fitur")
          .replace("{{head}}", "1234567")
          .replace("{{current}}", "main"),
      ),
    ).toBeInTheDocument();
    expect(state.branches[0]?.body).toEqual({ name: "fitur", from: "main" });
    exists = true;
    await userEvent.type(screen.getByLabelText(text.branch.name), "fitur");
    await userEvent.click(screen.getByRole("button", { name: text.branch.submit }));
    expect(await screen.findByText(text.branch.exists)).toBeInTheDocument();
  });

  it("tells a refused starting point from a refused name", async () => {
    let answer: Response = ok({}, 201);
    const state = backend({ branch: () => answer });
    renderPanel();
    const name = await screen.findByLabelText(text.branch.name);
    const submit = () => userEvent.click(screen.getByRole("button", { name: text.branch.submit }));
    const refusal = (code: string, status: number, reason: string) =>
      HttpResponse.json(
        { request_id: "r-x", status: "failed", error: { code, message: "x", details: [{ reason }] } },
        { status },
      );
    answer = refusal("GIT_ERROR", 409, "unknown_ref");
    await userEvent.type(name, "satu");
    await submit();
    expect(await screen.findByText(text.branch.invalidFrom, { exact: false })).toBeInTheDocument();
    answer = refusal("BAD_REQUEST", 400, "branch");
    await userEvent.type(name, "a");
    await submit();
    expect(await screen.findByText(text.branch.invalid, { exact: false })).toBeInTheDocument();
    await userEvent.clear(name);
    await userEvent.type(name, "x".repeat(256));
    await submit();
    expect(await screen.findByText(text.branch.tooLong)).toBeInTheDocument();
    expect(state.branches).toHaveLength(2);
  });

  it("does not name a current branch it does not know", async () => {
    const state = backend();
    state.branch = null;
    renderPanel();
    await userEvent.type(await screen.findByLabelText(text.branch.name), "fitur");
    await userEvent.click(screen.getByRole("button", { name: text.branch.submit }));
    expect(
      await screen.findByText(
        text.branch.doneNoCurrent.replace("{{name}}", "fitur").replace("{{head}}", "1234567"),
      ),
    ).toBeInTheDocument();
  });

  it("refuses reserved branch names before asking the server", async () => {
    const state = backend();
    renderPanel();
    await userEvent.type(await screen.findByLabelText(text.branch.name), "kk/punyaku");
    await userEvent.click(screen.getByRole("button", { name: text.branch.submit }));
    expect(await screen.findByText(text.branch.reserved)).toBeInTheDocument();
    expect(state.branches).toHaveLength(0);
  });

  it("marks the current branch and task branches", async () => {
    backend();
    renderPanel();
    const list = await screen.findByRole("list", { name: text.branch.title });
    const main = within(list).getByText("main").closest("li");
    expect(main).toHaveTextContent(text.branch.current);
    expect(within(list).getByText("kk/task-1").closest("li")).toHaveTextContent(text.branch.task);
  });

  it("loads more log entries up to the API's limit, then says the limit is reached", async () => {
    const state = backend({ logSize: 500 });
    renderPanel();
    for (const limit of [20, 50, 100]) {
      await screen.findByText(`commit ${limit - 1}`);
      await userEvent.click(screen.getByRole("button", { name: text.log.more }));
    }
    await screen.findByText("commit 199");
    expect(screen.queryByRole("button", { name: text.log.more })).toBeNull();
    expect(screen.getByText(text.log.limit.replace("{{count}}", "200"))).toBeInTheDocument();
    expect(state.logLimits).toEqual([20, 50, 100, 200]);
  });

  it("keeps the commits it read when loading more fails, and says so", async () => {
    const state = backend({ logSize: 500 });
    renderPanel();
    await screen.findByText("commit 19");
    server.use(http.get(`${BASE}/log`, () => fail(500, "INTERNAL_ERROR")));
    await userEvent.click(screen.getByRole("button", { name: text.log.more }));
    expect(await screen.findByText(text.log.stale, { exact: false })).toBeInTheDocument();
    expect(screen.getByText("commit 19")).toBeInTheDocument();
    expect(state.logLimits[0]).toBe(20);
  });

  it("reads the status again each time the view is opened", async () => {
    const state = backend();
    function Switch() {
      const [isActive, setIsActive] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setIsActive(true)}>
            show
          </button>
          <GitPanel projectId={PROJECT} canRead canCommit canBranch unsavedPaths={[]} isActive={isActive} />
        </>
      );
    }
    renderApp("/", [{ path: "/", element: <Switch /> }]);
    await screen.findByText(text.clean);
    const reads = state.statusReads;
    await userEvent.click(screen.getByRole("button", { name: "show" }));
    await waitFor(() => expect(state.statusReads).toBeGreaterThan(reads));
  });

  it("shows only what the user may do", async () => {
    backend({ changes: [{ path: "a.py", status: "modified" }] });
    const view = renderPanel({ canCommit: false, canBranch: false });
    expect(await screen.findByText("a.py")).toBeInTheDocument();
    expect(screen.queryByLabelText(text.commit.message)).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByLabelText(text.branch.name)).toBeNull();
    view.unmount();
    renderPanel({ canRead: false });
    expect(await screen.findByText(id.states.permission_denied.title)).toBeInTheDocument();
  });

  it("keeps the change list it read when status cannot be read again, and says so", async () => {
    backend({ changes: [{ path: "a.py", status: "modified" }] });
    const { queryClient } = renderPanel();
    expect(await screen.findByText("a.py")).toBeInTheDocument();
    server.use(http.get(`${BASE}/status`, () => fail(500, "INTERNAL_ERROR")));
    await act(() => queryClient.refetchQueries({ queryKey: ["workspace", PROJECT, "git-status"] }));
    expect(await screen.findByText(text.statusStale, { exact: false })).toBeInTheDocument();
    expect(screen.getByText("a.py")).toBeInTheDocument();
  });

  it("opens the diff of one file and of the whole tree", async () => {
    const asked: (string | null)[] = [];
    backend({ changes: [{ path: "a.py", status: "modified" }] });
    server.use(
      http.get(`${BASE}/diff`, ({ request }) => {
        asked.push(new URL(request.url).searchParams.get("path"));
        return ok({
          base: HEAD,
          head: null,
          files: [],
          stats: { files: 0, additions: 0, deletions: 0 },
          truncated: false,
        });
      }),
    );
    renderPanel();
    await userEvent.click(
      await screen.findByRole("button", { name: text.showDiffOf.replace("{{path}}", "a.py") }),
    );
    const dialog = await screen.findByRole("dialog", { name: text.diff.titleOf.replace("{{path}}", "a.py") });
    expect(await within(dialog).findByText(text.diff.empty)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: text.diff.close }));
    await userEvent.click(screen.getByRole("button", { name: text.showAllDiff }));
    await screen.findByRole("dialog", { name: text.diff.titleAll });
    expect(asked).toEqual(["a.py", null]);
  });
});

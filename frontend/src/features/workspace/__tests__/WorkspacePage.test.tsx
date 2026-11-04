import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { project as projectFixture } from "@/__tests__/dashboardFixtures";
import { API, deferred, fail, renderApp, signedIn } from "@/__tests__/harness";
import { getProject } from "@/api/projects";
import id from "@/i18n/locales/id.json";

// jsdom cannot run Monaco: the editor is a textarea that keeps the same contract.
vi.mock("@/features/workspace/CodeEditor", () => ({
  CodeEditor: (props: {
    ariaLabel: string;
    path: string;
    value: string;
    isReadOnly: boolean;
    reveal?: { path: string; line: number } | null;
    onChange: (value: string) => void;
    onSave: () => void;
  }) => (
    <>
      <textarea
        aria-label={props.ariaLabel}
        value={props.value}
        readOnly={props.isReadOnly}
        data-reveal-line={props.reveal?.path === props.path ? props.reveal.line : ""}
        onChange={(event) => props.onChange(event.target.value)}
        onKeyDown={(event) => {
          if ((event.ctrlKey || event.metaKey) && event.key === "s") {
            event.preventDefault();
            props.onSave();
          }
        }}
      />
      {/* Monaco can deliver a keystroke and Ctrl+S before React renders the keystroke. */}
      <button
        type="button"
        aria-label={`type-then-save ${props.ariaLabel}`}
        onClick={() => {
          props.onChange(`${props.value}!`);
          props.onSave();
        }}
      />
    </>
  ),
  DiffView: (props: { original: string; modified: string }) => (
    <div data-testid="diff">{`${props.original}|${props.modified}`}</div>
  ),
  disposeModel: () => undefined,
  disposeProjectModels: () => undefined,
}));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  document.cookie = "kk_csrf=csrf-test; path=/";
});

const PROJECT = projectFixture().id;
const BASE = `${API}/projects/${PROJECT}`;
const SHA_1 = "1".repeat(64);
const SHA_2 = "2".repeat(64);
const SHA_3 = "3".repeat(64);
const WRITE_PERMS = ["project:read", "project:write", "workspace:read", "workspace:write", "git:read"];

const ok = (data: unknown, meta: unknown = null) =>
  HttpResponse.json({ request_id: "r-ws", status: "completed", data, meta });
const conflict = (sha: string | null) =>
  HttpResponse.json(
    {
      request_id: "r-409",
      status: "failed",
      error: {
        code: "VERSION_CONFLICT",
        message: "changed",
        details: [{ path: "app.py", current_sha256: sha }],
      },
    },
    { status: 409 },
  );
const textFile = (path: string, content: string, sha = SHA_1) => ({
  path,
  content,
  encoding: "utf-8",
  size: content.length,
  sha256: sha,
  language: "python",
  too_large: false,
});

type Options = {
  permissions?: string[];
  files?: Record<string, unknown>;
  put?: (
    body: { content: string; expected_sha256: string | null },
    path: string,
  ) => Response | Promise<Response>;
  gitFails?: boolean;
  tree?: Record<string, { entries: unknown[]; truncated?: boolean }>;
};

function backend({ permissions = WRITE_PERMS, files = {}, put, gitFails = false, tree }: Options = {}) {
  const state = {
    puts: [] as { path: string; body: unknown }[],
    fileReads: [] as string[],
    treeReads: [] as string[],
  };
  const trees = tree ?? {
    ".": {
      entries: [
        { path: "src", name: "src", type: "dir", size: null },
        { path: "app.py", name: "app.py", type: "file", size: 12 },
        { path: "logo.png", name: "logo.png", type: "file", size: 90 },
      ],
    },
    src: { entries: [{ path: "src/util.py", name: "util.py", type: "file", size: 20 }], truncated: true },
  };
  server.use(
    signedIn(),
    http.get(`${API}/projects/${PROJECT}`, () => ok(projectFixture({ permissions }))),
    http.get(`${BASE}/tree`, ({ request }) => {
      const path = new URL(request.url).searchParams.get("path") ?? ".";
      state.treeReads.push(path);
      const folder = trees[path] ?? { entries: [] };
      return ok(folder.entries, { truncated: folder.truncated ?? false });
    }),
    http.get(`${BASE}/git/status`, () =>
      gitFails
        ? fail(500, "INTERNAL_ERROR")
        : ok({
            branch: "main",
            head: "c".repeat(40),
            clean: false,
            changes: [{ path: "app.py", status: "modified" }],
          }),
    ),
    http.get(`${BASE}/files/*`, ({ request }) => {
      const path = decodeURIComponent(new URL(request.url).pathname.split("/files/")[1] ?? "");
      state.fileReads.push(path);
      const file = files[path];
      return file ? ok(file) : fail(404, "NOT_FOUND", "gone");
    }),
    http.put(`${BASE}/files/*`, async ({ request }) => {
      const path = decodeURIComponent(new URL(request.url).pathname.split("/files/")[1] ?? "");
      const body = (await request.json()) as { content: string; expected_sha256: string | null };
      state.puts.push({ path, body });
      return put ? put(body, path) : ok({ path, sha256: SHA_2 });
    }),
  );
  return state;
}

const editorOf = (path: string) =>
  screen.findByRole("textbox", { name: id.workspace.editor.editorOf.replace("{{name}}", path) });
const openFromExplorer = async (name: string) =>
  userEvent.click(
    await screen.findByRole("button", { name: id.workspace.explorer.openOf.replace("{{name}}", name) }),
  );
const status = (label: string) => screen.getAllByText(label);
const openFiles = () => screen.getByRole("list", { name: id.workspace.editor.openFiles });

describe("WorkspacePage", () => {
  it("loads the root, opens folders lazily and marks changed files", async () => {
    backend({ files: { "app.py": textFile("app.py", "x = 1\n") } });
    renderApp(`/projects/${PROJECT}/workspace`);
    expect(await screen.findByRole("heading", { level: 1, name: "Kalkulator" })).toBeInTheDocument();
    const explorer = await screen.findByRole("navigation", { name: id.workspace.explorer.title });
    expect(await within(explorer).findByText(id.workspace.git.modified)).toBeInTheDocument();
    expect(within(explorer).queryByText("util.py")).toBeNull();
    await userEvent.click(
      within(explorer).getByRole("button", {
        name: id.workspace.explorer.expandOf.replace("{{name}}", "src"),
      }),
    );
    expect(await within(explorer).findByText("util.py")).toBeInTheDocument();
    expect(within(explorer).getByText(id.workspace.explorer.truncated)).toBeInTheDocument();
  });

  it("says why the tree could not be read and reads it again on request", async () => {
    let refuse = true;
    backend();
    server.use(
      http.get(`${BASE}/tree`, () =>
        refuse
          ? fail(403, "FORBIDDEN")
          : ok([{ path: "a.py", name: "a.py", type: "file", size: 1 }], { truncated: false }),
      ),
    );
    renderApp(`/projects/${PROJECT}/workspace`);
    const explorer = await screen.findByRole("navigation", { name: id.workspace.explorer.title });
    const alert = await within(explorer).findByRole("alert");
    expect(alert).toHaveTextContent(id.workspace.explorer.loadFailed);
    expect(alert).toHaveTextContent(id.states.permission_denied.title);
    refuse = false;
    await userEvent.click(within(alert).getByRole("button", { name: id.workspace.retry }));
    expect(await within(explorer).findByText("a.py")).toBeInTheDocument();
    expect(within(explorer).queryByRole("alert")).toBeNull();
  });

  it("never calls a cut-off folder with no entries empty", async () => {
    backend({ tree: { ".": { entries: [], truncated: true } } });
    renderApp(`/projects/${PROJECT}/workspace`);
    const explorer = await screen.findByRole("navigation", { name: id.workspace.explorer.title });
    expect(await within(explorer).findByText(id.workspace.explorer.truncated)).toBeInTheDocument();
    expect(within(explorer).queryByText(id.workspace.explorer.empty)).toBeNull();
  });

  it("drops Git markers it can no longer confirm", async () => {
    backend();
    const { queryClient } = renderApp(`/projects/${PROJECT}/workspace`);
    const explorer = await screen.findByRole("navigation", { name: id.workspace.explorer.title });
    expect(await within(explorer).findByText(id.workspace.git.modified)).toBeInTheDocument();
    server.use(http.get(`${BASE}/git/status`, () => fail(500, "INTERNAL_ERROR")));
    await act(() => queryClient.refetchQueries({ queryKey: ["workspace", PROJECT, "git-status"] }));
    expect(await within(explorer).findByText(id.workspace.explorer.gitUnavailable)).toBeInTheDocument();
    expect(within(explorer).queryByText(id.workspace.git.modified)).toBeNull();
  });

  it("keeps the explorer when git status cannot be read, and says so", async () => {
    backend({ gitFails: true });
    renderApp(`/projects/${PROJECT}/workspace`);
    expect(
      await screen.findByText(id.workspace.explorer.gitUnavailable, undefined, { timeout: 4000 }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: id.workspace.explorer.openOf.replace("{{name}}", "app.py") }),
    ).toBeInTheDocument();
  });

  it("opens a file once, saves it with the sha it was read at, and says saved only after the server agreed", async () => {
    const state = backend({ files: { "app.py": textFile("app.py", "x = 1\n") } });
    renderApp(`/projects/${PROJECT}/workspace`);
    await openFromExplorer("app.py");
    await openFromExplorer("app.py");
    expect(within(openFiles()).getAllByRole("button", { name: /^app\.py/ })).toHaveLength(1);
    const editor = await editorOf("app.py");
    fireEvent.change(editor, { target: { value: "x = 2\n" } });
    expect(status(id.workspace.editor.status.dirty).length).toBeGreaterThan(0);
    fireEvent.keyDown(editor, { key: "s", ctrlKey: true });
    await waitFor(() => expect(state.puts).toHaveLength(1));
    expect(state.puts[0]).toEqual({ path: "app.py", body: { content: "x = 2\n", expected_sha256: SHA_1 } });
    expect(await screen.findAllByText(id.workspace.editor.status.saved)).not.toHaveLength(0);
    // The explorer re-reads the folder, so the size it shows is the saved file's.
    await waitFor(() => expect(state.treeReads.filter((path) => path === ".")).toHaveLength(2));
    // The next save uses the sha the server returned.
    fireEvent.change(editor, { target: { value: "x = 3\n" } });
    await userEvent.click(
      screen.getByRole("button", { name: id.workspace.editor.saveOf.replace("{{name}}", "app.py") }),
    );
    await waitFor(() => expect(state.puts).toHaveLength(2));
    expect(state.puts[1]?.body).toEqual({ content: "x = 3\n", expected_sha256: SHA_2 });
  });

  it("saves the text on screen when Ctrl+S lands before the last keystroke has rendered", async () => {
    const state = backend({ files: { "app.py": textFile("app.py", "x = 1\n") } });
    renderApp(`/projects/${PROJECT}/workspace`);
    await openFromExplorer("app.py");
    await editorOf("app.py");
    const label = id.workspace.editor.editorOf.replace("{{name}}", "app.py");
    await userEvent.click(screen.getByRole("button", { name: `type-then-save ${label}` }));
    await waitFor(() => expect(state.puts).toHaveLength(1));
    expect(state.puts[0]?.body).toEqual({ content: "x = 1\n!", expected_sha256: SHA_1 });
    expect(await screen.findAllByText(id.workspace.editor.status.saved)).not.toHaveLength(0);
  });

  it("never overwrites on a conflict: the user reloads, or overwrites against the sha just read", async () => {
    let round = 0;
    const files: Record<string, unknown> = { "app.py": textFile("app.py", "x = 1\n") };
    const state = backend({
      files,
      put: (body) => {
        round += 1;
        if (round === 1) {
          files["app.py"] = textFile("app.py", "theirs\n", SHA_3);
          return conflict(SHA_3);
        }
        if (round === 2) return conflict(SHA_3); // changed again before the overwrite: a conflict again
        return ok({ path: "app.py", sha256: SHA_2, expected: body.expected_sha256 });
      },
    });
    renderApp(`/projects/${PROJECT}/workspace`);
    await openFromExplorer("app.py");
    const editor = await editorOf("app.py");
    fireEvent.change(editor, { target: { value: "mine\n" } });
    fireEvent.keyDown(editor, { key: "s", ctrlKey: true });
    const dialog = await screen.findByRole("dialog", { name: id.workspace.conflict.title });
    expect(status(id.workspace.editor.status.conflict).length).toBeGreaterThan(0);
    await userEvent.click(await within(dialog).findByRole("button", { name: id.workspace.conflict.diff }));
    expect((await within(dialog).findByTestId("diff")).textContent).toBe("theirs\n|mine\n");
    await userEvent.click(within(dialog).getByRole("button", { name: id.workspace.conflict.overwrite }));
    await waitFor(() => expect(state.puts).toHaveLength(2));
    expect(state.puts[1]?.body).toEqual({ content: "mine\n", expected_sha256: SHA_3 });
    // The second conflict opens the dialog again instead of writing.
    const again = await screen.findByRole("dialog", { name: id.workspace.conflict.title });
    await userEvent.click(await within(again).findByRole("button", { name: id.workspace.conflict.reload }));
    await userEvent.click(within(again).getByRole("button", { name: id.workspace.conflict.reload }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await editorOf("app.py")).toHaveValue("theirs\n");
    expect(state.puts).toHaveLength(2);
  });

  it("writes once when Overwrite is pressed twice, and says in the dialog when that write fails", async () => {
    const answer = deferred();
    let round = 0;
    const files: Record<string, unknown> = { "app.py": textFile("app.py", "x = 1\n") };
    const state = backend({
      files,
      put: async () => {
        round += 1;
        if (round === 1) {
          files["app.py"] = textFile("app.py", "theirs\n", SHA_3);
          return conflict(SHA_3);
        }
        await answer.gate;
        return fail(500, "INTERNAL_ERROR");
      },
    });
    renderApp(`/projects/${PROJECT}/workspace`);
    await openFromExplorer("app.py");
    const editor = await editorOf("app.py");
    fireEvent.change(editor, { target: { value: "mine\n" } });
    fireEvent.keyDown(editor, { key: "s", ctrlKey: true });
    const dialog = await screen.findByRole("dialog", { name: id.workspace.conflict.title });
    const overwrite = await within(dialog).findByRole("button", { name: id.workspace.conflict.overwrite });
    fireEvent.click(overwrite);
    fireEvent.click(overwrite);
    await waitFor(() => expect(overwrite).toBeDisabled());
    expect(within(dialog).getByRole("button", { name: id.workspace.conflict.reload })).toBeDisabled();
    answer.release();
    expect(await within(dialog).findByText(id.workspace.editor.failed.generic)).toBeInTheDocument();
    expect(state.puts).toHaveLength(2);
    expect(overwrite).toBeEnabled();
  });

  it("never offers to load a server version that is not text into the editor", async () => {
    const files: Record<string, unknown> = { "app.py": textFile("app.py", "x = 1\n") };
    backend({
      files,
      put: () => {
        files["app.py"] = {
          path: "app.py",
          content: null,
          encoding: "binary",
          size: 9,
          sha256: SHA_3,
          language: null,
          too_large: false,
        };
        return conflict(SHA_3);
      },
    });
    renderApp(`/projects/${PROJECT}/workspace`);
    await openFromExplorer("app.py");
    const editor = await editorOf("app.py");
    fireEvent.change(editor, { target: { value: "mine\n" } });
    fireEvent.keyDown(editor, { key: "s", ctrlKey: true });
    const dialog = await screen.findByRole("dialog", { name: id.workspace.conflict.title });
    expect(await within(dialog).findByText(id.workspace.conflict.notText)).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: id.workspace.conflict.reload })).toBeNull();
    expect(within(dialog).queryByRole("button", { name: id.workspace.conflict.diff })).toBeNull();
    expect(within(dialog).getByRole("button", { name: id.workspace.conflict.overwrite })).toBeEnabled();
  });

  it("says when a save may have been written, and why the server refused one", async () => {
    let round = 0;
    backend({
      files: { "app.py": textFile("app.py", "x\n") },
      put: () => {
        round += 1;
        if (round === 1) return HttpResponse.error();
        return HttpResponse.json(
          {
            request_id: "r-archived",
            status: "failed",
            error: { code: "CONFLICT", message: "x", details: [{ reason: "project_archived" }] },
          },
          { status: 409 },
        );
      },
    });
    renderApp(`/projects/${PROJECT}/workspace`);
    await openFromExplorer("app.py");
    const editor = await editorOf("app.py");
    fireEvent.change(editor, { target: { value: "y\n" } });
    fireEvent.keyDown(editor, { key: "s", ctrlKey: true });
    expect(await screen.findByText(id.workspace.editor.failed.unknown_outcome)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: id.workspace.editor.failed.retry }));
    const alert = await screen.findByText(id.workspace.repo.project_archived);
    expect(alert.closest("[role=alert]")).toHaveTextContent("r-archived");
    expect(screen.queryAllByText(id.workspace.editor.status.saved)).toHaveLength(0);
  });

  it("keeps the open tabs and their unsaved text when the project cannot be read again", async () => {
    backend({ files: { "app.py": textFile("app.py", "x = 1\n") } });
    const { queryClient } = renderApp(`/projects/${PROJECT}/workspace`);
    await openFromExplorer("app.py");
    fireEvent.change(await editorOf("app.py"), { target: { value: "unsaved\n" } });
    server.use(http.get(`${API}/projects/${PROJECT}`, () => fail(500, "INTERNAL_ERROR")));
    await act(() => queryClient.refetchQueries({ queryKey: ["projects"] }));
    expect(await screen.findByText(id.workspace.projectStale, { exact: false })).toBeInTheDocument();
    expect(await editorOf("app.py")).toHaveValue("unsaved\n");
  });

  it("starts empty in another project's workspace: no tab of the first one carries over", async () => {
    const other = "0d4f2a1b-1c2d-4e3f-8a9b-0c1d2e3f4a5b";
    backend({ files: { "app.py": textFile("app.py", "x = 1\n") } });
    server.use(
      http.get(`${API}/projects/${other}`, () => ok(projectFixture({ id: other, name: "Lain" }))),
      http.get(`${API}/projects/${other}/tree`, () => ok([], { truncated: false })),
      http.get(`${API}/projects/${other}/git/status`, () =>
        ok({ branch: "main", head: "c".repeat(40), clean: true, changes: [] }),
      ),
    );
    const { router, queryClient } = renderApp(`/projects/${PROJECT}/workspace`);
    // The other project is already known (e.g. from the list), so its workspace shows without loading.
    await queryClient.prefetchQuery({
      queryKey: ["projects", "detail", other],
      queryFn: () => getProject(other),
    });
    await openFromExplorer("app.py");
    await editorOf("app.py");
    await act(() => router.navigate(`/projects/${other}/workspace`));
    expect(await screen.findByRole("heading", { level: 1, name: "Lain" })).toBeInTheDocument();
    expect(within(openFiles()).queryAllByRole("button")).toHaveLength(0);
  });

  it("says why a file could not be opened", async () => {
    backend();
    server.use(http.get(`${BASE}/files/*`, () => fail(403, "FORBIDDEN")));
    renderApp(`/projects/${PROJECT}/workspace`);
    await openFromExplorer("app.py");
    const alert = await screen.findByText(id.workspace.editor.openFailed.replace("{{path}}", "app.py"), {
      exact: false,
    });
    expect(alert).toHaveTextContent(id.states.permission_denied.title);
  });

  it("reads every folder above a new path again, so a folder created on the way shows", async () => {
    const state = backend({ files: { "newdir/x.py": textFile("newdir/x.py", "") } });
    server.use(
      http.post(`${BASE}/files`, () =>
        HttpResponse.json(
          { request_id: "r", status: "completed", data: { path: "newdir/x.py" } },
          { status: 201 },
        ),
      ),
    );
    renderApp(`/projects/${PROJECT}/workspace`);
    await screen.findByRole("button", { name: id.workspace.explorer.openOf.replace("{{name}}", "app.py") });
    const rootReads = state.treeReads.filter((path) => path === ".").length;
    await userEvent.click(screen.getByRole("button", { name: id.workspace.explorer.newFile }));
    await userEvent.type(screen.getByLabelText(id.workspace.create.path), "newdir/x.py");
    await userEvent.click(screen.getByRole("button", { name: id.workspace.create.submit }));
    await waitFor(() => expect(state.treeReads.filter((path) => path === ".")).toHaveLength(rootReads + 1));
    expect(await editorOf("newdir/x.py")).toBeInTheDocument();
  });

  it("warns that deleting a file drops its unsaved text, and says when it changed on the server", async () => {
    backend({ files: { "app.py": textFile("app.py", "x\n") } });
    server.use(
      http.delete(`${BASE}/files/*`, () =>
        HttpResponse.json(
          {
            request_id: "r-del",
            status: "failed",
            error: {
              code: "VERSION_CONFLICT",
              message: "x",
              details: [{ path: "app.py", current_sha256: SHA_3 }],
            },
          },
          { status: 409 },
        ),
      ),
    );
    renderApp(`/projects/${PROJECT}/workspace`);
    await openFromExplorer("app.py");
    fireEvent.change(await editorOf("app.py"), { target: { value: "unsaved\n" } });
    await userEvent.click(
      screen.getByRole("button", { name: id.workspace.explorer.deleteOf.replace("{{name}}", "app.py") }),
    );
    const dialog = await screen.findByRole("dialog", { name: id.workspace.remove.title });
    expect(within(dialog).getByText(id.workspace.remove.unsaved)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: id.workspace.remove.submit }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(id.workspace.remove.changed);
    await userEvent.click(within(dialog).getByRole("button", { name: id.workspace.remove.cancel }));
    expect(await editorOf("app.py")).toHaveValue("unsaved\n");
  });

  it("says a save failed when the file is gone, never saved", async () => {
    backend({ files: { "app.py": textFile("app.py", "x\n") }, put: () => fail(404, "NOT_FOUND") });
    renderApp(`/projects/${PROJECT}/workspace`);
    await openFromExplorer("app.py");
    const editor = await editorOf("app.py");
    fireEvent.change(editor, { target: { value: "y\n" } });
    fireEvent.keyDown(editor, { key: "s", ctrlKey: true });
    expect(await screen.findByText(id.workspace.editor.failed.not_found)).toBeInTheDocument();
    expect(screen.queryAllByText(id.workspace.editor.status.saved)).toHaveLength(0);
  });

  it("asks before closing a tab with unsaved changes", async () => {
    backend({ files: { "app.py": textFile("app.py", "x\n") } });
    renderApp(`/projects/${PROJECT}/workspace`);
    await openFromExplorer("app.py");
    fireEvent.change(await editorOf("app.py"), { target: { value: "y\n" } });
    await userEvent.click(
      screen.getByRole("button", { name: id.workspace.editor.closeOf.replace("{{name}}", "app.py") }),
    );
    const dialog = await screen.findByRole("dialog", { name: id.workspace.unsaved.title });
    await userEvent.click(within(dialog).getByRole("button", { name: id.workspace.unsaved.stay }));
    expect(await editorOf("app.py")).toHaveValue("y\n");
    await userEvent.click(
      screen.getByRole("button", { name: id.workspace.editor.closeOf.replace("{{name}}", "app.py") }),
    );
    await userEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: id.workspace.unsaved.discard }),
    );
    await waitFor(() => expect(within(openFiles()).queryByRole("button", { name: /^app\.py/ })).toBeNull());
  });

  it("shows a binary file as metadata, not in the editor", async () => {
    backend({
      files: {
        "logo.png": {
          path: "logo.png",
          content: null,
          encoding: "binary",
          size: 90,
          sha256: SHA_1,
          language: null,
          too_large: false,
        },
      },
    });
    renderApp(`/projects/${PROJECT}/workspace`);
    await openFromExplorer("logo.png");
    expect(await screen.findByText(id.workspace.editor.binary)).toBeInTheDocument();
    expect(screen.getByText(SHA_1)).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /logo\.png/ })).toBeNull();
  });

  it("is read only without workspace:write: no save, no create, no delete", async () => {
    backend({
      permissions: ["project:read", "workspace:read"],
      files: { "app.py": textFile("app.py", "x\n") },
    });
    renderApp(`/projects/${PROJECT}/workspace`);
    await openFromExplorer("app.py");
    expect(await editorOf("app.py")).toHaveAttribute("readonly");
    expect(screen.getByText(id.workspace.editor.readOnly)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: id.workspace.editor.saveOf.replace("{{name}}", "app.py") }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: id.workspace.explorer.newFile })).toBeNull();
    expect(
      screen.queryByRole("button", { name: id.workspace.explorer.deleteOf.replace("{{name}}", "app.py") }),
    ).toBeNull();
  });

  it("searches on submit and opens a hit at its line; refusals have their own text", async () => {
    let calls = 0;
    backend({ files: { "app.py": textFile("app.py", "a\nb\nc\n") } });
    server.use(
      http.get(`${BASE}/search`, ({ request }) => {
        calls += 1;
        const regex = new URL(request.url).searchParams.get("regex");
        if (regex === "true")
          return HttpResponse.json(
            {
              request_id: "r",
              status: "failed",
              error: { code: "VALIDATION_ERROR", message: "x", details: [{ reason: "regex_unsupported" }] },
            },
            { status: 422 },
          );
        return ok(
          [{ path: "app.py", line: 3, preview: "<img src=x onerror=alert(1)>", symbol: null, kind: null }],
          {
            truncated: true,
            skipped: 0,
          },
        );
      }),
    );
    renderApp(`/projects/${PROJECT}/workspace`);
    const search = await screen.findByRole("search");
    await userEvent.type(within(search).getByLabelText(id.workspace.search.q, { selector: "input" }), "c");
    expect(calls).toBe(0); // typing alone never asks the server
    await userEvent.click(within(search).getByRole("button", { name: id.workspace.search.submit }));
    expect(await screen.findByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(screen.getByText(id.workspace.search.truncated)).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", {
        name: id.workspace.search.openHit.replace("{{path}}", "app.py").replace("{{line}}", "3"),
      }),
    );
    expect(await editorOf("app.py")).toHaveAttribute("data-reveal-line", "3");
    await userEvent.click(within(search).getByLabelText(id.workspace.search.regex));
    await userEvent.click(within(search).getByRole("button", { name: id.workspace.search.submit }));
    expect(await screen.findByText(id.workspace.search.error.regex_unsupported)).toBeInTheDocument();
  });

  it("says no results only for a whole answer, and asks again when the same search is submitted", async () => {
    const answers = [
      { hits: [], meta: { truncated: true, skipped: 0 } },
      { hits: [], meta: { truncated: false, skipped: 0 } },
    ];
    let calls = 0;
    backend();
    server.use(
      http.get(`${BASE}/search`, () => {
        const answer = answers[Math.min(calls, answers.length - 1)];
        calls += 1;
        return ok(answer?.hits, answer?.meta);
      }),
    );
    renderApp(`/projects/${PROJECT}/workspace`);
    const search = await screen.findByRole("search");
    const submit = within(search).getByRole("button", { name: id.workspace.search.submit });
    expect(submit).toBeDisabled(); // nothing to search for yet
    await userEvent.type(within(search).getByLabelText(id.workspace.search.q, { selector: "input" }), "zzz");
    await userEvent.click(submit);
    expect(await screen.findByText(id.workspace.search.truncated)).toBeInTheDocument();
    expect(screen.queryByText(id.workspace.search.empty)).toBeNull();
    await userEvent.click(submit);
    expect(await screen.findByText(id.workspace.search.empty)).toBeInTheDocument();
    expect(calls).toBe(2);
  });

  it("says chat and the terminal are not available yet", async () => {
    backend();
    renderApp(`/projects/${PROJECT}/workspace`);
    await userEvent.click(await screen.findByRole("tab", { name: id.workspace.chat.title }));
    expect(screen.getByText(id.workspace.chat.body)).toBeInTheDocument();
    expect(screen.getByText(id.workspace.bottom.body)).toBeInTheDocument();
  });

  it("shows not found for a project outside the user's scope", async () => {
    server.use(
      signedIn(),
      http.get(`${API}/projects/${PROJECT}`, () => fail(404, "NOT_FOUND")),
    );
    renderApp(`/projects/${PROJECT}/workspace`);
    expect(
      await screen.findByRole("heading", { level: 1, name: id.states.not_found.title }),
    ).toBeInTheDocument();
  });

  it("explains a repository that is not ready instead of an empty tree", async () => {
    const importing = projectFixture();
    server.use(
      signedIn(),
      http.get(`${API}/projects/${PROJECT}`, () =>
        ok({ ...importing, repository: { ...importing.repository, sync_state: "importing" } }),
      ),
    );
    renderApp(`/projects/${PROJECT}/workspace`);
    expect(await screen.findByText(id.workspace.repo.repository_not_ready)).toBeInTheDocument();
  });

  it("renders hostile file names as text", async () => {
    backend({
      tree: { ".": { entries: [{ path: "<b>x</b>.py", name: "<b>x</b>.py", type: "file", size: 1 }] } },
    });
    renderApp(`/projects/${PROJECT}/workspace`);
    expect(await screen.findByText("<b>x</b>.py")).toBeInTheDocument();
    expect(document.querySelector("main b")).toBeNull();
  });
});

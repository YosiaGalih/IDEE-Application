import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { project as projectFixture } from "@/__tests__/dashboardFixtures";
import { API, fail, renderApp, signedIn } from "@/__tests__/harness";
import id from "@/i18n/locales/id.json";

// jsdom cannot run Monaco: the editor is a textarea that keeps the same contract.
vi.mock("@/features/workspace/CodeEditor", () => ({
  CodeEditor: (props: {
    ariaLabel: string;
    value: string;
    isReadOnly: boolean;
    revealLine?: number | null;
    onChange: (value: string) => void;
    onSave: () => void;
  }) => (
    <textarea
      aria-label={props.ariaLabel}
      value={props.value}
      readOnly={props.isReadOnly}
      data-reveal-line={props.revealLine ?? ""}
      onChange={(event) => props.onChange(event.target.value)}
      onKeyDown={(event) => {
        if ((event.ctrlKey || event.metaKey) && event.key === "s") {
          event.preventDefault();
          props.onSave();
        }
      }}
    />
  ),
  DiffView: (props: { original: string; modified: string }) => (
    <div data-testid="diff">{`${props.original}|${props.modified}`}</div>
  ),
  disposeModel: () => undefined,
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
  put?: (body: { content: string; expected_sha256: string | null }, path: string) => Response;
  gitFails?: boolean;
  tree?: Record<string, { entries: unknown[]; truncated?: boolean }>;
};

function backend({ permissions = WRITE_PERMS, files = {}, put, gitFails = false, tree }: Options = {}) {
  const state = { puts: [] as { path: string; body: unknown }[], fileReads: [] as string[] };
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
    expect(screen.getAllByRole("tab", { name: /app\.py/ })).toHaveLength(1);
    const editor = await editorOf("app.py");
    fireEvent.change(editor, { target: { value: "x = 2\n" } });
    expect(status(id.workspace.editor.status.dirty).length).toBeGreaterThan(0);
    fireEvent.keyDown(editor, { key: "s", ctrlKey: true });
    await waitFor(() => expect(state.puts).toHaveLength(1));
    expect(state.puts[0]).toEqual({ path: "app.py", body: { content: "x = 2\n", expected_sha256: SHA_1 } });
    expect(await screen.findAllByText(id.workspace.editor.status.saved)).not.toHaveLength(0);
    // The next save uses the sha the server returned.
    fireEvent.change(editor, { target: { value: "x = 3\n" } });
    await userEvent.click(
      screen.getByRole("button", { name: id.workspace.editor.saveOf.replace("{{name}}", "app.py") }),
    );
    await waitFor(() => expect(state.puts).toHaveLength(2));
    expect(state.puts[1]?.body).toEqual({ content: "x = 3\n", expected_sha256: SHA_2 });
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
    await waitFor(() => expect(screen.queryByRole("tab", { name: /app\.py/ })).toBeNull());
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

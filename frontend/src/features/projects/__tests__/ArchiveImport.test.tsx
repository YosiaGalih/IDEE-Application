import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { API, deferred, fail, ok, okPage, pendingApprovals, renderApp, signedIn } from "@/__tests__/harness";
import id from "@/i18n/locales/id.json";

const server = setupServer(pendingApprovals());
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  document.cookie = "kk_csrf=csrf-test; path=/";
});

const PROJECT_ID = "6f2c1d3e-0a4b-4c5d-8e9f-0a1b2c3d4e5f";
const repo = (sync_state: string, error: string | null = null) => ({
  provider: "upload",
  remote_url: null,
  branch: "main",
  sync_state,
  last_indexed_at: null,
  error,
});

function project(overrides: Record<string, unknown> = {}) {
  return {
    id: PROJECT_ID,
    name: "Arsipku",
    slug: "arsipku",
    description: null,
    status: "active",
    stack: {},
    default_branch: "main",
    settings: {},
    owner_id: "0b6a3f1e-6a1f-4e0e-9a51-6c1c8d7f2a10",
    created_at: "2026-11-02T09:30:00+00:00",
    updated_at: "2026-11-02T09:30:00+00:00",
    repository: repo("importing"),
    permissions: ["project:read", "project:write"],
    stats: null,
    ...overrides,
  };
}

const accepted = () =>
  HttpResponse.json(
    {
      request_id: "r-up",
      status: "accepted",
      data: {
        repository: repo("importing"),
        job_id: "1b2c3d4e-0a4b-4c5d-8e9f-0a1b2c3d4e5f",
        upload: { filename: "kode.zip", size: 3, sha256: "a".repeat(64) },
      },
    },
    { status: 202 },
  );

/** The project endpoints: create, upload, and the project read (each answer from the given function). */
function backend({
  upload = () => accepted(),
  read = () =>
    ok(
      project({
        repository: repo("ready"),
        stats: { files: null, last_commit: "c".repeat(40), open_tasks: 0 },
      }),
    ),
}: { upload?: () => Response; read?: () => Response } = {}) {
  const state = { created: [] as unknown[], uploads: [] as string[], keys: [] as (string | null)[] };
  server.use(
    signedIn(),
    http.get(`${API}/projects/templates`, () => ok([])),
    http.get(`${API}/projects`, () => okPage([])),
    http.post(`${API}/projects`, async ({ request }) => {
      state.created.push(await request.json());
      return HttpResponse.json({ request_id: "r-c", status: "completed", data: project() }, { status: 201 });
    }),
    http.post(`${API}/projects/:projectId/imports`, ({ params, request }) => {
      state.uploads.push(String(params.projectId));
      state.keys.push(request.headers.get("Idempotency-Key"));
      return upload();
    }),
    http.get(`${API}/projects/:projectId`, () => read()),
  );
  return state;
}

const zip = (name = "kode.zip") =>
  new File([new Uint8Array([0x50, 0x4b, 0x03])], name, { type: "application/zip" });

async function chooseArchive(file: File) {
  await userEvent.type(await screen.findByLabelText(id.projects.new.name), "Arsipku");
  await userEvent.click(screen.getByRole("radio", { name: new RegExp(`^${id.projects.new.archive}`) }));
  await userEvent.upload(screen.getByLabelText(id.projects.new.archiveFile), file);
}

describe("project from an archive", () => {
  it("checks the archive before sending anything", async () => {
    const state = backend();
    renderApp("/projects/new");
    await userEvent.type(await screen.findByLabelText(id.projects.new.name), "Arsipku");
    await userEvent.click(screen.getByRole("radio", { name: new RegExp(`^${id.projects.new.archive}`) }));
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    expect(await screen.findByText(id.projects.errors.archiveRequired)).toBeInTheDocument();
    await userEvent.upload(screen.getByLabelText(id.projects.new.archiveFile), zip("kode.rar"), {
      applyAccept: false,
    });
    expect(await screen.findByText(id.projects.errors.archiveType)).toBeInTheDocument();
    const huge = zip("besar.zip");
    Object.defineProperty(huge, "size", { value: 200 * 1024 * 1024 + 1 });
    await userEvent.upload(screen.getByLabelText(id.projects.new.archiveFile), huge);
    expect(await screen.findByText(id.projects.errors.archiveTooLarge)).toBeInTheDocument();
    expect(state.created).toEqual([]);
    expect(state.uploads).toEqual([]);
  });

  it("creates an upload project, uploads the archive, follows the import and opens the workspace", async () => {
    const state = backend();
    const { router } = renderApp("/projects/new");
    await chooseArchive(zip());
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/projects/${PROJECT_ID}/workspace`));
    expect(state.created).toEqual([{ name: "Arsipku", description: null, source: { type: "upload" } }]);
    expect(state.uploads).toEqual([PROJECT_ID]);
  });

  it("shows the import running while the project still imports", async () => {
    backend({ read: () => ok(project()) });
    renderApp("/projects/new");
    await chooseArchive(zip());
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    expect(await screen.findByText(id.projects.import.importing)).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(id.projects.import.title);
  });

  it("says why an archive was refused, and sends another file to the same project", async () => {
    let refused = true;
    const state = backend({
      read: () =>
        ok(
          refused
            ? project({ repository: repo("error", "archive_rejected:git_dir") })
            : project({ repository: repo("ready") }),
        ),
    });
    const { router } = renderApp("/projects/new");
    await chooseArchive(zip());
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    expect(await screen.findByText(id.projects.import.reason.git_dir)).toBeInTheDocument();
    expect(screen.getByText(id.projects.import.failedTitle)).toBeInTheDocument();
    refused = false;
    await userEvent.upload(screen.getByLabelText(id.projects.new.archiveFile), zip("tanpa-git.zip"));
    await userEvent.click(screen.getByRole("button", { name: id.projects.import.upload }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/projects/${PROJECT_ID}/workspace`));
    expect(state.uploads).toEqual([PROJECT_ID, PROJECT_ID]);
    expect(state.created).toHaveLength(1);
    expect(state.keys[0]).not.toBe(state.keys[1]); // answered: the new file gets a new key
  });

  it("says another import is running (an answer: the next try gets a new key)", async () => {
    let busy = true;
    const state = backend({
      upload: () =>
        busy
          ? HttpResponse.json(
              {
                request_id: "r-409",
                status: "failed",
                error: { code: "CONFLICT", message: "busy", details: [{ reason: "import_running" }] },
              },
              { status: 409 },
            )
          : accepted(),
    });
    renderApp("/projects/new");
    await chooseArchive(zip());
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    expect(await screen.findByText(id.projects.import.conflict.import_running)).toBeInTheDocument();
    busy = false;
    await userEvent.upload(screen.getByLabelText(id.projects.new.archiveFile), zip());
    await userEvent.click(screen.getByRole("button", { name: id.projects.import.upload }));
    await waitFor(() => expect(state.uploads).toHaveLength(2));
    expect(state.keys[0]).not.toBe(state.keys[1]);
  });

  it("keeps the key when the upload's answer is lost: Try again resends the same file", async () => {
    let lost = true;
    const state = backend({ upload: () => (lost ? HttpResponse.error() : accepted()) });
    renderApp("/projects/new");
    await chooseArchive(zip());
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    expect(await screen.findByText(id.projects.import.outcomeUnknown)).toBeInTheDocument();
    expect(screen.getByText(/kode\.zip/)).toBeInTheDocument(); // the held file is named
    lost = false;
    await userEvent.click(screen.getByRole("button", { name: id.projects.import.retry }));
    await waitFor(() => expect(state.uploads).toHaveLength(2));
    expect(state.keys[0]).toBe(state.keys[1]);
  });

  it("keeps the key for the same file picked again (a new File object), and changes it for another file", async () => {
    const lost = true;
    const state = backend({ upload: () => (lost ? HttpResponse.error() : accepted()) });
    renderApp("/projects/new");
    const original = new File([new Uint8Array([0x50, 0x4b, 0x03])], "kode.zip", {
      lastModified: 1_700_000_000_000,
    });
    await chooseArchive(original);
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    await screen.findByText(id.projects.import.outcomeUnknown);
    // The picker hands back a new object for the same file.
    const again = new File([new Uint8Array([0x50, 0x4b, 0x03])], "kode.zip", {
      lastModified: 1_700_000_000_000,
    });
    await userEvent.upload(screen.getByLabelText(id.projects.new.archiveFile), again);
    await userEvent.click(screen.getByRole("button", { name: id.projects.import.upload }));
    await waitFor(() => expect(state.uploads).toHaveLength(2));
    expect(state.keys[1]).toBe(state.keys[0]);
    await screen.findByText(id.projects.import.outcomeUnknown);
    const other = new File([new Uint8Array([0x50, 0x4b, 0x03, 0x04])], "lain.zip", { lastModified: 1 });
    await userEvent.upload(screen.getByLabelText(id.projects.new.archiveFile), other);
    await userEvent.click(screen.getByRole("button", { name: id.projects.import.upload }));
    await waitFor(() => expect(state.uploads).toHaveLength(3));
    expect(state.keys[2]).not.toBe(state.keys[0]);
  });

  it("after a refused archive the form is empty and Upload waits for a new file", async () => {
    backend({ read: () => ok(project({ repository: repo("error", "archive_rejected:link") })) });
    renderApp("/projects/new");
    await chooseArchive(zip());
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    expect(await screen.findByText(id.projects.import.reason.link)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: id.projects.import.upload })).toBeDisabled();
    expect(screen.queryByRole("button", { name: id.projects.import.retry })).toBeNull(); // answered: no resend
  });

  it("forgets a chosen archive when the user switches to another source and back", async () => {
    const state = backend();
    renderApp("/projects/new");
    await chooseArchive(zip());
    await userEvent.click(screen.getByRole("radio", { name: new RegExp(`^${id.projects.new.blank}`) }));
    await userEvent.click(screen.getByRole("radio", { name: new RegExp(`^${id.projects.new.archive}`) }));
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    expect(await screen.findByText(id.projects.errors.archiveRequired)).toBeInTheDocument();
    expect(state.created).toEqual([]);
  });

  it("shows the upload's progress while it is sent", async () => {
    const { gate, release } = deferred();
    backend({ upload: () => accepted() });
    server.use(
      http.post(`${API}/projects/:projectId/imports`, async () => {
        await gate;
        return accepted();
      }),
    );
    renderApp("/projects/new");
    await chooseArchive(zip());
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    expect(await screen.findByText(id.projects.import.uploadingTitle)).toBeInTheDocument();
    // MSW in jsdom emits the XHR's upload progress only with the answer, so the percent itself is proven in
    // api/__tests__/upload.test.ts; here: the bar, its value for assistive tech, and its text.
    const bar = screen.getByRole("progressbar", { name: id.projects.import.progressLabel });
    expect(bar).toHaveAttribute("aria-valuenow", "0");
    expect(bar).toHaveAttribute("aria-valuetext", id.projects.import.uploading.replace("{{percent}}", "0"));
    release();
  });

  it("never shows the previous failure after a new upload is accepted", async () => {
    const { gate, release } = deferred();
    let reads = 0;
    backend();
    server.use(
      http.get(`${API}/projects/:projectId`, async () => {
        reads += 1;
        if (reads === 1) return ok(project({ repository: repo("error", "archive_rejected:git_dir") }));
        await gate; // the read after the 202 is slow
        return ok(project({ repository: repo("importing") }));
      }),
    );
    renderApp(`/projects/${PROJECT_ID}/import`);
    expect(await screen.findByText(id.projects.import.reason.git_dir)).toBeInTheDocument();
    await userEvent.upload(screen.getByLabelText(id.projects.new.archiveFile), zip("tanpa-git.zip"));
    await userEvent.click(screen.getByRole("button", { name: id.projects.import.upload }));
    expect(await screen.findByText(id.projects.import.importing)).toBeInTheDocument();
    expect(screen.queryByText(id.projects.import.reason.git_dir)).toBeNull();
    release();
  });

  it("says the archive is too large on the gateway's own 413 page", async () => {
    backend({ upload: () => new HttpResponse("<html>413 Request Entity Too Large</html>", { status: 413 }) });
    renderApp("/projects/new");
    await chooseArchive(zip());
    await userEvent.click(screen.getByRole("button", { name: id.projects.new.submit }));
    expect(await screen.findByText(id.projects.errors.archiveTooLarge)).toBeInTheDocument();
  });
});

describe("ImportArchivePage", () => {
  it("uploads again into a project whose import stopped", async () => {
    const state = backend({
      read: () => ok(project({ repository: repo("error", "import_incomplete") })),
    });
    renderApp(`/projects/${PROJECT_ID}/import`);
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(id.projects.import.title);
    await userEvent.upload(await screen.findByLabelText(id.projects.new.archiveFile), zip());
    await userEvent.click(screen.getByRole("button", { name: id.projects.import.upload }));
    await waitFor(() => expect(state.uploads).toEqual([PROJECT_ID]));
  });

  it("says a ready project is not waiting for an archive", async () => {
    backend();
    renderApp(`/projects/${PROJECT_ID}/import`);
    expect(await screen.findByText(id.projects.import.notWaiting)).toBeInTheDocument();
    expect(screen.queryByLabelText(id.projects.new.archiveFile)).toBeNull();
  });

  it("says why the last import failed when the page opens", async () => {
    backend({ read: () => ok(project({ repository: repo("error", "archive_rejected:git_dir") })) });
    renderApp(`/projects/${PROJECT_ID}/import`);
    expect(await screen.findByText(id.projects.import.reason.git_dir)).toBeInTheDocument();
    expect(screen.getByLabelText(id.projects.new.archiveFile)).toBeInTheDocument();
  });

  it("follows an import that is already running, without a form, until it is ready", async () => {
    let reads = 0;
    backend({
      read: () => {
        reads += 1;
        return ok(project({ repository: repo(reads === 1 ? "importing" : "ready") }));
      },
    });
    renderApp(`/projects/${PROJECT_ID}/import`);
    expect(await screen.findByText(id.projects.import.runningHere)).toBeInTheDocument();
    expect(screen.queryByLabelText(id.projects.new.archiveFile)).toBeNull();
    expect(
      await screen.findByText(id.projects.import.notWaiting, undefined, { timeout: 4000 }),
    ).toBeInTheDocument();
  });

  it("says an archived project must be restored first", async () => {
    backend({
      read: () => ok(project({ status: "archived", repository: repo("error", "import_incomplete") })),
    });
    renderApp(`/projects/${PROJECT_ID}/import`);
    expect(await screen.findByText(id.projects.import.conflict.project_archived)).toBeInTheDocument();
    expect(screen.queryByLabelText(id.projects.new.archiveFile)).toBeNull();
  });

  it("shows permission denied to a reader", async () => {
    backend({
      read: () =>
        ok(project({ repository: repo("error", "import_incomplete"), permissions: ["project:read"] })),
    });
    renderApp(`/projects/${PROJECT_ID}/import`);
    expect(await screen.findByText(id.states.permission_denied.title)).toBeInTheDocument();
    expect(screen.queryByLabelText(id.projects.new.archiveFile)).toBeNull();
  });
});

describe("ProjectsPage with upload projects", () => {
  function list(rows: unknown[]) {
    server.use(
      signedIn(),
      http.get(`${API}/projects`, () => okPage(rows)),
    );
  }

  it("marks a running import", async () => {
    list([project()]);
    renderApp("/projects");
    expect(await screen.findByText(id.projects.import.running)).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: id.projects.import.actionOf.replace("{{name}}", "Arsipku") }),
    ).toBeNull();
  });

  it("marks an import that stopped and offers to upload again, only to a writer", async () => {
    list([
      project({ repository: repo("error", "import_incomplete") }),
      project({
        id: "7a2c1d3e-0a4b-4c5d-8e9f-0a1b2c3d4e5f",
        name: "Bersama",
        repository: repo("error", "archive_rejected:link"),
        permissions: ["project:read"],
      }),
    ]);
    const { router } = renderApp("/projects");
    expect(await screen.findByText(id.projects.import.incomplete)).toBeInTheDocument();
    expect(screen.getByText(id.projects.import.failedTitle)).toHaveAttribute(
      "title",
      id.projects.import.reason.link,
    );
    expect(
      screen.queryByRole("link", { name: id.projects.import.actionOf.replace("{{name}}", "Bersama") }),
    ).toBeNull();
    await userEvent.click(
      screen.getByRole("link", { name: id.projects.import.actionOf.replace("{{name}}", "Arsipku") }),
    );
    expect(router.state.location.pathname).toBe(`/projects/${PROJECT_ID}/import`);
  });

  it("offers no upload to an archived project", async () => {
    list([project({ status: "archived", repository: repo("error", "import_incomplete") })]);
    renderApp("/projects?status=archived");
    expect(await screen.findByText(id.projects.import.incomplete)).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: id.projects.import.actionOf.replace("{{name}}", "Arsipku") }),
    ).toBeNull();
  });

  it.each([
    ["import_expired", id.projects.import.conflict.import_expired],
    ["cleanup_failed", id.projects.import.conflict.cleanup_failed],
  ])("says why the server refused the upload: %s", async (reason, text) => {
    server.use(
      signedIn(),
      http.get(`${API}/projects/:projectId`, () =>
        ok(project({ repository: repo("error", "import_incomplete") })),
      ),
      http.post(`${API}/projects/:projectId/imports`, () =>
        HttpResponse.json(
          {
            request_id: "r-409",
            status: "failed",
            error: { code: "CONFLICT", message: "no", details: [{ reason }] },
          },
          { status: 409 },
        ),
      ),
    );
    renderApp(`/projects/${PROJECT_ID}/import`);
    await userEvent.upload(await screen.findByLabelText(id.projects.new.archiveFile), zip());
    await userEvent.click(screen.getByRole("button", { name: id.projects.import.upload }));
    expect(await screen.findByText(text)).toBeInTheDocument();
  });

  it("says the upload stopped when the server answers 408", async () => {
    server.use(
      signedIn(),
      http.get(`${API}/projects/:projectId`, () =>
        ok(project({ repository: repo("error", "import_incomplete") })),
      ),
      http.post(`${API}/projects/:projectId/imports`, () => fail(408, "BAD_REQUEST")),
    );
    renderApp(`/projects/${PROJECT_ID}/import`);
    await userEvent.upload(await screen.findByLabelText(id.projects.new.archiveFile), zip());
    await userEvent.click(screen.getByRole("button", { name: id.projects.import.upload }));
    expect(await screen.findByText(id.projects.import.stalled)).toBeInTheDocument();
  });

  it("says an import that waited too long was dropped", async () => {
    backend({ read: () => ok(project({ repository: repo("error", "import_expired") })) });
    renderApp(`/projects/${PROJECT_ID}/import`);
    expect(await screen.findByText(id.projects.import.error.import_expired)).toBeInTheDocument();
  });

  it("refuses an upload that the server answers 413", async () => {
    server.use(
      signedIn(),
      http.get(`${API}/projects/:projectId`, () =>
        ok(project({ repository: repo("error", "import_incomplete") })),
      ),
      http.post(`${API}/projects/:projectId/imports`, () => fail(413, "PAYLOAD_TOO_LARGE")),
    );
    renderApp(`/projects/${PROJECT_ID}/import`);
    await userEvent.upload(await screen.findByLabelText(id.projects.new.archiveFile), zip());
    await userEvent.click(screen.getByRole("button", { name: id.projects.import.upload }));
    expect(await screen.findByText(id.projects.errors.archiveTooLarge)).toBeInTheDocument();
  });
});

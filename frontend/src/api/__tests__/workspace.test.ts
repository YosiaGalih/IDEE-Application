import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { ApiError } from "@/api/errors";
import {
  filePath,
  getFile,
  getGitStatus,
  getTree,
  putFile,
  searchProject,
  VersionConflict,
} from "@/api/workspace";

const API = `${window.location.origin}/api/v1`;
const PROJECT = "6f2c1d3e-0a4b-4c5d-8e9f-0a1b2c3d4e5f";
const SHA = "a".repeat(64);
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  document.cookie = "kk_csrf=csrf-test; path=/";
});

const ok = (data: unknown, meta: unknown = null) =>
  HttpResponse.json({ request_id: "r-ws-0001", status: "completed", data, meta });

describe("workspace client", () => {
  it("encodes every path segment, so names with spaces, # and % reach the server intact", () => {
    expect(filePath(PROJECT, "a b/#c%.py")).toBe(`/projects/${PROJECT}/files/a%20b/%23c%25.py`);
  });

  it("returns the tree with the server's truncation flag", async () => {
    server.use(
      http.get(`${API}/projects/${PROJECT}/tree`, ({ request }) => {
        const params = new URL(request.url).searchParams;
        expect([params.get("path"), params.get("depth")]).toEqual(["src", "1"]);
        return ok([{ path: "src/app.py", name: "app.py", type: "file", size: 12 }], { truncated: true });
      }),
    );
    expect(await getTree(PROJECT, "src")).toEqual({
      entries: [{ path: "src/app.py", name: "app.py", type: "file", size: 12 }],
      truncated: true,
    });
  });

  it("reads text and binary files", async () => {
    server.use(
      http.get(`${API}/projects/${PROJECT}/files/logo.png`, () =>
        ok({
          path: "logo.png",
          content: null,
          encoding: "binary",
          size: 90,
          sha256: SHA,
          language: null,
          too_large: false,
        }),
      ),
    );
    const file = await getFile(PROJECT, "logo.png");
    expect([file.content, file.encoding]).toEqual([null, "binary"]);
  });

  it("saves with the expected sha and turns a 409 into a conflict that carries the server's sha", async () => {
    const seen: unknown[] = [];
    server.use(
      http.put(`${API}/projects/${PROJECT}/files/app.py`, async ({ request }) => {
        seen.push(await request.json());
        return HttpResponse.json(
          {
            request_id: "r-ws-0002",
            status: "failed",
            error: {
              code: "VERSION_CONFLICT",
              message: "The file changed since it was read",
              details: [{ path: "app.py", current_sha256: "b".repeat(64) }],
            },
          },
          { status: 409 },
        );
      }),
    );
    const error = await putFile(PROJECT, "app.py", "print(1)\n", SHA).catch((caught: unknown) => caught);
    expect(seen).toEqual([{ content: "print(1)\n", expected_sha256: SHA }]);
    expect(error).toBeInstanceOf(VersionConflict);
    expect((error as VersionConflict).currentSha).toBe("b".repeat(64));
  });

  it("keeps other save failures as plain API errors", async () => {
    server.use(
      http.put(`${API}/projects/${PROJECT}/files/app.py`, () =>
        HttpResponse.json(
          { request_id: "r", status: "failed", error: { code: "NOT_FOUND", message: "gone" } },
          { status: 404 },
        ),
      ),
    );
    const error = await putFile(PROJECT, "app.py", "x", SHA).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).not.toBeInstanceOf(VersionConflict);
  });

  it("reads git status", async () => {
    server.use(
      http.get(`${API}/projects/${PROJECT}/git/status`, () =>
        ok({
          branch: "main",
          head: "c".repeat(40),
          clean: false,
          changes: [{ path: "app.py", status: "modified" }],
        }),
      ),
    );
    expect((await getGitStatus(PROJECT)).changes).toEqual([{ path: "app.py", status: "modified" }]);
  });

  it("searches and returns what the server says about the result", async () => {
    server.use(
      http.get(`${API}/projects/${PROJECT}/search`, ({ request }) => {
        const params = new URL(request.url).searchParams;
        expect(Object.fromEntries(params)).toEqual({
          q: "add(",
          mode: "text",
          regex: "false",
          ignore_case: "true",
        });
        return ok([{ path: "a.py", line: 3, preview: "def add(a, b):", symbol: null, kind: null }], {
          truncated: false,
          skipped: 2,
        });
      }),
    );
    const result = await searchProject(PROJECT, { q: "add(", mode: "text", regex: false, ignoreCase: true });
    expect(result.hits[0]?.line).toBe(3);
    expect(result.meta).toMatchObject({ truncated: false, skipped: 2 });
  });
});

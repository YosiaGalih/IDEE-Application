import { z } from "zod";
import { apiPath, apiRequest } from "./client";
import { ApiError } from "./errors";

/** One tree entry (API_CONTRACTS §3 `GET /tree`): folders may carry their children up to `depth`. */
export type TreeEntry = {
  path: string;
  name: string;
  type: "file" | "dir";
  size: number | null;
  children?: TreeEntry[] | undefined;
};
const TreeEntrySchema: z.ZodType<TreeEntry> = z.lazy(() =>
  z.object({
    path: z.string(),
    name: z.string(),
    type: z.enum(["file", "dir"]),
    size: z.number().int().nonnegative().nullable(),
    children: z.array(TreeEntrySchema).optional(),
  }),
);

/** `GET /files/{path}` (API_CONTRACTS §4): a binary or too large file comes without content. */
export const FileContentSchema = z.object({
  path: z.string(),
  content: z.string().nullable(),
  encoding: z.enum(["utf-8", "binary"]),
  size: z.number().int().nonnegative(),
  sha256: z.string().regex(/^[0-9a-f]{64}$/),
  language: z.string().nullable(),
  too_large: z.boolean(),
});
export type FileContent = z.infer<typeof FileContentSchema>;

export const GIT_CHANGE_STATUSES = ["added", "modified", "deleted", "untracked", "conflicted"] as const;
export const GitStatusSchema = z.object({
  branch: z.string().nullable(),
  head: z.string().nullable(),
  clean: z.boolean(),
  changes: z.array(z.object({ path: z.string(), status: z.enum(GIT_CHANGE_STATUSES) })),
});
export type GitStatus = z.infer<typeof GitStatusSchema>;

export const SearchHitSchema = z.object({
  path: z.string(),
  line: z.number().int().positive(),
  preview: z.string(),
  symbol: z.string().nullable().optional(),
  kind: z.string().nullable().optional(),
});
export type SearchHit = z.infer<typeof SearchHitSchema>;
const SearchMetaSchema = z.object({
  truncated: z.boolean(),
  skipped: z.number().int().nonnegative().optional(),
  index_commit: z.string().nullable().optional(),
  indexed_at: z.iso.datetime({ offset: true }).nullable().optional(),
  index_truncated: z.boolean().nullable().optional(),
});
export type SearchMeta = z.infer<typeof SearchMetaSchema>;

export type SearchParams = {
  q: string;
  mode: "text" | "symbol";
  glob?: string;
  regex?: boolean;
  ignoreCase?: boolean;
  kind?: string;
};

/** A save refused because the file changed since it was read (409 `VERSION_CONFLICT`, FR-029). */
export class VersionConflict extends ApiError {
  /** The file's sha on the server now; `null` when the file no longer exists. */
  readonly currentSha: string | null;

  constructor(error: ApiError, currentSha: string | null) {
    super({
      kind: error.kind,
      status: error.status,
      code: error.code,
      message: error.message,
      details: error.details,
      requestId: error.requestId,
      idempotencyKey: error.idempotencyKey,
    });
    this.name = "VersionConflict";
    this.currentSha = currentSha;
  }
}

const ConflictDetailsSchema = z.array(z.object({ current_sha256: z.string().nullable() }).loose()).min(1);

/** The API path of a file: each segment encoded, so `#`, `%` and spaces in names stay part of the name. */
export function filePath(projectId: string, path: string): string {
  return apiPath("projects", projectId, "files", ...path.split("/"));
}

const signalOf = (signal?: AbortSignal) => (signal ? { signal } : {});

function metaOrThrow<T>(schema: z.ZodType<T>, meta: unknown, requestId: string | null, what: string): T {
  const parsed = schema.safeParse(meta);
  if (!parsed.success) {
    // Without these flags a cut result would look complete.
    throw new ApiError({ kind: "bad_response", message: `${what} meta: ${parsed.error.message}`, requestId });
  }
  return parsed.data;
}

export async function getTree(
  projectId: string,
  path: string,
  signal?: AbortSignal,
): Promise<{ entries: TreeEntry[]; truncated: boolean }> {
  const params = new URLSearchParams({ path, depth: "1" });
  const result = await apiRequest(`${apiPath("projects", projectId, "tree")}?${params.toString()}`, {
    schema: z.array(TreeEntrySchema),
    ...signalOf(signal),
  });
  const meta = metaOrThrow(z.object({ truncated: z.boolean() }), result.meta, result.requestId, "Tree");
  return { entries: result.data, truncated: meta.truncated };
}

export async function getFile(projectId: string, path: string, signal?: AbortSignal): Promise<FileContent> {
  return (await apiRequest(filePath(projectId, path), { schema: FileContentSchema, ...signalOf(signal) }))
    .data;
}

export async function putFile(
  projectId: string,
  path: string,
  content: string,
  expectedSha: string | null,
): Promise<{ path: string; sha256: string }> {
  try {
    const result = await apiRequest(filePath(projectId, path), {
      method: "PUT",
      body: { content, expected_sha256: expectedSha },
      schema: z.object({ path: z.string(), sha256: z.string() }),
    });
    return result.data;
  } catch (error) {
    if (error instanceof ApiError && error.code === "VERSION_CONFLICT") {
      const details = ConflictDetailsSchema.safeParse(error.details);
      throw new VersionConflict(error, details.success ? (details.data[0]?.current_sha256 ?? null) : null);
    }
    throw error;
  }
}

export async function createPath(projectId: string, path: string, type: "file" | "dir"): Promise<void> {
  await apiRequest(apiPath("projects", projectId, "files"), {
    method: "POST",
    body: type === "file" ? { path, content: "", type } : { path, type },
    schema: z.object({ path: z.string() }),
  });
}

export async function deleteFile(projectId: string, path: string, expectedSha: string): Promise<void> {
  const params = new URLSearchParams({ expected_sha256: expectedSha });
  await apiRequest(`${filePath(projectId, path)}?${params.toString()}`, {
    method: "DELETE",
    schema: z.unknown(),
  });
}

export async function getGitStatus(projectId: string, signal?: AbortSignal): Promise<GitStatus> {
  return (
    await apiRequest(apiPath("projects", projectId, "git", "status"), {
      schema: GitStatusSchema,
      ...signalOf(signal),
    })
  ).data;
}

export async function searchProject(
  projectId: string,
  search: SearchParams,
  signal?: AbortSignal,
): Promise<{ hits: SearchHit[]; meta: SearchMeta }> {
  const params = new URLSearchParams({ q: search.q, mode: search.mode });
  if (search.glob) params.set("glob", search.glob);
  if (search.mode === "text") {
    params.set("regex", String(search.regex ?? false));
    params.set("ignore_case", String(search.ignoreCase ?? true));
  }
  if (search.mode === "symbol" && search.kind) params.set("kind", search.kind);
  const result = await apiRequest(`${apiPath("projects", projectId, "search")}?${params.toString()}`, {
    schema: z.array(SearchHitSchema),
    ...signalOf(signal),
  });
  return { hits: result.data, meta: metaOrThrow(SearchMetaSchema, result.meta, result.requestId, "Search") };
}

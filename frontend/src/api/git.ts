import { z } from "zod";
import { apiPath, apiRequest } from "./client";

/** Git of a project (API_CONTRACTS §4 `/git/*`, shapes of 2.1c `gitops/diffparse.py` and `adapter.py`). */

const CommitSchema = z.string().regex(/^[0-9a-f]{40}$/);

const DiffLineSchema = z.object({
  type: z.enum(["context", "add", "del"]),
  old_no: z.number().int().positive().nullable(),
  new_no: z.number().int().positive().nullable(),
  text: z.string(),
});
const DiffHunkSchema = z.object({
  header: z.string(),
  old_start: z.number().int().nonnegative(),
  old_lines: z.number().int().nonnegative(),
  new_start: z.number().int().nonnegative(),
  new_lines: z.number().int().nonnegative(),
  lines: z.array(DiffLineSchema),
});
const DiffFileSchema = z.object({
  path: z.string(),
  old_path: z.string().nullable(),
  status: z.enum(["added", "modified", "deleted"]),
  additions: z.number().int().nonnegative(),
  deletions: z.number().int().nonnegative(),
  is_binary: z.boolean(),
  hunks: z.array(DiffHunkSchema),
});
export const GitDiffSchema = z.object({
  base: z.string(),
  head: z.string().nullable(),
  files: z.array(DiffFileSchema),
  stats: z.object({
    files: z.number().int().nonnegative(),
    additions: z.number().int().nonnegative(),
    deletions: z.number().int().nonnegative(),
  }),
  truncated: z.boolean(),
});
export type GitDiff = z.infer<typeof GitDiffSchema>;
export type DiffFile = GitDiff["files"][number];
export type DiffHunk = DiffFile["hunks"][number];

const BranchSchema = z.object({ name: z.string(), head: CommitSchema, is_task_branch: z.boolean() });
export type Branch = z.infer<typeof BranchSchema>;

const LogEntrySchema = z.object({
  commit: CommitSchema,
  author: z.string(),
  date: z.iso.datetime({ offset: true }),
  message: z.string(),
});
export type LogEntry = z.infer<typeof LogEntrySchema>;

const gitPath = (projectId: string, ...rest: string[]) => apiPath("projects", projectId, "git", ...rest);
const signalOf = (signal?: AbortSignal) => (signal ? { signal } : {});

/** `path` null: the whole working tree against HEAD (untracked files included). */
export async function getGitDiff(
  projectId: string,
  path: string | null,
  signal?: AbortSignal,
): Promise<GitDiff> {
  const query = path === null ? "" : `?${new URLSearchParams({ path }).toString()}`;
  return (
    await apiRequest(`${gitPath(projectId, "diff")}${query}`, { schema: GitDiffSchema, ...signalOf(signal) })
  ).data;
}

export async function getBranches(projectId: string, signal?: AbortSignal): Promise<Branch[]> {
  return (
    await apiRequest(gitPath(projectId, "branches"), { schema: z.array(BranchSchema), ...signalOf(signal) })
  ).data;
}

export async function getGitLog(projectId: string, limit: number, signal?: AbortSignal): Promise<LogEntry[]> {
  const query = new URLSearchParams({ limit: String(limit) }).toString();
  return (
    await apiRequest(`${gitPath(projectId, "log")}?${query}`, {
      schema: z.array(LogEntrySchema),
      ...signalOf(signal),
    })
  ).data;
}

/** Only the ref is made: the working tree stays on its branch (no checkout in v1, FR-118). */
export async function createBranch(
  projectId: string,
  { name, from }: { name: string; from: string | null },
  idempotencyKey: string,
): Promise<{ name: string; head: string }> {
  return (
    await apiRequest(gitPath(projectId, "branch"), {
      method: "POST",
      body: from === null ? { name } : { name, from },
      schema: z.object({ name: z.string(), head: CommitSchema }),
      idempotencyKey,
    })
  ).data;
}

/** Always explicit paths: a commit never takes changes the user did not see listed. */
export async function commitChanges(
  projectId: string,
  { message, paths }: { message: string; paths: string[] },
  idempotencyKey: string,
): Promise<{ commit: string }> {
  return (
    await apiRequest(gitPath(projectId, "commit"), {
      method: "POST",
      body: { message, paths },
      schema: z.object({ commit: CommitSchema }),
      idempotencyKey,
    })
  ).data;
}

/** Names the platform keeps for task and user branches (backend `_reserved_branch`, FR-114). */
export const isReservedBranch = (name: string) =>
  name === "kk" || name === "HEAD" || name.startsWith("kk/") || name.startsWith("refs/");

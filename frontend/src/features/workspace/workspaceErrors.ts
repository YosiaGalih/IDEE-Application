import { ApiError } from "@/api/errors";

/** The first `details[].reason` of an API error (409 CONFLICT, 422 search), when the server named one. */
export function reasonOf(error: unknown): string | null {
  if (!(error instanceof ApiError) || !Array.isArray(error.details)) return null;
  for (const detail of error.details as unknown[]) {
    if (typeof detail === "object" && detail !== null && "reason" in detail) {
      const reason: unknown = detail.reason;
      if (typeof reason === "string") return reason;
    }
  }
  return null;
}

/** Repository preconditions of the workspace API (2.1c `open_repo`), each with its own text. */
export const REPO_REASONS = [
  "repository_not_ready",
  "repository_missing",
  "project_archived",
  "project_not_active",
] as const;
export type RepoReason = (typeof REPO_REASONS)[number];
export const isRepoReason = (reason: string | null): reason is RepoReason =>
  reason !== null && (REPO_REASONS as readonly string[]).includes(reason);

import type { TFunction } from "i18next";
import { ApiError } from "@/api/errors";
import { describeError, isOutcomeUnknown } from "@/lib/describeError";
import type { SaveError } from "./editorTabs";

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
  "repository_error",
  "project_archived",
  "project_not_active",
] as const;
export type RepoReason = (typeof REPO_REASONS)[number];
export const isRepoReason = (reason: string | null): reason is RepoReason =>
  reason !== null && (REPO_REASONS as readonly string[]).includes(reason);

const requestIdOf = (error: unknown) => (error instanceof ApiError ? error.requestId : null);

/** Why a save failed, as far as the answer tells; "unknown_outcome" when it may have been written. */
export function saveErrorOf(error: unknown): SaveError {
  const requestId = requestIdOf(error);
  const reason = reasonOf(error);
  if (isRepoReason(reason)) return { code: reason, requestId };
  if (isOutcomeUnknown(error)) return { code: "unknown_outcome", requestId };
  if (error instanceof ApiError && error.status === 404) return { code: "not_found", requestId };
  if (error instanceof ApiError && error.status === 403) return { code: "forbidden", requestId };
  if (error instanceof ApiError && error.status === 429) return { code: "rate_limited", requestId };
  return { code: "generic", requestId };
}

export function saveFailureText(t: TFunction, error: SaveError | undefined): string {
  const code = error?.code ?? "generic";
  return isRepoReason(code) ? t(`workspace.repo.${code}`) : t(`workspace.editor.failed.${code}`);
}

/**
 * A failed read or change in the workspace: the repository's own reason when the server gave one,
 * otherwise the screen state of the failure (permission denied, not found, timeout, ...).
 */
export function workspaceErrorText(
  t: TFunction,
  error: unknown,
  options: Parameters<typeof describeError>[2] = {},
): { text: string; requestId: string | null } {
  const reason = reasonOf(error);
  if (isRepoReason(reason)) return { text: t(`workspace.repo.${reason}`), requestId: requestIdOf(error) };
  const described = describeError(error, t, options);
  return {
    text: described.body ? `${described.title}. ${described.body}` : described.title,
    requestId: described.requestId,
  };
}

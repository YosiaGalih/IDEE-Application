import type { TFunction } from "i18next";

/** The texts for what the server says about an import (spec WP-2.1b): reasons, error codes, conflicts. */
const REASONS = {
  format: "projects.import.reason.format",
  oversize: "projects.import.reason.oversize",
  ratio: "projects.import.reason.ratio",
  too_many_files: "projects.import.reason.too_many_files",
  link: "projects.import.reason.link",
  device: "projects.import.reason.device",
  duplicate: "projects.import.reason.duplicate",
  invalid_name: "projects.import.reason.invalid_name",
  traversal: "projects.import.reason.traversal",
  absolute: "projects.import.reason.absolute",
  git_dir: "projects.import.reason.git_dir",
  empty: "projects.import.reason.empty",
} as const;
const ERRORS = {
  import_git_failed: "projects.import.error.import_git_failed",
  import_failed: "projects.import.error.import_failed",
  import_not_started: "projects.import.error.import_not_started",
  import_incomplete: "projects.import.error.import_incomplete",
  import_expired: "projects.import.error.import_expired",
} as const;
export const CONFLICTS = {
  import_running: "projects.import.conflict.import_running",
  repository_not_empty: "projects.import.conflict.repository_not_empty",
  not_an_upload_project: "projects.import.conflict.not_an_upload_project",
  project_archived: "projects.import.conflict.project_archived",
  import_expired: "projects.import.conflict.import_expired",
  cleanup_failed: "projects.import.conflict.cleanup_failed",
} as const;

const has = <T extends object>(table: T, key: string): key is Extract<keyof T, string> =>
  Object.hasOwn(table, key);

/** Is this `repository.error` about an import (as opposed to a repository read problem)? */
export const isImportError = (code: string | null | undefined): code is string =>
  typeof code === "string" && (code.startsWith("archive_rejected:") || has(ERRORS, code));

/** The text for an import's `repository.error`; an unknown reason or code gets a general text, never the raw code. */
export function importErrorText(code: string, t: TFunction): string {
  if (code.startsWith("archive_rejected:")) {
    const reason = code.slice("archive_rejected:".length);
    return has(REASONS, reason) ? t(REASONS[reason]) : t("projects.import.reason.unknown");
  }
  return has(ERRORS, code) ? t(ERRORS[code]) : t("states.error.title");
}

/** The reason of a 409 CONFLICT from the import route (`details[0].reason`), if it is one we know. */
export function conflictReason(details: unknown): keyof typeof CONFLICTS | null {
  const first: unknown = Array.isArray(details) ? details[0] : null;
  if (typeof first !== "object" || first === null || !("reason" in first)) return null;
  const reason = String(first.reason);
  return has(CONFLICTS, reason) ? reason : null;
}

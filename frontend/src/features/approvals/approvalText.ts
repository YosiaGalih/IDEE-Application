import type { TFunction } from "i18next";
import type { Approval } from "@/api/approvals";
import { ApiError } from "@/api/errors";
import { describeError, isOutcomeUnknown, type ErrorText } from "@/lib/describeError";

/** Action types with their own label; any other type is shown as the server sent it (never hidden). */
const KNOWN_ACTIONS = [
  "project_delete",
  "apply_changes",
  "budget_increase",
  "tool_r3",
  "git_push",
  "deploy_staging",
  "deploy_production",
  "waive_criterion",
] as const;
type KnownAction = (typeof KNOWN_ACTIONS)[number];
const isKnown = (action: string): action is KnownAction =>
  (KNOWN_ACTIONS as readonly string[]).includes(action);

export const actionLabel = (t: TFunction, action: string): string =>
  isKnown(action) ? t(`approvals.actions.${action}`) : action;

const RISKS = ["0", "1", "2", "3", "4"] as const;
/** "R3 Tinggi, butuh persetujuan": the level as text, not only the badge colour. */
export function riskText(t: TFunction, level: number): string {
  const key = RISKS[level];
  return key === undefined
    ? `R${level}`
    : t("approvals.riskLevel", { level, label: t(`approvals.risk.${key}`) });
}

/**
 * What the approval asks for, in the screen's language. For a known action it is built from the payload
 * (which the hash binds); otherwise it is the server's `summary` as sent (spec 3.1c2 Lead 3).
 */
export function summaryText(t: TFunction, approval: Approval): string {
  const name = approval.payload.name;
  if (approval.action_type === "project_delete" && typeof name === "string") {
    return t("approvals.summaries.project_delete", { name });
  }
  return approval.summary;
}

export type Outcome = { tone: "success" | "warning"; text: string };

/**
 * What a decision did, from the approval the server returned (spec 3.1c2 Lead 4): "deleted" only once the
 * approval is consumed and its handler succeeded; an approved delete still running or failing says so.
 */
export function decisionOutcome(t: TFunction, decided: Approval): Outcome {
  const name = decided.project_name;
  const isDelete = decided.action_type === "project_delete";
  if (decided.status === "rejected") {
    return {
      tone: "success",
      text: isDelete ? t("approvals.result.rejected", { name }) : t("approvals.result.rejectedGeneric"),
    };
  }
  if (!isDelete) return { tone: "success", text: t("approvals.result.approvedGeneric") };
  if (decided.status === "consumed" && decided.handler_status === "succeeded") {
    return { tone: "success", text: t("approvals.result.deleted", { name }) };
  }
  if (decided.handler_status === "failed") {
    return { tone: "warning", text: t("approvals.result.approvedRetrying", { name }) };
  }
  return { tone: "warning", text: t("approvals.result.approvedRunning", { name }) };
}

const reasonOf = (details: unknown): string | null => {
  if (!Array.isArray(details)) return null;
  for (const detail of details) {
    if (typeof detail === "object" && detail !== null && "reason" in detail) {
      const reason = (detail as { reason: unknown }).reason;
      if (typeof reason === "string") return reason;
    }
  }
  return null;
};

export type DecisionError = ErrorText & {
  /** The approval cannot be decided any more (or may already be): sending again would not help. */
  isFinal: boolean;
};

/** A failed decision in this screen's words: the 409 reason, the hash mismatch, or an unknown outcome. */
export function decisionError(t: TFunction, error: unknown): DecisionError {
  const api = error instanceof ApiError ? error : null;
  const requestId = api?.requestId ?? null;
  const own = (title: string, isFinal: boolean): DecisionError => ({ title, body: null, requestId, isFinal });
  if (api?.kind === "http" && api.code === "INVALID_TRANSITION") {
    const reason = reasonOf(api.details);
    if (reason === "expired") return own(t("approvals.errors.expired"), true);
    if (reason === "superseded") return own(t("approvals.errors.superseded"), true);
    return own(t("approvals.errors.decided"), true);
  }
  if (api?.kind === "http" && api.code === "APPROVAL_PAYLOAD_MISMATCH")
    return own(t("approvals.errors.mismatch"), true);
  if (api?.kind === "http" && api.code === "FORBIDDEN") return own(t("approvals.errors.forbidden"), true);
  if (api?.kind === "http" && api.code === "NOT_FOUND") return own(t("approvals.errors.notFound"), true);
  if (api?.kind === "http" && api.code === "VALIDATION_ERROR")
    return own(t("approvals.errors.invalidNote"), false);
  if (isOutcomeUnknown(error)) return own(t("approvals.errors.outcomeUnknown"), true);
  return { ...describeError(error, t), isFinal: false };
}

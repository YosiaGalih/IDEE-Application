import type { TFunction } from "i18next";
import type { Approval } from "@/api/approvals";
import { ApiError } from "@/api/errors";
import { describeError, errorReason, isOutcomeUnknown, type ErrorText } from "@/lib/describeError";

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
    return t("approvals.summaries.project_delete", { name: revealHidden(name) });
  }
  return revealHidden(approval.summary);
}

export type Outcome = { tone: "success" | "warning"; text: string };

/** The outcome texts per kind of action: a new action type adds one entry, not four branches. */
const RESULT_TEXTS = {
  project_delete: {
    rejected: "approvals.result.rejected",
    done: "approvals.result.deleted",
    retrying: "approvals.result.approvedRetrying",
    running: "approvals.result.approvedRunning",
  },
  generic: {
    rejected: "approvals.result.rejectedGeneric",
    done: "approvals.result.approvedGeneric",
    retrying: "approvals.result.approvedGenericRetrying",
    running: "approvals.result.approvedGenericRunning",
  },
} as const;

/**
 * What a decision did, from the approval the server returned (spec 3.1c2 Lead 4). Every state is named:
 * "done" only once the approval is consumed and its handler succeeded; running and failing say so; anything
 * else is "unclear, reload", never a claim (review WP-3.1c2, silent-failure L2/L3).
 */
export function decisionOutcome(t: TFunction, decided: Approval): Outcome {
  const texts = decided.action_type === "project_delete" ? RESULT_TEXTS.project_delete : RESULT_TEXTS.generic;
  const name = revealHidden(decided.project_name);
  const isApproved = decided.status === "approved" || decided.status === "consumed";
  const hasRun = decided.status === "consumed" && decided.handler_status === "succeeded";
  if (decided.status === "rejected") return { tone: "success", text: t(texts.rejected, { name }) };
  if (hasRun) return { tone: "success", text: t(texts.done, { name }) };
  if (isApproved && decided.handler_status === "failed") {
    return { tone: "warning", text: t(texts.retrying, { name }) };
  }
  if (isApproved && decided.handler_status === "pending") {
    return { tone: "warning", text: t(texts.running, { name }) };
  }
  return { tone: "warning", text: t("approvals.result.unclear") };
}

/**
 * Bidi controls, zero-width and line/paragraph separator characters as visible `\uXXXX` escapes (AG-13
 * WP-3.1c2 L1): what a person approves must not be reordered or hidden on screen. Line breaks and tabs stay.
 */
export const revealHidden = (text: string): string =>
  text.replace(/[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/gu, (character) => {
    if (character === "\n" || character === "\t") return character;
    const code = character.codePointAt(0) ?? 0;
    const hex = code.toString(16);
    // Outside the BMP in braces: "\\ue0041" would read as U+E004 followed by "1".
    return code > 0xffff ? `\\u{${hex}}` : `\\u${hex.padStart(4, "0")}`;
  });

export type DecisionError = ErrorText & {
  /** The approval cannot be decided any more (or may already be): sending again would not help. */
  isFinal: boolean;
};

const DECIDED = new Set(["approved", "rejected", "consumed"]);

/** Did a 422 come from the note field (`details[].loc` ends with "note")? */
const isNoteError = (details: unknown): boolean =>
  Array.isArray(details) &&
  details.some((detail: unknown) => {
    if (typeof detail !== "object" || detail === null || !("loc" in detail)) return false;
    const loc: unknown = detail.loc;
    return Array.isArray(loc) && loc[loc.length - 1] === "note";
  });

/** A failed decision in this screen's words: the 409 reason, the hash mismatch, or an unknown outcome. */
export function decisionError(t: TFunction, error: unknown): DecisionError {
  const api = error instanceof ApiError ? error : null;
  const requestId = api?.requestId ?? null;
  const screenError = (title: string, isFinal: boolean): DecisionError => ({
    title,
    body: null,
    requestId,
    isFinal,
  });
  if (api?.kind === "http" && api.code === "INVALID_TRANSITION") {
    const reason = errorReason(api.details);
    if (reason === "expired") return screenError(t("approvals.errors.expired"), true);
    if (reason === "superseded") return screenError(t("approvals.errors.superseded"), true);
    if (reason !== null && DECIDED.has(reason)) return screenError(t("approvals.errors.decided"), true);
    return screenError(t("approvals.errors.cannotDecide"), true); // a reason this screen does not know: no guess
  }
  if (api?.kind === "http" && api.code === "APPROVAL_PAYLOAD_MISMATCH") {
    return screenError(t("approvals.errors.mismatch"), true);
  }
  if (api?.kind === "http" && api.code === "FORBIDDEN")
    return screenError(t("approvals.errors.forbidden"), true);
  if (api?.kind === "http" && api.code === "NOT_FOUND")
    return screenError(t("approvals.errors.notFound"), true);
  if (api?.kind === "http" && api.code === "VALIDATION_ERROR" && isNoteError(api.details)) {
    return screenError(t("approvals.errors.invalidNote"), false);
  }
  // A timeout or lost answer: the decision may already be recorded, and a second one could only be refused.
  if (isOutcomeUnknown(error)) return screenError(t("approvals.errors.outcomeUnknown"), true);
  return { ...describeError(error, t), isFinal: false };
}

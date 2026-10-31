import type { TFunction } from "i18next";
import { ApiError } from "@/api/errors";
import { uxStateForError } from "@/api/uxState";

export type ErrorText = { title: string; body: string | null; requestId: string | null };

/**
 * The text for a failed auth call. `byStatus` gives this screen's own wording (e.g. 401 on login), 429
 * says how long to wait (Retry-After), and anything else is the screen state of the failure.
 */
export function describeError(
  error: unknown,
  t: TFunction,
  byStatus: Partial<Record<number, string>> = {},
): ErrorText {
  const requestId = error instanceof ApiError ? error.requestId : null;
  const status = error instanceof ApiError && error.kind === "http" ? error.status : null;
  const own = status !== null ? byStatus[status] : undefined;
  if (own) return { title: own, body: null, requestId };
  if (status === 429 && error instanceof ApiError) {
    const seconds = error.retryAfterSeconds;
    return {
      title: seconds !== null ? t("auth.rateLimited", { seconds }) : t("auth.rateLimitedNoTime"),
      body: null,
      requestId,
    };
  }
  const state = uxStateForError(error);
  return { title: t(`states.${state}.title`), body: t(`states.${state}.body`), requestId };
}

import type { TFunction } from "i18next";
import { ApiError } from "@/api/errors";
import { uxStateForError } from "@/api/uxState";

export type ErrorText = { title: string; body: string | null; requestId: string | null };

type Options = {
  /** This screen's own wording by error code (API_CONTRACTS §1), e.g. FORBIDDEN on change password. */
  byCode?: Partial<Record<string, string>>;
  /** For a call that changes something: what to say when no answer came, so the outcome is unknown. */
  unknownOutcome?: string;
};

/** A gateway's own 502/503/504 page (no envelope): the request may have reached the API and run. */
const GATEWAY_STATUSES = new Set([502, 503, 504]);

/**
 * The call may have run although no success came back:
 * - no answer at all (network, timeout) or a 2xx that is not the envelope;
 * - a gateway error page without the envelope (the API restarted mid-request);
 * - 409 CONFLICT from the idempotency store: the first request with this key is still running or already ran.
 *   A route's own CONFLICT carries `details[].reason` (e.g. an import already running): that is an answer.
 * A retry keeps its Idempotency-Key then, so the server answers it once (API_CONTRACTS §1).
 */
export const isOutcomeUnknown = (error: unknown): boolean =>
  error instanceof ApiError &&
  (error.kind === "network" ||
    error.kind === "timeout" ||
    (error.kind === "bad_response" &&
      ((error.status ?? 0) < 400 || GATEWAY_STATUSES.has(error.status ?? 0))) ||
    (error.kind === "http" &&
      error.status === 409 &&
      error.code === "CONFLICT" &&
      !hasReason(error.details)));

const hasReason = (details: unknown) =>
  Array.isArray(details) &&
  details.some((detail: unknown) => typeof detail === "object" && detail !== null && "reason" in detail);

/**
 * The text for a failed call: the screen's wording for a known code, how long to wait on 429
 * (Retry-After), and otherwise the screen state of the failure. The code decides, never the status alone:
 * 403 is both FORBIDDEN and CSRF_FAILED.
 */
export function describeError(
  error: unknown,
  t: TFunction,
  { byCode = {}, unknownOutcome }: Options = {},
): ErrorText {
  const api = error instanceof ApiError ? error : null;
  const requestId = api?.requestId ?? null;
  const own = api?.kind === "http" && api.code !== null ? byCode[api.code] : undefined;
  if (own) return { title: own, body: null, requestId };
  if (api?.status === 429) {
    const count = api.retryAfterSeconds;
    return {
      title: count !== null ? t("errors.rateLimited", { count }) : t("errors.rateLimitedNoTime"),
      body: null,
      requestId,
    };
  }
  const state = uxStateForError(error);
  const body =
    unknownOutcome !== undefined && api !== null && isOutcomeUnknown(api)
      ? unknownOutcome
      : t(`states.${state}.body`);
  return { title: t(`states.${state}.title`), body, requestId };
}

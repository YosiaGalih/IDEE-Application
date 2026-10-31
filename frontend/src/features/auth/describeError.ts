import type { TFunction } from "i18next";
import { ApiError } from "@/api/errors";
import { uxStateForError } from "@/api/uxState";

export type ErrorText = { title: string; body: string | null; requestId: string | null };

type Options = {
  /** This screen's own wording by error code (API_CONTRACTS §1), e.g. FORBIDDEN on change password. */
  byCode?: Partial<Record<string, string>>;
  /** The call changes something: without an answer its outcome is unknown, and the text says so. */
  isChange?: boolean;
};

/** No answer from the server, or an answer that is not the error envelope on a 2xx: it may have worked. */
const isUnanswered = (error: ApiError) =>
  error.kind === "network" ||
  error.kind === "timeout" ||
  (error.kind === "bad_response" && (error.status ?? 0) < 400);

/**
 * The text for a failed auth call: the screen's wording for a known code, how long to wait on 429
 * (Retry-After), and otherwise the screen state of the failure. The code decides, never the status alone:
 * 403 is both FORBIDDEN and CSRF_FAILED.
 */
export function describeError(
  error: unknown,
  t: TFunction,
  { byCode = {}, isChange = false }: Options = {},
): ErrorText {
  const api = error instanceof ApiError ? error : null;
  const requestId = api?.requestId ?? null;
  const own = api?.kind === "http" && api.code !== null ? byCode[api.code] : undefined;
  if (own) return { title: own, body: null, requestId };
  if (api?.status === 429) {
    const count = api.retryAfterSeconds;
    return {
      title: count !== null ? t("auth.rateLimited", { count }) : t("auth.rateLimitedNoTime"),
      body: null,
      requestId,
    };
  }
  const state = uxStateForError(error);
  const body =
    isChange && api !== null && isUnanswered(api) ? t("auth.outcomeUnknown") : t(`states.${state}.body`);
  return { title: t(`states.${state}.title`), body, requestId };
}

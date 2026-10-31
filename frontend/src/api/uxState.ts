import { ApiError } from "./errors";

/** The screen states every view must render (PRD §4.1–§4.2, NFR-21, frontend.md). */
export type UxState =
  | "loading"
  | "empty"
  | "error"
  | "timeout"
  | "disconnected"
  | "permission_denied"
  | "partial_success"
  | "quota_exceeded"
  | "capability_disabled"
  | "provider_degraded"
  | "unauthenticated"
  | "rate_limited"
  | "not_found";

const BY_CODE: Record<string, UxState> = {
  UNAUTHENTICATED: "unauthenticated",
  SESSION_EXPIRED: "unauthenticated",
  FORBIDDEN: "permission_denied",
  POLICY_DENIED: "permission_denied",
  CAPABILITY_DISABLED: "capability_disabled",
  NOT_FOUND: "not_found",
  QUOTA_EXCEEDED: "quota_exceeded",
  RATE_LIMITED: "rate_limited",
  DEPENDENCY_UNAVAILABLE: "provider_degraded",
};

/** Codes we do not know yet (or a gateway page without the envelope) still get the state of their status. */
const BY_STATUS: Record<number, UxState> = {
  401: "unauthenticated",
  403: "permission_denied",
  404: "not_found",
  429: "rate_limited",
  502: "provider_degraded",
  503: "provider_degraded",
  504: "provider_degraded",
};

/** The state a failed call puts a view in. Unknown failures are a plain error, never silently "empty". */
export function uxStateForError(error: unknown): UxState {
  if (!(error instanceof ApiError)) return "error";
  if (error.kind === "timeout") return "timeout";
  if (error.kind === "network") return "disconnected";
  if (error.kind !== "http" && error.kind !== "bad_response") return "error";
  const byCode = error.code ? BY_CODE[error.code] : undefined;
  if (byCode) return byCode;
  if (error.code === "CSRF_FAILED") return "error"; // a client bug, not missing rights
  return (error.status !== null ? BY_STATUS[error.status] : undefined) ?? "error";
}

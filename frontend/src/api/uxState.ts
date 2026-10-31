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

/** The state a failed call puts a view in. Unknown failures are a plain error, never silently "empty". */
export function uxStateForError(error: unknown): UxState {
  if (!(error instanceof ApiError)) return "error";
  switch (error.kind) {
    case "timeout":
      return "timeout";
    case "network":
      return "disconnected";
    case "http":
      break;
    default:
      return "error";
  }
  switch (error.code) {
    case "UNAUTHENTICATED":
    case "SESSION_EXPIRED":
      return "unauthenticated";
    case "FORBIDDEN":
    case "POLICY_DENIED":
      return "permission_denied";
    case "CAPABILITY_DISABLED":
      return "capability_disabled";
    case "NOT_FOUND":
      return "not_found";
    case "QUOTA_EXCEEDED":
      return "quota_exceeded";
    case "RATE_LIMITED":
      return "rate_limited";
    case "DEPENDENCY_UNAVAILABLE":
      return "provider_degraded";
    default:
      return error.status === 401 ? "unauthenticated" : "error";
  }
}

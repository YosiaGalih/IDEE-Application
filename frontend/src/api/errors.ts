/**
 * Every failure of an API call, typed (API_CONTRACTS §1):
 * - http: the server answered with the error envelope (`code` from the contract);
 * - network / timeout / aborted: no answer;
 * - bad_response: an answer that is not the envelope, or data that fails its schema;
 * - csrf_missing: a mutation refused before sending because the CSRF cookie is absent.
 */
export type ApiErrorKind = "http" | "network" | "timeout" | "aborted" | "bad_response" | "csrf_missing";

type ApiErrorInit = {
  kind: ApiErrorKind;
  status?: number | null;
  code?: string | null;
  message?: string;
  details?: unknown;
  requestId?: string | null;
  cause?: unknown;
};

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly code: string | null;
  readonly details: unknown;
  readonly requestId: string | null;

  constructor(init: ApiErrorInit) {
    super(init.message ?? init.code ?? init.kind, { cause: init.cause });
    this.name = "ApiError";
    this.kind = init.kind;
    this.status = init.status ?? null;
    this.code = init.code ?? null;
    this.details = init.details;
    this.requestId = init.requestId ?? null;
  }
}

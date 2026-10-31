import { z } from "zod";
import { CSRF_COOKIE, readCookie } from "./cookies";
import { ApiError } from "./errors";

/** Same-origin API base (CONFIGURATION: VITE_API_BASE, default /api/v1). Never holds a secret. */
export const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api/v1";
const DEFAULT_TIMEOUT_MS = 30_000;
const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

const failureEnvelope = z.object({
  request_id: z.string().nullish(),
  status: z.literal("failed"),
  error: z.object({ code: z.string(), message: z.string(), details: z.unknown().optional() }),
});
const successEnvelope = z.object({
  request_id: z.string().nullish(),
  status: z.enum(["completed", "accepted", "running"]),
  data: z.unknown(),
  error: z.null().optional(),
  meta: z.unknown().optional(),
});

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export type RequestOptions<T> = {
  method?: HttpMethod;
  body?: unknown;
  schema: z.ZodType<T>;
  /** Reuse the same key to retry the same action; a new one is made per call otherwise. */
  idempotencyKey?: string;
  signal?: AbortSignal;
  timeoutMs?: number;
};

export type ApiResult<T> = {
  data: T;
  status: "completed" | "accepted" | "running";
  meta: unknown;
  requestId: string | null;
};

function apiUrl(path: string): URL {
  if (!path.startsWith("/") || path.startsWith("//") || path.split(/[/?#]/).includes("..")) {
    throw new Error(`API path must be a plain absolute path under ${API_BASE}: ${path}`);
  }
  return new URL(`${API_BASE}${path}`, window.location.origin);
}

/**
 * One API call (API_CONTRACTS §1–§2): validates the envelope and the data, sends the CSRF cookie value on
 * every mutation except login, and an Idempotency-Key on every mutation outside /auth/.
 */
export async function apiRequest<T>(path: string, options: RequestOptions<T>): Promise<ApiResult<T>> {
  const method = options.method ?? "GET";
  const url = apiUrl(path);
  const headers = new Headers({ Accept: "application/json" });
  if (options.body !== undefined) headers.set("Content-Type", "application/json");

  if (MUTATING.has(method)) {
    if (path !== "/auth/login") {
      const csrf = readCookie(CSRF_COOKIE);
      if (!csrf) {
        throw new ApiError({ kind: "csrf_missing", message: "No CSRF cookie yet: load the session first" });
      }
      headers.set("X-CSRF-Token", csrf);
    }
    if (!path.startsWith("/auth/"))
      headers.set("Idempotency-Key", options.idempotencyKey ?? crypto.randomUUID());
  }

  const timeout = AbortSignal.timeout(options.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  const signal = options.signal ? AbortSignal.any([options.signal, timeout]) : timeout;
  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: options.body === undefined ? null : JSON.stringify(options.body),
      credentials: "same-origin",
      signal,
    });
  } catch (cause) {
    if (timeout.aborted) throw new ApiError({ kind: "timeout", cause });
    if (options.signal?.aborted) throw new ApiError({ kind: "aborted", cause });
    throw new ApiError({ kind: "network", cause });
  }

  const headerRequestId = response.headers.get("X-Request-ID");
  let body: unknown;
  try {
    body = await response.json();
  } catch (cause) {
    throw new ApiError({
      kind: "bad_response",
      status: response.status,
      message: "Response is not JSON",
      requestId: headerRequestId,
      cause,
    });
  }

  const failure = failureEnvelope.safeParse(body);
  if (failure.success) {
    const { error, request_id } = failure.data;
    throw new ApiError({
      kind: "http",
      status: response.status,
      code: error.code,
      message: error.message,
      details: error.details,
      requestId: request_id ?? headerRequestId,
    });
  }
  const success = successEnvelope.safeParse(body);
  if (!response.ok || !success.success) {
    throw new ApiError({
      kind: "bad_response",
      status: response.status,
      message: "Response is not the API envelope",
      requestId: headerRequestId,
    });
  }
  const data = options.schema.safeParse(success.data.data);
  if (!data.success) {
    throw new ApiError({
      kind: "bad_response",
      status: response.status,
      message: `Response data does not match the expected shape: ${data.error.message}`,
      requestId: success.data.request_id ?? headerRequestId,
    });
  }
  return {
    data: data.data,
    status: success.data.status,
    meta: success.data.meta,
    requestId: success.data.request_id ?? headerRequestId,
  };
}

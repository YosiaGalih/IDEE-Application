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

/** `meta` of a list response (API_CONTRACTS §1). */
export const PageMetaSchema = z.object({
  page: z.number().int().positive(),
  page_size: z.number().int().positive(),
  total: z.number().int().nonnegative(),
});
export type PageMeta = z.infer<typeof PageMetaSchema>;

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export type RequestOptions<T> = {
  method?: HttpMethod;
  /** JSON, or FormData for uploads (sent as multipart, never JSON-encoded). */
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

/**
 * Builds an API path from segments, each percent-encoded, so a route parameter can never add a segment:
 * `apiPath("projects", projectId, "imports")` → `/projects/<encoded id>/imports`.
 */
export function apiPath(...segments: (string | number)[]): string {
  return `/${segments.map((segment) => encodeURIComponent(String(segment))).join("/")}`;
}

/**
 * The URL of an API path. Refuses anything that could leave the API base once the URL parser resolves it
 * (`..`, `%2e`, `\`), and checks the result is still same-origin under API_BASE (used by HTTP and SSE).
 */
export function apiUrl(path: string): URL {
  const unsafe =
    !path.startsWith("/") ||
    path.startsWith("//") ||
    path.includes("\\") ||
    /%2e/i.test(path) ||
    // eslint-disable-next-line no-control-regex -- the point is to refuse control characters
    /[\u0000-\u001F\u007F]/.test(path) ||
    path.split(/[/?#]/).includes("..");
  const url = new URL(`${API_BASE}${path}`, window.location.origin);
  // Whatever the parser rewrites (dropped tab/CR/LF, folded dots, escaped characters) is refused.
  const rawPathname = `${API_BASE}${path.split(/[?#]/)[0] ?? ""}`;
  if (unsafe || url.origin !== window.location.origin || url.pathname !== rawPathname) {
    throw new Error(`API path must be a plain absolute path under ${API_BASE}: ${path}`);
  }
  return url;
}

/**
 * There is no CSRF endpoint: any API response sets the pre-session cookie (core/csrf.py), and logout
 * clears it. A mutation without the cookie first makes one GET to get it.
 */
let csrfBootstrap: Promise<void> | null = null;

async function csrfToken(signal: AbortSignal): Promise<string> {
  const existing = readCookie(CSRF_COOKIE);
  if (existing) return existing;
  // One GET for all mutations waiting at once: two would each set a different cookie.
  csrfBootstrap ??= fetch(apiUrl("/auth/me"), {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    signal,
  })
    .then(() => undefined)
    .catch(() => undefined) // reported below as csrf_missing
    .finally(() => {
      csrfBootstrap = null;
    });
  await csrfBootstrap;
  const issued = readCookie(CSRF_COOKIE);
  if (!issued)
    throw new ApiError({ kind: "csrf_missing", message: "The server did not issue a CSRF cookie" });
  return issued;
}

function abortKind(timeout: AbortSignal, caller?: AbortSignal): "timeout" | "aborted" | null {
  if (timeout.aborted) return "timeout";
  if (caller?.aborted) return "aborted";
  return null;
}

/**
 * One API call (API_CONTRACTS §1–§2): validates the envelope and the data, sends the CSRF cookie value on
 * every mutation except login, and an Idempotency-Key on every mutation outside /auth/.
 */
export async function apiRequest<T>(path: string, options: RequestOptions<T>): Promise<ApiResult<T>> {
  const method = options.method ?? "GET";
  const url = apiUrl(path);
  const timeout = AbortSignal.timeout(options.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  const signal = options.signal ? AbortSignal.any([options.signal, timeout]) : timeout;
  const headers = new Headers({ Accept: "application/json" });
  const isForm = options.body instanceof FormData;
  if (options.body !== undefined && !isForm) headers.set("Content-Type", "application/json");

  let idempotencyKey: string | null = null;
  if (MUTATING.has(method)) {
    if (path !== "/auth/login") headers.set("X-CSRF-Token", await csrfToken(signal));
    if (!path.startsWith("/auth/")) {
      idempotencyKey = options.idempotencyKey ?? crypto.randomUUID();
      headers.set("Idempotency-Key", idempotencyKey);
    }
  }
  const fail = (init: ConstructorParameters<typeof ApiError>[0]) => new ApiError({ ...init, idempotencyKey });

  let body: BodyInit | null = null;
  if (isForm) body = options.body as FormData;
  else if (options.body !== undefined) body = JSON.stringify(options.body);

  let response: Response;
  try {
    response = await fetch(url, { method, headers, body, credentials: "same-origin", signal });
  } catch (cause) {
    throw fail({ kind: abortKind(timeout, options.signal) ?? "network", cause });
  }

  const headerRequestId = response.headers.get("X-Request-ID");
  const retryAfter = /^\d{1,6}$/.test(response.headers.get("Retry-After") ?? "")
    ? Number(response.headers.get("Retry-After"))
    : null;
  let payload: unknown;
  try {
    payload = await response.json();
  } catch (cause) {
    const aborted = abortKind(timeout, options.signal);
    throw fail({
      kind: aborted ?? "bad_response",
      status: response.status,
      message: aborted ? `Request ${aborted} while reading the response` : "Response is not JSON",
      requestId: headerRequestId,
      cause,
    });
  }

  const failure = failureEnvelope.safeParse(payload);
  if (failure.success) {
    const { error, request_id } = failure.data;
    throw fail({
      kind: "http",
      status: response.status,
      code: error.code,
      message: error.message,
      details: error.details,
      requestId: request_id ?? headerRequestId,
      retryAfterSeconds: retryAfter,
    });
  }
  const success = successEnvelope.safeParse(payload);
  if (!response.ok || !success.success) {
    throw fail({
      kind: "bad_response",
      status: response.status,
      message: "Response is not the API envelope",
      requestId: headerRequestId,
    });
  }
  const data = options.schema.safeParse(success.data.data);
  if (!data.success) {
    throw fail({
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

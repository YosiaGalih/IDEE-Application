import type { z } from "zod";
import { abortKind, apiUrl, csrfToken, interpretResponse, type ApiResult, type RawResponse } from "./client";
import { ApiError } from "./errors";

/** Up to 200 MiB over a slow line; the server's own limits end a stuck upload sooner. */
const UPLOAD_TIMEOUT_MS = 30 * 60_000;

export type UploadOptions<T> = {
  file: File;
  /** The multipart field name the endpoint expects. */
  field?: string;
  schema: z.ZodType<T>;
  /** Kept by the caller while the outcome is unknown, so a retry is answered once (API_CONTRACTS §1). */
  idempotencyKey: string;
  onProgress?: (sent: number, total: number) => void;
  signal?: AbortSignal;
  timeoutMs?: number;
};

/**
 * A multipart file upload that reports progress, which `fetch` cannot. Same CSRF header, Idempotency-Key,
 * error kinds and envelope rules as `apiRequest`.
 */
export async function apiUpload<T>(path: string, options: UploadOptions<T>): Promise<ApiResult<T>> {
  const url = apiUrl(path);
  const timeout = AbortSignal.timeout(options.timeoutMs ?? UPLOAD_TIMEOUT_MS);
  const signal = options.signal ? AbortSignal.any([options.signal, timeout]) : timeout;
  const fail = (init: ConstructorParameters<typeof ApiError>[0]) =>
    new ApiError({ ...init, idempotencyKey: options.idempotencyKey });
  const csrf = await csrfToken(signal, timeout, options.signal);
  const form = new FormData();
  form.append(options.field ?? "file", options.file, options.file.name);

  const raw = await new Promise<RawResponse>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const onAbort = () => xhr.abort();
    const settle = () => signal.removeEventListener("abort", onAbort);
    xhr.open("POST", url.toString());
    xhr.setRequestHeader("Accept", "application/json");
    xhr.setRequestHeader("X-CSRF-Token", csrf);
    xhr.setRequestHeader("Idempotency-Key", options.idempotencyKey);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) options.onProgress?.(event.loaded, event.total);
    };
    xhr.onload = () => {
      settle();
      const text = xhr.responseText;
      resolve({
        status: xhr.status,
        ok: xhr.status >= 200 && xhr.status < 300,
        header: (name) => xhr.getResponseHeader(name),
        json: () => Promise.resolve().then(() => JSON.parse(text) as unknown),
      });
    };
    xhr.onerror = () => {
      settle();
      reject(fail({ kind: "network", message: "The upload got no answer" }));
    };
    xhr.onabort = () => {
      settle();
      reject(
        fail({ kind: abortKind(timeout, options.signal) ?? "aborted", message: "The upload was stopped" }),
      );
    };
    if (signal.aborted) {
      reject(
        fail({ kind: abortKind(timeout, options.signal) ?? "aborted", message: "The upload was stopped" }),
      );
      return;
    }
    signal.addEventListener("abort", onAbort, { once: true });
    xhr.send(form);
  });
  return interpretResponse(raw, options.schema, fail, () => abortKind(timeout, options.signal));
}

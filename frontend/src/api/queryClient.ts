import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import { ApiError } from "./errors";

/** Retries only what can recover by itself: no answer, or a 5xx. Never a 4xx or a malformed 2xx. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= 2) return false;
  if (!(error instanceof ApiError)) return false;
  if (error.kind === "network" || error.kind === "timeout") return true;
  return (
    (error.kind === "http" || error.kind === "bad_response") && error.status !== null && error.status >= 500
  );
}

const isUnauthenticated = (error: unknown) => error instanceof ApiError && error.status === 401;

/**
 * `meta` of a query or mutation that handles its own 401: the session guard's /auth/me (it redirects with
 * `next`) and the login form (a 401 there means wrong credentials, not an expired session).
 */
export const HANDLES_UNAUTHENTICATED = { handlesUnauthenticated: true } as const;
const handlesItself = (meta: Record<string, unknown> | undefined) =>
  meta?.["handlesUnauthenticated"] === true;

type Hooks = {
  /** Called once per 401 from any query or mutation not marked HANDLES_UNAUTHENTICATED. */
  onUnauthenticated?: () => void;
};

export function createQueryClient(hooks: Hooks = {}): QueryClient {
  const handle = (error: unknown, meta: Record<string, unknown> | undefined) => {
    if (isUnauthenticated(error) && !handlesItself(meta)) hooks.onUnauthenticated?.();
  };
  return new QueryClient({
    queryCache: new QueryCache({ onError: (error, query) => handle(error, query.meta) }),
    mutationCache: new MutationCache({
      onError: (error, _variables, _result, mutation) => handle(error, mutation.options.meta),
    }),
    defaultOptions: {
      // "always": offline is a network error (the disconnected state), not a query paused forever.
      queries: { retry: shouldRetry, staleTime: 15_000, refetchOnWindowFocus: false, networkMode: "always" },
      mutations: { retry: false, networkMode: "always" },
    },
  });
}

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

type Hooks = {
  /** Called once per 401 from any query or mutation, e.g. to send the user to /login. */
  onUnauthenticated?: () => void;
};

export function createQueryClient(hooks: Hooks = {}): QueryClient {
  const handle = (error: unknown) => {
    if (isUnauthenticated(error)) hooks.onUnauthenticated?.();
  };
  return new QueryClient({
    queryCache: new QueryCache({ onError: handle }),
    mutationCache: new MutationCache({ onError: handle }),
    defaultOptions: {
      // "always": offline is a network error (the disconnected state), not a query paused forever.
      queries: { retry: shouldRetry, staleTime: 15_000, refetchOnWindowFocus: false, networkMode: "always" },
      mutations: { retry: false, networkMode: "always" },
    },
  });
}

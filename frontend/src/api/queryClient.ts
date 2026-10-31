import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "./errors";

/** Retries only what can recover by itself: no answer, or a 5xx/503. Never a 4xx or a bad response. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= 2) return false;
  if (!(error instanceof ApiError)) return false;
  if (error.kind === "network" || error.kind === "timeout") return true;
  return error.kind === "http" && error.status !== null && error.status >= 500;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: shouldRetry, staleTime: 15_000, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
}

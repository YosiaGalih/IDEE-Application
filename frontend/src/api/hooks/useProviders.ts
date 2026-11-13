import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getReadiness,
  listProviders,
  patchProvider,
  runSmoke,
  type Provider,
  type ProviderPatch,
  type Readiness,
} from "../providers";

export const providersKey = ["settings", "providers"] as const;
/** How often readiness is read while a smoke that answered 202 runs (spec WP-3.1e2 Lead 4). */
export const SMOKE_POLL_MS = 3_000;
/** A run is waited for this long, then the screen says no report came (a stuck lock expires in 5 min). */
export const SMOKE_WAIT_LIMIT_MS = 5 * 60_000;
const readinessKey = (providerId: string) => [...providersKey, providerId, "readiness"] as const;

export function useProviders() {
  return useQuery({ queryKey: providersKey, queryFn: ({ signal }) => listProviders(signal) });
}

/** What a polling decision may look at: the last read and when it was made. */
export type ReadinessQuery = {
  state: { data?: Readiness | undefined; dataUpdatedAt: number; errorUpdatedAt: number };
};

/** The last report; `refetchInterval` polls while a smoke that answered 202 is still running. */
export function useReadiness(
  providerId: string,
  refetchInterval: false | ((query: ReadinessQuery) => number | false) = false,
) {
  return useQuery({
    queryKey: readinessKey(providerId),
    queryFn: ({ signal }) => getReadiness(providerId, signal),
    refetchInterval,
  });
}

export function usePatchProvider(providerId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (patch: ProviderPatch) => patchProvider(providerId, patch),
    // The server's answer is what was applied: shown at once, even if the list read after it fails
    // (silent-failure 3.1e2 H1).
    onSuccess: (updated) => {
      client.setQueryData<Provider[]>(providersKey, (rows) =>
        rows?.map((row) => (row.id === updated.id ? updated : row)),
      );
    },
    // Not awaited: the caller's onSuccess runs before the list read remounts what it re-keys.
    onSettled: () => {
      void client.invalidateQueries({ queryKey: providersKey });
    },
  });
}

export function useSmokeTest(providerId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => runSmoke(providerId),
    // Not awaited: the screen says "still running" as soon as the 202 arrives, not after the next read.
    onSettled: () => {
      void client.invalidateQueries({ queryKey: providersKey });
    },
  });
}

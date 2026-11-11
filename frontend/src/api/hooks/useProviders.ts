import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getReadiness,
  listProviders,
  patchProvider,
  runSmoke,
  type ProviderPatch,
  type Readiness,
} from "../providers";

export const providersKey = ["settings", "providers"] as const;
/** How often readiness is read while a smoke that answered 202 runs (spec WP-3.1e2 Lead 4). */
export const SMOKE_POLL_MS = 3_000;
const readinessKey = (providerId: string) => [...providersKey, providerId, "readiness"] as const;

export function useProviders() {
  return useQuery({ queryKey: providersKey, queryFn: ({ signal }) => listProviders(signal) });
}

/** What a polling decision may look at: the last read and when it was made. */
export type ReadinessQuery = { state: { data?: Readiness | undefined; dataUpdatedAt: number } };

/** The last report; `refetchInterval` polls while a smoke that answered 202 is still running. */
export function useReadiness(
  providerId: string,
  refetchInterval: number | false | ((query: ReadinessQuery) => number | false) = false,
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
    onSettled: () => client.invalidateQueries({ queryKey: providersKey }),
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

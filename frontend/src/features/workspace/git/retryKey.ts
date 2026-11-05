import { useMemo, useRef } from "react";
import { isOutcomeUnknown } from "@/lib/describeError";

/**
 * One Idempotency-Key per request (API_CONTRACTS §1). The key of a request whose outcome is unknown is reused
 * only for the same body against the same repository state (`context`: HEAD and the listed changes, or the
 * branch names). Once that state moves — the commit landed, a file changed, a branch appeared — any request
 * is a new one, so the server never answers it with an earlier commit's sha.
 */
export function useRetryKey() {
  const unknown = useRef<{ body: string; context: string; key: string } | null>(null);
  return useMemo(
    () => ({
      keyFor: (body: string, context: string) => {
        const kept = unknown.current;
        return kept && kept.body === body && kept.context === context ? kept.key : crypto.randomUUID();
      },
      settle: (body: string, context: string, key: string, error: unknown) => {
        unknown.current = error !== null && isOutcomeUnknown(error) ? { body, context, key } : null;
      },
    }),
    [],
  );
}

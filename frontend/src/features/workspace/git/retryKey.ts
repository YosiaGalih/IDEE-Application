import { useMemo, useRef } from "react";
import { isOutcomeUnknown } from "@/lib/describeError";

/**
 * One Idempotency-Key per request (API_CONTRACTS §1): the same body after an unknown outcome is retried with
 * the same key, so the server answers it once; any other request, or a changed body, gets a new one.
 */
export function useRetryKey() {
  const unknown = useRef<{ body: string; key: string } | null>(null);
  return useMemo(
    () => ({
      keyFor: (body: string) => (unknown.current?.body === body ? unknown.current.key : crypto.randomUUID()),
      settle: (body: string, key: string, error: unknown) => {
        unknown.current = error !== null && isOutcomeUnknown(error) ? { body, key } : null;
      },
    }),
    [],
  );
}

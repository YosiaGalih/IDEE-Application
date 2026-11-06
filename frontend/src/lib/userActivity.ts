/**
 * Whether someone is using the page (AG-13 WP-3.1c2 M1). Every authenticated request moves the session's
 * `last_seen_at` forward, so a poll that runs on an unattended screen would keep the session alive past its
 * idle timeout (SECURITY_MODEL §8). Polls ask `whileUserActive(ms)` for their interval instead of a number.
 */
export const IDLE_AFTER_MS = 10 * 60_000;

// No pointermove: browsers send synthetic moves when content changes under a still cursor, which a poll
// itself causes; that would keep an unattended page "in use" (AG-13 WP-3.1c2 delta INFO).
const INPUT_EVENTS = ["pointerdown", "keydown", "wheel", "touchstart"] as const;
let lastInput = Date.now();
const resumeListeners = new Set<() => void>();

export const isUserActive = (now: number = Date.now()): boolean => now - lastInput <= IDLE_AFTER_MS;

function recordInput() {
  const now = Date.now();
  const wasIdle = !isUserActive(now);
  lastInput = now;
  if (wasIdle) resumeListeners.forEach((listener) => listener());
}

if (typeof window !== "undefined") {
  for (const type of INPUT_EVENTS) {
    window.addEventListener(type, recordInput, { capture: true, passive: true });
  }
}

/** A TanStack Query `refetchInterval`: `ms` while the page is in use, no polling once it is left alone. */
export const whileUserActive =
  (ms: number): (() => number | false) =>
  () =>
    isUserActive() ? ms : false;

/** Called when someone uses the page again after it was idle (a stopped poll should catch up). */
export function onActivityResumed(listener: () => void): () => void {
  resumeListeners.add(listener);
  return () => {
    resumeListeners.delete(listener);
  };
}

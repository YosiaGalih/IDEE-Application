import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { IDLE_AFTER_MS, isUserActive, onActivityResumed, whileUserActive } from "@/lib/userActivity";

/**
 * AG-13 WP-3.1c2 M1: a poll must not keep an unattended session alive. Polls run only while someone has used
 * the page recently; the idle timeout on the server (SECURITY_MODEL §8) can then do its job.
 */
describe("userActivity", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    window.dispatchEvent(new KeyboardEvent("keydown")); // start from "just used"
  });
  afterEach(() => vi.useRealTimers());

  it("counts the page as in use until it has been left alone for the idle period", () => {
    expect(isUserActive()).toBe(true);
    vi.advanceTimersByTime(IDLE_AFTER_MS - 1);
    expect(isUserActive()).toBe(true);
    vi.advanceTimersByTime(2);
    expect(isUserActive()).toBe(false);
  });

  it("gives a poll its interval only while the page is in use", () => {
    const interval = whileUserActive(30_000);
    expect(interval()).toBe(30_000);
    vi.advanceTimersByTime(IDLE_AFTER_MS + 1);
    expect(interval()).toBe(false); // no request: the session can expire
    window.dispatchEvent(new PointerEvent("pointerdown"));
    expect(interval()).toBe(30_000);
  });

  it("tells listeners once when someone comes back after being idle", () => {
    const resumed = vi.fn();
    const stop = onActivityResumed(resumed);
    window.dispatchEvent(new KeyboardEvent("keydown")); // still active: not a resume
    expect(resumed).not.toHaveBeenCalled();
    vi.advanceTimersByTime(IDLE_AFTER_MS + 1);
    window.dispatchEvent(new KeyboardEvent("keydown"));
    window.dispatchEvent(new KeyboardEvent("keydown"));
    expect(resumed).toHaveBeenCalledTimes(1);
    stop();
    vi.advanceTimersByTime(IDLE_AFTER_MS + 1);
    window.dispatchEvent(new KeyboardEvent("keydown"));
    expect(resumed).toHaveBeenCalledTimes(1);
  });
});

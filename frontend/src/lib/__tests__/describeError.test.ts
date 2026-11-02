import { describe, expect, it } from "vitest";
import { ApiError } from "@/api/errors";
import { isOutcomeUnknown } from "../describeError";

describe("isOutcomeUnknown", () => {
  it("treats the idempotency store's 409 CONFLICT (no reason) as unknown", () => {
    expect(isOutcomeUnknown(new ApiError({ kind: "http", status: 409, code: "CONFLICT" }))).toBe(true);
  });

  it("treats a route's own 409 CONFLICT with a reason as an answer", () => {
    const routeConflict = new ApiError({
      kind: "http",
      status: 409,
      code: "CONFLICT",
      details: [{ reason: "import_running" }],
    });
    expect(isOutcomeUnknown(routeConflict)).toBe(false);
  });

  it("treats no answer and a gateway page as unknown, and an envelope 4xx as an answer", () => {
    expect(isOutcomeUnknown(new ApiError({ kind: "network" }))).toBe(true);
    expect(isOutcomeUnknown(new ApiError({ kind: "bad_response", status: 502 }))).toBe(true);
    expect(isOutcomeUnknown(new ApiError({ kind: "http", status: 413, code: "PAYLOAD_TOO_LARGE" }))).toBe(
      false,
    );
  });
});

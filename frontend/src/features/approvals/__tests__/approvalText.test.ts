import i18next from "i18next";
import { describe, expect, it } from "vitest";
import { approval } from "@/__tests__/approvalFixtures";
import type { Approval } from "@/api/approvals";
import { ApiError } from "@/api/errors";
import id from "@/i18n/locales/id.json";
import { decisionError, decisionOutcome, revealHidden } from "../approvalText";

const t = i18next.getFixedT("id");
const name = approval().project_name;
const fill = (text: string) => text.replace("{{name}}", name);

describe("decisionOutcome: says only what the server reported (evidence over claims)", () => {
  it.each<[string, Partial<Approval>, string, "success" | "warning"]>([
    ["delete rejected", { status: "rejected" }, fill(id.approvals.result.rejected), "success"],
    [
      "delete ran",
      { status: "consumed", handler_status: "succeeded" },
      fill(id.approvals.result.deleted),
      "success",
    ],
    [
      "delete failing",
      { status: "approved", handler_status: "failed" },
      fill(id.approvals.result.approvedRetrying),
      "warning",
    ],
    [
      "delete running",
      { status: "approved", handler_status: "pending" },
      fill(id.approvals.result.approvedRunning),
      "warning",
    ],
    [
      "superseded under us",
      { status: "superseded", handler_status: "not_required" },
      id.approvals.result.unclear,
      "warning",
    ],
    [
      "approved, nothing to run",
      { status: "approved", handler_status: "not_required" },
      id.approvals.result.unclear,
      "warning",
    ],
    ["still pending", { status: "pending" }, id.approvals.result.unclear, "warning"],
  ])("project_delete: %s", (_, overrides, text, tone) => {
    expect(decisionOutcome(t, approval(overrides))).toEqual({ text, tone });
  });

  it.each<[string, Partial<Approval>, string, "success" | "warning"]>([
    [
      "ran",
      { status: "consumed", handler_status: "succeeded" },
      id.approvals.result.approvedGeneric,
      "success",
    ],
    [
      "failing",
      { status: "approved", handler_status: "failed" },
      id.approvals.result.approvedGenericRetrying,
      "warning",
    ],
    [
      "running",
      { status: "approved", handler_status: "pending" },
      id.approvals.result.approvedGenericRunning,
      "warning",
    ],
    ["rejected", { status: "rejected" }, id.approvals.result.rejectedGeneric, "success"],
  ])("another action type: %s", (_, overrides, text, tone) => {
    expect(decisionOutcome(t, approval({ action_type: "budget_increase", ...overrides }))).toEqual({
      text,
      tone,
    });
  });
});

const http = (code: string, details?: unknown) =>
  new ApiError({
    kind: "http",
    status: code === "VALIDATION_ERROR" ? 422 : 409,
    code,
    details,
    requestId: "r-1",
  });

describe("decisionError", () => {
  it.each<[string, ApiError, string, boolean]>([
    ["expired", http("INVALID_TRANSITION", [{ reason: "expired" }]), id.approvals.errors.expired, true],
    [
      "superseded",
      http("INVALID_TRANSITION", [{ reason: "superseded" }]),
      id.approvals.errors.superseded,
      true,
    ],
    ["decided", http("INVALID_TRANSITION", [{ reason: "consumed" }]), id.approvals.errors.decided, true],
    [
      "unknown reason",
      http("INVALID_TRANSITION", [{ reason: "frozen" }]),
      id.approvals.errors.cannotDecide,
      true,
    ],
    ["no reason", http("INVALID_TRANSITION"), id.approvals.errors.cannotDecide, true],
    [
      "bad note",
      http("VALIDATION_ERROR", [{ loc: ["body", "note"], msg: "x" }]),
      id.approvals.errors.invalidNote,
      false,
    ],
    [
      "bad other field",
      http("VALIDATION_ERROR", [{ loc: ["body", "payload_hash"], msg: "x" }]),
      id.states.error.title,
      false,
    ],
  ])("%s", (_, error, title, isFinal) => {
    expect(decisionError(t, error)).toMatchObject({ title, isFinal });
  });
});

describe("revealHidden (AG-13 WP-3.1c2 L1)", () => {
  it("shows bidi, zero-width and separator characters as escapes, keeping line breaks and tabs", () => {
    expect(revealHidden("a\u202Eb\u2066c\u200Bd\u2028e\nf\tg")).toBe(
      "a\\u202eb\\u2066c\\u200bd\\u2028e\nf\tg",
    );
    expect(revealHidden("Kalkulator API")).toBe("Kalkulator API");
  });
});

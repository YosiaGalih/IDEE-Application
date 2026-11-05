import { z } from "zod";
import { apiPath, apiRequest, PageMetaSchema, type PageMeta } from "./client";
import { ApiError } from "./errors";

/** `approvals.status` (DATA_MODEL M14, spec WP-3.1c Goal 3). */
export const APPROVAL_STATUSES = [
  "pending",
  "approved",
  "rejected",
  "expired",
  "consumed",
  "superseded",
] as const;
export type ApprovalStatus = (typeof APPROVAL_STATUSES)[number];
/** ADR-024: whether the action behind a decided approval has run. */
export const HANDLER_STATUSES = ["pending", "succeeded", "failed", "not_required"] as const;

/**
 * `Approval` (API §8) + `project_name`, `consumed_at`, `handler_status`, `can_decide` (spec WP-3.1c Lead 16,
 * 3.1c2 Lead 1). `action_type` stays a string: a type this screen does not know yet is shown as it is, not
 * refused.
 */
export const ApprovalSchema = z.object({
  id: z.guid(),
  project_id: z.guid(),
  project_name: z.string(),
  task_id: z.guid().nullable(),
  action_type: z.string(),
  risk_level: z.number().int().min(0).max(4),
  summary: z.string(),
  payload: z.record(z.string(), z.unknown()),
  // The decision binds to this exact value; anything else is not an approval this screen may send.
  payload_hash: z.string().regex(/^[0-9a-f]{64}$/),
  requested_by: z.string(),
  status: z.enum(APPROVAL_STATUSES),
  decided_by: z.guid().nullable(),
  decided_at: z.iso.datetime({ offset: true }).nullable(),
  decision_note: z.string().nullable(),
  expires_at: z.iso.datetime({ offset: true }).nullable(),
  consumed_at: z.iso.datetime({ offset: true }).nullable(),
  handler_status: z.enum(HANDLER_STATUSES),
  created_at: z.iso.datetime({ offset: true }),
  can_decide: z.boolean(),
});
export type Approval = z.infer<typeof ApprovalSchema>;

export type ApprovalFilter = { status: ApprovalStatus | "all"; page: number; pageSize: number };
export type ApprovalPage = { items: Approval[]; meta: PageMeta };

/** `GET /approvals`: `pending` holds only what the caller can decide now; the rest is history (§8). */
export async function listApprovals(filter: ApprovalFilter, signal?: AbortSignal): Promise<ApprovalPage> {
  const params = new URLSearchParams({
    status: filter.status,
    page: String(filter.page),
    page_size: String(filter.pageSize),
  });
  const result = await apiRequest(`/approvals?${params.toString()}`, {
    schema: z.array(ApprovalSchema),
    ...(signal ? { signal } : {}),
  });
  const meta = PageMetaSchema.safeParse(result.meta);
  if (!meta.success) {
    // Without the total, the pending count and a later page would silently be wrong.
    throw new ApiError({
      kind: "bad_response",
      message: `List meta does not match the expected shape: ${meta.error.message}`,
      requestId: result.requestId,
    });
  }
  return { items: result.data, meta: meta.data };
}

export type DecisionInput = { decision: "approve" | "reject"; payload_hash: string; note: string | null };

/**
 * `POST /approvals/{id}/decision`. `payload_hash` is the hash of the approval the person looked at, never
 * one computed here: the server refuses the decision when the action changed since (409).
 */
export async function decideApproval(approvalId: string, input: DecisionInput): Promise<Approval> {
  const body = { decision: input.decision, payload_hash: input.payload_hash, note: input.note };
  return (
    await apiRequest(apiPath("approvals", approvalId, "decision"), {
      method: "POST",
      body,
      schema: ApprovalSchema,
    })
  ).data;
}

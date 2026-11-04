import { z } from "zod";
import { apiRequest } from "./client";
import { ApiError } from "./errors";
import { ProjectSchema } from "./projects";

/** `cost_status` (DATA_MODEL usage_records): `unavailable` is unknown, never 0 (AC-16). */
export const COST_STATUSES = ["estimated", "partial", "actual", "unavailable"] as const;
const UsageSchema = z.object({
  input_tokens: z.number().int().nullable(),
  output_tokens: z.number().int().nullable(),
  estimated_cost: z.number().nullable(),
  cost_status: z.enum(COST_STATUSES),
});

/** `TaskSummary` (API_CONTRACTS §11 #2) + `project_name` (conflict #70). */
export const TaskSummarySchema = z.object({
  id: z.guid(),
  project_id: z.guid(),
  project_name: z.string(),
  title: z.string(),
  status: z.string(),
  status_reason: z.string().nullable(),
  current_step: z.string().nullable(),
  workflow_type: z.string(),
  priority: z.string(),
  iteration: z.number().int(),
  usage: UsageSchema,
  has_pending_approval: z.boolean(),
  created_at: z.iso.datetime({ offset: true }),
  updated_at: z.iso.datetime({ offset: true }),
});
export type TaskSummary = z.infer<typeof TaskSummarySchema>;

/** `GET /dashboard` (API_CONTRACTS §3 + conflict #70). */
export const DashboardSchema = z.object({
  metrics: z.object({
    active_projects: z.number().int(),
    running_tasks: z.number().int(),
    completed_tasks: z.number().int(),
    failed_tasks: z.number().int(),
    waiting_approval: z.number().int(),
  }),
  recent_projects: z.array(ProjectSchema),
  recent_tasks: z.array(TaskSummarySchema),
  failed_queue: z.array(TaskSummarySchema),
  // `AgentRunSummary`; checked when agent runs are recorded (WP-3.1). Until then `meta.unavailable` says so.
  agent_activity: z.array(z.unknown()),
  usage: UsageSchema.extend({
    period: z.object({ start: z.iso.datetime({ offset: true }), end: z.iso.datetime({ offset: true }) }),
  }),
});
export type Dashboard = z.infer<typeof DashboardSchema>;

/** The parts of the dashboard without a data source yet: shown as "not available", never as 0. */
export type DashboardSection = "agent_activity" | "usage";
const DashboardMetaSchema = z.object({
  unavailable: z.array(z.string()),
  failed_queue_total: z.number().int().nonnegative(),
});

export type DashboardResult = { data: Dashboard; unavailable: string[]; failedQueueTotal: number };

export async function getDashboard(signal?: AbortSignal): Promise<DashboardResult> {
  const result = await apiRequest("/dashboard", { schema: DashboardSchema, ...(signal ? { signal } : {}) });
  const meta = DashboardMetaSchema.safeParse(result.meta);
  if (!meta.success) {
    // Without `unavailable`, a part with no data would look like a part with nothing in it.
    throw new ApiError({
      kind: "bad_response",
      message: `Dashboard meta does not match the expected shape: ${meta.error.message}`,
      requestId: result.requestId,
    });
  }
  return {
    data: result.data,
    unavailable: meta.data.unavailable,
    failedQueueTotal: meta.data.failed_queue_total,
  };
}

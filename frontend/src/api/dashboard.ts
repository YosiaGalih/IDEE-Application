import { z } from "zod";
import { apiRequest } from "./client";
import { ApiError } from "./errors";
import { ProjectSchema } from "./projects";
import { TASK_STATUSES } from "./taskStatus";

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
  status: z.enum(TASK_STATUSES),
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

/** `AgentRunSummary` (API_CONTRACTS §11): sent once agent runs are recorded (WP-3.1). */
export const AgentRunSummarySchema = z.object({
  id: z.guid(),
  task_id: z.guid(),
  project_id: z.guid(),
  agent_id: z.string(),
  agent_name: z.string(),
  status: z.string(),
  started_at: z.iso.datetime({ offset: true }),
  duration_ms: z.number().int().nonnegative().nullable(),
});
export type AgentRunSummary = z.infer<typeof AgentRunSummarySchema>;

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
  agent_activity: z.array(AgentRunSummarySchema),
  usage: UsageSchema.extend({
    period: z.object({ start: z.iso.datetime({ offset: true }), end: z.iso.datetime({ offset: true }) }),
  }),
});
export type Dashboard = z.infer<typeof DashboardSchema>;

/** The parts of the dashboard without a data source yet: shown as "not available", never as 0. */
export const DASHBOARD_SECTIONS = ["agent_activity", "usage"] as const;
export type DashboardSection = (typeof DASHBOARD_SECTIONS)[number];
const DashboardMetaSchema = z.object({
  unavailable: z.array(z.enum(DASHBOARD_SECTIONS)),
  failed_queue_total: z.number().int().nonnegative(),
});

export type DashboardResult = {
  data: Dashboard;
  unavailable: ReadonlySet<DashboardSection>;
  failedQueueTotal: number;
};

/** An answer that calls a part unavailable and still sends data for it contradicts itself (conflict #70). */
function contradiction(data: Dashboard, unavailable: ReadonlySet<DashboardSection>): string | null {
  if (unavailable.has("agent_activity") && data.agent_activity.length > 0)
    return "agent_activity is listed as unavailable but has runs";
  const { input_tokens, output_tokens, estimated_cost, cost_status } = data.usage;
  if (unavailable.has("usage") && [input_tokens, output_tokens, estimated_cost].some((v) => v !== null))
    return "usage is listed as unavailable but has values";
  if (unavailable.has("usage") && cost_status !== "unavailable")
    return `usage is listed as unavailable but its cost_status is ${cost_status}`;
  return null;
}

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
  const unavailable = new Set(meta.data.unavailable);
  const problem = contradiction(result.data, unavailable);
  if (problem) {
    throw new ApiError({
      kind: "bad_response",
      message: `Dashboard answer contradicts itself: ${problem}`,
      requestId: result.requestId,
    });
  }
  return { data: result.data, unavailable, failedQueueTotal: meta.data.failed_queue_total };
}

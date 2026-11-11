import { z } from "zod";
import { apiPath, apiRequest } from "./client";
import { ApiError } from "./errors";

/** Registry schema 2 groups, in catalog order (backend app/models/enums.py `AGENT_GROUPS`). */
export const AGENT_GROUPS = [
  "planning_intent",
  "software_development",
  "testing_debugging",
  "quality_security",
  "git_collaboration",
  "devops_operations",
  "documentation_knowledge",
  "specialized_engineering",
  "orchestration",
  "ai_assistant",
  "integrations",
  "portfolio",
] as const;
export type AgentGroup = (typeof AGENT_GROUPS)[number];
export const AGENT_MODES = ["llm", "profile", "tool", "planned"] as const;
export type AgentMode = (typeof AGENT_MODES)[number];
/** FR-045 + `not_ready` (spec WP-3.1b goal 4): "ready" is only claimed when nothing the agent needs is missing. */
export const AGENT_STATUSES = ["ready", "not_ready", "disabled", "planned"] as const;
export type AgentStatus = (typeof AGENT_STATUSES)[number];
export const STATUS_REASONS = [
  "runtime_unavailable",
  "provider_readiness_unknown",
  "provider_not_ready",
  "provider_disabled",
  "tools_unavailable",
] as const;
export type StatusReason = (typeof STATUS_REASONS)[number];
/** `agent_runs.status` (DATA_MODEL M07, ADR-025). */
export const AGENT_RUN_STATUSES = [
  "running",
  "succeeded",
  "failed",
  "cancelled",
  "budget_exceeded",
  "approval_required",
] as const;
export type AgentRunStatus = (typeof AGENT_RUN_STATUSES)[number];

/** The eight permission fields (AGENT_CATALOG §3, §12.2), as the server stores them. Values are shown as they
 * are: an unexpected value (an `approval_grant` other than `deny`) must stay visible, not be hidden. */
export const PERMISSION_FIELDS = [
  "filesystem",
  "sandbox",
  "git",
  "network",
  "deployment",
  "portfolio",
  "data_sensitivity_max",
  "approval_grant",
] as const;
export type PermissionField = (typeof PERMISSION_FIELDS)[number];
const PermissionProfileSchema = z.object(
  Object.fromEntries(PERMISSION_FIELDS.map((field) => [field, z.string()])) as Record<
    PermissionField,
    z.ZodString
  >,
);

/** `AgentProfile` (API §6) + FR-042 fields + status reasons (spec WP-3.1b goal 7). */
export const AgentSchema = z.object({
  agent_id: z.string(),
  slug: z.string(),
  name: z.string(),
  group: z.enum(AGENT_GROUPS),
  version: z.number().int(),
  enabled: z.boolean(),
  mvp_mode: z.enum(AGENT_MODES),
  priority: z.string(),
  description: z.string(),
  purpose: z.string(),
  triggers: z.array(z.string()),
  capabilities: z.array(z.string()),
  tools: z.array(z.string()),
  unavailable_tools: z.array(z.string()),
  permission_profile: PermissionProfileSchema,
  model_tier: z.string(),
  limits: z.object({
    max_iterations: z.number().int(),
    timeout_seconds: z.number().int(),
    tool_quota: z.record(z.string(), z.number().int()).nullable().optional(),
  }),
  runtime_agent: z.string().nullable(),
  input_contract: z.string().nullable(),
  output_contract: z.string().nullable(),
  access_note: z.string(),
  target_mode: z.string().nullable(),
  phase: z.string().nullable(),
  /** "planned": the contract names belong to a planned agent and do not exist yet. */
  contract_status: z.literal("planned").nullable(),
  status: z.enum(AGENT_STATUSES),
  status_reasons: z.array(z.enum(STATUS_REASONS)),
});
export type Agent = z.infer<typeof AgentSchema>;

const countsOf = <T extends readonly [string, ...string[]]>(values: T) =>
  z.object(
    Object.fromEntries(values.map((v) => [v, z.number().int().nonnegative()])) as Record<
      T[number],
      z.ZodNumber
    >,
  );
const AgentListMetaSchema = z.object({
  total: z.number().int().nonnegative(),
  counts: z.object({ mode: countsOf(AGENT_MODES), status: countsOf(AGENT_STATUSES) }),
});
export type AgentCounts = z.infer<typeof AgentListMetaSchema>["counts"];

export type AgentList = { agents: Agent[]; counts: AgentCounts };

const sum = (counts: Record<string, number>) => Object.values(counts).reduce((a, b) => a + b, 0);

/** The chips and cards must tell the same story: every count adds up to the rows actually sent. */
function listProblem(rows: number, meta: z.infer<typeof AgentListMetaSchema>): string | null {
  if (meta.total !== rows) return `Agent list total ${meta.total} does not match ${rows} agents`;
  if (sum(meta.counts.mode) !== rows || sum(meta.counts.status) !== rows) {
    return `Agent list counts do not add up to ${rows} agents`;
  }
  return null;
}

/** `GET /agents`: the whole catalog (it is bounded); filtering happens on the page. */
export async function getAgents(signal?: AbortSignal): Promise<AgentList> {
  const result = await apiRequest("/agents", { schema: z.array(AgentSchema), ...(signal ? { signal } : {}) });
  const bad = (message: string) =>
    new ApiError({ kind: "bad_response", message, requestId: result.requestId });
  const meta = AgentListMetaSchema.safeParse(result.meta);
  if (!meta.success) throw bad(`Agent list meta does not match the expected shape: ${meta.error.message}`);
  const problem = listProblem(result.data.length, meta.data);
  if (problem !== null) throw bad(problem);
  return { agents: result.data, counts: meta.data.counts };
}

/** `AgentRunSummary` (API §11.2), with the run statuses of DATA_MODEL M07. */
export const AgentRunSchema = z.object({
  id: z.guid(),
  task_id: z.guid(),
  project_id: z.guid(),
  agent_id: z.string(),
  agent_name: z.string(),
  status: z.enum(AGENT_RUN_STATUSES),
  started_at: z.iso.datetime({ offset: true }),
  duration_ms: z.number().int().nonnegative().nullable(),
});
export type AgentRun = z.infer<typeof AgentRunSchema>;

/** API §6: `AgentProfile + {recent_runs, stats}`, one flat object. */
export const AgentDetailSchema = AgentSchema.extend({
  recent_runs: z.array(AgentRunSchema),
  stats: z.object({
    runs: z.number().int().nonnegative(),
    success_rate: z.number().min(0).max(1).nullable(),
    avg_duration_ms: z.number().int().nonnegative().nullable(),
  }),
});
export type AgentDetail = z.infer<typeof AgentDetailSchema>;

/** `GET /agents/{id}`: recent runs and statistics are limited by the server to the user's projects. */
export async function getAgent(agentId: string, signal?: AbortSignal): Promise<AgentDetail> {
  const result = await apiRequest(apiPath("agents", agentId), {
    schema: AgentDetailSchema,
    ...(signal ? { signal } : {}),
  });
  return result.data;
}

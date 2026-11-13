import { z } from "zod";
import { apiPath, apiRequest } from "./client";
import { ApiError } from "./errors";

/**
 * Provider settings (API §9, §11 #8, §11.3; WP-3.1e1, ADR-073). The api process never holds the key: what
 * these answers say about it is what the worker last reported.
 */

export const MODEL_ALIASES = ["default", "fast", "complex"] as const;
export type ModelAlias = (typeof MODEL_ALIASES)[number];

/** Whether model calls may go to the provider now (backend `provider_ready`). */
export const EFFECTIVE_STATES = ["ready", "not_ready", "disabled", "unknown"] as const;
export type EffectiveState = (typeof EFFECTIVE_STATES)[number];

/** The nine named checks of the gate, in the order the worker runs them. */
export const CHECK_KEYS = [
  "api_key_valid",
  "model_accessible",
  "messages_ok",
  "streaming_ok",
  "usage_reported",
  "tool_use_roundtrip",
  "timeout_cancel_handled",
  "secret_not_in_logs",
  "pricing_verified",
] as const;

/** Above the server's own wait (SMOKE_WAIT_S ≤ 55 s): a slow worker answers 202, never this timeout. */
export const SMOKE_TIMEOUT_MS = 60_000;

const datetime = z.iso.datetime({ offset: true });

// A reason this screen does not know yet is shown generically, not refused; the state colours the screen.
export const VerdictSchema = z.object({ state: z.enum(EFFECTIVE_STATES), reason: z.string() });
const ReadinessStatusSchema = z.enum(["unknown", "passed", "failed"]);
export type Verdict = z.infer<typeof VerdictSchema>;

export const ProviderSchema = z.object({
  id: z.guid(),
  provider: z.string(),
  base_url: z.string().nullable(),
  model_policy: z.record(z.string(), z.string()),
  effective_models: z.object({ default: z.string(), fast: z.string(), complex: z.string() }),
  secret_status: z.enum(["unknown", "configured", "missing"]),
  secret_hint: z.string().nullable(),
  secret_checked_at: datetime.nullable(),
  enabled: z.boolean(),
  pricing_version: z.string().nullable(),
  llm_provider_mode: z.string().nullable(),
  readiness_status: ReadinessStatusSchema,
  readiness_checked_at: datetime.nullable(),
  readiness_effective: VerdictSchema,
});
export type Provider = z.infer<typeof ProviderSchema>;

export const CheckSchema = z.object({
  key: z.string(),
  label: z.string(),
  status: z.enum(["passed", "failed", "skipped"]),
  detail: z.string(),
});
export type Check = z.infer<typeof CheckSchema>;

/** §11.3: `report_ready` is the gate's own result; `effective` whether it counts now. */
export const ReadinessSchema = z.object({
  provider: z.string(),
  readiness_status: ReadinessStatusSchema,
  checked_at: datetime.nullable(),
  mode: z.string().nullable(),
  report_ready: z.boolean(),
  checks: z.array(CheckSchema),
  models: z.record(z.string(), z.string()).nullable(),
  job_id: z.string().nullable(),
  running_job_id: z.string().nullable(),
  effective: VerdictSchema,
});
export type Readiness = z.infer<typeof ReadinessSchema>;

/** §11 #8: the gate's own result, as the worker recorded it. */
export const SmokeReportSchema = z.object({
  provider: z.string(),
  mode: z.string(),
  ready: z.boolean(),
  checks: z.array(CheckSchema),
  ran_at: datetime,
});
export type SmokeReport = z.infer<typeof SmokeReportSchema>;

const JobSchema = z.object({ job_id: z.guid() });
const SmokeMetaSchema = z.object({ job_id: z.guid(), effective: VerdictSchema });

export type SmokeOutcome =
  | { kind: "finished"; jobId: string; report: SmokeReport; effective: Verdict }
  | { kind: "running"; jobId: string };

export async function listProviders(signal?: AbortSignal): Promise<Provider[]> {
  return (
    await apiRequest("/settings/providers", {
      schema: z.array(ProviderSchema),
      ...(signal ? { signal } : {}),
    })
  ).data;
}

/** An alias sent as `null` drops it (it then uses `CLAUDE_MODEL_*`); base_url and keys are not fields. */
export type ProviderPatch = { model_policy?: Partial<Record<ModelAlias, string | null>>; enabled?: boolean };

export async function patchProvider(providerId: string, patch: ProviderPatch): Promise<Provider> {
  return (
    await apiRequest(apiPath("settings", "providers", providerId), {
      method: "PATCH",
      body: patch,
      schema: ProviderSchema,
    })
  ).data;
}

export async function getReadiness(providerId: string, signal?: AbortSignal): Promise<Readiness> {
  return (
    await apiRequest(apiPath("settings", "providers", providerId, "readiness"), {
      schema: ReadinessSchema,
      ...(signal ? { signal } : {}),
    })
  ).data;
}

/**
 * `POST .../smoke-test`: the report when the worker answered within the server's wait, otherwise the job id
 * of a run still going (202). Never retried: each run makes a few paid calls.
 */
export async function runSmoke(
  providerId: string,
  options: { timeoutMs?: number } = {},
): Promise<SmokeOutcome> {
  const result = await apiRequest(apiPath("settings", "providers", providerId, "smoke-test"), {
    method: "POST",
    schema: z.union([SmokeReportSchema, JobSchema]),
    // 202 answers with the envelope status "accepted" (API §1): the job id of a run still going.
    timeoutMs: options.timeoutMs ?? SMOKE_TIMEOUT_MS,
  });
  if (result.status === "accepted") {
    const job = JobSchema.safeParse(result.data);
    if (job.success) return { kind: "running", jobId: job.data.job_id };
  } else {
    const parsed = SmokeReportSchema.safeParse(result.data);
    const meta = SmokeMetaSchema.safeParse(result.meta);
    if (parsed.success && meta.success) {
      return {
        kind: "finished",
        jobId: meta.data.job_id,
        report: parsed.data,
        effective: meta.data.effective,
      };
    }
  }
  // A 200 without its report or a 202 without its job: never shown as a result.
  throw new ApiError({
    kind: "bad_response",
    message: "Smoke test answer does not match the expected shape",
    requestId: result.requestId,
  });
}

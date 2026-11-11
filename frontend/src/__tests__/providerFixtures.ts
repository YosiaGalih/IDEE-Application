/** Provider settings fixtures in the shapes of API §9, §11 #8 and §11.3 (WP-3.1e1). */

export const PROVIDER_ID = "6f1d2c3b-4a5e-4f60-8a7b-9c0d1e2f3a4b";
export const JOB_ID = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d";
export const OTHER_JOB_ID = "9f8e7d6c-5b4a-4c3d-8e2f-1a0b9c8d7e6f";

export { CHECK_KEYS } from "@/api/providers";
import { CHECK_KEYS } from "@/api/providers";

export function provider(overrides: Record<string, unknown> = {}) {
  return {
    id: PROVIDER_ID,
    provider: "anthropic",
    base_url: null,
    model_policy: {},
    effective_models: {
      default: "claude-sonnet-5-5",
      fast: "claude-haiku-5-5",
      complex: "claude-sonnet-5-5",
    },
    secret_status: "configured",
    secret_hint: "sk-ant-…a1b2",
    secret_checked_at: "2026-11-11T08:00:00Z",
    enabled: true,
    pricing_version: "2026-11-05",
    llm_provider_mode: "anthropic",
    readiness_status: "passed",
    readiness_checked_at: "2026-11-11T08:00:00Z",
    readiness_effective: { state: "ready", reason: "passed" },
    ...overrides,
  };
}

export function checks(failed: string | null = null) {
  return CHECK_KEYS.map((key) => ({
    key,
    label: key,
    status: key === failed ? "failed" : "passed",
    detail: key === failed ? `${key} failed: overloaded (HTTP 529)` : `${key} ok`,
  }));
}

export function readiness(overrides: Record<string, unknown> = {}) {
  return {
    provider: "anthropic",
    readiness_status: "passed",
    checked_at: "2026-11-11T08:00:00+00:00",
    mode: "anthropic",
    report_ready: true,
    checks: checks(),
    models: { default: "claude-sonnet-5-5", fast: "claude-haiku-5-5", complex: "claude-sonnet-5-5" },
    job_id: JOB_ID,
    running_job_id: null,
    effective: { state: "ready", reason: "passed" },
    ...overrides,
  };
}

export function report(overrides: Record<string, unknown> = {}) {
  return {
    provider: "anthropic",
    mode: "anthropic",
    ready: true,
    checks: checks(),
    ran_at: "2026-11-11T08:05:00+00:00",
    ...overrides,
  };
}

/** Dashboard answers for tests (API_CONTRACTS §3 `GET /dashboard` + conflict #70). */

export const PROJECT_ID = "6f2c1d3e-0a4b-4c5d-8e9f-0a1b2c3d4e5f";
const OWNER_ID = "0b6a3f1e-6a1f-4e0e-9a51-6c1c8d7f2a10";
const UNAVAILABLE_USAGE = {
  input_tokens: null as number | null,
  output_tokens: null as number | null,
  estimated_cost: null as string | null, // a plain decimal string (WP-3.1f1)
  cost_status: "unavailable" as string,
};

export function project(overrides: Record<string, unknown> = {}) {
  return {
    id: PROJECT_ID,
    name: "Kalkulator",
    slug: "kalkulator",
    description: null,
    status: "active",
    stack: {
      languages: ["python"],
      frameworks: [],
      package_managers: ["pip"],
      test_profiles: [],
      entrypoints: [],
    },
    default_branch: "main",
    settings: {},
    owner_id: OWNER_ID,
    created_at: "2026-11-01T08:00:00+00:00",
    updated_at: "2026-11-02T09:30:00+00:00",
    repository: {
      provider: "template",
      remote_url: null,
      branch: "main",
      sync_state: "ready",
      last_indexed_at: null,
      error: null as string | null,
    },
    permissions: ["project:read", "project:write", "workspace:read"],
    stats: null,
    ...overrides,
  };
}

export function task(overrides: Record<string, unknown> = {}) {
  return {
    id: "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
    project_id: PROJECT_ID,
    project_name: "Kalkulator",
    title: "Tambahkan fungsi multiply",
    status: "failed",
    status_reason: "tests failed",
    current_step: "test",
    workflow_type: "modify",
    priority: "normal",
    iteration: 1,
    usage: UNAVAILABLE_USAGE,
    has_pending_approval: false,
    created_at: "2026-11-03T08:00:00+00:00",
    updated_at: "2026-11-03T09:00:00+00:00",
    ...overrides,
  };
}

export function agentRun(overrides: Record<string, unknown> = {}) {
  return {
    id: "3c4d5e6f-7a8b-4c9d-8e0f-1a2b3c4d5e6f",
    task_id: "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
    project_id: PROJECT_ID,
    agent_id: "B14",
    agent_name: "General Code Implementation Agent",
    status: "succeeded",
    started_at: "2026-11-03T09:00:00+00:00",
    duration_ms: 1200,
    ...overrides,
  };
}

export function dashboardData() {
  return {
    // A distinct value per metric: a number shown under the wrong label cannot pass.
    metrics: {
      active_projects: 2,
      running_tasks: 4,
      completed_tasks: 3,
      failed_tasks: 1,
      waiting_approval: 5,
    },
    recent_projects: [project()],
    recent_tasks: [task()],
    failed_queue: [task()],
    agent_activity: [] as ReturnType<typeof agentRun>[],
    usage: {
      period: { start: "2026-11-01T00:00:00Z", end: "2026-12-01T00:00:00Z" },
      ...UNAVAILABLE_USAGE,
    },
  };
}

export function dashboardMeta(overrides: Record<string, unknown> = {}) {
  return { unavailable: ["agent_activity", "usage"], failed_queue_total: 1, ...overrides };
}

import type { Approval } from "@/api/approvals";

/** A pending `project_delete` approval as the server sends it (spec WP-3.1c Goal 6 + 3.1c2 Lead 1). */
export function approval(overrides: Partial<Approval> = {}): Approval {
  const projectId = overrides.project_id ?? "7d1c9a52-3e1b-4c8e-9f0a-2b6d4e8f1a30";
  const name = overrides.project_name ?? "Kalkulator API";
  return {
    id: "5f2e8c41-9b7a-4d3e-8c1f-0a6b2d4e9f71",
    project_id: projectId,
    project_name: name,
    task_id: null,
    action_type: "project_delete",
    risk_level: 3,
    summary: "Delete project (workspace kept 7 days, then purged)",
    payload: { project_id: projectId, name, previous_status: "active" },
    payload_hash: "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
    requested_by: "user:0b6a3f1e-6a1f-4e0e-9a51-6c1c8d7f2a10",
    status: "pending",
    decided_by: null,
    decided_at: null,
    decision_note: null,
    expires_at: "2026-11-12T09:18:20+00:00",
    consumed_at: null,
    handler_status: "pending",
    created_at: "2026-11-05T09:18:20+00:00",
    can_decide: true,
    ...overrides,
  };
}

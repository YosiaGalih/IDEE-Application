import type { Agent, AgentCounts, AgentDetail, AgentRun } from "@/api/agents";

const DENY_ALL = {
  filesystem: "read_only",
  sandbox: "deny",
  git: "none",
  network: "deny",
  deployment: "deny",
  portfolio: "none",
  data_sensitivity_max: "confidential",
  approval_grant: "deny",
};

export function agent(overrides: Partial<Agent> = {}): Agent {
  return {
    agent_id: "A02",
    slug: "requirement_analyst",
    name: "Requirement Analyst",
    group: "planning_intent",
    version: 1,
    enabled: true,
    mvp_mode: "llm",
    priority: "P0",
    description: "Turns a prompt into requirements.",
    purpose: "Clear requirements with acceptance criteria.",
    triggers: ["task_created"],
    capabilities: ["requirements.extract"],
    tools: ["repo.get_tree", "repo.read_file", "submit_result"],
    unavailable_tools: ["repo.get_tree", "repo.read_file"],
    permission_profile: { ...DENY_ALL },
    model_tier: "default",
    limits: { max_iterations: 8, timeout_seconds: 300 },
    runtime_agent: null,
    input_contract: "IntakeInput",
    output_contract: "IntakeOutput",
    access_note: "Read-only.",
    target_mode: null,
    phase: null,
    contract_status: null,
    status: "not_ready",
    status_reasons: ["runtime_unavailable", "provider_readiness_unknown", "tools_unavailable"],
    ...overrides,
  };
}

/** A small catalog: one agent per mode, two groups, one planned. */
export function catalog(): Agent[] {
  return [
    agent(),
    agent({
      agent_id: "A01",
      slug: "intent_classifier",
      name: "Intent Classifier",
      mvp_mode: "profile",
      runtime_agent: "A02",
      model_tier: "fast",
    }),
    agent({
      agent_id: "E27",
      slug: "git_operator",
      name: "Git Operator",
      group: "git_collaboration",
      mvp_mode: "tool",
      tools: ["git.status", "git.commit_task"],
      unavailable_tools: ["git.status", "git.commit_task"],
      input_contract: null,
      output_contract: null,
      status_reasons: ["tools_unavailable"],
      permission_profile: { ...DENY_ALL, git: "branch_commit" },
    }),
    agent({
      agent_id: "F32",
      slug: "release_manager",
      name: "Release Manager",
      group: "devops_operations",
      mvp_mode: "planned",
      enabled: false,
      priority: "P2",
      tools: [],
      unavailable_tools: [],
      input_contract: null,
      output_contract: null,
      status: "planned",
      status_reasons: [],
    }),
  ];
}

export function counts(agents: Agent[] = catalog()): AgentCounts {
  const by = <K extends keyof Agent>(key: K, value: Agent[K]) =>
    agents.filter((a) => a[key] === value).length;
  return {
    mode: {
      llm: by("mvp_mode", "llm"),
      profile: by("mvp_mode", "profile"),
      tool: by("mvp_mode", "tool"),
      planned: by("mvp_mode", "planned"),
    },
    status: {
      ready: by("status", "ready"),
      not_ready: by("status", "not_ready"),
      disabled: by("status", "disabled"),
      planned: by("status", "planned"),
    },
  };
}

export function run(overrides: Partial<AgentRun> = {}): AgentRun {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    task_id: "22222222-2222-4222-8222-222222222222",
    project_id: "33333333-3333-4333-8333-333333333333",
    agent_id: "A02",
    agent_name: "Requirement Analyst",
    status: "succeeded",
    started_at: "2026-11-05T08:00:00+00:00",
    duration_ms: 1500,
    ...overrides,
  };
}

/** API §6: the agent's own fields plus its runs and statistics, one flat object. */
export function detail(overrides: Partial<AgentDetail> = {}): AgentDetail {
  return {
    ...agent(),
    recent_runs: [],
    stats: { runs: 0, success_rate: null, avg_duration_ms: null },
    ...overrides,
  };
}
